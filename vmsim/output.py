"""Output: CSV files, terminal tables (rich) and static charts (matplotlib).

Every table keeps the three disk-traffic counters in separate, clearly named
columns (reviewer feedback #5):

* ``page_ins``            — disk reads, one per fault
* ``eviction_writebacks`` — dirty pages written when evicted
* ``completion_flushes``  — dirty pages written when their process finished
"""

from __future__ import annotations

import csv
from dataclasses import asdict
from pathlib import Path
from typing import Iterable, Sequence

from rich import box
from rich.console import Console
from rich.table import Table

from .analysis import warning_intervals
from .engine import SimulationResult

# ----------------------------------------------------------------- formatting

def pct(x: float | None) -> str:
    return "N/A" if x is None else f"{100 * x:.1f}%"


def num(x: float | None, digits: int = 3) -> str:
    return "N/A" if x is None else f"{x:.{digits}f}"


# ------------------------------------------------------------------------ CSV

STEP_FIELDS = [
    "t", "pid", "page", "op", "result", "frame", "evicted", "writeback",
    "ws_size", "window_fault_rate", "demand_pre", "demand_post",
    "occupied", "utilization", "warned_pids", "system_warning", "completed", "flushed",
]

SUMMARY_FIELDS = [
    "pid", "references", "reads", "writes", "hits", "faults", "hit_rate", "fault_rate",
    "page_ins", "eviction_writebacks", "completion_flushes", "clean_evictions",
    "evictions_caused", "pages_evicted", "first_ref", "last_ref",
    "peak_resident", "peak_ws", "warning_refs",
]


