from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional, Union


class CancelledError(Exception):
    """Raised when a caller-provided cancellation signal is triggered."""


@dataclass
class CancellationToken:
    """Small cancellation primitive for synchronous SwarmBox operations."""

    _event: threading.Event = field(default_factory=threading.Event)
    reason: Optional[Union[BaseException, str]] = None

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = None

    def cancel(self, reason: Optional[Union[BaseException, str]] = None) -> None:
        self.reason = reason
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def throw_if_cancelled(self) -> None:
        if self._event.is_set():
            if isinstance(self.reason, BaseException):
                raise self.reason
            raise CancelledError(str(self.reason or "Operation cancelled"))


def is_cancelled(signal: Any) -> bool:
    if signal is None:
        return False
    if hasattr(signal, "throw_if_cancelled"):
        try:
            signal.throw_if_cancelled()
            return False
        except Exception:
            return True
    if hasattr(signal, "is_cancelled"):
        return bool(signal.is_cancelled())
    if hasattr(signal, "is_set"):
        return bool(signal.is_set())
    if hasattr(signal, "aborted"):
        return bool(signal.aborted)
    return False


def throw_if_cancelled(signal: Any) -> None:
    if signal is None:
        return
    if hasattr(signal, "throw_if_cancelled"):
        signal.throw_if_cancelled()
        return
    if hasattr(signal, "throwIfAborted"):
        signal.throwIfAborted()
        return
    if is_cancelled(signal):
        reason = getattr(signal, "reason", None)
        if isinstance(reason, BaseException):
            raise reason
        raise CancelledError(str(reason or "Operation cancelled"))
