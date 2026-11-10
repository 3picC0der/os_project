"""Executable invariants (reviewer feedback #1).

The memory-model rules are not only documented — they are checked after
*every* reference while the simulator runs (``Simulator(check=True)``, the
default). Any violation raises ``InvariantError`` immediately, naming the rule
and the reference index, so a bug can never silently produce wrong numbers.

Checked rules:

I1  hits + faults == references                      (per process and system)
I2  page_ins == faults                               (every fault reads once)
I3  per-process counters sum to the system totals    (totals kept independently)
I4  occupied frames <= F, and free list + occupied == F
I5  no (pid, page) is resident twice
I6  every occupied frame has exactly one owner, and the frame table and the
    page tables agree in both directions
I7  finished processes hold no frames and no policy metadata
I8  the policy tracks exactly the resident pages (no leaked metadata)
I9  Clock: empty frames have reference bit 0, hand is inside [0, F)
I10 clean pages never cause writes: disk writes + dirty resident pages
    <= write references, per process
"""

from __future__ import annotations

from dataclasses import fields
from typing import TYPE_CHECKING

from .metrics import Counters
from .policies.clock import ClockPolicy

if TYPE_CHECKING:  # avoid a circular import at runtime
    from .engine import Simulator


class InvariantError(AssertionError):
    pass


def _fail(sim: "Simulator", rule: str, detail: str) -> None:
    raise InvariantError(f"[{rule}] after reference {sim.t - 1} ({sim.policy.name}, F={sim.num_frames}): {detail}")


def check_invariants(sim: "Simulator") -> None:
    F = sim.num_frames

    # I1, I2 — per process
    for pid, ps in sim.stats.items():
        if ps.hits + ps.faults != ps.references:
            _fail(sim, "I1", f"P{pid}: hits {ps.hits} + faults {ps.faults} != refs {ps.references}")
        if ps.page_ins != ps.faults:
            _fail(sim, "I2", f"P{pid}: page_ins {ps.page_ins} != faults {ps.faults}")

    # I3 — per-process sums equal independently maintained totals
    summed = Counters()
    for ps in sim.stats.values():
        summed.add(Counters(**{f.name: getattr(ps, f.name) for f in fields(Counters)}))
    if summed != sim.totals:
        _fail(sim, "I3", f"per-process sum {summed} != totals {sim.totals}")
    if sim.totals.references != sim.t:
        _fail(sim, "I1", f"total refs {sim.totals.references} != executed {sim.t}")

    # I4 — occupancy
    occupied = [i for i, k in enumerate(sim.frames) if k is not None]
    if len(sim.frames) != F or len(occupied) > F:
        _fail(sim, "I4", f"{len(occupied)} occupied of {len(sim.frames)} frames, F={F}")
    if sorted(sim._free) != [i for i, k in enumerate(sim.frames) if k is None]:
        _fail(sim, "I4", f"free list {sorted(sim._free)} disagrees with frame table")

    # I5 — no duplicate resident page
    keys = [sim.frames[i] for i in occupied]
    if len(keys) != len(set(keys)):
        _fail(sim, "I5", f"duplicate resident page in frames {sim.frames}")

    # I6 — frame table <-> page tables agree (each frame exactly one owner)
    resident = sim.resident_pages()
    if len(resident) != len(occupied):
        _fail(sim, "I6", f"{len(resident)} page-table entries but {len(occupied)} occupied frames")
    for key, frame in resident.items():
        if sim.frames[frame] != key:
            _fail(sim, "I6", f"{key} thinks it is in frame {frame}, frame holds {sim.frames[frame]}")

    # I7 — finished processes are fully cleaned up
    for pid in sim.finished:
        if sim.page_tables.get(pid):
            _fail(sim, "I7", f"finished P{pid} still has resident pages")
        if pid in sim.tracker.active or pid in sim.tracker.warned:
            _fail(sim, "I7", f"finished P{pid} still active/warned in analysis")

    # I8 — policy metadata matches the resident set exactly
    tracked = sim.policy.tracked_keys()
    if tracked != set(resident):
        _fail(sim, "I8", f"policy tracks {sorted(map(str, tracked))}, resident {sorted(map(str, resident))}")

    # I9 — Clock specifics
    if isinstance(sim.policy, ClockPolicy):
        if not 0 <= sim.policy.hand < F:
            _fail(sim, "I9", f"clock hand {sim.policy.hand} out of range")
        for i, k in enumerate(sim.frames):
            if k is None and sim.policy.ref_bits[i] != 0:
                _fail(sim, "I9", f"empty frame {i} has reference bit 1")

    # I10 — only written pages are ever written back
    for pid, ps in sim.stats.items():
        dirty_now = sum(1 for e in sim.page_tables[pid].values() if e.dirty)
        if ps.disk_writes + dirty_now > ps.writes:
            _fail(sim, "I10", f"P{pid}: {ps.disk_writes} disk writes + {dirty_now} dirty > {ps.writes} write refs")
