"""Command-line interface: ``python -m vmsim <command> ...``

Commands
--------
run        simulate one trace with one (or all) policies at one frame count
compare    all policies × several frame counts on one trace, + FIFO anomaly check
generate   create and save a seeded synthetic trace or a fixed spec trace
phase      the changing-locality workload with warning timeline chart
seeds      repeat a synthetic workload over several seeds (variability)
onset      warning onset/clearance vs. working-set size and window length
reproduce  regenerate every trace, table and chart used in the report

All inputs are validated before anything runs; invalid parameters or trace
rows print an explanation and exit with status 2 without partial output.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console

from . import __version__
from .analysis import DEFAULT_THRESHOLD, DEFAULT_WINDOW, warning_intervals
from .engine import simulate
from .experiments import DEFAULT_FRAMES, compare, detect_anomalies, onset_sweep, run_phase, seed_sweep
from .generator import (ALL_WORKLOADS, DEFAULT_SEED, DEFAULT_SEEDS, DEFAULT_WRITE_PROB,
                        SYNTHETIC_WORKLOADS, build_workload, phase_change_trace, save_workload)
from .invariants import InvariantError
from .output import (chart_faults_vs_frames, chart_onset, chart_phase_timeline,
                     chart_seed_variability, compare_table, print_result,
                     write_anomalies_csv, write_rows_csv,
                     write_steps_csv, write_summary_csv, write_warnings_csv)
from .policies import MAIN_POLICIES, POLICIES
from .trace import TraceError, load_trace

console = Console()


# ------------------------------------------------------------ arg validators

def positive_int(text: str) -> int:
    """argparse type: accepts "1", "8"; rejects "0", "-3", "2.5", "abc"."""
    try:
        value = int(text, 10)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}") from None
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be a positive integer, got {text!r}")
    return value


def probability(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"must be a number in [0, 1], got {text!r}") from None
    if not 0.0 <= value <= 1.0:
        raise argparse.ArgumentTypeError(f"must be a number in [0, 1], got {text!r}")
    return value


def policy_name(text: str) -> str:
    name = text.lower()
    if name not in POLICIES and name != "all":
        raise argparse.ArgumentTypeError(f"unknown policy {text!r} (choose from: {', '.join(POLICIES)}, all)")
    return name


def _policies(args) -> list[str]:
    chosen = list(dict.fromkeys(args.policy))  # de-duplicate, keep order
    if "all" in chosen:
        chosen = list(MAIN_POLICIES)
    if getattr(args, "opt", False) and "opt" not in chosen:
        chosen.append("opt")
    return chosen


def _add_analysis_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--window", type=positive_int, default=DEFAULT_WINDOW,
                   help=f"per-process working-set / fault-rate window (default {DEFAULT_WINDOW})")
    p.add_argument("--threshold", type=probability, default=DEFAULT_THRESHOLD,
                   help=f"window fault rate that triggers a warning (default {DEFAULT_THRESHOLD})")


# ------------------------------------------------------------------ commands

def cmd_run(args) -> int:
    refs = load_trace(args.trace)  # strict validation before anything runs
    for p in _policies(args):
        res = simulate(refs, args.frames, p, window=args.window, threshold=args.threshold,
                       check=not args.no_check)
        print_result(res, console, steps=args.steps)
        if args.out:
            out = Path(args.out)
            stem = f"{p}_F{args.frames}"
            write_steps_csv(res, out / f"{stem}_steps.csv")
            write_summary_csv(res, out / f"{stem}_summary.csv")
            write_warnings_csv(res, out / f"{stem}_warnings.csv")
            console.print(f"[dim]saved {out}/{stem}_{{steps,summary,warnings}}.csv[/]")
        console.print()
    return 0


def cmd_compare(args) -> int:
    refs = load_trace(args.trace)
    rows = compare(refs, args.frames, _policies(args), window=args.window, threshold=args.threshold)
    anomalies = detect_anomalies(rows)
    console.print(compare_table(rows, anomalies))
    _report_anomalies(anomalies)
    if args.out:
        out = Path(args.out)
        write_rows_csv(rows, out / "compare.csv")
        write_anomalies_csv(anomalies, out / "anomalies.csv")
        chart_faults_vs_frames(rows, anomalies, out / "faults_vs_frames.png",
                               title=f"Page faults vs. frames — {Path(args.trace).stem}")
        console.print(f"[dim]saved {out}/compare.csv, anomalies.csv, faults_vs_frames.png[/]")
    return 0


def _report_anomalies(anomalies: list[dict]) -> None:
    fifo = [a for a in anomalies if a["policy"] == "fifo"]
    if fifo:
        for a in fifo:
            console.print(f"[bold red]FIFO anomaly:[/] {a['faults_from']} faults with F={a['frames_from']} "
                          f"→ {a['faults_to']} faults with F={a['frames_to']}")
    else:
        console.print("[green]FIFO check: no fault-count increase between adjacent tested frame counts.[/]")
    for a in anomalies:
        if a["policy"] != "fifo":
            console.print(f"[yellow]note:[/] {a['policy'].upper()} faults also rose "
                          f"{a['faults_from']}→{a['faults_to']} from F={a['frames_from']} to F={a['frames_to']}")


def cmd_generate(args) -> int:
    refs, meta = build_workload(args.workload, seed=args.seed, write_prob=args.write_prob)
    default_name = (f"{args.workload}_seed{args.seed}.csv" if args.workload in SYNTHETIC_WORKLOADS
                    else f"{args.workload}.csv")
    path, meta_path = save_workload(refs, meta, args.output or Path("traces") / default_name)
    pids = sorted({r.pid for r in refs})
    console.print(f"wrote [bold]{path}[/] ({len(refs)} references, processes {pids}) "
                  f"and {meta_path.name}")
    return 0


def cmd_phase(args) -> int:
    refs = phase_change_trace(high_ws=args.high_ws)
    results = run_phase(args.frames, _policies(args), refs=refs, window=args.window,
                        threshold=args.threshold)
    low = 80
    for p, res in results.items():
        iv = warning_intervals(res.warnings, 1)
        ws_check = res.steps[low + 160 + 19]  # 20th reference of the recovery phase
        on_off = ", ".join(f"ON at {a} (+{a - low} into phase 2), OFF at {b} (+{b - 240} into phase 3)"
                           if b is not None else f"ON at {a}, still on at end" for a, b in iv) or "never warned"
        console.print(f"[bold]{p.upper():6}[/] faults {res.totals.faults:3}  ·  {on_off}  ·  "
                      f"at t={ws_check.t}: WS={ws_check.ws_size}, warning "
                      f"{'[red]ON[/]' if ws_check.system_warning else '[green]off[/]'}")
    if args.out:
        out = Path(args.out)
        for p, res in results.items():
            write_steps_csv(res, out / f"phase_{p}_steps.csv")
            write_warnings_csv(res, out / f"phase_{p}_warnings.csv")
        chart_phase_timeline(results, out / "phase_timeline.png",
                             title=f"Changing locality (2 → {args.high_ws} → 2 pages, F = {args.frames})")
        console.print(f"[dim]saved {out}/phase_*_steps.csv, phase_*_warnings.csv, phase_timeline.png[/]")
    return 0


def cmd_seeds(args) -> int:
    trace_dir = Path(args.out) / "traces" if args.out else None
    raw, summary = seed_sweep(args.workload, args.seeds, args.frames, _policies(args),
                              write_prob=args.write_prob, trace_dir=trace_dir,
                              window=args.window, threshold=args.threshold)
    from rich.table import Table
    from rich import box
    t = Table(title=f"{args.workload}: variability over seeds {list(args.seeds)}",
              box=box.SIMPLE_HEAVY, title_justify="left", header_style="bold")
    for c in ("Policy", "F", "Fault rate mean", "± std", "min", "max",
              "Evict WB mean", "Compl. flush mean"):
        t.add_column(c, justify="left" if c == "Policy" else "right")
    for r in summary:
        t.add_row(r["policy"].upper(), str(r["frames"]), f"{r['fault_rate_mean']:.3f}",
                  f"{r['fault_rate_std']:.3f}", f"{r['fault_rate_min']:.3f}", f"{r['fault_rate_max']:.3f}",
                  f"{r['eviction_writebacks_mean']:.1f}", f"{r['completion_flushes_mean']:.1f}")
    console.print(t)
    if args.out:
        out = Path(args.out)
        write_rows_csv(raw, out / f"seeds_{args.workload}_raw.csv")
        write_rows_csv(summary, out / f"seeds_{args.workload}_summary.csv")
        chart_seed_variability(summary, out / f"seeds_{args.workload}.png",
                               title=f"Fault rate across {len(args.seeds)} seeds — {args.workload} workload")
        console.print(f"[dim]saved {out}/seeds_{args.workload}_{{raw,summary}}.csv and chart[/]")
    return 0


def cmd_onset(args) -> int:
    rows = onset_sweep(args.high_ws, args.frames, _policies(args), windows=args.windows,
                       threshold=args.threshold)
    from rich.table import Table
    from rich import box
    t = Table(title=f"Warning reaction vs. working-set size (F = {args.frames})",
              box=box.SIMPLE_HEAVY, title_justify="left", header_style="bold")
    for c in ("Policy", "Window", "High-phase WS", "ON after", "OFF after", "Warned refs"):
        t.add_column(c, justify="left" if c == "Policy" else "right")
    for r in rows:
        t.add_row(r["policy"].upper(), str(r["window"]), str(r["high_ws"]),
                  "never" if r["onset_delay"] is None else str(r["onset_delay"]),
                  "—" if r["clear_delay"] is None else str(r["clear_delay"]), str(r["warned_refs"]))
    console.print(t)
    if args.out:
        out = Path(args.out)
        write_rows_csv(rows, out / "onset.csv")
        chart_onset(rows, out / "onset.png", policy=_policies(args)[0])
        console.print(f"[dim]saved {out}/onset.csv, onset.png[/]")
    return 0


def cmd_reproduce(args) -> int:
    """Regenerate every artefact used in the report, from scratch."""
    out, traces = Path(args.out), Path(args.traces)
    pols = [*MAIN_POLICIES, "opt"]
    console.rule("[bold]1. Save traces once")
    saved = {}
    for w in ALL_WORKLOADS:
        refs, meta = build_workload(w, seed=DEFAULT_SEED, write_prob=DEFAULT_WRITE_PROB)
        name = f"{w}_seed{DEFAULT_SEED}.csv" if w in SYNTHETIC_WORKLOADS else f"{w}.csv"
        saved[w] = save_workload(refs, meta, traces / name)[0]
        console.print(f"  {saved[w]}  ({len(refs)} refs)")

    console.rule("[bold]2. Policy comparison per workload (F = 2, 3, 4, 8)")
    for w in ALL_WORKLOADS:
        refs = load_trace(saved[w])  # always re-read the saved file: identical input for all policies
        frames = (1, 2, 3, 4, 5) if w == "belady" else DEFAULT_FRAMES
        rows = compare(refs, frames, pols)
        anomalies = detect_anomalies(rows)
        console.print(compare_table(rows, anomalies))
        write_rows_csv(rows, out / w / "compare.csv")
        write_anomalies_csv(anomalies, out / w / "anomalies.csv")
        chart_faults_vs_frames(rows, anomalies, out / w / "faults_vs_frames.png",
                               title=f"Page faults vs. frames — {w} workload")
        # Full per-process detail at F = 4 for the report tables.
        for p in pols:
            res = simulate(refs, 4, p)
            write_summary_csv(res, out / w / f"{p}_F4_summary.csv")

    console.rule("[bold]3. Changing-locality workload (F = 4)")
    phase_refs = load_trace(saved["phase"])
    results = run_phase(4, pols, refs=phase_refs)
    for p, res in results.items():
        write_steps_csv(res, out / "phase" / f"{p}_steps.csv")
        write_warnings_csv(res, out / "phase" / f"{p}_warnings.csv")
        console.print(f"  {p.upper():6} warning intervals: {warning_intervals(res.warnings, 1)}")
    chart_phase_timeline(results, out / "phase" / "phase_timeline.png",
                         title="Changing locality (2 → 8 → 2 pages, F = 4)")

    console.rule("[bold]4. Seed variability (seeds " + ", ".join(map(str, DEFAULT_SEEDS)) + ")")
    for w in SYNTHETIC_WORKLOADS:
        raw, summary = seed_sweep(w, DEFAULT_SEEDS, DEFAULT_FRAMES, pols,
                                  trace_dir=traces / "seeds")
        write_rows_csv(raw, out / "seeds" / f"{w}_raw.csv")
        write_rows_csv(summary, out / "seeds" / f"{w}_summary.csv")
        chart_seed_variability(summary, out / "seeds" / f"{w}.png",
                               title=f"Fault rate across {len(DEFAULT_SEEDS)} seeds — {w} workload")
        console.print(f"  {w}: {len(raw)} runs summarised")

    console.rule("[bold]5. Warning onset vs. working-set size and window")
    rows = onset_sweep(windows=(10, 20, 40), policies=pols)
    write_rows_csv(rows, out / "onset" / "onset.csv")
    chart_onset(rows, out / "onset" / "onset.png", policy="lru")
    console.print(f"  {len(rows)} rows")

    (out / "settings.json").write_text(json.dumps({
        "vmsim_version": __version__, "seed": DEFAULT_SEED, "variability_seeds": list(DEFAULT_SEEDS),
        "write_prob": DEFAULT_WRITE_PROB, "frames": list(DEFAULT_FRAMES), "window": DEFAULT_WINDOW,
        "threshold": DEFAULT_THRESHOLD, "policies": pols,
    }, indent=2) + "\n")
    console.print(f"\n[bold green]Done.[/] Results in {out}/, traces in {traces}/")
    return 0


# -------------------------------------------------------------------- parser

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="vmsim", description="Virtual memory management simulator (ICS 433)")
    ap.add_argument("--version", action="version", version=f"vmsim {__version__}")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="simulate a trace with one policy")
    p.add_argument("trace", help="CSV trace with header pid,page,op")
    p.add_argument("-p", "--policy", type=policy_name, nargs="+", default=["lru"],
                   help="fifo, lru, clock, opt, or all (default lru)")
    p.add_argument("-f", "--frames", type=positive_int, required=True, help="number of physical frames F")
    p.add_argument("-o", "--out", help="directory for step log, summary and warning CSVs")
    p.add_argument("--steps", type=int, default=0, metavar="N", help="print the first N steps")
    p.add_argument("--no-check", action="store_true", help="skip per-step invariant checks (faster)")
    _add_analysis_args(p)
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("compare", help="all policies × frame counts on one trace")
    p.add_argument("trace")
    p.add_argument("-f", "--frames", type=positive_int, nargs="+", default=list(DEFAULT_FRAMES))
    p.add_argument("-p", "--policy", type=policy_name, nargs="+", default=list(MAIN_POLICIES))
    p.add_argument("--opt", action="store_true", help="also run the OPT baseline")
    p.add_argument("-o", "--out", help="directory for compare.csv and the chart")
    _add_analysis_args(p)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("generate", help="create and save a workload trace")
    p.add_argument("workload", choices=ALL_WORKLOADS)
    p.add_argument("--seed", type=int, default=DEFAULT_SEED)
    p.add_argument("--write-prob", type=probability, default=DEFAULT_WRITE_PROB)
    p.add_argument("-o", "--output", help="output CSV path (default traces/<workload>...csv)")
    p.set_defaults(func=cmd_generate)

    p = sub.add_parser("phase", help="changing-locality workload with warning timeline")
    p.add_argument("-f", "--frames", type=positive_int, default=4)
    p.add_argument("--high-ws", type=positive_int, default=8, help="pages in the middle phase (default 8)")
    p.add_argument("-p", "--policy", type=policy_name, nargs="+", default=list(MAIN_POLICIES))
    p.add_argument("--opt", action="store_true")
    p.add_argument("-o", "--out")
    _add_analysis_args(p)
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("seeds", help="variability of a synthetic workload over several seeds")
    p.add_argument("workload", choices=list(SYNTHETIC_WORKLOADS))
    p.add_argument("--seeds", type=int, nargs="+", default=list(DEFAULT_SEEDS))
    p.add_argument("-f", "--frames", type=positive_int, nargs="+", default=list(DEFAULT_FRAMES))
    p.add_argument("-p", "--policy", type=policy_name, nargs="+", default=list(MAIN_POLICIES))
    p.add_argument("--opt", action="store_true")
    p.add_argument("--write-prob", type=probability, default=DEFAULT_WRITE_PROB)
    p.add_argument("-o", "--out")
    _add_analysis_args(p)
    p.set_defaults(func=cmd_seeds)

    p = sub.add_parser("onset", help="warning onset vs. working-set size")
    p.add_argument("--high-ws", type=positive_int, nargs="+", default=[3, 4, 5, 6, 8, 10, 12])
    p.add_argument("--windows", type=positive_int, nargs="+", default=[DEFAULT_WINDOW])
    p.add_argument("-f", "--frames", type=positive_int, default=4)
    p.add_argument("-p", "--policy", type=policy_name, nargs="+", default=list(MAIN_POLICIES))
    p.add_argument("--opt", action="store_true")
    p.add_argument("--threshold", type=probability, default=DEFAULT_THRESHOLD)
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_onset)

    p = sub.add_parser("reproduce", help="regenerate all report traces, tables and charts")
    p.add_argument("-o", "--out", default="results")
    p.add_argument("--traces", default="traces")
    p.set_defaults(func=cmd_reproduce)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)  # argparse exits with status 2 on bad parameters
    try:
        return args.func(args)
    except TraceError as exc:
        console.print(f"[bold red]error:[/] {exc}", highlight=False)
        console.print("[dim]Nothing was simulated.[/]")
        return 2
    except InvariantError as exc:
        console.print(f"[bold red]INVARIANT VIOLATED:[/] {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
