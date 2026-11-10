"""Run every experiment with the C simulator and summarise the results.

Usage (from the project root, after `make`):
    python3 analysis/run_experiments.py

What it does
  1. Generates the seeded traces with bin/vmgen (saved in traces/).
  2. Runs bin/vmsim compare on each trace (FIFO, LRU, Clock, FIFO-Reinsertion).
  3. Writes CSV tables and Markdown tables to results/.
  4. Draws the charts in results/figures/.

The C program does all of the simulation; this script only runs it,
averages across seeds (mean and sample standard deviation) and plots.
"""
import csv
import io
import os
import statistics
import subprocess

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES = os.path.join(ROOT, "traces")
RESULTS = os.path.join(ROOT, "results")
FIGURES = os.path.join(RESULTS, "figures")
VMSIM = os.path.join(ROOT, "bin", "vmsim")
VMGEN = os.path.join(ROOT, "bin", "vmgen")
MSR_ZST = os.path.join(ROOT, "data", "msr_hm_0.oracleGeneral.zst")   # committed to git (18 MB)
MSR = os.path.join(ROOT, "data", "msr_hm_0.csv")                     # built from it (60 MB, not committed)

SEEDS = [11, 23, 47]
POLICIES = ["FIFO", "LRU", "CLOCK", "FIFO-R"]
SIZES = ["0.1%", "1%", "10%"]            # cache sizes used by the HotOS'23 paper (+1%)
SMALL_FRAMES = ["2", "3", "4", "8"]      # frame counts from our proposal
PAPER_LRU_HM0 = 0.5263                   # HotOS'23 Table 2, LRU miss ratio on MSR hm_0

# One fixed color per policy (validated categorical palette), never re-assigned.
COLOR = {"FIFO": "#2a78d6", "LRU": "#eb6834", "CLOCK": "#1baf7a", "FIFO-R": "#eda100"}
INK, MUTED, GRID = "#1f1f1e", "#6b6a63", "#e4e3dd"

# Workload parameters, recorded here and in results/workloads.csv.
GEN_ARGS = {
    "zipf":  ["-n", "4", "-r", "25000", "-p", "1000", "-a", "0.9", "-w", "0.3"],
    "shift": ["-n", "4", "-r", "25000", "-p", "1000", "-a", "0.9", "-w", "0.3", "-k", "10"],
}
PHASE_WS = [3, 4, 5, 6, 8, 12, 16]       # middle-phase working-set sizes, F = 4


def sh(args, out_path=None):
    """Runs a command; returns (stdout, stderr). Writes stdout to a file if asked."""
    proc = subprocess.run(args, check=True, capture_output=True, text=True)
    if out_path:
        with open(out_path, "w") as f:
            f.write(proc.stdout)
    return proc.stdout, proc.stderr


def compare(trace, frames):
    """Runs all policies on one trace; returns the rows and any FIFO-anomaly lines."""
    out, err = sh([VMSIM, "compare", trace, ",".join(frames)])
    rows = list(csv.DictReader(io.StringIO(out)))
    labels = frames * len(POLICIES)      # rows come policy by policy, frames in order
    for row, label in zip(rows, labels):
        row["size"] = label
    return rows, [line for line in err.splitlines() if line]


# ---------------------------------------------------------------- experiments

def make_traces():
    os.makedirs(TRACES, exist_ok=True)
    manifest = []
    for kind, args in GEN_ARGS.items():
        for seed in SEEDS:
            path = os.path.join(TRACES, f"{kind}_seed{seed}.csv")
            sh([VMGEN, kind, "-s", str(seed)] + args, path)
            manifest.append({"trace": os.path.relpath(path, ROOT),
                             "command": " ".join(["vmgen", kind, "-s", str(seed)] + args)})
    for ws in PHASE_WS:
        path = os.path.join(TRACES, f"phase_ws{ws}.csv")
        sh([VMGEN, "phase", "-p", str(ws)], path)
        manifest.append({"trace": os.path.relpath(path, ROOT),
                         "command": f"vmgen phase -p {ws}"})
    write_csv("workloads.csv", manifest)


