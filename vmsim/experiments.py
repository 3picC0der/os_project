"""Experiments built on top of the engine.

* ``compare``          — every policy × every frame count on one saved trace.
* ``detect_anomalies`` — flag any policy whose faults *increase* between
                          adjacent tested frame counts (Belady's anomaly).
                          FIFO can do this; LRU and OPT are stack algorithms
                          and never should, which makes a nice sanity check.
* ``seed_sweep``       — repeat a synthetic workload with several independent
                          seeds and summarise run-to-run variability as
                          mean, sample standard deviation, min and max
                          (reviewer feedback #3).
* ``onset_sweep``      — vary the working-set size of the changing-locality
                          workload and record when the thrashing warning turns
                          on and off (reviewer feedback #2).
"""

from __future__ import annotations

import statistics
from pathlib import Path
from typing import Sequence

from .analysis import DEFAULT_THRESHOLD, DEFAULT_WINDOW, warning_intervals
from .engine import SimulationResult, simulate
from .generator import DEFAULT_WRITE_PROB, build_workload, phase_change_trace, save_workload
from .policies import MAIN_POLICIES
from .trace import Reference

DEFAULT_FRAMES = (2, 3, 4, 8)


def result_row(res: SimulationResult) -> dict:
    """Flatten one run into a comparison-table row."""
    T = res.totals
    first_on = next((e.t for e in res.warnings if e.state == "ON"), None)
    return {
        "policy": res.policy,
        "frames": res.num_frames,
        "references": T.references,
        "hits": T.hits,
        "faults": T.faults,
        "page_ins": T.page_ins,
        "eviction_writebacks": T.eviction_writebacks,
        "completion_flushes": T.completion_flushes,
        "hit_rate": T.hit_rate,
        "fault_rate": T.fault_rate,
        "utilization_mean": res.utilization_mean,
        "utilization_peak": res.utilization_peak,
        "warned_refs": res.warned_reference_count,
        "first_warning_t": first_on,
    }


def compare(refs: Sequence[Reference], frames: Sequence[int] = DEFAULT_FRAMES,
            policies: Sequence[str] = MAIN_POLICIES, window: int = DEFAULT_WINDOW,
            threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """Run each policy at each frame count on the *same* trace."""
    rows = []
    for p in policies:
        for f in sorted(frames):
            res = simulate(refs, f, p, window=window, threshold=threshold, record_steps=False)
            rows.append(result_row(res))
    return rows


def detect_anomalies(rows: list[dict]) -> list[dict]:
    """Find fault-count increases between adjacent tested frame counts."""
    out = []
    for p in dict.fromkeys(r["policy"] for r in rows):
        series = sorted((r["frames"], r["faults"]) for r in rows if r["policy"] == p)
        for (f1, m1), (f2, m2) in zip(series, series[1:]):
            if m2 > m1:
                out.append({"policy": p, "frames_from": f1, "frames_to": f2,
                            "faults_from": m1, "faults_to": m2})
    return out


SWEEP_METRICS = ("faults", "fault_rate", "eviction_writebacks", "completion_flushes", "warned_refs")


def _summarise(values: list[float]) -> dict:
    return {
        "mean": statistics.fmean(values),
        "std": statistics.stdev(values) if len(values) > 1 else 0.0,
        "min": min(values),
        "max": max(values),
    }


def seed_sweep(workload: str, seeds: Sequence[int], frames: Sequence[int] = DEFAULT_FRAMES,
               policies: Sequence[str] = MAIN_POLICIES, write_prob: float = DEFAULT_WRITE_PROB,
               trace_dir: str | Path | None = None, **sim_kwargs) -> tuple[list[dict], list[dict]]:
    """Run ``compare`` once per seed. Returns (raw rows, summary rows).

    Each seed's trace is generated once, optionally saved, and then reused for
    every policy and frame count — so policies always see identical input.
    """
    raw: list[dict] = []
    for seed in seeds:
        refs, meta = build_workload(workload, seed=seed, write_prob=write_prob)
        if trace_dir is not None:
            save_workload(refs, meta, Path(trace_dir) / f"{workload}_seed{seed}.csv")
        for row in compare(refs, frames, policies, **sim_kwargs):
            raw.append({"workload": workload, "seed": seed, **row})

    summary: list[dict] = []
    for p in policies:
        for f in sorted(frames):
            group = [r for r in raw if r["policy"] == p and r["frames"] == f]
            row = {"workload": workload, "policy": p, "frames": f, "n_seeds": len(group)}
            for m in SWEEP_METRICS:
                for stat, v in _summarise([float(r[m]) for r in group]).items():
                    row[f"{m}_{stat}"] = v
            summary.append(row)
    return raw, summary


def run_phase(num_frames: int = 4, policies: Sequence[str] = MAIN_POLICIES,
              refs: Sequence[Reference] | None = None, **sim_kwargs) -> dict[str, SimulationResult]:
    """Run the changing-locality workload under each policy (full step logs kept)."""
    refs = list(refs) if refs is not None else phase_change_trace()
    return {p: simulate(refs, num_frames, p, **sim_kwargs) for p in policies}


def onset_sweep(high_ws_sizes: Sequence[int] = (3, 4, 5, 6, 8, 10, 12), num_frames: int = 4,
                policies: Sequence[str] = MAIN_POLICIES, windows: Sequence[int] = (DEFAULT_WINDOW,),
                low_ws: int = 2, low_refs: int = 80, high_refs: int = 160,
                threshold: float = DEFAULT_THRESHOLD) -> list[dict]:
    """How quickly does the warning react, as a function of working-set size
    (and optionally of the analysis window length)?

    For each high-phase working-set size and window, record (per policy):
    * ``onset_delay`` — references into the high phase before the first ON
    * ``clear_delay`` — references into the recovery phase before it turns OFF
    * ``warned_refs`` — total references with the warning on
    ``None`` means the warning never turned on (expected when WS size <= F).
    """
    rows = []
    for window in windows:
        for ws in high_ws_sizes:
            refs = phase_change_trace(low_ws=low_ws, high_ws=ws, low_refs=low_refs, high_refs=high_refs)
            for p in policies:
                res = simulate(refs, num_frames, p, window=window, threshold=threshold)
                iv = warning_intervals(res.warnings, pid=1)
                on = next((a for a, _ in iv if a >= low_refs), None)
                off = next((b for a, b in iv if a >= low_refs and b is not None), None)
                rows.append({
                    "policy": p, "frames": num_frames, "window": window, "high_ws": ws,
                    "onset_t": on,
                    "onset_delay": None if on is None else on - low_refs,
                    "clear_t": off,
                    "clear_delay": None if off is None else off - (low_refs + high_refs),
                    "warned_refs": res.warned_reference_count,
                    "faults": res.totals.faults,
                })
    return rows
