"""Workload generation: seeded synthetic traces and the fixed spec traces.

Synthetic traces (``generate``)
-------------------------------
Each process walks **cyclically** through a small working set of pages
(``ws_size`` pages starting at ``base``). With probability ``1 - locality`` a
reference instead jumps to a random page of the process's address space,
which models occasional non-local accesses. If ``shift_every`` is set, the
working set moves to a fresh range every that-many references (a phase
change). Each reference is a write with probability ``write_prob``.

Processes are interleaved **round-robin**: one reference per unfinished
process in ascending PID order, skipping processes that already issued their
configured number of references. Processes with fewer references finish
earlier, which exercises cleanup and frame reuse.

Every random choice comes from a ``random.Random`` seeded from
(seed, pid), so a given seed always produces the exact same trace. The
seed and all parameters are saved in a JSON file next to the trace.

Fixed traces
------------
``phase_change_trace`` (the proposal's changing-locality workload) and
``belady_trace`` (the FIFO anomaly) are fully deterministic.
"""

from __future__ import annotations

import json
import random
from dataclasses import asdict, dataclass
from pathlib import Path

from .trace import Reference, save_trace

GENERATOR_VERSION = 1
DEFAULT_SEED = 11
DEFAULT_WRITE_PROB = 0.30
#: Seeds used for run-to-run variability (reviewer feedback #3). 11 is the proposal's.
DEFAULT_SEEDS = (11, 23, 37, 53, 71)


@dataclass(frozen=True)
class ProcessSpec:
    pid: int
    refs: int                    # how many references this process issues
    ws_size: int                 # pages in its cyclic working set
    address_space: int = 16      # pages 0..address_space-1 exist for random jumps
    locality: float = 0.9        # probability a reference stays in the working set
    base: int = 0                # first page of the initial working set
    shift_every: int | None = None  # move the working set every N refs (phase change)

    def validate(self) -> None:
        if self.pid <= 0:
            raise ValueError(f"pid must be positive, got {self.pid}")
        if self.refs < 0:
            raise ValueError(f"P{self.pid}: refs must be >= 0")
        if self.ws_size <= 0:
            raise ValueError(f"P{self.pid}: ws_size must be >= 1")
        if self.address_space < self.base + self.ws_size:
            raise ValueError(f"P{self.pid}: address_space too small for working set")
        if not 0.0 <= self.locality <= 1.0:
            raise ValueError(f"P{self.pid}: locality must be in [0, 1]")
        if self.shift_every is not None and self.shift_every <= 0:
            raise ValueError(f"P{self.pid}: shift_every must be positive")


def _process_stream(spec: ProcessSpec, seed: int, write_prob: float):
    """Yield (page, op) for one process — independent of other processes."""
    rng = random.Random(seed * 100_003 + spec.pid)
    base, cursor = spec.base, 0
    for i in range(spec.refs):
        if spec.shift_every and i > 0 and i % spec.shift_every == 0:
            # Phase change: slide the working set to the next block of pages.
            span = spec.address_space - spec.ws_size + 1
            base = (base + spec.ws_size) % span
            cursor = 0
        if rng.random() < spec.locality:
            page = base + cursor
            cursor = (cursor + 1) % spec.ws_size  # cyclic walk
        else:
            page = rng.randrange(spec.address_space)  # non-local jump
        op = "W" if rng.random() < write_prob else "R"
        yield page, op


def generate(specs: list[ProcessSpec], seed: int = DEFAULT_SEED,
             write_prob: float = DEFAULT_WRITE_PROB) -> list[Reference]:
    """Build a round-robin interleaved trace from per-process specs."""
    if not 0.0 <= write_prob <= 1.0:
        raise ValueError("write_prob must be in [0, 1]")
    pids = [s.pid for s in specs]
    if len(pids) != len(set(pids)):
        raise ValueError("duplicate pid in process specs")
    for s in specs:
        s.validate()

    streams = {s.pid: _process_stream(s, seed, write_prob) for s in sorted(specs, key=lambda s: s.pid)}
    refs: list[Reference] = []
    while streams:
        for pid in sorted(streams):          # ascending PID order
            item = next(streams[pid], None)
            if item is None:                  # this process is exhausted: skip it from now on
                del streams[pid]
                continue
            refs.append(Reference(pid, item[0], item[1]))
    return refs