def run_policy_comparison():
    """Experiment 1-3: replication on Zipf, MSR hm_0 and the VM-like shift workload."""
    rows, anomalies = [], []
    jobs = [(k, s, os.path.join(TRACES, f"{k}_seed{s}.csv")) for k in GEN_ARGS for s in SEEDS]
    if not os.path.exists(MSR) and os.path.exists(MSR_ZST):
        print("converting the MSR hm_0 trace (first run only)...")
        subprocess.run(["python3", os.path.join(ROOT, "tools", "msr_to_trace.py"), MSR_ZST, MSR], check=True)
    if os.path.exists(MSR):
        jobs.append(("msr_hm_0", "-", MSR))
    else:
        print("note: data/msr_hm_0.oracleGeneral.zst missing; see README. Skipping MSR.")
    jobs.append(("belady", "-", os.path.join(TRACES, "belady.csv")))

    for workload, seed, path in jobs:
        frames = SMALL_FRAMES if workload == "belady" else SMALL_FRAMES + SIZES
        if workload == "msr_hm_0":
            frames = ["0.03%", "0.1%", "0.3%", "1%", "3%", "10%"]
        print(f"running {workload} seed {seed} ...")
        result, anomaly = compare(path, frames)
        for r in result:
            r.update(workload=workload, seed=seed)
        rows += result
        anomalies += [f"{workload} (seed {seed}): {a}" for a in anomaly]
    write_csv("all_runs.csv", rows)
    with open(os.path.join(RESULTS, "fifo_anomalies.txt"), "w") as f:
        f.write("\n".join(anomalies) + "\n" if anomalies else "none\n")
    return rows


def run_warning_onset():
    """Experiment 4: warning onset and clearance vs. middle-phase working-set size (F = 4)."""
    rows = []
    for ws in PHASE_WS:
        trace = os.path.join(TRACES, f"phase_ws{ws}.csv")
        for policy in POLICIES:
            prefix = os.path.join(RESULTS, "logs", f"phase_ws{ws}_{policy}")
            sh([VMSIM, "run", "-p", policy.lower(), "-f", "4", "-l", prefix, trace])
            with open(prefix + "_warnings.csv") as f:
                events = list(csv.DictReader(f))
            on = next((int(e["step"]) for e in events if e["event"] == "ON"), None)
            off = next((int(e["step"]) for e in events if e["event"] == "OFF"
                        and on and int(e["step"]) > on), None)
            rows.append({"ws": ws, "policy": policy,
                         "warning_on_step": on or "", "warning_off_step": off or "",
                         "refs_into_phase2_until_on": on - 80 if on else "",
                         "refs_into_phase3_until_off": off - 240 if off else ""})
    write_csv("warning_onset.csv", rows)
    return rows


# ---------------------------------------------------------------- summaries

