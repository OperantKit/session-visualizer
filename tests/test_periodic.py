"""Tests for the PeriodicTicker — a fixed-cadence analysis scheduler.

Unlike FitThrottle (which skips refits when inputs haven't changed), the
PeriodicTicker fires at a regular wall-clock cadence regardless of
whether the data has grown. This is what a live dashboard wants for
"every 10 s / every 1 min" descriptive ticks (moving-window rate,
provisional matching slope, IRT histogram refresh).

The ticker is best-effort: if a tick's callback is still running when
the next deadline arrives, the tick is dropped rather than queued. This
mirrors the visualizer's overall "never pile up work" contract.
"""

from __future__ import annotations

import threading
import time

import pytest

from session_visualizer.periodic import PeriodicTicker


@pytest.mark.unit
def test_periodic_ticker_fires_on_cadence() -> None:
    calls: list[float] = []

    ticker = PeriodicTicker(interval=0.05, callback=lambda: calls.append(time.monotonic()))
    ticker.start()
    try:
        time.sleep(0.18)
    finally:
        ticker.stop()

    # Expect ~3 ticks in 0.18 s at 0.05 s cadence; allow scheduling slack.
    assert 2 <= len(calls) <= 5


@pytest.mark.unit
def test_periodic_ticker_stop_is_idempotent() -> None:
    ticker = PeriodicTicker(interval=0.05, callback=lambda: None)
    ticker.start()
    ticker.stop()
    ticker.stop()  # must not raise


@pytest.mark.unit
def test_periodic_ticker_stop_timeout_does_not_leak_double_threads() -> None:
    """If stop() times out on a blocked callback, start() must not spawn a second daemon.

    Regression guard: an earlier version cleared `_thread = None`
    unconditionally after `join()`, even when the join timed out. A
    subsequent `start()` then saw `None` and launched a second ticker
    thread running the same callback concurrently.
    """
    gate = threading.Event()

    def blocked() -> None:
        gate.wait(timeout=5.0)

    ticker = PeriodicTicker(interval=0.01, callback=blocked)
    ticker.start()
    time.sleep(0.03)  # ensure callback has entered gate.wait()

    # Very short join timeout — the callback is still blocked on gate.
    ticker.stop(timeout=0.01)

    thread_before = ticker._thread
    ticker.start()  # should be a no-op because the original thread is still alive
    thread_after = ticker._thread

    try:
        # No second thread was spawned.
        assert thread_before is thread_after
    finally:
        gate.set()
        ticker.stop(timeout=1.0)


@pytest.mark.unit
def test_periodic_ticker_drops_overrun_callbacks() -> None:
    """A slow callback must not queue up backlogged ticks.

    If the user's callback takes longer than `interval`, subsequent
    deadlines that elapse during execution are collapsed into a single
    "skipped" count, not replayed.
    """
    gate = threading.Event()
    started = threading.Event()
    completed: list[int] = []

    def slow() -> None:
        started.set()
        gate.wait(timeout=1.0)
        completed.append(1)

    ticker = PeriodicTicker(interval=0.01, callback=slow)
    ticker.start()
    try:
        started.wait(timeout=1.0)
        time.sleep(0.1)  # several deadlines pass while slow() is blocked
        gate.set()
        time.sleep(0.05)
    finally:
        ticker.stop()

    # Slow callback ran at least once; overruns must have been dropped,
    # not queued into 10+ completions.
    assert len(completed) >= 1
    assert ticker.skipped >= 1


@pytest.mark.unit
def test_periodic_ticker_suppresses_callback_exceptions() -> None:
    """Exceptions inside the callback must never kill the ticker thread."""
    calls = [0]

    def boom() -> None:
        calls[0] += 1
        raise RuntimeError("kaboom")

    ticker = PeriodicTicker(interval=0.02, callback=boom)
    ticker.start()
    try:
        time.sleep(0.1)
    finally:
        ticker.stop()

    # Multiple ticks fired despite the exception on every call.
    assert calls[0] >= 2
