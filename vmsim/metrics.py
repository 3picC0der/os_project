"""Counters collected during a run.

Attribution rules (from the proposal):

* hits, faults and page-ins are charged to the **requesting** process;
* eviction write-backs and completion flushes are charged to the **owner** of
  the dirty page (the process whose page had to be written to disk).

Page-ins, eviction write-backs and completion flushes are deliberately kept as
three separate counters (reviewer feedback #5). In this model every fault
causes exactly one page-in, so ``page_ins == faults`` always; the column is
still reported on its own so disk reads and disk writes are never mixed up.

System-wide rates are computed from summed counts, never by averaging the
per-process rates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields


def ratio(num: int, den: int) -> float | None:
    """num/den, or None when den is 0 (reported as "N/A")."""
    return num / den if den else None


@dataclass
class Counters:
    references: int = 0
    reads: int = 0
    writes: int = 0
    hits: int = 0
    faults: int = 0
    page_ins: int = 0              # disk reads: one per fault
    eviction_writebacks: int = 0   # dirty page written to disk when evicted
    completion_flushes: int = 0    # dirty page written to disk when its process ended
    clean_evictions: int = 0       # evicted without any disk write
    evictions_caused: int = 0      # faults by this process that had to evict something
    pages_evicted: int = 0         # this process's pages that were evicted (by anyone)

    @property
    def hit_rate(self) -> float | None:
        return ratio(self.hits, self.references)

    @property
    def fault_rate(self) -> float | None:
        return ratio(self.faults, self.references)

    @property
    def disk_writes(self) -> int:
        return self.eviction_writebacks + self.completion_flushes

    def add(self, other: "Counters") -> None:
        for f in fields(self):
            setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    def as_dict(self) -> dict:
        d = asdict(self)
        d["hit_rate"] = self.hit_rate
        d["fault_rate"] = self.fault_rate
        return d


@dataclass
class ProcessStats(Counters):
    pid: int = 0
    first_ref: int | None = None   # global index of first reference
    last_ref: int | None = None    # global index of last reference (completion time)
    peak_resident: int = 0         # max frames held at once
    peak_ws: int = 0               # max working-set size seen
    warning_refs: int = 0          # references after which this process was warned


@dataclass
class UtilizationTracker:
    """Occupied frames / F, sampled after each reference and before cleanup."""

    num_frames: int
    samples: list[int] = field(default_factory=list)

    def sample(self, occupied: int) -> None:
        self.samples.append(occupied)

    @property
    def mean(self) -> float | None:
        if not self.samples:
            return None
        return sum(self.samples) / (len(self.samples) * self.num_frames)

    @property
    def peak(self) -> float | None:
        if not self.samples:
            return None
        return max(self.samples) / self.num_frames
