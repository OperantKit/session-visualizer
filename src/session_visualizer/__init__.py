"""Best-effort live visualization for OperantKit sessions.

The package attaches to a running experiment via two equivalent input
sources, both feeding the same sink → aggregator → snapshot pipeline:

- :class:`SessionEventBridge` mirrors a same-process
  ``experiment_core.Session`` into a :class:`NonBlockingEventSink`.
- :class:`JSONLTailSource` tails a JSONL file written by a separate
  process (e.g. ``session-recorder`` running on different hardware) and
  emits each new line into one or more sinks.

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
from .jsonl_source import JSONLTailSource, TailStats, read_new
from .periodic import PeriodicTicker
from .sink import NonBlockingEventSink, SinkStats

__all__ = [
    "FitResult",
    "FitThrottle",
    "JSONLTailSource",
    "LiveAggregator",
    "NonBlockingEventSink",
    "PeriodicTicker",
    "SessionEventBridge",
    "SinkStats",
    "Snapshot",
    "TailStats",
    "compute_fits",
    "read_new",
]
