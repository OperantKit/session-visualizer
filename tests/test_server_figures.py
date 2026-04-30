"""Tests for /theme.json and /figure/* endpoints on session-visualizer."""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")
# session_analyzer is a required runtime dependency; absence is a hard
# failure, not a skip.

from fastapi.testclient import TestClient  # noqa: E402

from session_visualizer.aggregator import LiveAggregator  # noqa: E402
from session_visualizer.server import build_app  # noqa: E402
from session_visualizer.sink import NonBlockingEventSink  # noqa: E402


def _client_with_events(
    response_times: list[float] | None = None,
    reinforcement_times: list[float] | None = None,
) -> TestClient:
    sink = NonBlockingEventSink(maxsize=1024)
    agg = LiveAggregator(sink)
    agg.start()
    base_ts = 1_000_000.0  # arbitrary monotonic origin
    for t in response_times or []:
        sink.emit({"type": "response", "timestamp": base_ts + t})
    for t in reinforcement_times or []:
        sink.emit({"type": "reinforcer_start", "timestamp": base_ts + t})
    # Force at least one drain cycle by explicit aggregator pump.
    import time

    time.sleep(0.1)
    app = build_app(agg)
    return TestClient(app)


@pytest.mark.integration
class TestThemeJsonEndpoint:
    def test_returns_single_theme_by_name(self) -> None:
        client = _client_with_events()
        r = client.get("/theme.json", params={"name": "jeab-bw"})
        assert r.status_code == 200
        body = r.json()
        assert body["name"] == "jeab-bw"
        assert body["dpi"] >= 300
        assert body["grid"] == "none"
        assert isinstance(body["palette"], list)

    def test_returns_404_for_unknown(self) -> None:
        client = _client_with_events()
        r = client.get("/theme.json", params={"name": "does-not-exist"})
        assert r.status_code == 404

    def test_lists_all_themes_when_no_name(self) -> None:
        client = _client_with_events()
        r = client.get("/theme.json")
        assert r.status_code == 200
        body = r.json()
        assert set(body["themes"]) >= {
            "readable",
            "jeab-bw",
            "nature-color",
            "preprint-draft",
        }


@pytest.mark.integration
class TestFigureCumulativeRecordEndpoint:
    def test_returns_png_by_default(self) -> None:
        client = _client_with_events(
            response_times=[0.5, 1.0, 1.5, 2.0, 2.5],
            reinforcement_times=[1.2, 2.4],
        )
        r = client.get("/figure/cumulative-record", params={"theme": "readable"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_returns_svg(self) -> None:
        client = _client_with_events(
            response_times=[0.5, 1.0, 1.5],
            reinforcement_times=[1.2],
        )
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "jeab-bw", "fmt": "svg"},
        )
        assert r.status_code == 200
        assert "svg" in r.headers["content-type"]
        assert b"<svg" in r.content[:512] or r.content.lstrip().startswith(b"<?xml")

    def test_returns_pdf(self) -> None:
        client = _client_with_events(
            response_times=[0.5, 1.0, 1.5],
            reinforcement_times=[],
        )
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "nature-color", "fmt": "pdf"},
        )
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/pdf"
        assert r.content[:4] == b"%PDF"

    def test_unknown_theme_404(self) -> None:
        """Unknown theme is 'not found', matching /theme.json semantics."""
        client = _client_with_events(response_times=[0.5])
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "no-such-theme"},
        )
        assert r.status_code == 404

    def test_unknown_format_400(self) -> None:
        client = _client_with_events(response_times=[0.5])
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "fmt": "bmp"},
        )
        assert r.status_code == 400

    def test_empty_snapshot_returns_204(self) -> None:
        client = _client_with_events()  # no events
        r = client.get("/figure/cumulative-record", params={"theme": "readable"})
        assert r.status_code == 204

    def test_show_event_pen_false(self) -> None:
        """Disabling the event pen should still produce a valid figure."""
        client = _client_with_events(
            response_times=[0.5, 1.0, 1.5, 2.0], reinforcement_times=[1.2]
        )
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "show_event_pen": "false"},
        )
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_reset_responses_custom(self) -> None:
        """Custom reset_responses should be accepted as a positive int."""
        client = _client_with_events(
            response_times=[float(i) * 0.1 for i in range(15)],
            reinforcement_times=[1.0],
        )
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "reset_responses": 10},
        )
        assert r.status_code == 200

    def test_wrap_false_disables_wrapping(self) -> None:
        """wrap=false disables pen wrapping, independent of reset_responses."""
        client = _client_with_events(
            response_times=[float(i) * 0.1 for i in range(5)],
            reinforcement_times=[],
        )
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "wrap": "false"},
        )
        assert r.status_code == 200

    def test_reset_responses_zero_400(self) -> None:
        """reset_responses=0 is invalid; use wrap=false instead."""
        client = _client_with_events(response_times=[0.5])
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "reset_responses": 0},
        )
        assert r.status_code == 400

    def test_reset_responses_negative_400(self) -> None:
        client = _client_with_events(response_times=[0.5])
        r = client.get(
            "/figure/cumulative-record",
            params={"theme": "readable", "reset_responses": -5},
        )
        assert r.status_code == 400


