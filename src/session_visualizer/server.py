"""FastAPI + SSE server for live dashboards.

The server exposes the aggregator's snapshot as JSON and as a
server-sent-events stream. It is optional: consumers that want to embed
visualization directly (Jupyter, custom tools) can use :class:`LiveAggregator`
alone.

The server never applies back-pressure to the experiment: the aggregator
is already non-blocking, and the SSE loop simply re-reads the current
snapshot at a fixed cadence. If no client is connected, no work is done.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from typing import Any

from session_analyzer.suggester import suggest_from_ast, suggestions_to_json
from session_analyzer.visualizer.cumulative_diff import render_irt_coded_cumulative_record
from session_analyzer.visualizer.cumulative_record import render_cumulative_record
from session_analyzer.visualizer.response_rate import render_response_rate_timeline
from session_analyzer.visualizer.themes import get_theme, list_themes

from .aggregator import LiveAggregator


def build_app(aggregator: LiveAggregator, push_interval: float = 0.5) -> Any:
    """Build a FastAPI app bound to ``aggregator``.

    Parameters
    ----------
    aggregator:
        A started :class:`LiveAggregator`. The caller is responsible for
        lifecycle (``start`` / ``stop``) so that the aggregator can
        outlive individual HTTP requests.
    push_interval:
        SSE tick interval in seconds. Each tick re-reads the snapshot
        and emits it. 0.5s (2 Hz) is a reasonable default for a live
        cumulative record without stressing the browser.
    """
    try:
        from fastapi import Body, FastAPI, HTTPException, Query, Response
        from sse_starlette.sse import EventSourceResponse
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "session-visualizer[server] not installed: "
            "pip install 'session-visualizer[server]'"
        ) from exc

    app = FastAPI(title="session-visualizer")

    def _snapshot_dict() -> dict[str, Any]:
        snap = aggregator.snapshot()
        payload = asdict(snap)
        payload["sink_stats"] = asdict(snap.sink_stats)
        payload["response_times"] = list(snap.response_times)
        payload["reinforcement_times"] = list(snap.reinforcement_times)
        return payload

    @app.get("/snapshot")
    def snapshot_endpoint() -> dict[str, Any]:
        return _snapshot_dict()

    @app.post("/suggest")
    def suggest_endpoint(ast: dict[str, Any] = Body(...)) -> dict[str, Any]:
        """Return the analysis panels recommended for ``ast``.

        ``ast`` is a resolved ``contingency-dsl`` Program or ScheduleExpr
        subtree. The response shape is::

            {"suggestions": [{"name": ..., "reason": ..., "tier": ...}, ...]}

        Consumed by ``operantkit-frontend`` to decide which dashboard
        panels to render before the session starts. The suggester
        itself lives in ``session-analyzer``; this endpoint is a
        thin HTTP surface over it.
        """
        try:
            suggestions = suggest_from_ast(ast)
        except ValueError as exc:
            return {"suggestions": [], "error": str(exc)}
        return {"suggestions": suggestions_to_json(suggestions)}

    def _theme_to_dict(theme: Any) -> dict[str, Any]:
        return {
            "name": theme.name,
            "description": theme.description,
            "font_family": theme.font_family,
            "font_size_pt": theme.font_size_pt,
            "background": theme.background,
            "foreground": theme.foreground,
            "palette": list(theme.palette),
            "line_style_cycle": list(theme.line_style_cycle),
            "marker_cycle": list(theme.marker_cycle),
            "line_width_pt": theme.line_width_pt,
            "figure_width_in": theme.figure_width_in,
            "figure_height_in": theme.figure_height_in,
            "dpi": theme.dpi,
            "grid": theme.grid,
            "spine_width_pt": theme.spine_width_pt,
            "legend_frame": theme.legend_frame,
            "color_blind_safe": theme.color_blind_safe,
            "intended_use": theme.intended_use,
            "tags": list(theme.tags),
        }

    @app.get("/theme.json")
    def theme_json_endpoint(
        name: str | None = Query(default=None),
    ) -> dict[str, Any]:
        """Return a single theme spec, or a list of available theme names."""
        if name is None:
            return {"themes": list_themes()}
        try:
            theme = get_theme(name)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return _theme_to_dict(theme)

    _FIGURE_MEDIA_TYPES = {
        "png": "image/png",
        "svg": "image/svg+xml",
        "pdf": "application/pdf",
    }

    @app.get("/figure/cumulative-record")
    def figure_cumulative_record(
        theme: str = Query(default="readable"),
        fmt: str = Query(default="png"),
        show_event_pen: bool = Query(default=True),
        wrap: bool = Query(default=True),
        reset_responses: int | None = Query(default=None),
    ) -> Response:
        fmt_norm = fmt.lower().lstrip(".")
        if fmt_norm not in _FIGURE_MEDIA_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported format {fmt!r}; expected one of "
                    f"{sorted(_FIGURE_MEDIA_TYPES)}."
                ),
            )
        if reset_responses is not None and reset_responses <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"reset_responses must be a positive integer; "
                    f"got {reset_responses!r}. To disable wrapping, "
                    f"pass wrap=false instead."
                ),
            )
        try:
            theme_spec = get_theme(theme)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        snap = aggregator.snapshot()
        if not snap.response_times:
            return Response(status_code=204)

        import io

        buf = io.BytesIO()
        kwargs: dict[str, Any] = {
            "response_times": snap.response_times,
            "reinforcement_times": snap.reinforcement_times,
            "output": buf,
            "theme": theme_spec,
            "fmt": fmt_norm,
            "show_event_pen": show_event_pen,
        }
        if not wrap:
            kwargs["reset_responses"] = None
        elif reset_responses is not None:
            kwargs["reset_responses"] = reset_responses
        render_cumulative_record(**kwargs)
        return Response(
            content=buf.getvalue(),
            media_type=_FIGURE_MEDIA_TYPES[fmt_norm],
        )

    @app.get("/figure/irt-coded-cumulative-record")
    def figure_irt_coded(
        irt_threshold_sec: float = Query(...),
        theme: str = Query(default="readable"),
        fmt: str = Query(default="png"),
        show_event_pen: bool = Query(default=True),
        wrap: bool = Query(default=True),
        reset_responses: int | None = Query(default=None),
    ) -> Response:
        # Validate all parameters eagerly before consulting the
        # snapshot so clients get deterministic feedback regardless
        # of session state (symmetric with fmt / theme validation).
        fmt_norm = fmt.lower().lstrip(".")
        if fmt_norm not in _FIGURE_MEDIA_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported format {fmt!r}; expected one of "
                    f"{sorted(_FIGURE_MEDIA_TYPES)}."
                ),
            )
        if irt_threshold_sec <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"irt_threshold_sec must be positive, "
                    f"got {irt_threshold_sec!r}"
                ),
            )
        if reset_responses is not None and reset_responses <= 0:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"reset_responses must be a positive integer; "
                    f"got {reset_responses!r}. To disable wrapping, "
                    f"pass wrap=false instead."
                ),
            )
        try:
            theme_spec = get_theme(theme)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        snap = aggregator.snapshot()
        if not snap.response_times:
            return Response(status_code=204)

        import io

        buf = io.BytesIO()
        kwargs: dict[str, Any] = {
            "response_times": snap.response_times,
            "reinforcement_times": snap.reinforcement_times,
            "output": buf,
            "irt_threshold_sec": irt_threshold_sec,
            "theme": theme_spec,
            "fmt": fmt_norm,
            "show_event_pen": show_event_pen,
        }
        if not wrap:
            kwargs["reset_responses"] = None
        elif reset_responses is not None:
            kwargs["reset_responses"] = reset_responses
        render_irt_coded_cumulative_record(**kwargs)
        return Response(
            content=buf.getvalue(),
            media_type=_FIGURE_MEDIA_TYPES[fmt_norm],
        )

    @app.get("/figure/response-rate")
    def figure_response_rate(
        window_sec: float = Query(...),
        step_sec: float = Query(...),
        theme: str = Query(default="readable"),
        fmt: str = Query(default="png"),
    ) -> Response:
        fmt_norm = fmt.lower().lstrip(".")
        if fmt_norm not in _FIGURE_MEDIA_TYPES:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Unsupported format {fmt!r}; expected one of "
                    f"{sorted(_FIGURE_MEDIA_TYPES)}."
                ),
            )
        if window_sec <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"window_sec must be positive, got {window_sec!r}",
            )
        if step_sec <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"step_sec must be positive, got {step_sec!r}",
            )
        try:
            theme_spec = get_theme(theme)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

        snap = aggregator.snapshot()
        if not snap.response_times:
            return Response(status_code=204)

        import io

        buf = io.BytesIO()
        render_response_rate_timeline(
            response_times=snap.response_times,
            output=buf,
            window_sec=window_sec,
            step_sec=step_sec,
            theme=theme_spec,
            fmt=fmt_norm,
            reinforcement_times=snap.reinforcement_times,
        )
        return Response(
            content=buf.getvalue(),
            media_type=_FIGURE_MEDIA_TYPES[fmt_norm],
        )

    @app.get("/events")
    async def events_endpoint() -> EventSourceResponse:
        async def gen() -> Any:
            while True:
                yield {"event": "snapshot", "data": json.dumps(_snapshot_dict())}
                await asyncio.sleep(push_interval)

        return EventSourceResponse(gen())

    return app