def write_csv(name, rows):
    with open(os.path.join(RESULTS, name), "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def mean_sd(values):
    return statistics.mean(values), (statistics.stdev(values) if len(values) > 1 else 0.0)


def summarise(rows):
    """Mean +- sd across seeds, plus the paper's metric: miss-ratio reduction from FIFO."""
    fifo = {(r["workload"], r["seed"], r["size"]): float(r["fault_rate"])
            for r in rows if r["policy"] == "FIFO"}
    groups = {}
    for r in rows:
        key = (r["workload"], r["size"], r["policy"])
        reduction = (fifo[(r["workload"], r["seed"], r["size"])] - float(r["fault_rate"])) \
            / fifo[(r["workload"], r["seed"], r["size"])]
        groups.setdefault(key, []).append((r, reduction))

    summary = []
    for (workload, size, policy), items in groups.items():
        rate_m, rate_sd = mean_sd([float(r["fault_rate"]) for r, _ in items])
        red_m, red_sd = mean_sd([red for _, red in items])
        avg = lambda col: statistics.mean(float(r[col]) for r, _ in items)  # noqa: E731
        summary.append({
            "workload": workload, "size": size, "frames": items[0][0]["frames"],
            "policy": policy, "runs": len(items),
            "fault_rate_mean": f"{rate_m:.4f}", "fault_rate_sd": f"{rate_sd:.4f}",
            "reduction_vs_fifo_mean": f"{red_m:.4f}", "reduction_vs_fifo_sd": f"{red_sd:.4f}",
            "page_ins_mean": f"{avg('page_ins'):.0f}",
            "evict_writebacks_mean": f"{avg('evict_writebacks'):.0f}",
            "completion_flushes_mean": f"{avg('completion_flushes'):.0f}",
            "warn_steps_mean": f"{avg('warn_steps'):.0f}",
        })
    write_csv("summary.csv", summary)
    return summary


def head_to_head(rows, a="FIFO-R", b="LRU"):
    """The paper's Fig. 2 statistic: in how many runs does `a` beat `b`?"""
    rate = {(r["workload"], r["seed"], r["size"], r["policy"]): float(r["fault_rate"]) for r in rows}
    out = []
    for workload in ["zipf", "msr_hm_0", "shift"]:
        for size in SIZES:
            runs = [k for k in rate if k[0] == workload and k[2] == size and k[3] == a]
            if runs:
                wins = sum(rate[k] < rate[(k[0], k[1], k[2], b)] for k in runs)
                out.append((workload, size, wins, len(runs)))
    return out


def write_markdown(summary, onset, rows):
    lines = ["# Results", "",
             "Generated by `analysis/run_experiments.py`. Fault rate = faults / references "
             "(the paper's miss ratio). Mean and sample SD over seeds 11, 23, 47 for the "
             "synthetic workloads; MSR hm_0 is a single real trace. Page-ins, eviction "
             "write-backs and completion flushes are counted separately.", ""]
    for workload in ["zipf", "msr_hm_0", "shift"]:
        sizes = ["0.03%", "0.1%", "0.3%", "1%", "3%", "10%"] if workload == "msr_hm_0" else SMALL_FRAMES + SIZES
        lines += [f"## {workload}", "",
                  "| size | frames | policy | fault rate (mean ± sd) | reduction vs FIFO | "
                  "page-ins | eviction write-backs | completion flushes | warning steps |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for size in sizes:
            for policy in POLICIES:
                s = next((x for x in summary if x["workload"] == workload
                          and x["size"] == size and x["policy"] == policy), None)
                if s:
                    lines.append(f"| {size} | {s['frames']} | {policy} | {s['fault_rate_mean']} ± "
                                 f"{s['fault_rate_sd']} | {float(s['reduction_vs_fifo_mean']):+.2%} | "
                                 f"{s['page_ins_mean']} | {s['evict_writebacks_mean']} | "
                                 f"{s['completion_flushes_mean']} | {s['warn_steps_mean']} |")
        lines.append("")

    lines += ["## FIFO-Reinsertion vs LRU (the paper's Fig. 2 statistic)", "",
              "| workload | size | runs where FIFO-R has a lower fault rate than LRU |", "|---|---|---|"]
    lines += [f"| {w} | {s} | {wins} of {n} |" for w, s, wins, n in head_to_head(rows)]
    lines += ["", "## Clock (proposal rule) vs LRU", "",
              "| workload | size | runs where CLOCK has a lower fault rate than LRU |", "|---|---|---|"]
    lines += [f"| {w} | {s} | {wins} of {n} |" for w, s, wins, n in head_to_head(rows, "CLOCK")]

    lines += ["", "## Thrashing-warning onset (heuristic), phase workload, F = 4", "",
              "| middle working set | policy | refs into phase 2 until ON | refs into phase 3 until OFF |",
              "|---|---|---|---|"]
    lines += [f"| {o['ws']} | {o['policy']} | {o['refs_into_phase2_until_on'] or 'never'} | "
              f"{o['refs_into_phase3_until_off'] or '-'} |" for o in onset]
    with open(os.path.join(RESULTS, "results.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- charts

def style(ax, title):
    ax.set_title(title, loc="left", color=INK, fontsize=11)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ["top", "right"]:
        ax.spines[side].set_visible(False)
    for side in ["left", "bottom"]:
        ax.spines[side].set_color(MUTED)
    ax.tick_params(colors=MUTED, labelsize=9)


def fig_replication(summary):
    """Miss-ratio reduction from FIFO, as in the paper's Fig. 5 metric."""
    panels = [("zipf", "Zipf (block/web-like), 3 seeds"),
              ("msr_hm_0", "MSR hm_0 (real block trace)"),
              ("shift", "Shifting working sets (VM-like), 3 seeds")]
    shown = ["LRU", "CLOCK", "FIFO-R"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), sharey=True)
    for ax, (workload, title) in zip(axes, panels):
        width = 0.26
        for i, policy in enumerate(shown):
            xs, ys, errs = [], [], []
            for j, size in enumerate(SIZES):
                s = next((x for x in summary if x["workload"] == workload
                          and x["size"] == size and x["policy"] == policy), None)
                if s:
                    xs.append(j + (i - 1) * width)
                    ys.append(100 * float(s["reduction_vs_fifo_mean"]))
                    errs.append(100 * float(s["reduction_vs_fifo_sd"]))
            ax.bar(xs, ys, width - 0.03, color=COLOR[policy], label=policy,
                   yerr=errs, error_kw={"ecolor": MUTED, "elinewidth": 1})
        ax.axhline(0, color=MUTED, linewidth=1)
        ax.set_xticks(range(len(SIZES)), [f"{s} of pages" for s in SIZES])
        style(ax, title)
    axes[0].set_ylabel("fault-rate reduction vs FIFO (%)", color=INK)
    axes[0].legend(frameon=False, fontsize=9)
    fig.suptitle("Higher is better: how much each policy lowers the fault rate compared with FIFO",
                 x=0.01, ha="left", color=INK)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURES, "fig1_reduction_vs_fifo.png"), dpi=160)
    plt.close(fig)


def fig_msr_curve(summary):
    sizes = ["0.03%", "0.1%", "0.3%", "1%", "3%", "10%"]
    rows = [x for x in summary if x["workload"] == "msr_hm_0"]
    if not rows:
        return
    fig, ax = plt.subplots(figsize=(7, 4))
    for policy in POLICIES:
        ys = [float(next(x for x in rows if x["size"] == s and x["policy"] == policy)
                     ["fault_rate_mean"]) for s in sizes]
        ax.plot(range(len(sizes)), ys, color=COLOR[policy], linewidth=2, marker="o",
                markersize=5, label=policy)
    ax.plot([1], [PAPER_LRU_HM0], marker="x", markersize=12, color=INK, linestyle="none",
            label="paper, LRU (Table 2)")
    ax.set_xticks(range(len(sizes)), [f"{s}" for s in sizes])
    ax.set_xlabel("cache size (% of distinct pages)", color=INK)
    ax.set_ylabel("fault rate", color=INK)
    style(ax, "MSR hm_0: fault rate vs cache size (lower is better)")
    ax.legend(frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURES, "fig2_msr_hm0_fault_rate.png"), dpi=160)
    plt.close(fig)


