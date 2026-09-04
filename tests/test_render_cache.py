"""Plain unit tests for `SpectrogramImageCache` -- no Flask, no DB. The background purge
thread's own sleep-loop timing is deliberately NOT tested here (would be flaky); `purge()`
itself is a directly callable, independently testable method the thread just calls
periodically, and that's what these tests exercise with an injected fake clock instead of
waiting on real time."""

from __future__ import annotations

from collections.abc import Callable

from fledermap.media.render_cache import SpectrogramImageCache


def _fake_image(tag: str) -> str:
    """A cheap stand-in for a `FullSpectrogramImage` -- the cache doesn't care what it
    stores, and a plain string is far easier to assert identity/equality on than a real
    PIL image + numpy array pair."""
    return tag


class FakeClock:
    """An injectable, manually-advanced clock -- `SpectrogramImageCache` takes any
    zero-arg `clock` callable, so real time never enters these tests."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_get_or_compute_computes_once_on_a_miss() -> None:
    cache = SpectrogramImageCache(start_purge_thread=False)
    calls = []

    def compute() -> str:
        calls.append(1)
        return _fake_image("a")

    result = cache.get_or_compute("key-a", compute)

    assert result == "a"
    assert len(calls) == 1


def test_get_or_compute_reuses_a_cached_value_without_recomputing() -> None:
    cache = SpectrogramImageCache(start_purge_thread=False)
    calls = []

    def compute() -> str:
        calls.append(1)
        return _fake_image("a")

    cache.get_or_compute("key-a", compute)
    result = cache.get_or_compute("key-a", compute)

    assert result == "a"
    assert len(calls) == 1  # NOT recomputed on the second call


def test_get_or_compute_recomputes_for_a_different_key() -> None:
    cache = SpectrogramImageCache(start_purge_thread=False)
    calls = []

    def compute_a() -> str:
        calls.append("a")
        return _fake_image("a")

    def compute_b() -> str:
        calls.append("b")
        return _fake_image("b")

    cache.get_or_compute("key-a", compute_a)
    cache.get_or_compute("key-b", compute_b)

    assert calls == ["a", "b"]


def test_max_size_evicts_the_least_recently_used_entry() -> None:
    cache = SpectrogramImageCache(max_size=2, start_purge_thread=False)
    calls = []

    def compute(tag: str) -> Callable[[], str]:
        def inner() -> str:
            calls.append(tag)
            return _fake_image(tag)

        return inner

    cache.get_or_compute("a", compute("a"))
    cache.get_or_compute("b", compute("b"))
    # Touch "a" so "b" becomes the least-recently-used, not "a".
    cache.get_or_compute("a", compute("a"))
    # A third distinct key exceeds max_size=2 -- "b" (least recently used) must be evicted.
    cache.get_or_compute("c", compute("c"))

    calls.clear()
    cache.get_or_compute("a", compute("a"))
    cache.get_or_compute("c", compute("c"))
    assert calls == []  # both still cached, neither recomputed

    cache.get_or_compute("b", compute("b"))
    assert calls == ["b"]  # evicted earlier -- had to recompute


def test_expired_entry_is_recomputed_on_lookup() -> None:
    clock = FakeClock()
    cache = SpectrogramImageCache(ttl_s=30.0, clock=clock, start_purge_thread=False)
    calls = []

    def compute() -> str:
        calls.append(1)
        return _fake_image("a")

    cache.get_or_compute("key-a", compute)
    clock.advance(31.0)
    cache.get_or_compute("key-a", compute)

    assert len(calls) == 2  # the stale entry was never returned as a hit


def test_unexpired_entry_survives_a_purge() -> None:
    clock = FakeClock()
    cache = SpectrogramImageCache(ttl_s=30.0, clock=clock, start_purge_thread=False)
    cache.get_or_compute("key-a", lambda: _fake_image("a"))

    clock.advance(10.0)
    cache.purge()

    assert cache.get_or_compute("key-a", lambda: _fake_image("SHOULD-NOT-RUN")) == "a"


def test_purge_actively_evicts_an_expired_entry_without_a_lookup() -> None:
    """The whole point of active purging (Janna, 2026-09-04: a lazy check-on-lookup cache can
    leave a large entry sitting in RAM indefinitely on a long-idle, low-RAM box) -- `purge()`
    must remove a stale entry on its own, not merely refuse to return it on the next access."""
    clock = FakeClock()
    cache = SpectrogramImageCache(ttl_s=30.0, clock=clock, start_purge_thread=False)
    cache.get_or_compute("key-a", lambda: _fake_image("a"))

    clock.advance(31.0)
    cache.purge()

    assert cache.size() == 0


def test_start_purge_thread_launches_a_daemon_thread() -> None:
    """Light-touch: only checks the thread was actually started and is a daemon (so it can't
    block process shutdown) -- not the sleep-loop's real timing, which would be flaky."""
    cache = SpectrogramImageCache(purge_interval_s=9999.0, start_purge_thread=True)

    assert cache.purge_thread is not None
    assert cache.purge_thread.daemon is True
    assert cache.purge_thread.is_alive()