@pytest.mark.integration
class TestFigureIrtCodedEndpoint:
    def test_png_default(self) -> None:
        client = _client_with_events(
            response_times=[0.0, 0.5, 1.0, 10.0, 10.5, 20.0],
            reinforcement_times=[10.0, 20.0],
        )
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "readable", "irt_threshold_sec": 5.0},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_svg(self) -> None:
        client = _client_with_events(
            response_times=[0.0, 0.5, 6.0, 6.5],
            reinforcement_times=[6.0],
        )
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "jeab-bw", "irt_threshold_sec": 5.0, "fmt": "svg"},
        )
        assert r.status_code == 200
        assert "svg" in r.headers["content-type"]

    def test_missing_threshold_400(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "readable"},
        )
        assert r.status_code == 422  # FastAPI missing-param validation

    def test_non_positive_threshold_400(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "readable", "irt_threshold_sec": 0.0},
        )
        assert r.status_code == 400

    def test_unknown_theme_404(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "no-such", "irt_threshold_sec": 5.0},
        )
        assert r.status_code == 404

    def test_unknown_fmt_400(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={
                "theme": "readable",
                "irt_threshold_sec": 5.0,
                "fmt": "bmp",
            },
        )
        assert r.status_code == 400

    def test_empty_snapshot_returns_204(self) -> None:
        client = _client_with_events()
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "readable", "irt_threshold_sec": 5.0},
        )
        assert r.status_code == 204

    def test_non_positive_threshold_400_on_empty_snapshot(self) -> None:
        """Bad threshold must be rejected even when the snapshot is empty
        (validation is eager, not session-state-dependent)."""
        client = _client_with_events()  # no events
        r = client.get(
            "/figure/irt-coded-cumulative-record",
            params={"theme": "readable", "irt_threshold_sec": -1.0},
        )
        assert r.status_code == 400


@pytest.mark.integration
class TestFigureResponseRateEndpoint:
    def test_png_default(self) -> None:
        client = _client_with_events(
            response_times=[float(i) * 0.5 for i in range(40)],
            reinforcement_times=[5.0, 10.0],
        )
        r = client.get(
            "/figure/response-rate",
            params={"theme": "readable", "window_sec": 5.0, "step_sec": 1.0},
        )
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("image/png")
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"

    def test_svg(self) -> None:
        client = _client_with_events(
            response_times=[float(i) * 0.2 for i in range(50)],
        )
        r = client.get(
            "/figure/response-rate",
            params={
                "theme": "jeab-bw",
                "fmt": "svg",
                "window_sec": 3.0,
                "step_sec": 0.5,
            },
        )
        assert r.status_code == 200
        assert "svg" in r.headers["content-type"]

    def test_missing_window_sec_422(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get("/figure/response-rate", params={"theme": "readable"})
        assert r.status_code == 422

    def test_non_positive_window_400(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/response-rate",
            params={"theme": "readable", "window_sec": 0.0, "step_sec": 1.0},
        )
        assert r.status_code == 400

    def test_non_positive_step_400(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/response-rate",
            params={"theme": "readable", "window_sec": 5.0, "step_sec": 0.0},
        )
        assert r.status_code == 400

    def test_unknown_theme_404(self) -> None:
        client = _client_with_events(response_times=[0.0, 1.0])
        r = client.get(
            "/figure/response-rate",
            params={"theme": "no-such", "window_sec": 5.0, "step_sec": 1.0},
        )
        assert r.status_code == 404

    def test_empty_snapshot_204(self) -> None:
        client = _client_with_events()
        r = client.get(
            "/figure/response-rate",
            params={"theme": "readable", "window_sec": 5.0, "step_sec": 1.0},
        )
        assert r.status_code == 204
