"""Standalone tests for the typed streaming protocol and task lifecycle."""

import queue
import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from claude_chat.stream_protocol import (
    StreamEventQueue,
    StreamEventType,
    StreamTask,
    StreamTaskState,
    is_stream_error_message,
    stream_error_content,
)


class CloseTracker:
    def __init__(self):
        self.closed = False
        self.close_called = threading.Event()

    def close(self):
        self.closed = True
        self.close_called.set()


class StreamProtocolTests(unittest.TestCase):
    def test_legacy_events_become_versioned_wire_events(self):
        events = StreamEventQueue(task_id="task-1", conversation_id="conv-1")
        events.put(("text", "hello"))
        events.put(("done", {"input_tokens": 2, "output_tokens": 1}))

        first = events.get_event()
        second = events.get_event()
        self.assertEqual(first.type, StreamEventType.TEXT)
        self.assertEqual(first.sequence, 1)
        self.assertEqual(second.sequence, 2)
        self.assertEqual(second.to_wire()["task_state"], "completed")
        self.assertEqual(
            first.to_wire(),
            {
                "version": 1,
                "task_id": "task-1",
                "conversation_id": "conv-1",
                "sequence": 1,
                "type": "text",
                "task_state": "running",
                "data": "hello",
            },
        )

    def test_legacy_get_remains_compatible(self):
        events = StreamEventQueue()
        events.put(("thinking", "step"))
        self.assertEqual(events.get(), ("thinking", "step"))

    def test_unknown_event_is_rejected(self):
        events = StreamEventQueue()
        with self.assertRaises(ValueError):
            events.put(("mystery", {}))

    def test_concurrent_producers_keep_wire_sequence_ordered(self):
        events = StreamEventQueue()
        threads = [
            threading.Thread(target=lambda: [events.put(("text", "x")) for _ in range(50)])
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        sequences = [events.get_event().sequence for _ in range(100)]
        self.assertEqual(sequences, list(range(1, 101)))

    def test_task_state_and_abort_are_explicit(self):
        task = StreamTask("conv-1", StreamEventQueue(conversation_id="conv-1"))
        self.assertEqual(task.state, StreamTaskState.STARTING)
        task.transition(StreamTaskState.RUNNING)
        stream = CloseTracker()
        task.active_stream = stream
        task.request_abort()
        self.assertEqual(task.state, StreamTaskState.CANCELLING)
        self.assertTrue(task.abort_event.is_set())
        self.assertTrue(stream.close_called.wait(1))
        self.assertTrue(stream.closed)
        task.transition(StreamTaskState.ABORTED)
        self.assertFalse(task.is_active)
        late_stream = CloseTracker()
        task.bind_stream(late_stream)
        self.assertTrue(late_stream.closed)

    def test_abort_wakes_reader_before_a_blocked_close_and_drops_late_output(self):
        events = StreamEventQueue(conversation_id="conv-1")
        task = StreamTask("conv-1", events)
        release = threading.Event()
        entered = threading.Event()

        class BlockedStream:
            def close(self):
                entered.set()
                release.wait(2)

        task.bind_stream(BlockedStream())
        events.put(("text", "partial"))
        try:
            task.request_abort()
            self.assertTrue(entered.wait(1))
            self.assertFalse(release.is_set())
            self.assertEqual(events.get(), ("text", "partial"))
            self.assertEqual(events.get(), ("aborted", {}))
            task.request_abort()
            events.put(("text", "late"))
            events.put(("error", "closed transport"))
            events.put(("done", {}))
            with self.assertRaises(queue.Empty):
                events.get_event(block=False)
        finally:
            release.set()

    def test_completion_winning_abort_race_remains_completed(self):
        events = StreamEventQueue(conversation_id="conv-1")
        task = StreamTask("conv-1", events)
        task.transition(StreamTaskState.RUNNING)
        events.put(("done", {}))
        task.request_abort()
        self.assertFalse(task.abort_event.is_set())
        task.finish_for_event(events.get_event().type)
        self.assertEqual(task.state, StreamTaskState.COMPLETED)

    def test_illegal_terminal_transition_is_rejected(self):
        task = StreamTask("conv-1", StreamEventQueue(conversation_id="conv-1"))
        with self.assertRaises(RuntimeError):
            task.transition(StreamTaskState.COMPLETED)

    def test_persisted_error_blocks_are_identifiable_and_keep_details(self):
        content = stream_error_content({"text": "temperature must be 1"})
        message = {"role": "assistant", "content": content}
        self.assertTrue(is_stream_error_message(message))
        self.assertIn("temperature must be 1", content[0]["text"])
        self.assertFalse(is_stream_error_message({"role": "assistant", "content": "normal"}))


if __name__ == "__main__":
    unittest.main()
