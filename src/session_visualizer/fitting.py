"""Opportunistic model fitting over live snapshots.

The goal of this module is to let a live dashboard surface model-based
summaries (bout decomposition, hyperbolic discount fits, single-
alternative matching, demand curves, ...) without ever holding up the
experiment. Two mechanisms enforce that:

- **Lazy import.** ``session-analyzer`` is an optional dependency. If it
  is not installed, :func:`compute_fits` returns ``None`` and no work is
  attempted.
- **Wall-clock throttle.** Fits are expensive relative to the 1-2 Hz
  dashboard tick rate. :class:`FitThrottle` caches the last result and
  re-runs only after ``min_interval`` has elapsed *and* the data has
  changed enough to justify a refit.

Fit quality is not a correctness guarantee of this module — we merely
forward inputs to ``session-analyzer`` and report whatever it produces
(including fit failures). The convention is that any exception inside
the fit function produces a ``None`` entry in the returned dict rather
than propagating to the server loop.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FitResult:
    """A single fit result. ``error`` is populated iff the fit raised."""

    name: str
    value: Any | None
    error: str | None


def _try_fit(name: str, fn: Any, *args: Any, **kwargs: Any) -> FitResult:
    try:
        return FitResult(name=name, value=fn(*args, **kwargs), error=None)
    except Exception as exc:  # noqa: BLE001 — we deliberately catch everything
        logger.debug("fit %s failed: %s", name, exc)
        return FitResult(name=name, value=None, error=f"{type(exc).__name__}: {exc}")


def compute_fits(
    response_times: Sequence[float],
    reinforcement_times: Sequence[float],
) -> dict[str, FitResult] | None:
    """Run the subset of session-analyzer fits that need only a single
    session's response/reinforcement time stream.

    Returns ``None`` if ``session-analyzer`` is not importable. This lets
    callers render a degraded dashboard ("fits unavailable") without
    branching on import errors.
    """
    try:
        from session_analyzer.analytics import analyze_bouts, analyze_latencies
    except ImportError:
        return None

    out: dict[str, FitResult] = {}
    if len(response_times) >= 10:
        out["bouts"] = _try_fit("bouts", analyze_bouts, list(response_times))
    if response_times and reinforcement_times:
        out["latency"] = _try_fit(
            "latency",
            analyze_latencies,
            list(response_times),
            list(reinforcement_times),
        )
    return out


class FitThrottle:
    """Cache fit results and re-run only when the data or clock warrants it.

    Invariants:

    - ``get()`` never blocks.
    - A background fit never starves because the caller always releases
      the lock before calling :func:`compute_fits`.
    - When ``compute_fits`` is slow, concurrent ``get()`` calls return the
      stale cached value rather than piling up queued fits.
    """

    def __init__(self, min_interval: float = 1.0) -> None:
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._last_run: float = 0.0
        self._last_inputs: tuple[int, int] = (-1, -1)
        self._cached: dict[str, FitResult] | None = None
        self._in_flight = False

    def get(
        self,
        response_times: Sequence[float],
        reinforcement_times: Sequence[float],
    ) -> dict[str, FitResult] | None:
        now = time.monotonic()
        key = (len(response_times), len(reinforcement_times))
        with self._lock:
            fresh = now - self._last_run < self._min_interval
            unchanged = key == self._last_inputs
            if self._in_flight or fresh or unchanged:
                return self._cached
            self._in_flight = True

        try:
            result = compute_fits(response_times, reinforcement_times)
        finally:
            with self._lock:
                self._cached = result
                self._last_run = time.monotonic()
                self._last_inputs = key
                self._in_flight = False
        return result
