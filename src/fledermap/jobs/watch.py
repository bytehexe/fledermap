"""Filesystem watching that triggers `run_ingest_cycle` (jobs/tasks.py)
between cron ticks. See design spec §5.

Debounced, not immediate: watchdog's Observer runs its own thread and fires
an event per filesystem change, not per "burst" -- an active Syncthing sync
can raise dozens of events over a couple of minutes. Deferring a cycle per
event would mean most of those cycles just get their sweep refused
(IncompleteScanError -- files still arriving, jobs/tasks.py). Instead each
event (re)starts a debounce timer; only once `debounce_seconds` pass with no
further event does the handler actually call `defer`.

Deliberately generic (`defer: Callable[[], Awaitable[None]]`, no import of
`run_ingest_cycle` or anything Procrastinate-specific): keeps this module
testable in isolation and mirrors this project's `media/`-stays-pure
separation. `cli/main.py` supplies the actual `run_ingest_cycle.defer_async`
closure.

Threading note: watchdog calls its event handler from its OWN thread, never
the asyncio loop the caller passes in. `_Debouncer.notify` is the one method
safe to call from that thread (`loop.call_soon_threadsafe`); every other
method runs ON `loop`, so no further synchronization is needed anywhere else
in this module.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from fledermap.ingest.scan import DEFAULT_SETTLE_SECONDS

if TYPE_CHECKING:
    from watchdog.observers.api import BaseObserver

_Defer = Callable[[], Awaitable[None]]


class _Debouncer:
    """Coalesces a burst of filesystem events into one `defer()` call, fired
    only after `debounce_seconds` of quiet."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        defer: _Defer,
        debounce_seconds: float,
    ) -> None:
        self._loop = loop
        self._defer = defer
        self._debounce_seconds = debounce_seconds
        self._handle: asyncio.TimerHandle | None = None
        # asyncio only holds a WEAK reference to a task it isn't otherwise
        # tracking -- `asyncio.ensure_future(...)` below with the result
        # discarded is the standard GC-risk footgun (a task can be collected
        # mid-run, silently). Keeping a strong reference here for the task's
        # full lifetime, discarded via `done_callback` once it finishes,
        # avoids that.
        self._tasks: set[asyncio.Task[None]] = set()

    def notify(self) -> None:
        """Call from ANY thread -- schedules the actual reset onto `_loop`."""
        self._loop.call_soon_threadsafe(self._reset)

    def _reset(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
        self._handle = self._loop.call_later(self._debounce_seconds, self._fire)

    def _fire(self) -> None:
        self._handle = None
        # `loop.create_task` requires a `Coroutine` specifically; `_Defer` is
        # deliberately typed as the broader `Awaitable[None]` so callers
        # aren't forced to hand this a coroutine function specifically.
        # `asyncio.ensure_future` accepts any `Awaitable` and schedules it on
        # `loop` the same way `create_task` would for an actual coroutine.
        task = asyncio.ensure_future(self._defer(), loop=self._loop)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)


class _Handler(FileSystemEventHandler):
    def __init__(self, debouncer: _Debouncer) -> None:
        self._debouncer = debouncer

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        self._debouncer.notify()


def start_watching(
    archive_roots: Sequence[Path],
    loop: asyncio.AbstractEventLoop,
    defer: _Defer,
    *,
    debounce_seconds: float = DEFAULT_SETTLE_SECONDS,
) -> BaseObserver:
    """Start one Observer watching every configured root, debounced onto
    `defer`. Caller owns the returned Observer's lifecycle: `.stop()` then
    `.join()` it on shutdown."""
    debouncer = _Debouncer(loop, defer, debounce_seconds)
    handler = _Handler(debouncer)
    observer = Observer()
    for root in archive_roots:
        observer.schedule(handler, str(root), recursive=True)
    observer.start()
    return observer
