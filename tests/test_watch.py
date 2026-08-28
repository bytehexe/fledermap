from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fledermap.jobs.watch import start_watching


async def _defer_recorder(calls: list[float]) -> None:
    calls.append(time.monotonic())


async def _run(coro_factory: Callable[[], Awaitable[None]], timeout: float) -> None:
    """Run the given async body with a hard timeout so a bug that never
    fires the debounce can't hang the test suite."""
    await asyncio.wait_for(coro_factory(), timeout=timeout)


def test_a_single_event_defers_once_after_the_debounce_window(
    tmp_path: Path,
) -> None:
    calls: list[float] = []

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path],
            loop,
            lambda: _defer_recorder(calls),
            debounce_seconds=0.05,
        )
        try:
            (tmp_path / "new.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1


def test_a_second_event_before_the_window_elapses_resets_the_timer(
    tmp_path: Path,
) -> None:
    """Two events 0.03s apart, debounce window 0.05s: if the timer were NOT
    reset, the first event's timer would fire at ~0.05s regardless -- the
    only way this test can see exactly one call at ~0.08s (not ~0.05s) is if
    the second event genuinely restarted the countdown."""
    calls: list[float] = []
    start = time.monotonic()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [tmp_path],
            loop,
            lambda: _defer_recorder(calls),
            debounce_seconds=0.05,
        )
        try:
            (tmp_path / "a.wav").write_bytes(b"x")
            await asyncio.sleep(0.03)
            (tmp_path / "b.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1
    assert calls[0] - start >= 0.08 - 0.01  # small tolerance for scheduler jitter


def test_events_across_multiple_roots_are_all_watched(tmp_path: Path) -> None:
    calls: list[float] = []
    root_a = tmp_path / "a"
    root_b = tmp_path / "b"
    root_a.mkdir()
    root_b.mkdir()

    async def body() -> None:
        loop = asyncio.get_running_loop()
        observer = start_watching(
            [root_a, root_b],
            loop,
            lambda: _defer_recorder(calls),
            debounce_seconds=0.05,
        )
        try:
            (root_b / "new.wav").write_bytes(b"x")
            await asyncio.sleep(0.2)
        finally:
            observer.stop()
            observer.join()

    asyncio.run(_run(body, timeout=2.0))
    assert len(calls) == 1
