"""A small, size- and TTL-bounded in-process cache for the recording-detail page's expensive
shared spectrogram computation (render-cost optimization, v1 backlog "render-cost optimization
for tiled long recordings"). Not persistent, not shared across processes -- `fledermap serve`
runs as a single process (`app.run()` with neither `threaded=True` nor a process count), so a
plain dict-backed cache with its own lock is enough; there is no multi-worker/multi-process
sharing to design for.

Deliberately narrow in scope: this exists to let `web/views/media.py`'s `detail_spectrogram`
route reuse ONE recording's already-computed `FullSpectrogramImage` across the several HTTP
requests a browser fires for that recording's tiles in one page load, not to serve as a
general-purpose or long-lived cache -- that's what the drawer/overview's params-hash disk cache
already is, a different mechanism for a different tradeoff (see the v1 backlog's own
"switch to the existing params-hash cache mechanism" alternative, not chosen here: this stays
scoped to shaving the redundant per-tile recompute within one view, not eliminating render cost
across repeat views, which is real added complexity -- new job type, tile-aware cache keys, a
"rendering..." placeholder UX).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import TypeVar

# Not bound to `FullSpectrogramImage` -- this cache is mechanically generic (any Hashable key,
# any value `compute()` returns), even though `detail_spectrogram` is its only real caller
# today. A bound would need a `TYPE_CHECKING`-only import of `spectrogram.py` for no actual
# type-safety gain here: the cache never inspects or constructs a value, only stores/returns it.
T = TypeVar("T")


class SpectrogramImageCache:
    """`get_or_compute(key, compute)` returns the cached value for `key` if one exists and
    hasn't expired, otherwise calls `compute()`, stores, and returns its result.

    Size-bounded (default 2 entries, LRU-evicted): a burst of tile requests only ever belongs
    to one recording's page view in flight at a time -- 2 is a small safety margin for e.g.
    quick prev/next navigation between two recordings, not a general-purpose cache size.

    TTL-bounded (default 30s) AND actively purged by a background daemon thread waking every
    `purge_interval_s` (default 10s): a lazily-checked-on-lookup-only cache can leave a large
    entry (a full recording's rendered spectrogram, potentially hundreds of MB for a long one)
    sitting in RAM indefinitely if nobody happens to request that recording's tiles again --
    exactly the failure mode to avoid on the modest, self-hosted hardware this project targets
    (Janna, 2026-09-04). `purge()` is itself a plain, directly-callable method (not just an
    internal detail of the thread loop) so it's independently unit-testable without relying on
    real sleep timing.
    """

    def __init__(
        self,
        *,
        max_size: int = 2,
        ttl_s: float = 30.0,
        purge_interval_s: float = 10.0,
        clock: Callable[[], float] = time.monotonic,
        start_purge_thread: bool = True,
    ) -> None:
        self._max_size = max_size
        self._ttl_s = ttl_s
        self._purge_interval_s = purge_interval_s
        self._clock = clock
        self._lock = threading.Lock()
        # OrderedDict, not a plain dict: `move_to_end` on every hit is what makes eviction
        # genuinely least-RECENTLY-USED rather than least-recently-INSERTED.
        self._entries: OrderedDict[Hashable, tuple[float, object]] = OrderedDict()
        self.purge_thread: threading.Thread | None = None
        if start_purge_thread:
            self.purge_thread = threading.Thread(
                target=self._purge_loop,
                daemon=True,  # never blocks process shutdown
                name="spectrogram-image-cache-purge",
            )
            self.purge_thread.start()

    def get_or_compute(self, key: Hashable, compute: Callable[[], T]) -> T:
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expires_at, value = entry
                if self._clock() < expires_at:
                    self._entries.move_to_end(key)
                    return value  # type: ignore[return-value]
                del self._entries[key]  # expired -- fall through and recompute

        # Deliberately computed OUTSIDE the lock: `compute()` runs the actual expensive
        # STFT/palette rendering, and holding the lock across that would block every other
        # request (including unrelated recordings' tile requests) for the full render
        # duration, not just for the cheap dict bookkeeping this lock actually protects. Two
        # concurrent misses for the SAME key recomputing independently is an acceptable rare
        # race (fledermap serve is single-threaded today besides the purge thread, so this
        # can't even happen in practice yet) -- the alternative, a per-key lock or lock
        # held across compute(), is real complexity this cache's narrow scope doesn't
        # justify.
        value = compute()
        with self._lock:
            self._entries[key] = (self._clock() + self._ttl_s, value)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_size:
                self._entries.popitem(last=False)  # evict least-recently-used
        return value

    def purge(self) -> None:
        """Actively evict every expired entry, independent of whether anyone looks it up
        again. Called periodically by the background purge thread; also directly callable
        (and unit-tested that way, with an injected clock) without waiting on that thread."""
        now = self._clock()
        with self._lock:
            expired = [
                k for k, (expires_at, _) in self._entries.items() if expires_at <= now
            ]
            for key in expired:
                del self._entries[key]

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    def _purge_loop(self) -> None:
        while True:
            time.sleep(self._purge_interval_s)
            self.purge()
