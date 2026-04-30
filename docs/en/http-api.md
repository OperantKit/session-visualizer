# HTTP API Reference

:jp: [日本語版](../ja/http-api.md)

`session-visualizer` exposes a language-agnostic HTTP + Server-Sent-Events
contract. Any client — a browser dashboard, a native application, a command-
line tool, or a notebook — can consume this contract with no special
knowledge of the server implementation.

This document is the canonical surface specification. It does not prescribe
how to render the data; that is the client's concern.

## Responsibility boundary

- **`session-visualizer`** owns the HTTP/SSE surface, the in-memory
  `LiveAggregator`, and the `EventSink` Protocol. It does **not** ship a
  built-in browser UI.
- **Clients** (e.g. `operantkit-frontend`, Jupyter notebooks, native apps)
  own rendering, interaction, and export-button UX.
- **`session-analyzer`** owns static figure generation and the theme
  registry. `session-visualizer` delegates to it through optional imports
  and never reimplements analyzer-owned logic.

The dependency direction is fixed by [`apps/SCOPE`](../../../../SCOPE):
`session-visualizer → session-analyzer`. The reverse is forbidden.

## Starting the server

```bash
uvicorn session_visualizer.cli:app --host 127.0.0.1 --port 8765
```

All endpoints below are served under the chosen `--host:--port`.

## Endpoint summary

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/snapshot` | One-shot JSON of the current aggregator state |
| `GET` | `/events` | SSE stream of snapshots at a fixed cadence |
| `GET` | `/theme.json` | List themes or fetch a single theme spec |
| `GET` | `/figure/cumulative-record` | Static cumulative record (PNG/SVG/PDF) |
| `GET` | `/figure/irt-coded-cumulative-record` | IRT-coded cumulative record for DRL/DRO |
| `GET` | `/figure/response-rate` | Moving-window response-rate timeline (rpm) |
| `POST` | `/suggest` | Recommended analysis panels for a DSL AST |

All endpoints are always registered. `session-analyzer` is a required
dependency of `session-visualizer`: the theme, figure, and suggester
implementations live there, so the documented surface above is the
single contract — clients never need to probe which endpoints exist.

## `GET /snapshot`

Returns a single snapshot of the current aggregator state as JSON.

### Response

`200 OK` with `Content-Type: application/json`:

```json
{
  "response_times": [0.5, 1.2, 2.0],
  "reinforcement_times": [1.3],
  "sink_stats": {"enqueued": 3, "dropped": 0, "queue_high_water": 2},
  "state": "...",
  "fits": {}
}
```

`response_times` and `reinforcement_times` are seconds since session start.

## `GET /events`

Server-Sent-Events stream. Each tick emits the same payload as `/snapshot`
with `event: snapshot`. Default cadence is 2 Hz (one frame every 500 ms);
the cadence is fixed at server build time via `build_app(push_interval=...)`.

### Response

`200 OK` with `Content-Type: text/event-stream`:

```
event: snapshot
data: {"response_times": [...], "reinforcement_times": [...], ...}

event: snapshot
data: {...}
```

Clients reconnect automatically on transport errors per the SSE spec.

## `GET /theme.json`

Fetches theme metadata. Themes are declarative, rendering-engine-independent
specs defined in `session-analyzer`. Clients map these specs into their own
rendering library (recharts, D3, matplotlib, Plotly, GDI+ …).

### List mode

`GET /theme.json` with no query parameter:

```json
{"themes": ["jeab-bw", "jeab-bw-marker", "nature-color",
            "preprint-draft", "readable", "readable-dark",
            "science-color"]}