def _write_csv(path: Path, fieldnames: Sequence[str], rows: Iterable[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for row in rows:
            w.writerow({k: ("N/A" if v is None else v) for k, v in row.items()})
    return path


def write_steps_csv(result: SimulationResult, path: str | Path) -> Path:
    """Per-reference event log: one row per global reference index."""
    return _write_csv(Path(path), STEP_FIELDS, (asdict(s) for s in result.steps))


def write_warnings_csv(result: SimulationResult, path: str | Path) -> Path:
    fields = ["t", "pid", "state", "cause", "demand", "ws_size", "fault_rate"]
    return _write_csv(Path(path), fields, (asdict(e) for e in result.warnings))


def summary_rows(result: SimulationResult) -> list[dict]:
    """Per-process rows plus a final ``TOTAL`` row (rates from summed counts)."""
    rows = []
    for ps in result.processes.values():
        d = ps.as_dict()
        rows.append(d)
    total = result.totals.as_dict()
    total.update(pid="TOTAL",
                 peak_resident=round(result.utilization_peak * result.num_frames) if result.utilization_peak is not None else None,
                 warning_refs=result.warned_reference_count)
    rows.append(total)
    return rows


def write_summary_csv(result: SimulationResult, path: str | Path) -> Path:
    return _write_csv(Path(path), SUMMARY_FIELDS, summary_rows(result))


def write_rows_csv(rows: list[dict], path: str | Path) -> Path:
    """Write a list of homogeneous dicts (comparison / sweep results)."""
    fields = list(rows[0].keys()) if rows else []
    return _write_csv(Path(path), fields, rows)


ANOMALY_FIELDS = ["policy", "frames_from", "frames_to", "faults_from", "faults_to"]


def write_anomalies_csv(anomalies: list[dict], path: str | Path) -> Path:
    """Always writes the header, so "no anomaly" is an explicit empty table."""
    return _write_csv(Path(path), ANOMALY_FIELDS, anomalies)


# --------------------------------------------------------------- rich tables

def summary_table(result: SimulationResult, title: str | None = None) -> Table:
    t = Table(
        title=title or f"{result.policy.upper()}  ·  F = {result.num_frames} frames  ·  {result.references} references",
        box=box.SIMPLE_HEAVY, title_justify="left", header_style="bold",
    )
    for col, justify in [
        ("PID", "left"), ("Refs", "right"), ("Hits", "right"), ("Faults", "right"),
        ("Hit%", "right"), ("Page-in", "right"), ("Evict WB", "right"),
        ("Compl FL", "right"), ("Peak fr", "right"), ("Peak WS", "right"),
        ("Warned", "right"),
    ]:
        t.add_column(col, justify=justify, no_wrap=True)

    for ps in result.processes.values():
        t.add_row(
            f"P{ps.pid}", str(ps.references), str(ps.hits), str(ps.faults), pct(ps.hit_rate),
            str(ps.page_ins), str(ps.eviction_writebacks), str(ps.completion_flushes),
            str(ps.peak_resident), str(ps.peak_ws),
            f"[yellow]{ps.warning_refs}[/]" if ps.warning_refs else "0",
        )
    T = result.totals
    t.add_section()
    t.add_row(
        "[bold]TOTAL[/]", str(T.references), str(T.hits), str(T.faults), pct(T.hit_rate),
        str(T.page_ins), str(T.eviction_writebacks), str(T.completion_flushes),
        "", "", str(result.warned_reference_count), style="bold",
    )
    t.caption = (
        f"utilization mean {pct(result.utilization_mean)}, peak {pct(result.utilization_peak)}  ·  "
        f"window {result.window} refs, warn if D > F and fault rate ≥ {result.threshold:.2f}"
    )
    t.caption_justify = "left"
    return t


def warnings_table(result: SimulationResult, limit: int = 20) -> Table | None:
    if not result.warnings:
        return None
    t = Table(title="Thrashing-warning transitions (heuristic)", box=box.SIMPLE,
              title_justify="left", header_style="bold")
    for col in ("t", "PID", "State", "Cause", "D", "F", "WS", "Window fault rate"):
        t.add_column(col, justify="right" if col not in ("State", "Cause", "PID") else "left")
    for e in result.warnings[:limit]:
        state = "[bold yellow]⚠ ON[/]" if e.state == "ON" else "[green]✓ OFF[/]"
        t.add_row(str(e.t), f"P{e.pid}", state, e.cause, str(e.demand), str(result.num_frames),
                  str(e.ws_size), num(e.fault_rate, 2))
    if len(result.warnings) > limit:
        t.caption = f"... {len(result.warnings) - limit} more in warnings.csv"
    return t


def steps_table(result: SimulationResult, first: int = 30) -> Table:
    t = Table(title=f"First {min(first, len(result.steps))} references", box=box.SIMPLE,
              title_justify="left", header_style="bold")
    for col in ("t", "Ref", "Result", "Frame", "Evicted", "WS", "Win FR", "D", "Occ.", "Warn"):
        t.add_column(col, justify="right" if col in ("t", "Frame", "WS", "Win FR", "D", "Occ.") else "left")
    for s in result.steps[:first]:
        res = "[green]HIT[/]" if s.result == "HIT" else "[red]FAULT[/]"
        ev = s.evicted + (" [magenta](dirty→WB)[/]" if s.writeback else "")
        extra = f"  [cyan]done, flushed {s.flushed}[/]" if s.completed else ""
        t.add_row(str(s.t), f"P{s.pid}:{s.page}{s.op}", res + extra, str(s.frame), ev,
                  str(s.ws_size), num(s.window_fault_rate, 2),
                  str(s.demand_pre) if s.demand_pre == s.demand_post else f"{s.demand_pre}→{s.demand_post}",
                  f"{s.occupied}/{result.num_frames}",
                  "[yellow]⚠ " + s.warned_pids + "[/]" if s.system_warning else "")
    return t


def compare_table(rows: list[dict], anomalies: list[dict]) -> Table:
    t = Table(title="Policy comparison (same saved trace)", box=box.SIMPLE_HEAVY,
              title_justify="left", header_style="bold")
    for col in ("Policy", "F", "Hits", "Faults", "Fault%", "Page-in", "Evict WB",
                "Compl FL", "Util", "Warned"):
        t.add_column(col, justify="left" if col == "Policy" else "right", no_wrap=True)
    flagged = {(a["policy"], a["frames_to"]) for a in anomalies}
    last_policy = None
    for r in rows:
        if last_policy is not None and r["policy"] != last_policy:
            t.add_section()
        last_policy = r["policy"]
        name = r["policy"].upper() + ("*" if r["policy"] == "opt" else "")
        faults = str(r["faults"])
        if (r["policy"], r["frames"]) in flagged:
            faults = f"[bold red]{faults} ▲[/]"
        t.add_row(name, str(r["frames"]), str(r["hits"]), faults, pct(r["fault_rate"]),
                  str(r["page_ins"]), str(r["eviction_writebacks"]), str(r["completion_flushes"]),
                  pct(r["utilization_mean"]), str(r["warned_refs"]))
    notes = ["Page-in = disk reads (1 per fault) · Evict WB = dirty eviction write-backs · "
             "Compl FL = completion flushes · Util = mean occupied/F · Warned = refs with warning on"]
    if any(r["policy"] == "opt" for r in rows):
        notes.append("* OPT = optimal baseline (needs future knowledge, not a real policy)")
    if anomalies:
        notes.append("▲ = more faults than at the next-smaller tested frame count (Belady's anomaly)")
    t.caption = "\n".join(notes)
    t.caption_justify = "left"
    return t


def print_result(result: SimulationResult, console: Console | None = None, steps: int = 0) -> None:
    console = console or Console()
    if steps:
        console.print(steps_table(result, steps))
    console.print(summary_table(result))
    wt = warnings_table(result)
    if wt is not None:
        console.print(wt)
        for pid in result.processes:
            iv = warning_intervals(result.warnings, pid)
            if iv:
                spans = ", ".join(f"{a}–{b if b is not None else 'end'}" for a, b in iv)
                console.print(f"  P{pid} warned during references {spans}")
    else:
        console.print("[green]No thrashing warnings.[/]")


# --------------------------------------------------------------------- charts
# Fixed colour per policy (colour follows the entity, never its rank).
POLICY_COLORS = {"fifo": "#2a78d6", "lru": "#eb6834", "clock": "#1baf7a", "opt": "#8a8984"}
POLICY_LABELS = {"fifo": "FIFO", "lru": "LRU", "clock": "Clock", "opt": "OPT (baseline)"}
WARN_COLOR = "#fab219"     # reserved status colour: warning
CRIT_COLOR = "#d03b3b"     # reserved status colour: critical (anomaly marker)
INK, INK_2, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#e1e0d9", "#c3c2b7", "#fcfcfb"


def _plt():
    import matplotlib
    matplotlib.use("Agg")  # file output only, no display needed
    import matplotlib.pyplot as plt
    plt.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.edgecolor": AXIS, "axes.labelcolor": INK_2, "axes.titlecolor": INK,
        "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.spines.top": False, "axes.spines.right": False,
        "xtick.color": INK_2, "ytick.color": INK_2, "font.size": 10,
        "legend.frameon": False, "lines.linewidth": 2, "lines.markersize": 7,
    })
    return plt


