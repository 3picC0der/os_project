"""FIFO: evict the page that was *loaded* earliest.

Hits do not change a page's position in the queue — FIFO only cares about
arrival order. The queue is global, so the oldest page may belong to any
process (global replacement).
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Sequence

from ..trace import PageKey
from .base import ReplacementPolicy


class FIFOPolicy(ReplacementPolicy):
    name = "fifo"

    def reset(self, num_frames: int) -> None:
        super().reset(num_frames)
        # key -> frame, kept in load order (front = oldest).
        self._queue: OrderedDict[PageKey, int] = OrderedDict()

    def on_hit(self, key: PageKey, frame: int, t: int) -> None:
        pass  # arrival order is unchanged by a hit

    def on_load(self, key: PageKey, frame: int, t: int) -> None:
        self._queue[key] = frame  # newest goes to the back

    def choose_victim(self, frames: Sequence[PageKey | None], t: int) -> int:
        oldest_key = next(iter(self._queue))
        return self._queue[oldest_key]

    def on_evict(self, key: PageKey, frame: int) -> None:
        del self._queue[key]

    def on_release(self, key: PageKey, frame: int) -> None:
        del self._queue[key]

    def tracked_keys(self) -> set[PageKey]:
        return set(self._queue)

    def snapshot(self) -> dict:
        return {"queue": [str(k) for k in self._queue]}
