"""Glue for attaching :class:`NonBlockingEventSink` to an existing Session.

``experiment_core.Session`` accumulates events on ``session.events`` (a
plain list). ``session-recorder`` drains that list into a ``SessionLogger``
by index. This module provides the same mirror pattern but forwards to
one or more non-blocking sinks, so the live visualizer can coexist with
the durable JSONL recorder without modifying either upstream package.

The bridge is *pull-based*: the caller invokes :meth:`sync` after every
operation that could have produced events (response, tick, reinforcement
end). This matches the existing ``SessionRunner._sync_events`` discipline
and avoids monkey-patching the session's ``events`` list.
"""

from __future__ import annotations

from typing import Any, Protocol


class _HasEvents(Protocol):
    events: list[Any]


class _HasEmit(Protocol):
    def emit(self, event: Any) -> None: ...


class SessionEventBridge:
    """Mirror ``session.events`` into one or more event sinks.

    Parameters
    ----------
    session:
        Any object exposing an ``events: list[Any]`` attribute. The bridge
        forwards *references* to the event objects; it does not copy.
    sinks:
        Objects implementing ``emit(event)``. Typically a
        :class:`NonBlockingEventSink`, but a ``SessionLogger`` or any
        duck-typed sink works.
    """

    def __init__(self, session: _HasEvents, sinks: list[_HasEmit]) -> None:
        self._session = session
        self._sinks = list(sinks)
        self._cursor = 0

    def sync(self) -> int:
        """Forward any events produced since the last :meth:`sync`.

        Returns the number of events forwarded. Safe to call after every
        session operation; the cursor is monotonic so repeated calls
        never re-emit the same event.
        """
        events = self._session.events
        new = events[self._cursor :]
        for event in new:
            for sink in self._sinks:
                sink.emit(event)
        self._cursor = len(events)
        return len(new)

    def reset(self) -> None:
        """Reset the cursor (used when the underlying session is restarted)."""
        self._cursor = 0