def _line_style(policy: str) -> dict:
    return dict(color=POLICY_COLORS.get(policy, INK_2), label=POLICY_LABELS.get(policy, policy),
                linestyle="--" if policy == "opt" else "-",
                marker="o", markeredgecolor=SURFACE, markeredgewidth=1.5)


def _duplicate_labels(series: dict[str, list]) -> dict[str, str]:
    """Legend labels that say when a policy's curve is identical to an earlier one
    (e.g. "Clock (= FIFO)"), since identical lines would otherwise hide each other."""
    labels, seen = {}, {}
    for p, ys in series.items():
        key = tuple(ys)
        base = POLICY_LABELS.get(p, p)
        labels[p] = f"{base} (= {POLICY_LABELS.get(seen[key], seen[key])})" if key in seen else base
        seen.setdefault(key, p)
    return labels


#: Dash pattern for a curve that sits exactly on top of an earlier one, so both colours show.
OVERLAP_DASH = (0, (3, 3))


def chart_faults_vs_frames(rows: list[dict], anomalies: list[dict], path: str | Path,
                           title: str = "Page faults vs. frame count") -> Path:
    plt = _plt()
    fig, ax = plt.subplots(figsize=(8.5, 4.5))
    policies = list(dict.fromkeys(r["policy"] for r in rows))
    series = {p: sorted((r["frames"], r["faults"]) for r in rows if r["policy"] == p) for p in policies}
    labels = _duplicate_labels(series)
    for p, pts in series.items():
        xs, ys = zip(*pts)
        style = _line_style(p)
        style["label"] = labels[p]
        if "(=" in labels[p]:
            style["linestyle"] = OVERLAP_DASH
        ax.plot(xs, ys, **style)
    seen_points = set()
    for a in anomalies:
        pt = (a["frames_to"], a["faults_to"])
        ax.plot([pt[0]], [pt[1]], "o", ms=14, mfc="none", mec=CRIT_COLOR, mew=2, zorder=5)
        if pt in seen_points:
            continue
        seen_points.add(pt)
        who = " & ".join(POLICY_LABELS.get(b["policy"], b["policy"]) for b in anomalies
                         if (b["frames_to"], b["faults_to"]) == pt)
        ax.annotate(f"Belady's anomaly ({who})\nF={a['frames_from']}→{a['frames_to']}: "
                    f"{a['faults_from']}→{a['faults_to']} faults",
                    pt, xytext=(-20, -48), textcoords="offset points", ha="right",
                    color=INK, fontsize=9, arrowprops=dict(arrowstyle="->", color=CRIT_COLOR))
    frames = sorted({r["frames"] for r in rows})
    ax.set_xticks(frames)
    ax.set_xlabel("Physical frames (F)")
    ax.set_ylabel("Page faults")
    ax.set_ylim(bottom=0)
    ax.set_title(title)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1))
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def chart_phase_timeline(results: dict[str, SimulationResult], path: str | Path,
                         pid: int = 1, title: str = "Changing-locality workload") -> Path:
    """Three aligned panels (no dual axis): working-set size vs F; window fault
    rate per policy vs threshold; warning-on strips per policy."""
    plt = _plt()
    any_res = next(iter(results.values()))
    F, thr = any_res.num_frames, any_res.threshold
    fig, (ax1, ax2, ax3) = plt.subplots(
        3, 1, figsize=(10.5, 7.2), sharex=True, gridspec_kw={"height_ratios": [2, 2, 1.1]})

    steps = [s for s in any_res.steps if s.pid == pid]
    ts = [s.t for s in steps]
    ax1.step(ts, [s.ws_size for s in steps], where="post", color=INK_2, label="Working-set size")
    ax1.axhline(F, color=CRIT_COLOR, lw=1.2, ls=":", label=f"F = {F} frames")
    ax1.set_ylabel("Pages")
    fig.suptitle(title, x=0.01, ha="left", fontsize=13, fontweight="bold", color=INK)
    ax1.set_title(f"Working-set size (last {any_res.window} refs) vs. physical memory", fontsize=10)
    ax1.legend(loc="upper left", bbox_to_anchor=(1.01, 1))
    ax1.set_ylim(0, max(s.ws_size for s in steps) + 1.5)

    rates = {p: [s.window_fault_rate for s in res.steps if s.pid == pid] for p, res in results.items()}
    labels = _duplicate_labels(rates)
    for p, res in results.items():
        ps = [s for s in res.steps if s.pid == pid]
        ls = "--" if p == "opt" else (OVERLAP_DASH if "(=" in labels[p] else "-")
        ax2.plot([s.t for s in ps], rates[p], color=POLICY_COLORS.get(p, INK_2),
                 label=labels[p], lw=1.8, ls=ls)
    ax2.axhline(thr, color=CRIT_COLOR, lw=1.2, ls=":", label=f"threshold {thr:.2f}")
    ax2.set_title(f"Fault rate over the process's last {any_res.window} references", fontsize=10)
    ax2.set_ylabel("Window fault rate")
    ax2.set_ylim(-0.03, 1.08)
    ax2.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8.5)

    names = list(results)
    for i, p in enumerate(names):
        for on, off in warning_intervals(results[p].warnings, pid):
            end = off if off is not None else ts[-1]
            ax3.broken_barh([(on, end - on)], (i - 0.35, 0.7), color=WARN_COLOR, edgecolor=SURFACE)
            ax3.text(on, i, f" ⚠ {on}–{end}", va="center", ha="left", fontsize=8, color=INK)
    ax3.set_yticks(range(len(names)), [POLICY_LABELS.get(p, p) for p in names])
    ax3.set_ylim(-0.6, len(names) - 0.4)
    ax3.invert_yaxis()
    ax3.grid(axis="y", visible=False)
    ax3.set_title("Warning on (heuristic: ≥20 refs, D > F, window fault rate ≥ threshold)", fontsize=10)
    ax3.set_xlabel("Global reference index")
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