# --------------------------------------------------------------------------
# Named workloads used in the evaluation
# --------------------------------------------------------------------------

def locality_specs() -> list[ProcessSpec]:
    """Locality-heavy: small, stable working sets (total demand ~5 pages).

    P3 finishes first, after which demand drops to ~4 pages — this shows
    cleanup freeing frames for the remaining processes.
    """
    return [
        ProcessSpec(pid=1, refs=120, ws_size=2, locality=0.95),
        ProcessSpec(pid=2, refs=90, ws_size=2, locality=0.95),
        ProcessSpec(pid=3, refs=60, ws_size=1, locality=0.95),
    ]


def pressure_specs() -> list[ProcessSpec]:
    """Multi-process pressure: four processes whose combined working sets
    (~11 pages) exceed every tested memory size, with periodic phase changes
    that move each working set to new pages. P3 and P4 finish first, which
    relieves pressure on P1 and P2 for the last part of the trace."""
    return [
        ProcessSpec(pid=1, refs=150, ws_size=3, locality=0.95, shift_every=75),
        ProcessSpec(pid=2, refs=150, ws_size=3, locality=0.95, shift_every=75),
        ProcessSpec(pid=3, refs=100, ws_size=3, locality=0.95, shift_every=50),
        ProcessSpec(pid=4, refs=100, ws_size=2, locality=0.95, shift_every=50),
    ]


SYNTHETIC_WORKLOADS = {
    "locality": locality_specs,
    "pressure": pressure_specs,
}


def phase_change_trace(low_ws: int = 2, high_ws: int = 8, low_refs: int = 80,
                       high_refs: int = 160, pid: int = 1) -> list[Reference]:
    """The proposal's changing-locality workload (all reads).

    Default: pages 0-1 for 80 refs, then cycle 0-7 for 160 refs, then 0-1 for
    80 refs. ``high_ws`` can be varied to compare warning onset across
    working-set sizes (reviewer feedback #2).
    """
    def cycle(n_pages: int, n_refs: int) -> list[Reference]:
        return [Reference(pid, i % n_pages, "R") for i in range(n_refs)]

    return cycle(low_ws, low_refs) + cycle(high_ws, high_refs) + cycle(low_ws, low_refs)


def belady_trace() -> list[Reference]:
    """Classic FIFO anomaly: 9 faults with 3 frames, 10 faults with 4 frames."""
    return [Reference(1, p, "R") for p in (1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5)]


FIXED_WORKLOADS = {
    "phase": phase_change_trace,
    "belady": belady_trace,
}

ALL_WORKLOADS = (*SYNTHETIC_WORKLOADS, *FIXED_WORKLOADS)


def build_workload(name: str, seed: int = DEFAULT_SEED,
                   write_prob: float = DEFAULT_WRITE_PROB) -> tuple[list[Reference], dict]:
    """Return (references, metadata) for a named workload."""
    if name in SYNTHETIC_WORKLOADS:
        specs = SYNTHETIC_WORKLOADS[name]()
        refs = generate(specs, seed=seed, write_prob=write_prob)
        meta = {
            "workload": name,
            "kind": "synthetic",
            "seed": seed,
            "write_prob": write_prob,
            "interleaving": "round-robin, ascending pid, exhausted processes skipped",
            "processes": [asdict(s) for s in specs],
        }
    elif name in FIXED_WORKLOADS:
        refs = FIXED_WORKLOADS[name]()
        meta = {"workload": name, "kind": "fixed", "seed": None, "write_prob": 0.0}
    else:
        raise ValueError(f"unknown workload {name!r} (choose from: {', '.join(ALL_WORKLOADS)})")
    meta["references"] = len(refs)
    meta["generator_version"] = GENERATOR_VERSION
    return refs, meta


def save_workload(refs: list[Reference], meta: dict, path: str | Path) -> tuple[Path, Path]:
    """Save the trace CSV plus a ``<name>.meta.json`` with seed and parameters."""
    path = save_trace(refs, path)
    meta_path = path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return path, meta_path
