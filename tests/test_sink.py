"""Tests for the non-blocking sink.

Focus: the experiment-thread guarantees — emit() must not block, must not
raise, and must preserve counters accurately even under drop conditions.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import pytest

from session_visualizer.sink import NonBlockingEventSink


@dataclass
class _FakeEvent:
    timestamp: float


def test_emit_and_drain_roundtrip() -> None:
    sink = NonBlockingEventSink(maxsize=10)
    for i in range(5):
        sink.emit(_FakeEvent(timestamp=float(i)))
    events = sink.drain()
    assert [e.timestamp for e in events] == [0.0, 1.0, 2.0, 3.0, 4.0]
    stats = sink.stats()
    assert stats.emitted == 5
    assert stats.enqueued == 5
    assert stats.dropped == 0


def test_emit_never_blocks_when_full() -> None:
    sink = NonBlockingEventSink(maxsize=2)
    sink.emit(_FakeEvent(timestamp=0.0))
    sink.emit(_FakeEvent(timestamp=1.0))

    start = time.monotonic()
    for i in range(1000):
        sink.emit(_FakeEvent(timestamp=float(i)))
    elapsed = time.monotonic() - start

    assert elapsed < 0.2, f"emit() blocked or was too slow: {elapsed:.3f}s"
    stats = sink.stats()
    assert stats.emitted == 1002
    assert stats.enqueued == 2
    assert stats.dropped == 1000


def test_rejects_unbounded_queue() -> None:
    with pytest.raises(ValueError):
        NonBlockingEventSink(maxsize=0)
    with pytest.raises(ValueError):
        NonBlockingEventSink(maxsize=-1)


def test_concurrent_emit_is_safe() -> None:
    sink = NonBlockingEventSink(maxsize=5000)

    def producer(n: int) -> None:
        for i in range(n):
            sink.emit(_FakeEvent(timestamp=float(i)))

    threads = [threading.Thread(target=producer, args=(500,)) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    stats = sink.stats()
    assert stats.emitted == 4000
    assert stats.enqueued + stats.dropped == 4000


def test_drain_with_limit() -> None:
    sink = NonBlockingEventSink(maxsize=100)
    for i in range(20):
        sink.emit(_FakeEvent(timestamp=float(i)))
    first = sink.drain(limit=5)
    assert len(first) == 5
    assert sink.qsize() == 15
    rest = sink.drain()
    assert len(rest) == 15
