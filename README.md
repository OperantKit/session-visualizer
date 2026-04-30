# session-visualizer

:jp: [日本語版 README](README.ja.md)

Best-effort **live** visualization layer for OperantKit experiment sessions.
Attaches to a running `experiment-core` `Session` via the `EventSink`
Protocol and exposes an always-available snapshot (cumulative record,
reinforcement ticks, state, optional model fits) over HTTP/SSE so that
`operantkit-frontend` — or any dashboard — can draw the session as it
runs.

**HTTP API reference:** [`docs/en/http-api.md`](docs/en/http-api.md) —
language-agnostic contract for clients (browsers, native apps, notebooks,
`curl`).

## Design constraints

1. **The experiment thread must never block.** `NonBlockingEventSink.emit`
   is O(1), uses `queue.Queue.put_nowait`, and drops events (incrementing
   a counter) when its bounded queue is full rather than waiting.
2. **The visualizer may lag.** A daemon `LiveAggregator` thread drains the
   queue and maintains an incremental snapshot. Snapshot reads are
   copy-on-read and never contend with the producer.
3. **No client connected → no work done.** The SSE server reads the
   aggregator's current snapshot at a fixed cadence only while a client
   is subscribed. Heavy fits (GML, demand curve) run on demand and can
   be skipped under CPU pressure.

## Integration

```python
from experiment_core import Session
from session_visualizer import NonBlockingEventSink, LiveAggregator

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

session = Session(..., sinks=[recorder_sink, sink])  # sibling of session-recorder
session.run()
```

For a live dashboard:

```
session-visualizer serve --host 127.0.0.1 --port 8765
```

and point `operantkit-frontend` at `http://127.0.0.1:8765/events`.

For post-hoc replay of an existing JSONL log at wall-clock speed:

```
session-visualizer replay path/to/session.jsonl --speed 4
```

## What this tool is (and is not)

- **Is:** a realtime observation pipeline (sink → aggregator → snapshot →
  SSE). Counters and timestamps flow out of the experiment process
  without stalling it.
- **Is not:** an authoritative record. The sink drops when saturated,
  by design. Use `session-recorder`'s JSONL log for durable storage.
- **Is not:** an authoritative statistical analyzer. Heavy fits
  (non-linear demand curves, EM bout decomposition, Bayesian matching-law
  CIs) live in `session-analyzer`. This package calls into it
  opportunistically when the optional `analytics` extra is installed.

### What runs in-process vs. hand-off

| Tier | What | Where | Extra |
|---|---|---|---|
| Cumulative record, response ticks, state | drawn every frame | `LiveAggregator` snapshot | (core) |
| Moving-window rate, IRT descriptives, log-log GML slope | periodic tick (e.g. 10 s / 1 min) | `PeriodicTicker` + light fits | `[fit]` |
| Non-linear demand curve, EM bout decomp, bootstrap CIs | session end / on demand | `session-analyzer` | `[analytics]` |

The boundary is **CPU budget**, not "can it be computed at all": a
generalized-matching slope over a few dozen reinforcers is trivially
cheap and is allowed on the in-process tick; a Hursh-Silberberg α with
bootstrap is not.

## Install

```
mise exec -- python -m venv .venv

# Minimum viable viz (no numpy/scipy):
.venv/bin/python -m pip install -e .

# With lightweight periodic fits (numpy + scipy; log-log OLS etc.):
.venv/bin/python -m pip install -e ".[fit]"

# Full live dashboard (server + fit + analytics hand-off + realtime):
.venv/bin/python -m pip install -e ".[full]"

# Maintainers:
.venv/bin/python -m pip install -e ".[dev,server]"

# Sibling packages for end-to-end use:
.venv/bin/python -m pip install -e ../../experiment/experiment-core
.venv/bin/python -m pip install -e ../../analysis/session-analyzer
```

### Extras

| Extra | Adds | Purpose |
|---|---|---|
| `[fit]` | `numpy`, `scipy` | Light periodic-tick fits (linear regression, moving windows) |
| `[realtime]` | (reserved) | Future async transports (WebSocket, async file tail) |
| `[server]` | `fastapi`, `sse-starlette`, `uvicorn` | HTTP/SSE dashboard endpoint |
| `[analytics]` | `session-analyzer` (sibling) | Heavy fits hand-off |
| `[full]` | all of the above | End-user "just works" install |

## Test

```
.venv/bin/pytest
```

## DSL-driven analysis suggestions (via `session-analyzer`)

The visualizer can serve the panel-recommendation API owned by
`session-analyzer` over HTTP so that `operantkit-frontend` can decide
which live panels to render before a session starts.

The endpoint is registered only when `session-analyzer` is installed
(the `[analytics]` extra or an editable sibling install):

```
POST /suggest
Content-Type: application/json

<contingency-dsl resolved AST — Program or ScheduleExpr subtree>
```

Response:

```
{"suggestions": [{"name": "...", "reason": "...", "tier": "light" | "heavy"}, ...]}
```

Malformed input returns HTTP 200 with `{"suggestions": [], "error": "..."}`
so the frontend can show a graceful empty state.

For the Python API, the mapping table, TypeScript types, and the full
list of suggested panels per DSL node, see the
[`session-analyzer` README](../../analysis/session-analyzer/README.md#dsl-driven-analysis-suggestions-for-operantkit-frontend).

## Related packages

- `experiment-core` — `Session`, `EventSink` Protocol, event dataclasses.
- `session-recorder` — durable JSONL log of the same event stream.
- `session-analyzer` — offline fits and plots over JSONL logs.
- `operantkit-frontend` — Next.js UI; consumes `/events` SSE.
