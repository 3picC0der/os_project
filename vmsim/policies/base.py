"""Common interface every replacement policy implements.

The engine owns the frames and page tables; a policy only owns its *ordering
metadata* (a queue, recency stamps, reference bits...). The engine tells the
policy about every event through these hooks, and asks it for a victim frame
only when no free frame exists.

Event order for one reference at global index ``t``:

* hit                  -> ``on_hit(key, frame, t)``
* fault, free frame    -> ``on_load(key, frame, t)``
* fault, memory full   -> ``choose_victim(frames, t)`` -> ``on_evict(old_key, frame)``
                          -> ``on_load(new_key, frame, t)``
* process completion   -> ``on_release(key, frame)`` for each frame it held
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Sequence

from ..trace import PageKey, Reference


class ReplacementPolicy(ABC):
    #: Short identifier used on the command line and in result files.
    name: str = "base"
    #: True for policies that need the future (OPT). Shown as "baseline" in reports.
    is_baseline: bool = False

    def __init__(self) -> None:
        self.num_frames = 0

    # ----- lifecycle ---------------------------------------------------------
    def reset(self, num_frames: int) -> None:
        """Forget all state. Called once at the start of every run."""
        self.num_frames = num_frames

    def prepare(self, refs: Sequence[Reference]) -> None:
        """Optional: see the whole trace before the run (only OPT uses this)."""

    # ----- events ------------------------------------------------------------
    @abstractmethod
    def on_hit(self, key: PageKey, frame: int, t: int) -> None:
        """The referenced page was already resident in ``frame``."""

    @abstractmethod
    def on_load(self, key: PageKey, frame: int, t: int) -> None:
        """``key`` was just loaded into ``frame`` (free or freshly evicted)."""

    @abstractmethod
    def choose_victim(self, frames: Sequence[PageKey | None], t: int) -> int:
        """Memory is full: return the index of the frame to evict."""

    @abstractmethod
    def on_evict(self, key: PageKey, frame: int) -> None:
        """``key`` was evicted from ``frame``; drop its metadata."""

    @abstractmethod
    def on_release(self, key: PageKey, frame: int) -> None:
        """``key``'s process finished and ``frame`` was freed (not an eviction)."""

    # ----- introspection (used by invariants, tests and logs) ----------------
    @abstractmethod
    def tracked_keys(self) -> set[PageKey]:
        """Pages this policy currently holds metadata for (should = resident set)."""

    def snapshot(self) -> dict:
        """Small JSON-friendly view of internal state for logs and debugging."""
        return {}
