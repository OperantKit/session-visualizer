# session-visualizer

:jp: [日本語版 README](README.ja.md)

session-visualizer lets you watch an operant session while it runs: the cumulative record, reinforcer marks, the session state, and optional light model fits, served over HTTP to a browser, a notebook, or [operantkit-frontend](https://github.com/OperantKit/operantkit-frontend). It is for people who run sessions with [OperantKit](https://github.com/OperantKit) and want a live view without risking the session's timing. It attaches to a session in the same Python process, or follows the log file that another process or machine is writing.

- **The session never waits for the display.** Events go into a bounded queue; if the viewer falls behind, events are dropped from the view and counted, never delayed.
- **Same view for local and remote sessions.** Attach to a session in your own program, or follow an OKL or JSONL log file as it grows.
- **A documented HTTP interface.** Snapshots, a live stream, and cumulative-record figures; see the [HTTP API reference](docs/en/http-api.md).

The view is not a record: use [session-recorder](https://github.com/OperantKit/session-recorder) for the durable log and [session-analyzer](https://github.com/OperantKit/session-analyzer) for final analyses. Notes for AI coding agents are in [AGENTS.md](AGENTS.md).

## Installation

Python 3.11 or newer. session-visualizer is not on PyPI yet, so `pip install session-visualizer` does not work today. Install it and the OperantKit packages it needs from GitHub without dependency resolution, then add the third-party libraries. `fastapi`, `sse-starlette`, and `uvicorn` are needed only for the HTTP server; session-runner only for the example below.

```bash
G=git+https://github.com/OperantKit
pip install --no-deps $G/contingency-dsl-py $G/contingency-py $G/experiment-core \
  $G/session-recorder $G/session-runner $G/session-analyzer $G/session-visualizer
pip install numpy scipy matplotlib fastapi sse-starlette uvicorn
```

`pip check` reports version mismatches between these development builds; they do not affect the examples below.

## Example

Run a short FR 3 session, record it to an OKL file, and watch it at the same time:

```python
from contingency import ScheduleBuilder
from experiment_core import ReinforcementCountExit
from session_recorder import OklSink
from session_runner import SessionConfig, SessionRunner, tee
from session_visualizer import LiveAggregator, NonBlockingEventSink

sink = NonBlockingEventSink(maxsize=4096)  # emit() never waits; drops and counts when full
aggregator = LiveAggregator(sink)
aggregator.start()  # background thread builds the snapshot

log = OklSink("fr3.txt")
log.write_header(session_name="fr3", clock_type="VirtualClock", session_start=0.0)
runner = SessionRunner(
    SessionConfig(name="fr3", schedule=ScheduleBuilder.fr(3), exit_condition=ReinforcementCountExit(count=3)),
    sink=tee(log, sink),  # record and watch at once
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

`snapshot.response_times` and `snapshot.reinforcement_times` are what the cumulative-record panel draws; `sink_stats.dropped` tells you whether the viewer fell behind. To see the same session in a browser, serve the log file:

```bash
session-visualizer tail fr3.txt --port 8765
```

`http://127.0.0.1:8765/snapshot` then returns the same counts (9 responses, 3 reinforcers, state `FINISHED`), and `http://127.0.0.1:8765/figure/cumulative-record` returns the cumulative record as a PNG.

## Usage

### Following a log file

When the session runs in another process or on another machine, follow the log it writes. The HTTP interface is the same as for a session in your own process.

```bash
session-visualizer tail session.txt                    # OKL log written by session-recorder
session-visualizer tail events.jsonl --format jsonl    # plain JSONL from your own program
session-visualizer tail session.txt --from-end         # skip events written before the viewer started
```

Installing `watchdog` (`pip install watchdog`) lets the tail react to file changes instead of polling, which lowers the delay. From Python, `LogTailSource` does the same with `OKLTailReader` (default) or `JSONLTailReader`.

### Attaching to a session in the same process

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

With session-runner, pass the sink as `sink=`, as in the example above. To serve it over HTTP, build the app with `session_visualizer.server.build_app(aggregator)` and run it with uvicorn.

### HTTP interface

`/snapshot` (current state as JSON), `/events` (a server-sent-events stream of snapshots), `/figure/cumulative-record`, `/figure/irt-coded-cumulative-record`, `/figure/response-rate`, `/theme.json`, and `POST /suggest` (the analyses worth showing for a [contingency-dsl](https://github.com/OperantKit/contingency-dsl) expression, from session-analyzer's [analysis suggestions](https://github.com/OperantKit/session-analyzer/blob/main/docs/en/suggestions.md)). Parameters and response formats are in the [HTTP API reference](docs/en/http-api.md).

### What runs during the session

| What | When | Where |
|---|---|---|
| Cumulative record, response marks, state | Every update | `LiveAggregator` snapshot |
| Moving-window rate, IRT descriptives, log-log generalized-matching slope | On a periodic tick (for example 10 s or 1 min) | `PeriodicTicker` with light fits (`FitThrottle`, `compute_fits`) |
| Non-linear demand curve, EM bout decomposition, bootstrap confidence intervals | After the session | session-analyzer |

The boundary is CPU cost, not whether a measure can be computed: a generalized-matching slope over a few dozen reinforcers is cheap enough for the periodic tick; a demand-curve fit with bootstrap is not.

### Optional extras

| Extra | Adds | Purpose |
|---|---|---|
| `[fit]` | `numpy`, `scipy` | Light periodic fits |
| `[server]` | `fastapi`, `sse-starlette`, `uvicorn` | HTTP server |
| `[stream]` | `watchdog` | File-change signals for `tail` |
| `[realtime]` | nothing | Installs no packages |
| `[full]` | `[fit]`, `[server]`, `[stream]`, `[realtime]` | Everything above |

### Replaying a JSONL log

```bash
session-visualizer replay events.jsonl --speed 4
```

replays a JSONL log at four times wall-clock speed and prints the final counts.

## Limitations

- The live view drops events when its queue is full, by design. It is not a record of the session.
- `replay` reads JSONL only, not OKL.
- `session-visualizer serve` starts a server with an empty view, for a program that attaches its own events; to watch a session from the command line, use `tail`.

## Development

```bash
python -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pytest
```

Every `python` block in this README and `README.ja.md` is run by `tests/test_readme_examples.py`.

## License

MIT (declared in `pyproject.toml`; the repository has no LICENSE file).