def fig_phase_timeline(ws=8, policy="LRU"):
    """Working set and window fault rate over time, warning periods shaded."""
    with open(os.path.join(RESULTS, "logs", f"phase_ws{ws}_{policy}_steps.csv")) as f:
        steps = list(csv.DictReader(f))
    x = [int(s["step"]) for s in steps]
    warn = [int(s["sys_warn"]) for s in steps]
    fig, (top, bottom) = plt.subplots(2, 1, figsize=(9, 5), sharex=True)
    top.plot(x, [int(s["ws"]) for s in steps], color=COLOR[policy], linewidth=2, label="working-set size")
    top.axhline(4, color=MUTED, linestyle="--", linewidth=1)
    top.text(2, 4.2, "F = 4 frames", color=MUTED, fontsize=9)
    bottom.plot(x, [float(s["fault_rate"]) for s in steps], color=COLOR[policy], linewidth=2)
    bottom.axhline(0.5, color=MUTED, linestyle="--", linewidth=1)
    bottom.text(268, 0.53, "0.50 threshold", color=MUTED, fontsize=9)
    for ax in (top, bottom):
        ax.fill_between(x, 0, 1, where=warn, transform=ax.get_xaxis_transform(),
                        color=GRID, step="mid", label="warning on")
        for b in (80, 240):
            ax.axvline(b, color=MUTED, linewidth=0.8)
    style(top, f"Phase workload, {policy}, F = 4: working-set size (pages)")
    style(bottom, "Window fault rate (last 20 references)")
    bottom.set_xlabel("reference number (phases change at 80 and 240)", color=INK)
    top.legend(frameon=False, fontsize=9, loc="upper right")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURES, "fig3_phase_timeline.png"), dpi=160)
    plt.close(fig)


def fig_onset(onset):
    fig, ax = plt.subplots(figsize=(7, 4))
    offsets = {"FIFO": -0.15, "LRU": -0.05, "CLOCK": 0.05, "FIFO-R": 0.15}
    for policy in POLICIES:
        pts = [(PHASE_WS.index(o["ws"]) + offsets[policy], o["refs_into_phase2_until_on"])
               for o in onset if o["policy"] == policy and o["refs_into_phase2_until_on"] != ""]
        if pts:
            ax.plot(*zip(*pts), marker="o", markersize=8, linestyle="none",
                    color=COLOR[policy], label=policy)
    for i, ws in enumerate(PHASE_WS):          # label the cases that never warn
        if not any(o["ws"] == ws and o["refs_into_phase2_until_on"] != "" for o in onset):
            ax.text(i, 2, "never\n(D ≤ F)", ha="center", color=MUTED, fontsize=9)
    ax.set_ylim(0, 20)
    ax.set_yticks(range(0, 21, 4))
    ax.set_xticks(range(len(PHASE_WS)), [str(w) for w in PHASE_WS])
    ax.set_xlabel("working-set size of the middle phase (pages); F = 4", color=INK)
    ax.set_ylabel("references until warning turns on", color=INK)
    style(ax, "Thrashing-warning onset after the working set grows")
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(os.path.join(FIGURES, "fig4_warning_onset.png"), dpi=160)
    plt.close(fig)


def main():
    os.makedirs(FIGURES, exist_ok=True)
    os.makedirs(os.path.join(RESULTS, "logs"), exist_ok=True)
    make_traces()
    rows = run_policy_comparison()
    summary = summarise(rows)
    onset = run_warning_onset()
    write_markdown(summary, onset, rows)
    fig_replication(summary)
    fig_msr_curve(summary)
    fig_phase_timeline()
    fig_onset(onset)
    print("done: see results/results.md and results/figures/")


if __name__ == "__main__":
    main()
