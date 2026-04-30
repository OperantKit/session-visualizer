"""Test the /suggest endpoint on the FastAPI app.

``session_analyzer`` is a required runtime dependency of
``session-visualizer``; its absence is a hard failure, not a skip
condition. ``fastapi`` / ``httpx`` remain behind the ``[server]``
optional extra since ``LiveAggregator`` itself is usable without them.
"""

from __future__ import annotations

import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # TestClient backend for fastapi >= 0.110

from fastapi.testclient import TestClient  # noqa: E402

from session_visualizer.aggregator import LiveAggregator  # noqa: E402
from session_visualizer.server import build_app  # noqa: E402
from session_visualizer.sink import NonBlockingEventSink  # noqa: E402


@pytest.fixture
def client() -> TestClient:
    sink = NonBlockingEventSink(maxsize=16)
    agg = LiveAggregator(sink)
    app = build_app(agg)
    return TestClient(app)


@pytest.mark.integration
def test_suggest_endpoint_concurrent_returns_matching_law(client: TestClient) -> None:
    ast = {
        "type": "Program",
        "param_decls": [],
        "bindings": [],
        "schedule": {
            "type": "Compound",
            "combinator": "Conc",
            "components": [
                {"type": "Atomic", "dist": "V", "domain": "I", "value": 30},
                {"type": "Atomic", "dist": "V", "domain": "I", "value": 60},
            ],
        },
    }
    resp = client.post("/suggest", json=ast)
    assert resp.status_code == 200
    body = resp.json()
    names = {s["name"] for s in body["suggestions"]}
    assert "matching_law" in names
    assert "cumulative_record" in names


@pytest.mark.integration
def test_suggest_endpoint_invalid_ast_returns_error_field(client: TestClient) -> None:
    resp = client.post("/suggest", json={"not": "a program"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["suggestions"] == []
    assert "error" in body
