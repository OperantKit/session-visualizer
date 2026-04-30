"""Non-blocking EventSink for live visualization.

Conforms structurally to ``experiment_core.EventSink`` (a ``Protocol``
with a single ``emit(event)`` method). The implementation trades
completeness for realtime safety: ``emit`` is O(1), lock-free on the hot
path (``queue.Queue.put_nowait`` uses a mutex but never waits), and will
drop rather than block when the queue is saturated.

Consumers drain the queue asynchronously. See :mod:`.aggregator`.
"""

from __future__ import annotations

import queue
import threading
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SinkStats:
    """Immutable snapshot of sink counters.

    Attributes
    ----------
    emitted:
        Total number of events passed to :meth:`NonBlockingEventSink.emit`.
    enqueued:
        Number of events that were successfully placed on the queue.
    dropped:
        Number of events discarded because the queue was full at the
        moment of :meth:`emit`. Equal to ``emitted - enqueued``.
    """

    emitted: int
    enqueued: int
    dropped: int


class NonBlockingEventSink:
    """EventSink that never blocks the caller.

    Parameters
    ----------
    maxsize:
        Bounded queue capacity. Once reached, subsequent events are
        dropped until the aggregator drains the queue. ``0`` is rejected
        (interpreted as unbounded by :class:`queue.Queue`, which would
        violate the non-blocking guarantee under memory pressure).
    """

    def __init__(self, maxsize: int = 4096) -> None:
        if maxsize <= 0:
            raise ValueError("maxsize must be positive (unbounded queue is unsafe)")
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=maxsize)
        self._lock = threading.Lock()
        self._emitted = 0
        self._enqueued = 0
        self._dropped = 0

    def emit(self, event: Any) -> None:
        """Accept a ``SessionEvent`` (or any object) without blocking.

        The caller is the experiment hot path. This method must never
        raise, never block, and never allocate unbounded memory.
        """
        try:
            self._queue.put_nowait(event)
            with self._lock:
                self._emitted += 1
                self._enqueued += 1
        except queue.Full:
            with self._lock:
                self._emitted += 1
                self._dropped += 1

    def get(self, timeout: float | None = None) -> Any:
        """Block the *consumer* (not the experiment) waiting for an event."""
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> Any:
        """Return immediately; raise :class:`queue.Empty` if no event is pending."""
        return self._queue.get_nowait()

    def drain(self, limit: int | None = None) -> list[Any]:
        """Drain up to ``limit`` events from the queue without blocking.

        Returns an empty list if no events are pending. ``limit=None``
        drains everything currently queued. The aggregator uses this to
        batch-process events and avoid per-event thread wake-ups.
        """
        out: list[Any] = []
        while limit is None or len(out) < limit:
            try:
                out.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return out

    def stats(self) -> SinkStats:
        with self._lock:
            return SinkStats(
                emitted=self._emitted,
                enqueued=self._enqueued,
                dropped=self._dropped,
            )

    def qsize(self) -> int:
        """Approximate queue depth. Not authoritative under concurrent load."""
        return self._queue.qsize()