def chart_seed_variability(rows: list[dict], path: str | Path, metric: str = "fault_rate",
                           title: str = "Fault rate across seeds") -> Path:
    """Mean with min–max range per policy and frame count."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    policies = list(dict.fromkeys(r["policy"] for r in rows))
    frames = sorted({r["frames"] for r in rows})
    width = 0.6 / max(1, len(policies))
    for i, p in enumerate(policies):
        pr = {r["frames"]: r for r in rows if r["policy"] == p}
        xs = [j + (i - (len(policies) - 1) / 2) * width for j in range(len(frames))]
        means = [pr[f][f"{metric}_mean"] for f in frames]
        lo = [pr[f][f"{metric}_mean"] - pr[f][f"{metric}_min"] for f in frames]
        hi = [pr[f][f"{metric}_max"] - pr[f][f"{metric}_mean"] for f in frames]
        ax.errorbar(xs, means, yerr=[lo, hi], fmt="o", capsize=4, elinewidth=2,
                    markeredgecolor=SURFACE, color=POLICY_COLORS.get(p, INK_2),
                    label=POLICY_LABELS.get(p, p))
    ax.set_xticks(range(len(frames)), [str(f) for f in frames])
    ax.set_xlabel("Physical frames (F)")
    ax.set_ylabel("Fault rate (mean, min–max)")
    ax.set_ylim(0, 1.05)
    ax.set_title(title)
    ax.legend(loc="lower left")
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path


#: Sequential blue ramp (light -> dark) for ordered magnitudes such as window length.
SEQ_BLUES = ["#86b6ef", "#3987e5", "#2a78d6", "#1c5cab", "#104281"]


def chart_onset(rows: list[dict], path: str | Path, policy: str = "lru",
                title: str = "Warning reaction time vs. working-set size") -> Path:
    """Onset (solid) and clearance (dashed) delay, one colour per window length."""
    plt = _plt()
    fig, ax = plt.subplots(figsize=(7.5, 4.5))
    rows = [r for r in rows if r["policy"] == policy]
    windows = sorted({r["window"] for r in rows})
    # Spread picks across the ramp so neighbouring windows stay distinguishable.
    n = len(windows)
    ramp = [SEQ_BLUES[round(i * (len(SEQ_BLUES) - 1) / max(1, n - 1))] for i in range(n)] if n > 1 else [SEQ_BLUES[2]]
    for w, color in zip(windows, ramp):
        wr = sorted((r for r in rows if r["window"] == w), key=lambda r: r["high_ws"])
        on = [(r["high_ws"], r["onset_delay"]) for r in wr if r["onset_delay"] is not None]
        off = [(r["high_ws"], r["clear_delay"]) for r in wr if r["clear_delay"] is not None]
        if on:
            ax.plot(*zip(*on), color=color, marker="o", markeredgecolor=SURFACE,
                    label=f"window {w}: turns ON after")
        if off:
            ax.plot(*zip(*off), color=color, ls="--", marker="s", markeredgecolor=SURFACE,
                    label=f"window {w}: turns OFF after")
    never = sorted({r["high_ws"] for r in rows if r["onset_delay"] is None})
    F = rows[0]["frames"] if rows else "?"
    if never:
        ax.axvspan(min(never) - 0.5, max(never) + 0.5, color=GRID, alpha=0.6, lw=0)
        ax.text((min(never) + max(never)) / 2, 0.5, f"no warning\n(WS ≤ F = {F})",
                transform=ax.get_xaxis_transform(), ha="center", va="center", fontsize=9, color=INK_2)
    ax.set_xticks(sorted({r["high_ws"] for r in rows}))
    ax.set_xlabel("Working-set size in the high-demand phase (pages)")
    ax.set_ylabel("References into the phase")
    ax.set_ylim(bottom=0)
    ax.set_title(f"{title} ({POLICY_LABELS.get(policy, policy)}, F = {F})")
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8.5)
    fig.tight_layout()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160)
    plt.close(fig)
    return path
