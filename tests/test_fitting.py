"""Tests for the fit throttle. Does not require session-analyzer."""

from __future__ import annotations

import time

from session_visualizer.fitting import FitThrottle


def test_fit_throttle_returns_none_without_analyzer() -> None:
    """If session-analyzer is not installed, compute_fits returns None.

    The throttle must cache that None without crashing.
    """
    throttle = FitThrottle(min_interval=0.0)
    result = throttle.get([0.1, 0.2, 0.3], [0.25])
    assert result is None or isinstance(result, dict)


def test_fit_throttle_honors_min_interval() -> None:
    """A second call within min_interval must not re-run the fit."""
    throttle = FitThrottle(min_interval=1.0)
    r1 = throttle.get([0.1], [])
    r2 = throttle.get([0.1, 0.2], [])
    # Same cache pointer when data changed but interval blocks a refit.
    assert r2 is r1


def test_fit_throttle_reruns_after_interval_and_change() -> None:
    throttle = FitThrottle(min_interval=0.01)
    throttle.get([0.1], [])
    time.sleep(0.02)
    # Data change + interval elapsed -> refit (even if result is None,
    # the cache timestamp must update).
    last_before = throttle._last_run
    throttle.get([0.1, 0.2], [])
    assert throttle._last_run > last_before
