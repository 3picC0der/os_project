# vmsim — Virtual Memory Management Simulator (alternate Python version)

> [!WARNING]
> **This branch is an alternate version, not the team's submission.**
> The official Phase 2 project is on [`main`](../../tree/main): a C simulator
> that replicates *"FIFO can be Better than LRU"*. This branch is a separate
> Python implementation of the original proposal (FIFO / LRU / Clock with
> working-set warnings), built at the same time as `main`. It is kept only
> for reference and comparison and is not merged into `main`.

**ICS 433 Operating Systems Lab · Page Replacement and Thrashing Analysis**

A trace-driven simulator of demand paging where several simulated processes
share one global pool of physical frames. It compares **FIFO**, **LRU** and
**Clock** page replacement (plus **OPT** as a reference baseline), tracks
dirty pages and disk traffic, estimates per-process working sets, and raises
heuristic thrashing warnings.

> This is a simulation of memory management. It does not modify a kernel or
> run real processes; CPU scheduling, timing and real concurrency are out of scope.

## Team

| # | Member | Primary responsibility |
|---|---|---|
| 1 | Mohammed Alzaid | Shared engine & frame state, FIFO and LRU, process cleanup, policy unit tests |
| 2 | Saad Bin Waqas | Working sets, warning logic, per-process metrics, phase-change analysis, analysis tests |
| 3 | Muhammad Ammar Sohail | CSV validation, seeded workload generator, command-line interface, input tests |
| 4 | Belal Shebl | Clock policy and tests, comparison runner, FIFO anomaly check, CSV results, static charts |

## Setup

Requires Python 3.10+ (developed and tested on Python 3.13).

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Quick start

```bash
# simulate one trace with one policy and 4 frames, show the first 15 steps
python -m vmsim run traces/pressure_seed11.csv -p clock -f 4 --steps 15

# all policies × F = 2, 3, 4, 8 on the same trace, with the FIFO anomaly check
python -m vmsim compare traces/belady.csv -f 2 3 4 8 --opt -o results/my_compare

# the changing-locality workload with its warning timeline chart
python -m vmsim phase -f 4 -o results/my_phase

# generate a new seeded trace (parameters saved to a .meta.json next to it)
python -m vmsim generate pressure --seed 23 -o traces/pressure_seed23.csv

# run-to-run variability over several seeds
python -m vmsim seeds locality --seeds 11 23 37 -o results/my_seeds

# how fast the warning reacts, for several working-set sizes and windows
python -m vmsim onset --high-ws 3 4 5 6 8 10 --windows 10 20 40 -o results/my_onset

# regenerate every trace, table and chart in results/ from scratch
python -m vmsim reproduce
```

Use `python -m vmsim <command> --help` for every option. `--window` and
`--threshold` change the analysis window (default 20) and warning threshold
(default 0.50).

### Trace format

A CSV with the exact header `pid,page,op`, one reference per row, executed in
file order:

```csv
pid,page,op
1,0,R
2,0,W
1,1,R
```

`pid` is a positive integer, `page` a non-negative integer, `op` is uppercase
`R` or `W`. Page identity is `(pid, page)`, so processes never share pages.
Invalid files are rejected **before** simulation with the offending line
numbers (e.g. `line 3: op must be uppercase 'R' or 'W', got 'r'`), and no
partial results are written. A header-only file is a valid empty trace.

## Project structure

```
vmsim/
  trace.py        trace data model, strict CSV validation, saving
  generator.py    seeded synthetic workloads + fixed spec traces (phase, belady)
  policies/       FIFO, LRU, Clock, OPT behind one ReplacementPolicy interface
  engine.py       frames, page tables, faults, evictions, dirty bits, cleanup
  analysis.py     per-process working-set / fault-rate windows, warnings
  invariants.py   executable consistency checks run after every reference
  metrics.py      per-process and system counters
  experiments.py  comparison runner, anomaly detection, seed and onset sweeps
  output.py       CSV writers, terminal tables (rich), charts (matplotlib)
  cli.py          command-line interface
tests/            pytest suite (224 tests)
traces/           saved input traces + .meta.json (seed and parameters)
results/          saved comparison CSVs and charts (from `vmsim reproduce`)
```

## Memory model

