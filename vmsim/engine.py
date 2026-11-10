"""The simulation engine: frames, page tables, faults, evictions and cleanup.

Memory model (implemented exactly as specified in the proposal):

* ``F`` physical frames, all empty at the start.
* Demand paging with one **global** pool: no reserved frames, no quotas.
  A process's allocation is simply how many of its pages are resident now.
* On a fault the page goes into the **lowest-index free frame**; if none is
  free, the replacement policy picks a victim, which may belong to any process.
* A read load is clean; a write load or a write hit makes the page dirty
  (a later read does not clean it). Evicting a dirty page costs exactly one
  write-back; evicting a clean page costs none.
* The whole trace is known up front, so we know each process's last reference.
  Right after it, the process **completes**: its dirty pages are flushed
  (counted separately as completion flushes), all its frames are freed, and
  its policy metadata is removed. Metrics for that last reference are sampled
  *before* this cleanup.

Usage::

    sim = Simulator(refs, num_frames=4, policy="lru")
    result = sim.run()

or drive it one reference at a time with ``sim.step()`` (used by the tests to
inspect Clock bits mid-run).
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass, field
from typing import Sequence

from .analysis import DEFAULT_THRESHOLD, DEFAULT_WINDOW, WarningEvent, WorkingSetTracker
from .invariants import check_invariants
from .metrics import Counters, ProcessStats, UtilizationTracker
from .policies import ReplacementPolicy, make_policy
from .trace import PageKey, Reference, validate_refs


@dataclass
class PageTableEntry:
    """Per-page bookkeeping for a *resident* page.

    Non-resident pages simply have no entry (equivalent to valid bit = 0).
    """

    frame: int
    dirty: bool
    loaded_at: int   # global index of the fault that loaded it
    last_ref: int    # global index of the latest reference to it


@dataclass
class StepRecord:
    """Everything that happened at one global reference index (one log row)."""

    t: int
    pid: int
    page: int
    op: str
    result: str                 # "HIT" or "FAULT"
    frame: int                  # frame that now holds the page
    evicted: str = ""           # e.g. "P2:5" if a page was evicted
    writeback: bool = False     # True if the eviction wrote a dirty page
    ws_size: int = 0            # requester's working-set size after this reference
    window_fault_rate: float | None = None
    demand_pre: int = 0         # D before any completion cleanup
    demand_post: int = 0        # D after cleanup (== demand_pre if nobody finished)
    occupied: int = 0           # frames in use, sampled before cleanup
    utilization: float = 0.0    # occupied / F
    warned_pids: str = ""       # e.g. "1;3" — processes warned after this step
    system_warning: bool = False
    completed: bool = False     # this was the requester's last reference
    flushed: int = 0            # dirty pages flushed by that completion
    policy_state: dict = field(default_factory=dict)


@dataclass
class SimulationResult:
    policy: str
    num_frames: int
    window: int
    threshold: float
    references: int
    processes: dict[int, ProcessStats]
    totals: Counters
    utilization_mean: float | None
    utilization_peak: float | None
    steps: list[StepRecord]
    warnings: list[WarningEvent]
    final_policy_state: dict

    @property
    def warned_reference_count(self) -> int:
        """How many references ended with the system warning on."""
        return sum(1 for s in self.steps if s.system_warning)


class Simulator:
    def __init__(
        self,
        refs: Sequence[Reference],
        num_frames: int,
        policy: str | ReplacementPolicy,
        window: int = DEFAULT_WINDOW,
        threshold: float = DEFAULT_THRESHOLD,
        check: bool = True,
        record_steps: bool = True,
        record_policy_state: bool = False,
    ):
        if not isinstance(num_frames, int) or isinstance(num_frames, bool) or num_frames <= 0:
            raise ValueError(f"frame count must be a positive integer, got {num_frames!r}")
        self.refs = validate_refs(refs)
        self.num_frames = num_frames
        self.policy = make_policy(policy) if isinstance(policy, str) else policy
        self.check = check
        self.record_steps = record_steps
        self.record_policy_state = record_policy_state

        # --- replacement state is rebuilt from scratch for every run --------
        self.policy.reset(num_frames)
        self.policy.prepare(self.refs)

        self.frames: list[PageKey | None] = [None] * num_frames
        self._free: list[int] = list(range(num_frames))  # min-heap of free frame indices
        self.page_tables: dict[int, dict[int, PageTableEntry]] = {}

        # When does each process issue its last reference? (Trace is fully loaded.)
        self.last_index: dict[int, int] = {}
        for t, r in enumerate(self.refs):
            self.last_index[r.pid] = t

        self.stats: dict[int, ProcessStats] = {}
        self.totals = Counters()  # maintained independently; invariants compare it to the per-process sum
        self.util = UtilizationTracker(num_frames)
        self.tracker = WorkingSetTracker(num_frames, window, threshold)
        self.finished: set[int] = set()
        self.steps: list[StepRecord] = []
        self.t = 0  # index of the next reference to execute

    # ------------------------------------------------------------------ run
    @property
    def done(self) -> bool:
        return self.t >= len(self.refs)

    def run(self) -> SimulationResult:
        while not self.done:
            self.step()
        return self.result()

    def step(self) -> StepRecord:
        """Execute the next reference, then (if it was the last one for that
        process) clean the process up. Returns the step's log record."""
        t = self.t
        ref = self.refs[t]
        pid, key = ref.pid, ref.key

        # A process becomes active at its first reference.
        if pid not in self.stats:
            self.stats[pid] = ProcessStats(pid=pid, first_ref=t)
            self.page_tables[pid] = {}
            self.tracker.activate(pid)
        ps = self.stats[pid]

        self._count(ps, "references")
        self._count(ps, "writes" if ref.is_write else "reads")

        pte = self.page_tables[pid].get(ref.page)
        evicted, writeback = "", False

        if pte is not None:
            # ---------------- hit ----------------
            self._count(ps, "hits")
            pte.last_ref = t
            if ref.is_write:
                pte.dirty = True
            frame = pte.frame
            self.policy.on_hit(key, frame, t)
            was_fault = False
        else:
            # ---------------- fault --------------
            self._count(ps, "faults")
            self._count(ps, "page_ins")  # every fault reads the page from disk
            if self._free:
                frame = heapq.heappop(self._free)  # lowest-index free frame
            else:
                frame = self.policy.choose_victim(self.frames, t)
                evicted, writeback = self._evict(frame, requester=ps)
            self.frames[frame] = key
            self.page_tables[pid][ref.page] = PageTableEntry(
                frame=frame, dirty=ref.is_write, loaded_at=t, last_ref=t
            )
            self.policy.on_load(key, frame, t)
            was_fault = True

        # ---------------- analysis (before cleanup) ----------------
        self.tracker.record(pid, ref.page, was_fault)
        demand_pre = self.tracker.evaluate(t, cause="reference")
        occupied = self.num_frames - len(self._free)
        self.util.sample(occupied)

        ps.peak_resident = max(ps.peak_resident, len(self.page_tables[pid]))
        ps.peak_ws = max(ps.peak_ws, self.tracker.ws_size(pid))
        for warned_pid in self.tracker.warned:
            self.stats[warned_pid].warning_refs += 1

        rec = StepRecord(
            t=t, pid=pid, page=ref.page, op=ref.op,
            result="FAULT" if was_fault else "HIT", frame=frame,
            evicted=evicted, writeback=writeback,
            ws_size=self.tracker.ws_size(pid),
            window_fault_rate=self.tracker.window_fault_rate(pid),
            demand_pre=demand_pre, demand_post=demand_pre,
            occupied=occupied, utilization=occupied / self.num_frames,
        )
        if self.record_policy_state:
            rec.policy_state = self.policy.snapshot()  # pre-cleanup, like all step metrics

        # ---------------- completion cleanup ----------------
        if t == self.last_index[pid]:
            ps.last_ref = t
            rec.completed = True
            rec.flushed = self._complete(pid)
            rec.demand_post = self.tracker.finish(pid, t)

        rec.warned_pids = ";".join(str(p) for p in sorted(self.tracker.warned))
        rec.system_warning = self.tracker.system_warning

        self.t += 1
        if self.check:
            check_invariants(self)
        if self.record_steps:
            self.steps.append(rec)
        return rec

    # ------------------------------------------------------------- helpers
    def _count(self, ps: ProcessStats, name: str, amount: int = 1) -> None:
        """Bump a counter for one process *and* the independent system total."""
        setattr(ps, name, getattr(ps, name) + amount)
        setattr(self.totals, name, getattr(self.totals, name) + amount)

    def _evict(self, frame: int, requester: ProcessStats) -> tuple[str, bool]:
        """Evict whatever is in ``frame``. Returns (label, was_dirty)."""
        victim = self.frames[frame]
        assert victim is not None, "policy chose an empty frame as victim"
        owner = self.stats[victim.pid]
        entry = self.page_tables[victim.pid].pop(victim.page)

        self._count(requester, "evictions_caused")
        self._count(owner, "pages_evicted")
        if entry.dirty:
            # One synchronous write-back, charged to the page's owner,
            # no matter how many times the page was written.
            self._count(owner, "eviction_writebacks")
        else:
            self._count(owner, "clean_evictions")

        self.policy.on_evict(victim, frame)
        self.frames[frame] = None
        return str(victim), entry.dirty

    def _complete(self, pid: int) -> int:
        """Process ``pid`` issued its last reference: flush, free, forget.

        Cleanup is not a memory reference: it does not touch any window.
        Returns the number of dirty pages flushed.
        """
        ps = self.stats[pid]
        flushed = 0
        # Release in frame order so logs and Clock state are deterministic.
        for page, entry in sorted(self.page_tables[pid].items(), key=lambda kv: kv[1].frame):
            if entry.dirty:
                self._count(ps, "completion_flushes")
                flushed += 1
            self.policy.on_release(PageKey(pid, page), entry.frame)
            self.frames[entry.frame] = None
            heapq.heappush(self._free, entry.frame)
        self.page_tables[pid].clear()
        self.finished.add(pid)
        return flushed

    def resident_pages(self) -> dict[PageKey, int]:
        """All resident pages -> frame (from the page tables)."""
        return {
            PageKey(pid, page): e.frame
            for pid, table in self.page_tables.items()
            for page, e in table.items()
        }

    def result(self) -> SimulationResult:
        return SimulationResult(
            policy=self.policy.name,
            num_frames=self.num_frames,
            window=self.tracker.window,
            threshold=self.tracker.threshold,
            references=self.t,
            processes=dict(sorted(self.stats.items())),
            totals=self.totals,
            utilization_mean=self.util.mean,
            utilization_peak=self.util.peak,
            steps=self.steps,
            warnings=list(self.tracker.events),
            final_policy_state=self.policy.snapshot(),
        )


def simulate(refs: Sequence[Reference], num_frames: int, policy: str, **kwargs) -> SimulationResult:
    """Convenience wrapper: run one policy over a trace and return the result."""
    return Simulator(refs, num_frames, policy, **kwargs).run()