```

### Single mode

`GET /theme.json?name=<theme-id>`:

```json
{
  "name": "jeab-bw",
  "description": "JEAB / JABA / Behavioural Processes monochrome house style.",
  "font_family": "Helvetica",
  "font_size_pt": 8.0,
  "background": "#ffffff",
  "foreground": "#000000",
  "palette": ["#000000", "#4d4d4d", "#808080", "#b3b3b3"],
  "line_style_cycle": ["-", "--", "-.", ":"],
  "marker_cycle": ["o", "s", "^", "D"],
  "line_width_pt": 1.0,
  "figure_width_in": 3.3,
  "figure_height_in": 2.5,
  "dpi": 300,
  "grid": "none",
  "spine_width_pt": 0.75,
  "legend_frame": false,
  "color_blind_safe": true,
  "intended_use": "Direct paste into JEAB / JABA / Beh. Processes manuscripts.",
  "tags": ["paper", "monochrome", "jeab"]
}
```

### Status codes

- `200 OK` — theme found (or listed)
- `404 Not Found` — unknown theme name

## `GET /figure/cumulative-record`

Returns a publication-quality static figure rendered server-side with
matplotlib. Intended for native clients that cannot render the spec
themselves, or for users who want a canonical paper-grade output.

### Query parameters

| Name | Type | Default | Notes |
|------|------|---------|-------|
| `theme` | string | `readable` | Any id from `/theme.json` |
| `fmt` | string | `png` | One of `png`, `svg`, `pdf` |
| `show_event_pen` | bool | `true` | Render the F&S event-pen trace below the cumulative record |
| `wrap` | bool | `true` | Whether the response pen wraps. `false` disables pen reset entirely |
| `reset_responses` | int | *(canonical 550)* | Pen-reset interval when `wrap=true`. Must be a positive integer; ignored when `wrap=false` |

### Status codes

- `200 OK` — figure rendered, body is raw image bytes
- `204 No Content` — snapshot has zero responses; nothing to render
- `400 Bad Request` — unsupported `fmt` or non-positive `reset_responses`
- `404 Not Found` — unknown `theme`

The figure is a best-effort copy of the aggregator snapshot at call time.
Calling this endpoint does **not** block or slow the experiment thread;
the snapshot is copy-on-read and matplotlib runs in the server's thread
pool on a freshly-constructed `Figure` (no global state).

## `GET /figure/irt-coded-cumulative-record`

IRT-coded cumulative record with canonical Ferster & Skinner styling. Each
response is classified by whether its preceding inter-response time meets
the threshold, and the two populations are rendered in distinct markers.
Intended for visualizing DRL / DRO performance.

### Query parameters

| Name | Type | Default | Notes |
|------|------|---------|-------|
| `irt_threshold_sec` | float | **required** | Positive. Responses with preceding IRT ≥ this are "long"; shorter are "short" |
| `theme` | string | `readable` | Any id from `/theme.json` |
| `fmt` | string | `png` | One of `png`, `svg`, `pdf` |
| `show_event_pen` | bool | `true` | Render the F&S event-pen trace below the cumulative record |
| `wrap` | bool | `true` | Whether the response pen wraps. `false` disables pen reset entirely |
| `reset_responses` | int | *(canonical 550)* | Pen-reset interval when `wrap=true`. Must be a positive integer; ignored when `wrap=false` |

### Status codes

- `200 OK` — figure rendered, body is raw image bytes
- `204 No Content` — snapshot has zero responses
- `400 Bad Request` — unsupported `fmt`, `irt_threshold_sec <= 0`, or non-positive `reset_responses`
- `404 Not Found` — unknown `theme`
- `422 Unprocessable Entity` — missing `irt_threshold_sec`

## `GET /figure/response-rate`

Moving-window response-rate timeline. For each time point on a regular
grid, the reported rate is the number of responses in the preceding
`window_sec` seconds divided by `window_sec`, scaled to responses per
minute. Reinforcement events are overlaid as vertical tick marks when
present in the snapshot.

### Query parameters

| Name | Type | Default | Notes |
|------|------|---------|-------|
| `window_sec` | float | **required** | Positive. Window width in seconds |
| `step_sec` | float | **required** | Positive. Grid spacing in seconds |
| `theme` | string | `readable` | Any id from `/theme.json` |
| `fmt` | string | `png` | One of `png`, `svg`, `pdf` |

### Status codes

- `200 OK` — figure rendered, body is raw image bytes
- `204 No Content` — snapshot has zero responses
- `400 Bad Request` — unsupported `fmt`, `window_sec <= 0`, or `step_sec <= 0`
- `404 Not Found` — unknown `theme`
- `422 Unprocessable Entity` — missing required parameter

## `POST /suggest`

Returns the analysis panels recommended for a resolved DSL AST. This is a
thin HTTP surface over `session_analyzer.suggester`; see the analyzer
package for the AST schema.

### Request

`Content-Type: application/json`. Body is a resolved `contingency-dsl`
Program or ScheduleExpr subtree.

### Response

```json
{"suggestions": [{"name": "...", "reason": "...", "tier": "..."}]}
```

## Client recipes

### `curl`

```bash
# one-shot snapshot
curl http://127.0.0.1:8765/snapshot

# list themes
curl http://127.0.0.1:8765/theme.json

# download a JEAB-style PDF of the current cumulative record
curl -o cumrec.pdf "http://127.0.0.1:8765/figure/cumulative-record?theme=jeab-bw&fmt=pdf"

# download an IRT-coded cumulative record for DRL 5 s
curl -o drl.svg "http://127.0.0.1:8765/figure/irt-coded-cumulative-record?theme=jeab-bw&fmt=svg&irt_threshold_sec=5.0"

# download a response-rate timeline (10 s window, 1 s step)
curl -o rate.svg "http://127.0.0.1:8765/figure/response-rate?window_sec=10&step_sec=1&theme=nature-color&fmt=svg"
```

### Python (`httpx`)

```python
import httpx

async with httpx.AsyncClient(base_url="http://127.0.0.1:8765") as cli:
    theme = (await cli.get("/theme.json", params={"name": "nature-color"})).json()
    img = (await cli.get("/figure/cumulative-record",
                         params={"theme": "nature-color", "fmt": "svg"})).content
```

### Browser (`fetch` + SSE)

```js
const ev = new EventSource("http://127.0.0.1:8765/events");
ev.addEventListener("snapshot", (e) => {
  const snap = JSON.parse(e.data);
  // pass snap.response_times to your charting library
});

const theme = await (await fetch("/theme.json?name=readable")).json();
// map theme.palette, theme.font_family, etc. into your library's config
```

### Any HTTP-capable language

The contract above is deliberately language-neutral. Any client that can
issue HTTP GETs and parse JSON (or consume SSE, or save binary responses
to disk) can integrate. No language-specific SDK is shipped; the HTTP
contract **is** the SDK.

## Stability and versioning

This document describes the current surface. Breaking changes to endpoint
shapes will be called out in the package `CHANGELOG`. Additive fields in
JSON responses are considered backward-compatible; clients should ignore
unknown keys.
