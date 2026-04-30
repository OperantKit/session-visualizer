"""Command-line entry point for standalone demos, JSONL replay, and live tail.

The CLI is intentionally thin. Production use cases attach the sink
directly to a Session and embed :class:`LiveAggregator` in the experiment
process. This CLI exists to:

- demo the pipeline,
- replay an existing ``session-recorder`` JSONL log at wall-clock speed
  (``replay``),
- tail a JSONL file being written by a separate process / machine into a
  live SSE dashboard (``tail``).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from .aggregator import LiveAggregator
from .sink import NonBlockingEventSink


def _cmd_replay(args: argparse.Namespace) -> int:
    log_path = Path(args.log)
    sink = NonBlockingEventSink(maxsize=args.queue_size)
    aggregator = LiveAggregator(sink)
    aggregator.start()
    start_wall = time.monotonic()
    start_session: float | None = None
    try:
        with log_path.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                ts = event.get("timestamp")
                if isinstance(ts, (int, float)):
                    if start_session is None:
                        start_session = float(ts)
                    target = float(ts) - start_session
                    now = time.monotonic() - start_wall
                    if target > now:
                        time.sleep((target - now) / max(args.speed, 1e-9))
                sink.emit(event)
        time.sleep(0.1)
        snap = aggregator.snapshot()
        print(
            f"responses={len(snap.response_times)} "
            f"reinforcers={len(snap.reinforcement_times)} "
            f"state={snap.state} "
            f"dropped={snap.sink_stats.dropped}"
        )
    finally:
        aggregator.stop()
    return 0


def _cmd_serve(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "session-visualizer[server] not installed: "
            "pip install 'session-visualizer[server]'",
            file=sys.stderr,
        )
        return 2

    from .server import build_app

    sink = NonBlockingEventSink(maxsize=args.queue_size)
    aggregator = LiveAggregator(sink)
    aggregator.start()
    app = build_app(aggregator, push_interval=args.push_interval)
    # Expose sink so that an embedding process can attach to it.
    app.state.sink = sink
    app.state.aggregator = aggregator
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        aggregator.stop()
    return 0


def _cmd_tail(args: argparse.Namespace) -> int:
    try:
        import uvicorn
    except ImportError:
        print(
            "session-visualizer[server] not installed: "
            "pip install 'session-visualizer[server]'",
            file=sys.stderr,
        )
        return 2

    from .jsonl_source import JSONLTailSource
    from .server import build_app

    log_path = Path(args.log)
    sink = NonBlockingEventSink(maxsize=args.queue_size)
    aggregator = LiveAggregator(sink)
    aggregator.start()

    start_offset = 0
    if args.from_end:
        try:
            start_offset = log_path.stat().st_size
        except FileNotFoundError:
            start_offset = 0

    tail = JSONLTailSource(
        log_path,
        sinks=[sink],
        poll_interval=args.poll_interval,
        use_watchdog=not args.no_watchdog,
        start_offset=start_offset,
    )
    tail.start()

    app = build_app(aggregator, push_interval=args.push_interval)
    app.state.sink = sink
    app.state.aggregator = aggregator
    app.state.tail = tail
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    finally:
        tail.stop()
        aggregator.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="session-visualizer")
    sub = parser.add_subparsers(dest="command", required=True)

    replay = sub.add_parser("replay", help="Replay a JSONL log into the aggregator")
    replay.add_argument("log", help="Path to session-recorder JSONL log")
    replay.add_argument("--speed", type=float, default=1.0, help="Replay speed multiplier")
    replay.add_argument("--queue-size", type=int, default=4096)
    replay.set_defaults(func=_cmd_replay)

    serve = sub.add_parser("serve", help="Serve snapshots over HTTP/SSE")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--push-interval", type=float, default=0.5)
    serve.add_argument("--queue-size", type=int, default=4096)
    serve.set_defaults(func=_cmd_serve)

    tail = sub.add_parser(
        "tail",
        help=(
            "Tail a JSONL file being written by a separate process / "
            "machine and serve a live SSE dashboard"
        ),
    )
    tail.add_argument(
        "log",
        help=(
            "Path to the JSONL file being written. Need not exist yet; "
            "the source will pick it up once the writer creates it."
        ),
    )
    tail.add_argument("--host", default="127.0.0.1")
    tail.add_argument("--port", type=int, default=8765)
    tail.add_argument("--push-interval", type=float, default=0.5)
    tail.add_argument(
        "--poll-interval",
        type=float,
        default=0.25,
        help="Polling interval (s) when no filesystem-watcher signal arrives.",
    )
    tail.add_argument("--queue-size", type=int, default=4096)
    tail.add_argument(
        "--from-end",
        action="store_true",
        help="Skip historical events and only stream lines appended after start.",
    )
    tail.add_argument(
        "--no-watchdog",
        action="store_true",
        help="Disable filesystem watcher; use plain time-based polling only.",
    )
    tail.set_defaults(func=_cmd_tail)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
