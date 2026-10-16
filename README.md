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

### Same-process attachment (default)

Attach a sink to a running ``Session`` for the lowest-latency path (with `SessionRunner`, pass the sink as `sink=`, as in the 30-second example below):

```python
from contingency import ScheduleBuilder
from experiment_core import Session
from session_visualizer import LiveAggregator, NonBlockingEventSink, SessionEventBridge

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

session = Session(ScheduleBuilder.fr(3))
bridge = SessionEventBridge(session, sinks=[sink])  # add an OklSink here to record as well
session.start(0.0)
bridge.sync()  # call after every session operation
```

For a live dashboard:

```
session-visualizer serve --host 127.0.0.1 --port 8765
```

and point `operantkit-frontend` at `http://127.0.0.1:8765/events`.

### Cross-process tail

When the experiment runs in a separate process — different machine,
embedded device writing to a shared mount, supervised subprocess —
tail the on-disk session log instead. The same SSE / HTTP surface is
served regardless of input source.

```
# Default: OKL v1 produced by session-recorder
session-visualizer tail path/to/session.txt

# Plain JSONL written by a custom embedded producer
session-visualizer tail path/to/events.jsonl --format jsonl

# Skip events written before the visualizer started
session-visualizer tail path/to/session.txt --from-end
```

Programmatic use:

```python
# requires a log file that another process is writing
from session_visualizer import (
    JSONLTailReader,
    LiveAggregator,
    LogTailSource,
    NonBlockingEventSink,
    OKLTailReader,
)

sink = NonBlockingEventSink(maxsize=4096)
aggregator = LiveAggregator(sink)
aggregator.start()

# OKLTailReader is the default; pass JSONLTailReader for plain JSON.
source = LogTailSource("/mnt/embedded-rig/session.okl", sinks=[sink])
source.start()
```

Install ``session-visualizer[stream]`` for ``watchdog``-based
filesystem signals (lower tail latency); plain time-based polling is
used as a fallback when ``watchdog`` is missing.

### Post-hoc replay

To replay an existing JSONL log at wall-clock speed:

```
session-visualizer replay path/to/session.jsonl --speed 4
```

## What this tool is (and is not)

- **Is:** a realtime observation pipeline (sink → aggregator → snapshot →
  SSE). Counters and timestamps flow out of the experiment process
  without stalling it.
- **Is not:** an authoritative record. The sink drops when saturated,
  by design. Use `session-recorder`'s OKL v1 log for durable storage.
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

## 30-second example

```bash
pip install git+https://github.com/OperantKit/session-visualizer
pip install session-runner   # the session driver used below
```

Attach the non-blocking sink to a session and read the live snapshot that a dashboard would draw:

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from session_runner import SessionConfig, SessionRunner
from session_visualizer import LiveAggregator, NonBlockingEventSink

sink = NonBlockingEventSink(maxsize=4096)  # emit() never waits; drops and counts when full
aggregator = LiveAggregator(sink)
aggregator.start()  # background thread builds the snapshot

runner = SessionRunner(
    SessionConfig(name="fr3", schedule=ScheduleBuilder.fr(3), exit_condition=ReinforcementCountExit(count=3)),
    sink=sink,  # or tee(OklSink("fr3.okl"), sink) to record and watch at once
)
runner.start(start_time=0.0)
t = 0.0
while not runner.is_finished:
    t += 1.0
    if runner.is_reinforcing:
        runner.end_reinforcement(t)
    else:
        runner.handle_response(t)

aggregator.stop()  # final drain
snapshot = aggregator.snapshot()
print(snapshot.state, len(snapshot.response_times), snapshot.reinforcement_times)
# FINISHED 9 (3.0, 7.0, 11.0)
print(snapshot.sink_stats)
# SinkStats(emitted=23, enqueued=23, dropped=0)
```

`snapshot.response_times` and `snapshot.reinforcement_times` are what the cumulative-record panel draws; `sink_stats.dropped` tells you whether the visualizer fell behind. `session-visualizer serve` exposes the same snapshot over HTTP/SSE.

## Why not draw the live view from your own session loop?

- **The experiment never waits for the display.** `NonBlockingEventSink.emit` is `put_nowait` on a bounded queue; when the queue is full the event is dropped and counted (`sink_stats.dropped`) instead of delaying the next response or reinforcer.
- **Reads never contend with the producer.** A daemon `LiveAggregator` drains the queue and builds an immutable `Snapshot`; any thread can call `snapshot()` at any time.
- **Same view for in-process and cross-process sessions.** Attach to a running session (`SessionEventBridge` or a `SessionRunner` sink), or tail an OKL v1 / JSONL file written by another process or machine (`LogTailSource`, `session-visualizer tail`).
- **Fits on a CPU budget.** Light fits run on a periodic tick (`PeriodicTicker`, `FitThrottle`); heavy fits are handed to session-analyzer.
- **Documented HTTP contract.** The SSE and figure endpoints are specified in `docs/en/http-api.md` for browsers, notebooks, or `curl`.

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
| `[stream]` | `watchdog` | Filesystem watcher for `LogTailSource` (lower tail latency) |
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
[`session-analyzer` README](https://github.com/OperantKit/session-analyzer#dsl-driven-analysis-suggestions-for-operantkit-frontend).

## Related packages

- `experiment-core` — `Session`, `EventSink` Protocol, event dataclasses.
- `session-recorder` — durable OKL v1 log of the same event stream.
- `session-analyzer` — offline fits and plots over OKL v1 logs.
- `operantkit-frontend` — Next.js UI; consumes `/events` SSE.
