from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional


@dataclass(frozen=True)
class AgentStreamEvent:
    type: str
    iteration: int
    timestamp: datetime
    message: Optional[str] = None
    name: Optional[str] = None
    formatted_args: Optional[str] = None


class AgentStreamEmitter:
    def __init__(self, on_event: Optional[Callable[[AgentStreamEvent], None]] = None) -> None:
        self._on_event = on_event

    def emit_text(self, message: str, iteration: int) -> None:
        self.emit(AgentStreamEvent("text", iteration, datetime.now(timezone.utc), message=message))

    def emit_tool_call(self, name: str, formatted_args: str, iteration: int) -> None:
        self.emit(
            AgentStreamEvent(
                "tool_call",
                iteration,
                datetime.now(timezone.utc),
                name=name,
                formatted_args=formatted_args,
            )
        )

    def emit(self, event: AgentStreamEvent) -> None:
        if not self._on_event:
            return
        try:
            self._on_event(event)
        except Exception:
            return


class TextDeltaBuffer:
    def __init__(
        self,
        on_flush: Callable[[str], None],
        length_threshold: int = 80,
        debounce_seconds: float = 0.05,
    ) -> None:
        self._on_flush = on_flush
        self._length_threshold = length_threshold
        self._debounce_seconds = debounce_seconds
        self._buffer = ""
        self._timer: Optional[threading.Timer] = None
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._buffer += text
            self._clear_timer_locked()
            if self._should_flush_locked():
                self._flush_locked()
                return
            self._timer = threading.Timer(self._debounce_seconds, self.flush)
            self._timer.daemon = True
            self._timer.start()

    def flush(self) -> None:
        with self._lock:
            self._clear_timer_locked()
            self._flush_locked()

    def dispose(self) -> None:
        self.flush()

    def _should_flush_locked(self) -> bool:
        if "\n" in self._buffer:
            return True
        if self._buffer.endswith(". ") or self._buffer.endswith("! ") or self._buffer.endswith("? "):
            return True
        return len(self._buffer) >= self._length_threshold

    def _flush_locked(self) -> None:
        if not self._buffer:
            return
        text = self._buffer
        self._buffer = ""
        self._on_flush(text)

    def _clear_timer_locked(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
