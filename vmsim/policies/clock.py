"""Clock (second-chance): an approximation of LRU using one bit per frame.

Rules (from the proposal, implemented exactly):

* Frames form a circular array; the hand starts at frame 0.
* Every reference — hit or load — sets that frame's reference bit to 1.
* When memory is full, scan from the hand: a frame with bit 1 gets its bit
  cleared (its "second chance") and the hand advances; the first frame found
  with bit 0 is the victim.
* After **every insertion**, including into a free frame, the hand moves to
  the frame right after the one just filled: ``hand = (frame + 1) % F``.
  (Design decision: with free-frame reuse after a process finishes, the
  lowest free frame need not be under the hand, so we anchor the hand to the
  inserted frame rather than doing ``hand += 1``.)
* Hits never move the hand.
* When a process finishes, its freed frames get bit 0, but the hand stays put.
"""

from __future__ import annotations

from typing import Sequence

from ..trace import PageKey
from .base import ReplacementPolicy


class ClockPolicy(ReplacementPolicy):
    name = "clock"

    def reset(self, num_frames: int) -> None:
        super().reset(num_frames)
        self.hand = 0
        self.ref_bits = [0] * num_frames
        self._resident: dict[PageKey, int] = {}  # key -> frame, for invariants

    def on_hit(self, key: PageKey, frame: int, t: int) -> None:
        self.ref_bits[frame] = 1  # hand does not move on a hit

    def on_load(self, key: PageKey, frame: int, t: int) -> None:
        self.ref_bits[frame] = 1
        self._resident[key] = frame
        self.hand = (frame + 1) % self.num_frames

    def choose_victim(self, frames: Sequence[PageKey | None], t: int) -> int:
        # Terminates within 2 sweeps: the first sweep clears every 1 bit.
        while self.ref_bits[self.hand] == 1:
            self.ref_bits[self.hand] = 0
            self.hand = (self.hand + 1) % self.num_frames
        return self.hand

    def on_evict(self, key: PageKey, frame: int) -> None:
        del self._resident[key]
        # The bit is about to be set again by on_load for the incoming page.

    def on_release(self, key: PageKey, frame: int) -> None:
        del self._resident[key]
        self.ref_bits[frame] = 0  # free frame carries no history; hand unchanged

    def tracked_keys(self) -> set[PageKey]:
        return set(self._resident)

    def snapshot(self) -> dict:
        return {"hand": self.hand, "bits": list(self.ref_bits)}
