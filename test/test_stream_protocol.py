"""Standalone tests for the typed streaming protocol and task lifecycle."""

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
)


class CloseTracker:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


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
        self.assertTrue(stream.closed)
        task.transition(StreamTaskState.ABORTED)
        self.assertFalse(task.is_active)
        late_stream = CloseTracker()
        task.bind_stream(late_stream)
        self.assertTrue(late_stream.closed)

    def test_illegal_terminal_transition_is_rejected(self):
        task = StreamTask("conv-1", StreamEventQueue(conversation_id="conv-1"))
        with self.assertRaises(RuntimeError):
            task.transition(StreamTaskState.COMPLETED)


if __name__ == "__main__":
    unittest.main()
