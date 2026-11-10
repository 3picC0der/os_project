# Virtual Memory Management Simulator

ICS 433 (Operating Systems Lab) term project: a multi-process virtual-memory
simulator written in C, with Python used only to run experiments and draw charts.

## The question

The HotOS 2023 paper *"FIFO can be Better than LRU: the Power of Lazy Promotion
and Quick Demotion"* (Yang, Qiu, Zhang, Yue, Rashmi) found, on 5,307 block and
web cache traces, that **FIFO-Reinsertion (a form of Clock) has a lower miss ratio than
LRU**. In its footnote 2 the authors guess this may *not* hold for virtual-memory
workloads, where working sets change suddenly. They did not test that.

**Use case:** the page cache of a shared server. Several processes share one pool
of physical frames. Is the cheap Clock policy good enough, or does LRU win once
processes change their working sets?

We replicate the paper's FIFO / LRU / FIFO-Reinsertion comparison, including one
of the paper's own traces (MSR Cambridge `hm_0`), and then test the footnote-2
question with a VM-like workload.

## Build and run

Needs a C11 compiler and `make`. Python 3 with matplotlib is only needed for
the experiments.

```bash
make            # builds bin/vmsim, bin/vmgen, bin/test_sim
make test       # runs the unit tests (all should pass)
```

Run one policy on a trace:

```bash
./bin/vmsim run -p lru -f 4 traces/phase_ws8.csv
./bin/vmsim run -p clock -f 4 -l results/logs/demo traces/phase_ws8.csv   # also writes per-step and warning logs
```

Compare all policies on one trace (frame counts can be percentages of the distinct pages):

```bash
./bin/vmsim compare traces/belady.csv 2,3,4,8
./bin/vmsim compare traces/zipf_seed11.csv 0.1%,1%,10%
```

Generate workloads (the same arguments always produce the same trace):

```bash
./bin/vmgen zipf  -s 11 -n 4 -r 25000 -p 1000 -a 0.9 -w 0.3 > my_trace.csv
./bin/vmgen shift -s 11 -n 4 -r 25000 -p 1000 -a 0.9 -w 0.3 -k 10 > my_trace.csv
./bin/vmgen phase -p 8 > my_trace.csv
```

Reproduce every result and chart:

```bash
make experiments    # = python3 analysis/run_experiments.py
```

Results go to `results/results.md`, `results/*.csv` and `results/figures/`.

### The MSR hm_0 trace

The compressed real trace, `data/msr_hm_0.oracleGeneral.zst` (18 MB), is
committed to the repo. It is the paper authors' public copy, from
`https://ftp.pdl.cmu.edu/pub/datasets/twemcacheWorkload/cacheDatasets/msr/`.
`make experiments` converts it to `data/msr_hm_0.csv` on the first run. The CSV
is 60 MB, so it is not committed. To convert it yourself:

```bash
python3 tools/msr_to_trace.py data/msr_hm_0.oracleGeneral.zst data/msr_hm_0.csv
```

The converter needs the `zstd` command (`brew install zstd`).

**macOS Homebrew note:** if `import matplotlib.pyplot` fails with a `pyexpat` /
`libexpat` symbol error, run the experiments with
`DYLD_LIBRARY_PATH=/opt/homebrew/opt/expat/lib make experiments`.

## Trace format

A CSV file whose first line is exactly `pid,page,op`. Each following line is
one reference, run in file order: a positive `pid`, a non-negative virtual
`page`, and `op` = `R` or `W`. A page is the pair (pid, page), so processes
never share pages. Any invalid line rejects the whole file with its line number.

## Memory model (summary)

* `F` frames, all empty at start, shared by every process (global replacement).
* A fault takes the lowest free frame; otherwise the policy evicts any process's page.
* **FIFO** evicts the oldest load. **LRU** evicts the least recently used page.
* **CLOCK** (the proposal's rule): every reference, *including a load*, sets the
  reference bit. The hand clears 1-bits until it finds a 0, and moves past every insertion.
* **FIFO-R** (the paper's FIFO-Reinsertion): the same as Clock, except a load
  leaves the bit at 0. A page survives the hand only if it was hit.
* Every fault = 1 page-in. Evicting a dirty page = 1 **eviction write-back**.
* A process finishes right after its last reference. Its dirty pages are counted
  as **completion flushes** (separate from write-backs) and its frames are freed.
  Clock keeps its hand position.
* **Thrashing warning (a simulation heuristic, not a real OS detector):** a process
  warns when it has ≥ 20 references, total working-set demand D > F, and the
  fault rate over its last 20 references is ≥ 0.50.

## Project layout

```
src/trace.c, trace.h   CSV loading and strict validation
src/sim.c, sim.h       frames, the four policies, cleanup, working sets, warnings, invariants
src/main.c             vmsim command line (run / compare)
src/gen.c              vmgen seeded workload generator
tests/test_sim.c       unit tests: proposal test table, cleanup/Clock hand, warnings, invariants
tools/msr_to_trace.py  converts the MSR trace to our CSV format
analysis/run_experiments.py   runs all experiments, writes tables and charts
traces/                saved input traces (generated once, reused by every policy)
results/               CSV results, results.md, logs and figures
docs/                  progress report
```
