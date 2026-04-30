"""Background aggregator: drains the sink, maintains cumulative state.

The aggregator runs on its own daemon thread. It is fully decoupled from
the experiment thread via the bounded queue in :class:`NonBlockingEventSink`.
Snapshots are copy-on-read immutable dataclasses so consumers (SSE handler,
plotting code, REST endpoint) can read without coordinating with the
aggregator.

Event handling is duck-typed: the aggregator tolerates both
``experiment_core`` dataclass instances and plain dict events (e.g. parsed
JSONL lines from ``session-recorder``). This keeps the module importable
without a hard dependency on ``experiment-core``.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .fitting import FitResult, FitThrottle
from .sink import NonBlockingEventSink, SinkStats


@dataclass(frozen=True)
class Snapshot:
    """Immutable view of the current cumulative state."""

    wall_clock: float
    last_event_time: float | None
    response_times: tuple[float, ...]
    reinforcement_times: tuple[float, ...]
    state: str
    sink_stats: SinkStats
    fits: dict[str, FitResult] | None = None


@dataclass
class _MutableState:
    response_times: list[float] = field(default_factory=list)
    reinforcement_times: list[float] = field(default_factory=list)
    state: str = "IDLE"
    last_event_time: float | None = None


def _event_type(event: Any) -> str | None:
    """Return a canonical event-type string for either dataclass or dict."""
    cls_name = type(event).__name__
    if cls_name == "ResponseEvent":
        return "response"
    if cls_name == "ReinforcerStartEvent":
        return "reinforcer_start"
    if cls_name == "ReinforcerEndEvent":
        return "reinforcer_end"
    if cls_name == "StateChangeEvent":
        return "state_change"
    if isinstance(event, dict):
        t = event.get("type")
        return str(t) if t is not None else None
    return None


def _event_attr(event: Any, name: str) -> Any:
    if isinstance(event, dict):
        return event.get(name)
    return getattr(event, name, None)


class LiveAggregator:
    """Drain a :class:`NonBlockingEventSink` into an incremental snapshot.

    Parameters
    ----------
    sink:
        The sink to drain. Typically shared with a running ``Session``.
    poll_interval:
        Sleep interval for the drain thread when the queue is empty.
        Short enough to feel live; long enough to keep CPU cost negligible.
    batch_limit:
        Maximum events processed per wake-up. Caps latency spikes when the
        sink has accumulated a burst.
    """

    def __init__(
        self,
        sink: NonBlockingEventSink,
        poll_interval: float = 0.05,
        batch_limit: int = 1024,
        fit_throttle: FitThrottle | None = None,
    ) -> None:
        self._sink = sink
        self._poll_interval = poll_interval
        self._batch_limit = batch_limit
        self._fit_throttle = fit_throttle
        self._state = _MutableState()
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # Lifecycle ----------------------------------------------------------
    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="session-visualizer-aggregator",
            daemon=True,
        )
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)

    # Drain loop ---------------------------------------------------------
    def _run(self) -> None:
        while not self._stop_event.is_set():
            events = self._sink.drain(limit=self._batch_limit)
            if events:
                self._ingest(events)
            else:
                time.sleep(self._poll_interval)
        # Final drain so tail events reach consumers after stop().
        tail = self._sink.drain()
        if tail:
            self._ingest(tail)

    def _ingest(self, events: list[Any]) -> None:
        with self._lock:
            st = self._state
            for event in events:
                etype = _event_type(event)
                ts = _event_attr(event, "timestamp")
                if isinstance(ts, (int, float)):
                    st.last_event_time = float(ts)
                if etype == "response" and isinstance(ts, (int, float)):
                    st.response_times.append(float(ts))
                elif etype == "reinforcer_start" and isinstance(ts, (int, float)):
                    st.reinforcement_times.append(float(ts))
                elif etype == "state_change":
                    to = _event_attr(event, "to") or _event_attr(event, "to_state")
                    if hasattr(to, "name"):
                        st.state = to.name
                    elif isinstance(to, str):
                        st.state = to

    # Read-side ----------------------------------------------------------
    def snapshot(self) -> Snapshot:
        """Copy-on-read: safe to call from any thread at any time."""
        with self._lock:
            st = self._state
            responses = tuple(st.response_times)
            reinforcers = tuple(st.reinforcement_times)
            state = st.state
            last_event_time = st.last_event_time
        fits = self._fit_throttle.get(responses, reinforcers) if self._fit_throttle else None
        return Snapshot(
            wall_clock=time.time(),
            last_event_time=last_event_time,
            response_times=responses,
            reinforcement_times=reinforcers,
            state=state,
            sink_stats=self._sink.stats(),
            fits=fits,
        )

    # Context manager ---------------------------------------------------
    def __enter__(self) -> LiveAggregator:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()
