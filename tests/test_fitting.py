"""Tests for the fit throttle and compute_fits surface."""

from __future__ import annotations

import time

import pytest

from session_visualizer.fitting import FitThrottle, compute_fits


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


# ---------------------------------------------------------------------------
# compute_fits — live indicator surface
# ---------------------------------------------------------------------------


def _has_session_analyzer() -> bool:
    try:
        import session_analyzer.analytics  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _has_session_analyzer(), reason="session-analyzer not installed")
def test_compute_fits_includes_pr_classification_when_reinforcers_present() -> None:
    # Three responses then a reinforcer, repeated; produces a linear PR-like sequence.
    response_times: list[float] = []
    reinforcement_times: list[float] = []
    t = 0.0
    for step in range(3, 7):  # 3, 4, 5, 6 responses per step
        for _ in range(step):
            t += 1.0
            response_times.append(t)
        reinforcement_times.append(t)
    out = compute_fits(response_times, reinforcement_times)
    assert out is not None
    assert "pr_classification" in out
    pr = out["pr_classification"].value
    assert pr is not None
    assert pr.is_pr is True
    assert pr.step_type in ("linear", "unknown", "hodos")


@pytest.mark.skipif(not _has_session_analyzer(), reason="session-analyzer not installed")
def test_compute_fits_includes_irt_breakpoint_when_reinforcers_present() -> None:
    # Reinforcer at t=10, then a long IRT in the current step.
    response_times = [11.0, 12.0, 200.0]
    reinforcement_times = [10.0]
    out = compute_fits(response_times, reinforcement_times)
    assert out is not None
    assert "irt_breakpoint" in out
    bp = out["irt_breakpoint"].value
    assert bp is not None
    assert bp.status == "reached"
    assert bp.breakpoint_reached is True


@pytest.mark.skipif(not _has_session_analyzer(), reason="session-analyzer not installed")
def test_compute_fits_includes_irt_breakpoint_during_first_step() -> None:
    # No reinforcer yet, but already enough responses to compute IRTs.
    response_times = [0.0, 1.0, 2.0, 3.0]
    reinforcement_times: list[float] = []
    out = compute_fits(response_times, reinforcement_times)
    assert out is not None
    assert "irt_breakpoint" in out
    assert "pr_classification" not in out  # no reinforcers ⇒ no classification
    bp = out["irt_breakpoint"].value
    assert bp is not None
    assert bp.status == "active"


@pytest.mark.skipif(not _has_session_analyzer(), reason="session-analyzer not installed")
def test_compute_fits_omits_pr_when_no_reinforcers() -> None:
    out = compute_fits([1.0, 2.0, 3.0], [])
    assert out is not None
    assert "pr_classification" not in out

