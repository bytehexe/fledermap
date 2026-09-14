"""A small, size- and TTL-bounded in-process cache for expensive per-recording renders that a
single browser session re-requests several times in quick succession -- the recording-detail
page's shared spectrogram computation (render-cost optimization, v1 backlog "render-cost
optimization for tiled long recordings"), and the TE/HET preview-audio routes' rendered Opus
files (`web/views/media.py`'s `detail_preview`/`het_preview` -- design spec
docs/superpowers/specs/2026-09-14-fledermap-audio-denoise-design.md's follow-up bugfix: those
routes used to re-render from scratch on every single HTTP request, including the browser's own
mid-playback Range sub-requests, and `ffmpeg`'s Ogg muxer picks a new random stream serial number
on every invocation -- so two independent renders of identical audio are never byte-identical,
which silently corrupted playback the moment a browser's second Range request landed on a freshly
re-rendered, differently-serialed file. Caching the render across those requests, not just within
one, is the actual fix). Not persistent, not shared across processes -- `fledermap serve` runs as
a single process (`app.run()` with neither `threaded=True` nor a process count), so a plain
dict-backed cache with its own lock is enough; there is no multi-worker/multi-process sharing to
design for.

Deliberately narrow in scope: this exists to let a handful of `web/views/media.py` routes reuse
one recording's already-computed render across the several HTTP requests a browser fires for it
in one page view/playback session, not to serve as a general-purpose or long-lived cache -- that's
what the drawer/overview's params-hash disk cache already is, a different mechanism for a
different tradeoff (see the v1 backlog's own "switch to the existing params-hash cache mechanism"
alternative, not chosen here: this stays scoped to shaving the redundant recompute within one
view/session, not eliminating render cost across repeat views, which is real added complexity --
new job type, tile-aware cache keys, a "rendering..." placeholder UX).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Generic, TypeVar

# Not bound to any particular value type -- this cache is mechanically generic (any Hashable
# key, any value `compute()` returns), used today for both an in-memory `FullSpectrogramImage`
# and an on-disk preview-audio `Path`. A real `Generic[T]` (not just a bare TypeVar used in
# method signatures) so a caller's `on_evict` gets the actual value type back, not `object` --
# e.g. `RenderCache[Path](on_evict=lambda p: p.unlink())` type-checks `p` as `Path`.
T = TypeVar("T")


class RenderCache(Generic[T]):
    """`get_or_compute(key, compute)` returns the cached value for `key` if one exists and
    hasn't expired, otherwise calls `compute()`, stores, and returns its result.

    Size-bounded (default 2 entries, LRU-evicted): a burst of requests only ever belongs to a
    small number of recordings/playback sessions in flight at once -- 2 is a small safety margin
    for e.g. quick prev/next navigation between two recordings, not a general-purpose cache size.
    A caller with a different access pattern (e.g. several small cached files rather than one
    large one) passes its own `max_size`.

    TTL-bounded (default 30s), and the TTL is a SLIDING window: every cache HIT (not just the
    original insert) pushes that entry's expiry back out to `now + ttl_s`. A one-off render that
    nobody asks for again still ages out on schedule, but an entry under active, repeated use --
    a browser buffering through a long TE-expanded preview, tile requests trickling in on a slow
    connection -- keeps refreshing its own lifetime and won't expire out from under an in-progress
    session just because the ORIGINAL render happened more than `ttl_s` ago.

    Also actively purged by a background daemon thread waking every `purge_interval_s` (default
    10s): a lazily-checked-on-lookup-only cache can leave a large entry (a full recording's
    rendered spectrogram, potentially hundreds of MB for a long one) sitting in RAM indefinitely
    if nobody happens to request that recording again -- exactly the failure mode to avoid on the
    modest, self-hosted hardware this project targets (Janna, 2026-09-04). `purge()` is itself a
    plain, directly-callable method (not just an internal detail of the thread loop) so it's
    independently unit-testable without relying on real sleep timing.

    `on_evict`, if given, is called with a removed entry's VALUE whenever this cache stops holding
    it -- TTL expiry (on lookup or via `purge()`) and LRU eviction alike -- so a caller whose
    cached value owns an external resource (e.g. a temp file on disk) can release it. Never called
    while holding this cache's internal lock, matching `compute()`'s own outside-the-lock
    treatment below: cleanup can be arbitrary caller code, and holding the lock across it would
    block every other request for however long that cleanup takes, not just the cheap dict
    bookkeeping the lock actually protects.
    """

    def __init__(
        self,
        *,
        max_size: int = 2,
        ttl_s: float = 30.0,
        purge_interval_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        on_evict: Callable[[T], None] | None = None,
        start_purge_thread: bool = True,
    ) -> None:
        self._max_size = max_size
        self._ttl_s = ttl_s
        self._purge_interval_s = purge_interval_s
        self._clock = clock
        self._on_evict = on_evict
        self._lock = threading.Lock()
        # OrderedDict, not a plain dict: `move_to_end` on every hit is what makes eviction
        # genuinely least-RECENTLY-USED rather than least-recently-INSERTED.
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
        self.purge_thread: threading.Thread | None = None
        if start_purge_thread:
            self.purge_thread = threading.Thread(
                target=self._purge_loop,
                daemon=True,  # never blocks process shutdown
                name="render-cache-purge",
            )
            self.purge_thread.start()

    def _evict(self, value: object) -> None:
        if self._on_evict is not None:
            self._on_evict(value)  # type: ignore[arg-type]

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expires_at, value = entry
                if self._clock() < expires_at:
                    # Sliding TTL: a hit refreshes this entry's own expiry, not just its
                    # recency for LRU purposes.
                    self._entries[key] = (self._clock() + self._ttl_s, value)
                    self._entries.move_to_end(key)
                    return value  # type: ignore[return-value]
                del self._entries[key]  # expired -- fall through and recompute
                stale_value = value
            else:
                stale_value = None
        if entry is not None:
            self._evict(stale_value)

        # Deliberately computed OUTSIDE the lock: `compute()` runs the actual expensive
        # rendering, and holding the lock across that would block every other request
        # (including unrelated recordings' requests) for the full render duration, not just
        # for the cheap dict bookkeeping this lock actually protects. Two concurrent misses
        # for the SAME key recomputing independently is an acceptable rare race (fledermap
        # serve is single-threaded today besides the purge thread, so this can't even happen
        # in practice yet) -- the alternative, a per-key lock or lock held across compute(),
        # is real complexity this cache's narrow scope doesn't justify.
        value = compute()
        evicted: list[object] = []
        with self._lock:
            self._entries[key] = (self._clock() + self._ttl_s, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                _evicted_key, (_expires_at, evicted_value) = self._entries.popitem(
                    last=False
                )  # evict least-recently-used
                evicted.append(evicted_value)
        for evicted_value in evicted:
            self._evict(evicted_value)
        return value

    def purge(self) -> None:
        """Actively evict every expired entry, independent of whether anyone looks it up
        again. Called periodically by the background purge thread; also directly callable
        (and unit-tested that way, with an injected clock) without waiting on that thread."""
        now = self._clock()
        evicted: list[object] = []
        with self._lock:
            expired_keys = [
                k for k, (expires_at, _) in self._entries.items() if expires_at <= now
            ]
            for key in expired_keys:
                _expires_at, value = self._entries.pop(key)
                evicted.append(value)
        for value in evicted:
            self._evict(value)

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge_loop(self) -> None:
        while True:
            time.sleep(self._purge_interval_s)
            self.purge()


# Backward-compatible alias -- this class was originally named for its one-time-only caller
# (the spectrogram image render). Kept as an alias, not removed, since the name still reads
# fine at that one call site and a rename-only diff at that site has no behavioral point.
SpectrogramImageCache = RenderCache
