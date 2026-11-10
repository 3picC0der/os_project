"""OPT (Belady's optimal): evict the page whose next use is farthest away.

OPT needs to know the future, so no real OS can implement it. We include it
only as a **reference baseline**: it gives the minimum possible number of
faults for a trace and frame count, so the report can say how close FIFO,
LRU and Clock come to the best achievable.

Ties (several pages never used again) are broken by lowest frame index so
results are deterministic.
"""

from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from typing import Sequence

from ..trace import PageKey, Reference
from .base import ReplacementPolicy

NEVER = float("inf")


class OPTPolicy(ReplacementPolicy):
    name = "opt"
    is_baseline = True

    def reset(self, num_frames: int) -> None:
        super().reset(num_frames)
        self._resident: dict[PageKey, int] = {}
        self._uses: dict[PageKey, list[int]] = {}

    def prepare(self, refs: Sequence[Reference]) -> None:
        uses: dict[PageKey, list[int]] = defaultdict(list)
        for t, r in enumerate(refs):
            uses[r.key].append(t)  # indices are appended in increasing order
        self._uses = dict(uses)

    def _next_use(self, key: PageKey, t: int) -> float:
        times = self._uses.get(key, [])
        i = bisect_right(times, t)  # first use strictly after t
        return times[i] if i < len(times) else NEVER

    def on_hit(self, key: PageKey, frame: int, t: int) -> None:
        pass

    def on_load(self, key: PageKey, frame: int, t: int) -> None:
        self._resident[key] = frame

    def choose_victim(self, frames: Sequence[PageKey | None], t: int) -> int:
        best_frame, best_next = -1, -1.0
        for frame, key in enumerate(frames):
            nxt = self._next_use(key, t)  # memory is full, so key is never None
            if nxt > best_next:  # strict ">" keeps the lowest frame on ties
                best_frame, best_next = frame, nxt
        return best_frame

    def on_evict(self, key: PageKey, frame: int) -> None:
        del self._resident[key]

    def on_release(self, key: PageKey, frame: int) -> None:
        del self._resident[key]

    def tracked_keys(self) -> set[PageKey]:
        return set(self._resident)
