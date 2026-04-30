"""End-to-end wiring test: real experiment_core.Session + contingency.FR
feeding NonBlockingEventSink via SessionEventBridge, aggregated and read
as a snapshot.

Skipped automatically when experiment-core / contingency-py are not
installed so that the default ``session-visualizer[test]`` install (which
has no sibling dependencies) still passes.
"""

from __future__ import annotations

import time

import pytest

from session_visualizer import (
    LiveAggregator,
    NonBlockingEventSink,
    SessionEventBridge,
)

pytest.importorskip("experiment_core")
pytest.importorskip("contingency")

from contingency.schedules import FR  # noqa: E402
from experiment_core import ResponseEvent, Session, SessionState  # noqa: E402
from experiment_core.exit_condition import ReinforcementCountExit  # noqa: E402


def _wait_until(pred, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


@pytest.mark.integration
def test_fr3_session_flows_through_sink_and_aggregator() -> None:
    session = Session(
        schedule=FR(3),
        exit_condition=ReinforcementCountExit(count=2),
    )
    sink = NonBlockingEventSink(maxsize=256)
    bridge = SessionEventBridge(session, sinks=[sink])

    with LiveAggregator(sink, poll_interval=0.005) as agg:
        session.start(0.0)
        bridge.sync()

        total = 0
        for i in range(10):
            t = (i + 1) * 0.1
            total += 1
            session.events.append(ResponseEvent(id=total, timestamp=t))
            session.update(t, total)
            bridge.sync()
            if session.state == SessionState.REINFORCING:
                session.end_reinforcement(t + 0.05)
                bridge.sync()
            if session.state == SessionState.FINISHED:
                break

        assert _wait_until(
            lambda: len(agg.snapshot().reinforcement_times) == 2,
            timeout=1.0,
        ), f"snapshot stalled: {agg.snapshot()}"

        snap = agg.snapshot()

    assert session.state == SessionState.FINISHED
    assert session.reinforcement_count == 2
    assert len(snap.response_times) == 6, snap.response_times
    assert len(snap.reinforcement_times) == 2
    assert snap.state in {"FINISHED", "RUNNING", "REINFORCING"}
    assert snap.sink_stats.dropped == 0
    assert snap.sink_stats.emitted == snap.sink_stats.enqueued


@pytest.mark.integration
def test_bridge_cursor_is_monotonic() -> None:
    """Sync called many times must never re-emit the same event."""
    session = Session(schedule=FR(2))
    sink = NonBlockingEventSink(maxsize=256)
    bridge = SessionEventBridge(session, sinks=[sink])

    session.start(0.0)
    bridge.sync()
    bridge.sync()
    bridge.sync()

    session.events.append(ResponseEvent(id=1, timestamp=0.1))
    session.update(0.1, 1)
    session.events.append(ResponseEvent(id=2, timestamp=0.2))
    session.update(0.2, 2)

    for _ in range(5):
        bridge.sync()

    drained = sink.drain()
    ids = [
        e.id
        for e in drained
        if getattr(e, "id", None) is not None and type(e).__name__ == "ResponseEvent"
    ]
    assert ids == [1, 2]


@pytest.mark.integration
def test_emit_latency_stays_bounded_under_bursty_load() -> None:
    """The sink must not add per-event latency beyond a small upper bound."""
    sink = NonBlockingEventSink(maxsize=16)

    start = time.perf_counter()
    for i in range(100_000):
        sink.emit(ResponseEvent(id=i, timestamp=float(i) * 1e-6))
    elapsed = time.perf_counter() - start

    per_event_us = (elapsed / 100_000) * 1e6
    assert per_event_us < 20, f"emit() too slow: {per_event_us:.2f} us/event"
    stats = sink.stats()
    assert stats.emitted == 100_000
    assert stats.dropped >= 100_000 - 16
