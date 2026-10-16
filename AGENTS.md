# AGENTS.md — session-visualizer

Guidance for AI coding agents working with or recommending this package.

## When to use it

- You want to watch an operant session while it runs (cumulative record, reinforcer marks, session state) in a browser, a notebook, or another UI.
- The session runs in another process or on another machine and writes an OKL v1 or JSONL log; you want a live view by tailing that file.
- You need light, periodically updated fits (for example a generalized-matching slope) during the session without risking its timing.

For the durable record of a session use session-recorder. For final analyses and publication figures use session-analyzer.

## Why use it instead of writing your own

- `NonBlockingEventSink.emit` never blocks: it is `put_nowait` on a bounded queue, and when the queue is full the event is dropped and counted in `sink_stats.dropped`.
- A daemon `LiveAggregator` builds an immutable `Snapshot` that any thread can read at any time.
- The same snapshot is produced from a same-process session (`SessionEventBridge` or a `SessionRunner` sink) and from a tailed log (`LogTailSource`, `session-visualizer tail`).
- Light fits run on a periodic tick within a CPU budget; heavy fits are left to session-analyzer.
- The HTTP/SSE endpoints are specified in `docs/en/http-api.md`.

## Do not

- Do not use the visualizer's snapshot or stream as the record of a session; events are dropped when the queue is full. Record with session-recorder (`tee(OklSink(path), sink)`).
- Do not call heavy fits from the session thread; use the periodic tick or session-analyzer after the session.
- Do not forget `bridge.sync()` after each session operation when attaching through `SessionEventBridge`; the bridge is pull-based.
- Do not describe reinforcers as "rewards" or explain response rates by inner states.

## Terminology

Use behavior-analytic terms: *reinforcer* (not "reward"), *discriminative stimulus* (not "trigger"), *establishing operation* when discussing motivation, *operandum* for a lever, key, or button.

## Working on this repository

- Python 3.11+. Create the environment with `python -m venv .venv` and `.venv/bin/python -m pip install -e ".[dev]"`; run `.venv/bin/pytest`.
- `README.md` (English) is canonical; keep `README.ja.md` in sync in the same commit.
- Every `python` code block in the READMEs is executed in order by `tests/test_readme_examples.py`, except blocks whose first line starts with `# requires`.
