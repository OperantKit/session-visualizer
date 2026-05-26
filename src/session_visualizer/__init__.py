"""Best-effort live visualization for OperantKit sessions.

The package attaches to a running experiment via two equivalent input
sources, both feeding the same sink → aggregator → snapshot pipeline:

- :class:`SessionEventBridge` mirrors a same-process
  ``experiment_core.Session`` into a :class:`NonBlockingEventSink`.
- :class:`LogTailSource` tails a log file written by a separate process
  (e.g. ``session-recorder`` running on different hardware) and emits
  each new record into one or more sinks. Default reader is OKL v1
  (:class:`OKLTailReader`); pass :class:`JSONLTailReader` for plain
  JSON producers.

In both cases the sink never blocks the producer: if the internal queue
is full, new events are dropped and counted. A background aggregator
drains the queue, maintains cumulative state, and exposes snapshots that
a server (FastAPI + SSE) or any other consumer can pull at its own pace.

The core guarantee is one-directional: experiment correctness and timing
are prioritized; visualization is opportunistic.
"""

from .aggregator import LiveAggregator, Snapshot
from .fitting import FitResult, FitThrottle, compute_fits
from .integration import SessionEventBridge
from .periodic import PeriodicTicker
from .sink import NonBlockingEventSink, SinkStats
from .tail_source import (
    JSONLTailReader,
    LogTailSource,
    OKLTailReader,
    TailReader,
    TailStats,
)

__all__ = [
    "FitResult",
    "FitThrottle",
    "JSONLTailReader",
    "LiveAggregator",
    "LogTailSource",
    "NonBlockingEventSink",
    "OKLTailReader",
    "PeriodicTicker",
    "SessionEventBridge",
    "SinkStats",
    "Snapshot",
    "TailReader",
    "TailStats",
    "compute_fits",
]
