"""Working sets and the thrashing-warning heuristic.

Definitions (fixed defaults from the proposal, configurable from the CLI):

* Each process keeps a window of its own most recent ``window`` (=20)
  references — per process, not global. A process's window only changes when
  that process issues a reference.
* **Working-set size** = number of distinct pages in that window (fewer than
  20 references → use all of them).
* **Window fault rate** = faults in the window / references in the window.
* **Total demand D** = sum of working-set sizes of all *active* processes.
* A process is **warned** iff
  ``it has >= window references  AND  D > F  AND  window fault rate >= threshold``.
* The system is warned if any active process is warned.

Important: this is a *heuristic inside this simulation model*. It says "this
process keeps faulting while the active working sets do not fit in memory".
It is not a measurement of CPU utilisation and not a proof that a real OS
would thrash (reviewer feedback #2).
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

DEFAULT_WINDOW = 20
DEFAULT_THRESHOLD = 0.50


@dataclass(frozen=True)
class WarningEvent:
    """A warning turning ON or OFF for one process."""

    t: int            # global reference index at which the transition happened
    pid: int
    state: str        # "ON" or "OFF"
    cause: str        # "reference" | "completion" (re-evaluated after a cleanup) | "finished"
    demand: int       # D used for the decision
    ws_size: int
    fault_rate: float | None


class WorkingSetTracker:
    """Per-process reference windows, demand D, and warning state."""

    def __init__(self, num_frames: int, window: int = DEFAULT_WINDOW,
                 threshold: float = DEFAULT_THRESHOLD):
        if window <= 0:
            raise ValueError("window must be a positive integer")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self.num_frames = num_frames
        self.window = window
        self.threshold = threshold
        # pid -> deque of (page, was_fault) for that process's last `window` refs
        self._history: dict[int, deque[tuple[int, bool]]] = {}
        self._ref_count: dict[int, int] = {}
        self.active: set[int] = set()
        self.warned: set[int] = set()
        self.events: list[WarningEvent] = []

    # ----- per-process window -----------------------------------------------
    def activate(self, pid: int) -> None:
        self.active.add(pid)
        self._history.setdefault(pid, deque(maxlen=self.window))
        self._ref_count.setdefault(pid, 0)

    def record(self, pid: int, page: int, was_fault: bool) -> None:
        """Add one reference by ``pid`` to its own window."""
        self._history[pid].append((page, was_fault))
        self._ref_count[pid] += 1

    def ws_size(self, pid: int) -> int:
        return len({page for page, _ in self._history.get(pid, ())})

    def window_fault_rate(self, pid: int) -> float | None:
        hist = self._history.get(pid)
        if not hist:
            return None
        return sum(1 for _, fault in hist if fault) / len(hist)

    def ref_count(self, pid: int) -> int:
        return self._ref_count.get(pid, 0)

    def demand(self) -> int:
        """D: sum of working-set sizes over currently active processes."""
        return sum(self.ws_size(pid) for pid in self.active)

    # ----- warnings ---------------------------------------------------------
    def should_warn(self, pid: int, demand: int) -> bool:
        rate = self.window_fault_rate(pid)
        return (
            self.ref_count(pid) >= self.window
            and demand > self.num_frames
            and rate is not None
            and rate >= self.threshold
        )

    def evaluate(self, t: int, cause: str = "reference") -> int:
        """Re-evaluate every active process against the current D.

        Returns the D that was used. Transitions are appended to ``events``.
        """
        demand = self.demand()
        for pid in sorted(self.active):
            now = self.should_warn(pid, demand)
            was = pid in self.warned
            if now != was:
                (self.warned.add if now else self.warned.discard)(pid)
                self._log(t, pid, "ON" if now else "OFF", cause, demand)
        return demand

    def finish(self, pid: int, t: int) -> int:
        """Process ``pid`` completed: drop it from demand, clear its warning,
        and re-evaluate the others with the post-cleanup demand.

        Returns the post-cleanup D.
        """
        self.active.discard(pid)
        if pid in self.warned:
            self.warned.discard(pid)
            self._log(t, pid, "OFF", "finished", self.demand())
        return self.evaluate(t, cause="completion")

    @property
    def system_warning(self) -> bool:
        return bool(self.warned)

    def _log(self, t: int, pid: int, state: str, cause: str, demand: int) -> None:
        self.events.append(WarningEvent(
            t=t, pid=pid, state=state, cause=cause, demand=demand,
            ws_size=self.ws_size(pid), fault_rate=self.window_fault_rate(pid),
        ))


def warning_intervals(events: list[WarningEvent], pid: int) -> list[tuple[int, int | None]]:
    """Turn a process's ON/OFF events into [(on_t, off_t), ...] intervals.

    ``off_t`` is None if the warning was still on at the end of the trace.
    """
    intervals: list[tuple[int, int | None]] = []
    on_at: int | None = None
    for e in events:
        if e.pid != pid:
            continue
        if e.state == "ON":
            on_at = e.t
        elif on_at is not None:
            intervals.append((on_at, e.t))
            on_at = None
    if on_at is not None:
        intervals.append((on_at, None))
    return intervals
