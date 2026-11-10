"""Replacement policy registry.

``make_policy("lru")`` returns a fresh policy object. Each run gets its own
instance, so no state can leak between runs.
"""

from __future__ import annotations

from .base import ReplacementPolicy
from .clock import ClockPolicy
from .fifo import FIFOPolicy
from .lru import LRUPolicy
from .opt import OPTPolicy

POLICIES: dict[str, type[ReplacementPolicy]] = {
    "fifo": FIFOPolicy,
    "lru": LRUPolicy,
    "clock": ClockPolicy,
    "opt": OPTPolicy,
}

#: The three policies the project actually evaluates (OPT is a baseline).
MAIN_POLICIES = ("fifo", "lru", "clock")


def make_policy(name: str) -> ReplacementPolicy:
    try:
        return POLICIES[name.lower()]()
    except KeyError:
        valid = ", ".join(POLICIES)
        raise ValueError(f"unknown policy {name!r} (choose from: {valid})") from None


__all__ = [
    "ReplacementPolicy",
    "FIFOPolicy",
    "LRUPolicy",
    "ClockPolicy",
    "OPTPolicy",
    "POLICIES",
    "MAIN_POLICIES",
    "make_policy",
]
