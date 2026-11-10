"""LRU: evict the page whose most recent reference is oldest.

Every reference (load or hit) stamps the page with the global reference index
``t``. The victim is the resident page with the smallest stamp. Global indices
are unique, so there are never ties.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Sequence

from ..trace import PageKey
from .base import ReplacementPolicy


class LRUPolicy(ReplacementPolicy):
    name = "lru"

    def reset(self, num_frames: int) -> None:
        super().reset(num_frames)
        # key -> (frame, last_ref_index). Ordered least -> most recently used,
        # so the victim is always at the front (O(1) instead of a min() scan).
        self._recency: OrderedDict[PageKey, tuple[int, int]] = OrderedDict()

    def _touch(self, key: PageKey, frame: int, t: int) -> None:
        self._recency[key] = (frame, t)
        self._recency.move_to_end(key)

    def on_hit(self, key: PageKey, frame: int, t: int) -> None:
        self._touch(key, frame, t)

    def on_load(self, key: PageKey, frame: int, t: int) -> None:
        self._touch(key, frame, t)

    def choose_victim(self, frames: Sequence[PageKey | None], t: int) -> int:
        lru_key = next(iter(self._recency))
        return self._recency[lru_key][0]

    def on_evict(self, key: PageKey, frame: int) -> None:
        del self._recency[key]

    def on_release(self, key: PageKey, frame: int) -> None:
        del self._recency[key]

    def tracked_keys(self) -> set[PageKey]:
        return set(self._recency)

    def last_reference(self, key: PageKey) -> int:
        """Global index of ``key``'s most recent reference (for tests)."""
        return self._recency[key][1]

    def snapshot(self) -> dict:
        return {"lru_to_mru": [f"{k}@{t}" for k, (_, t) in self._recency.items()]}
