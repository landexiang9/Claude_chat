"""Typed protocol and lifecycle primitives for model streaming tasks."""

from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, TypeAlias, TypedDict


STREAM_PROTOCOL_VERSION = 1


class StreamEventType(str, Enum):
    TEXT = "text"
    THINKING = "thinking"
    SEARCH_START = "search_start"
    SEARCH_DONE = "search_done"
    FETCH_START = "fetch_start"
    FETCH_DONE = "fetch_done"
    DONE = "done"
    ABORTED = "aborted"
    ERROR = "error"


class StreamTaskState(str, Enum):
    STARTING = "starting"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    ABORTED = "aborted"
    FAILED = "failed"


ACTIVE_STREAM_STATES = {
    StreamTaskState.STARTING,
    StreamTaskState.RUNNING,
    StreamTaskState.CANCELLING,
}
TERMINAL_EVENT_TYPES = {
    StreamEventType.DONE,
    StreamEventType.ABORTED,
    StreamEventType.ERROR,
}

StreamPayload: TypeAlias = str | dict[str, Any]
LegacyStreamEvent: TypeAlias = tuple[str, StreamPayload]


class StreamEventWire(TypedDict):
    version: int
    task_id: str
    conversation_id: str | None
    sequence: int
    type: str
    task_state: str
    data: StreamPayload


@dataclass(frozen=True, slots=True)
class StreamEvent:
    task_id: str
    conversation_id: str | None
    sequence: int
    type: StreamEventType
    data: StreamPayload
    version: int = STREAM_PROTOCOL_VERSION

    def to_wire(self) -> StreamEventWire:
        task_state = {
            StreamEventType.DONE: StreamTaskState.COMPLETED,
            StreamEventType.ABORTED: StreamTaskState.ABORTED,
            StreamEventType.ERROR: StreamTaskState.FAILED,
        }.get(self.type, StreamTaskState.RUNNING)
        return {
            "version": self.version,
            "task_id": self.task_id,
            "conversation_id": self.conversation_id,
            "sequence": self.sequence,
            "type": self.type.value,
            "task_state": task_state.value,
            "data": self.data,
        }

    def to_legacy(self) -> LegacyStreamEvent:
        return self.type.value, self.data


def _normalize_payload(event_type: StreamEventType, payload: Any) -> StreamPayload:
    if event_type in {StreamEventType.TEXT, StreamEventType.THINKING, StreamEventType.ERROR}:
        if event_type == StreamEventType.ERROR and isinstance(payload, Mapping):
            return dict(payload)
        return str(payload or "")
    if isinstance(payload, Mapping):
        return dict(payload)
    return {}


class StreamEventQueue:
    """Queue adapter accepting legacy tuples while exposing typed events internally."""

    def __init__(self, task_id: str | None = None, conversation_id: str | None = None):
        self.task_id = task_id or uuid.uuid4().hex
        self.conversation_id = conversation_id
        self._queue: queue.Queue[StreamEvent] = queue.Queue()
        self._sequence = 0
        self._lock = threading.Lock()

    def put(self, item: StreamEvent | LegacyStreamEvent, block: bool = True, timeout: float | None = None):
        with self._lock:
            if isinstance(item, StreamEvent):
                event = item
            else:
                if not isinstance(item, tuple) or len(item) != 2:
                    raise TypeError("流事件必须是 StreamEvent 或 (type, data) 二元组")
                raw_type, payload = item
                try:
                    event_type = StreamEventType(raw_type)
                except ValueError as exc:
                    raise ValueError(f"未知流事件类型: {raw_type}") from exc
                self._sequence += 1
                sequence = self._sequence
                event = StreamEvent(
                    task_id=self.task_id,
                    conversation_id=self.conversation_id,
                    sequence=sequence,
                    type=event_type,
                    data=_normalize_payload(event_type, payload),
                )
            self._queue.put(event, block=block, timeout=timeout)

    def get_event(self, block: bool = True, timeout: float | None = None) -> StreamEvent:
        return self._queue.get(block=block, timeout=timeout)

    def get(self, block: bool = True, timeout: float | None = None) -> LegacyStreamEvent:
        """Compatibility API for existing scripts that still unpack ``(type, data)``."""
        return self.get_event(block=block, timeout=timeout).to_legacy()

    def empty(self) -> bool:
        return self._queue.empty()


_ALLOWED_TRANSITIONS = {
    StreamTaskState.STARTING: {
        StreamTaskState.RUNNING,
        StreamTaskState.CANCELLING,
        StreamTaskState.ABORTED,
        StreamTaskState.FAILED,
    },
    StreamTaskState.RUNNING: {
        StreamTaskState.CANCELLING,
        StreamTaskState.COMPLETED,
        StreamTaskState.ABORTED,
        StreamTaskState.FAILED,
    },
    StreamTaskState.CANCELLING: {StreamTaskState.ABORTED, StreamTaskState.FAILED},
}


@dataclass(slots=True)
class StreamTask:
    conversation_id: str
    events: StreamEventQueue
    task_id: str = field(init=False)
    state: StreamTaskState = StreamTaskState.STARTING
    abort_event: threading.Event = field(default_factory=threading.Event)
    active_stream: Any = None
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    def __post_init__(self):
        self.task_id = self.events.task_id

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self.state in ACTIVE_STREAM_STATES

    def transition(self, new_state: StreamTaskState):
        with self._lock:
            if new_state == self.state:
                return
            allowed = _ALLOWED_TRANSITIONS.get(self.state, set())
            if new_state not in allowed:
                raise RuntimeError(f"非法流任务状态转换: {self.state.value} -> {new_state.value}")
            self.state = new_state

    def request_abort(self):
        with self._lock:
            if self.state in {StreamTaskState.STARTING, StreamTaskState.RUNNING}:
                self.state = StreamTaskState.CANCELLING
            self.abort_event.set()
            stream = self.active_stream
            self.active_stream = None
        if stream:
            try:
                stream.close()
            except Exception:
                pass

    def bind_stream(self, stream):
        """Attach a transport, closing it immediately if cancellation already won the race."""
        with self._lock:
            should_close = self.abort_event.is_set() or self.state not in ACTIVE_STREAM_STATES
            if not should_close:
                self.active_stream = stream
        if should_close and stream:
            try:
                stream.close()
            except Exception:
                pass

    def clear_stream(self):
        with self._lock:
            self.active_stream = None

    def finish_for_event(self, event_type: StreamEventType):
        target = {
            StreamEventType.DONE: StreamTaskState.COMPLETED,
            StreamEventType.ABORTED: StreamTaskState.ABORTED,
            StreamEventType.ERROR: StreamTaskState.FAILED,
        }.get(event_type)
        if target:
            self.transition(target)


def ensure_stream_event_queue(value: Any, conversation_id: str) -> StreamEventQueue:
    if isinstance(value, StreamEventQueue):
        return value
    if value is not None:
        raise TypeError("custom_queue 必须是 StreamEventQueue")
    return StreamEventQueue(conversation_id=conversation_id)
