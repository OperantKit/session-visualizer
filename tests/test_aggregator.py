"""Tests for the background aggregator and snapshot accessor."""

from __future__ import annotations

import time
from dataclasses import dataclass

from session_visualizer.aggregator import LiveAggregator
from session_visualizer.sink import NonBlockingEventSink


@dataclass
class ResponseEvent:
    id: int
    timestamp: float


@dataclass
class ReinforcerStartEvent:
    id: int
    timestamp: float
    potency: float = 1.0


class _FakeState:
    def __init__(self, name: str) -> None:
        self.name = name


@dataclass
class StateChangeEvent:
    from_state: _FakeState
    to_state: _FakeState
    timestamp: float


def _wait_until(pred, timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if pred():
            return True
        time.sleep(0.01)
    return False


def test_aggregator_accumulates_dataclass_events() -> None:
    sink = NonBlockingEventSink(maxsize=100)
    with LiveAggregator(sink, poll_interval=0.005) as agg:
        sink.emit(ResponseEvent(id=1, timestamp=0.1))
        sink.emit(ResponseEvent(id=2, timestamp=0.2))
        sink.emit(ReinforcerStartEvent(id=1, timestamp=0.25))
        sink.emit(
            StateChangeEvent(
                from_state=_FakeState("IDLE"),
                to_state=_FakeState("RUNNING"),
                timestamp=0.0,
            )
        )
        assert _wait_until(lambda: len(agg.snapshot().response_times) == 2)
        snap = agg.snapshot()
        assert snap.response_times == (0.1, 0.2)
        assert snap.reinforcement_times == (0.25,)
        assert snap.state == "RUNNING"


def test_aggregator_accumulates_dict_events() -> None:
    """JSONL replay path emits plain dicts. The aggregator must accept both."""
    sink = NonBlockingEventSink(maxsize=100)
    with LiveAggregator(sink, poll_interval=0.005) as agg:
        sink.emit({"type": "response", "id": 1, "timestamp": 1.0})
        sink.emit({"type": "reinforcer_start", "id": 1, "timestamp": 1.5})
        sink.emit({"type": "state_change", "from": "IDLE", "to": "FINISHED", "timestamp": 2.0})
        assert _wait_until(lambda: agg.snapshot().state == "FINISHED")
        snap = agg.snapshot()
        assert snap.response_times == (1.0,)
        assert snap.reinforcement_times == (1.5,)


def test_snapshot_is_immutable_copy() -> None:
    sink = NonBlockingEventSink(maxsize=10)
    with LiveAggregator(sink, poll_interval=0.005) as agg:
        sink.emit({"type": "response", "id": 1, "timestamp": 0.5})
        assert _wait_until(lambda: len(agg.snapshot().response_times) == 1)
        first = agg.snapshot()
        sink.emit({"type": "response", "id": 2, "timestamp": 0.6})
        assert _wait_until(lambda: len(agg.snapshot().response_times) == 2)
        second = agg.snapshot()
        assert first.response_times == (0.5,)
        assert second.response_times == (0.5, 0.6)


def test_stop_drains_remaining_events() -> None:
    sink = NonBlockingEventSink(maxsize=1000)
    agg = LiveAggregator(sink, poll_interval=0.05)
    agg.start()
    for i in range(100):
        sink.emit({"type": "response", "id": i, "timestamp": float(i) * 0.01})
    agg.stop(timeout=2.0)
    snap = agg.snapshot()
    assert len(snap.response_times) == 100