* `F` frames, all empty at the start; **global replacement**, no per-process quotas.
* A fault takes the **lowest-index free frame**; if memory is full the policy
  chooses a victim, which may belong to any process.
* Read load → clean page. Write load or write hit → dirty (a later read does not clean it).
* Evicting a dirty page → one **eviction write-back**; evicting a clean page costs nothing.
* Every fault → one **page-in**.
* A process finishes right after its last reference. Its metrics are sampled
  first, then **cleanup** flushes its dirty pages (**completion flushes**,
  counted separately), frees its frames and removes its policy metadata.
  Clock clears the freed frames' bits but keeps its hand where it is.

| Policy | Evicts | On a hit |
|---|---|---|
| FIFO | page loaded earliest | nothing changes |
| LRU | page with the oldest last-reference index | recency updated |
| Clock | first frame with reference bit 0, scanning from the hand and clearing 1-bits | bit set to 1, hand does not move |
| OPT (baseline) | page whose next use is farthest in the future | nothing changes |

### Design decisions (where the proposal left room)

1. **Clock hand after insertion:** `hand = (inserted_frame + 1) % F`. After a
   cleanup the lowest free frame need not be under the hand; anchoring to the
   inserted frame keeps the rule well-defined (tested in `tests/test_cleanup.py`).
2. **Page-ins equal faults by construction.** They are still reported as their
   own column so disk reads, eviction write-backs and completion flushes are
   never mixed together.
3. **OPT is a baseline, not a policy.** It needs future knowledge and is shown
   only to measure how far FIFO/LRU/Clock are from the best possible result.
4. **Seeds.** The proposal's seed 11 is the main trace; seeds 11, 23, 37, 53, 71
   are used for variability, summarised as mean, sample standard deviation, min and max.

## Working sets and the thrashing warning

For each process, the **working set** is the set of distinct pages among its
own last 20 references. **Demand `D`** is the sum of working-set sizes of all
active processes. After every reference each active process is warned if:

> it has made ≥ 20 references **and** `D > F` **and** its fault rate over its last 20 references ≥ 0.50

This is a **heuristic inside this simulation model**: it flags "this process
keeps faulting while the active working sets do not fit in memory". It is
not a measurement of CPU utilisation and not proof that a real OS would thrash.
`vmsim onset` shows how its reaction time depends on the working-set size and
the window length.

## Executable invariants

With `check=True` (the default) the engine verifies after **every** reference that:

* hits + faults = references, and page-ins = faults (per process and overall);
* per-process counters sum to independently kept system totals;
* occupancy ≤ F and the free list matches the frame table;
* no `(pid, page)` is resident twice and every occupied frame has exactly one owner;
* finished processes hold no frames, no policy metadata and no warning;
* the policy tracks exactly the resident pages; Clock bits of empty frames are 0;
* clean pages never cause disk writes.

Any violation stops the run with `InvariantError` naming the rule. The tests
also corrupt the state on purpose to prove that each check fires.

## Testing

```bash
python -m pytest
```

The suite covers every row of the proposal's testing table (one-frame cases,
dirty eviction, FIFO vs LRU, Clock bits and hand, separate page identities,
completion and reuse, empty trace, invalid inputs, the FIFO anomaly), the
window boundary cases (19 vs 20 references, `D = F` vs `D > F`, fault rate
below vs equal to 0.50), the full phase-change workload, process cleanup with
frame reuse and the Clock hand position, policy reset between runs, generator
reproducibility, and the CLI.

## Reproducing the results

`python -m vmsim reproduce` writes:

| Path | Contents |
|---|---|
| `traces/*.csv`, `*.meta.json` | the saved input traces with seed and parameters |
| `results/<workload>/compare.csv` | every policy × F = 2, 3, 4, 8 (Belady: F = 1–5) |
| `results/<workload>/faults_vs_frames.png` | fault-count chart, anomalies circled |
| `results/<workload>/<policy>_F4_summary.csv` | per-process table at F = 4 |
| `results/phase/` | per-step logs, warning transitions, timeline chart |
| `results/seeds/` | raw runs and mean/std/min/max per policy and F |
| `results/onset/` | warning onset and clearance vs. working-set size and window |
| `results/settings.json` | every setting used |
