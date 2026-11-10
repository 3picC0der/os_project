"""vmsim — Virtual Memory Management Simulator (ICS 433 project).

A trace-driven simulator of demand paging with a global frame pool shared by
several simulated processes. It compares FIFO, LRU and Clock page replacement
(plus OPT as a reference baseline), tracks dirty-page write-backs, per-process
working sets, and raises heuristic thrashing warnings.

Package layout
--------------
trace       Reading, validating and writing reference traces (CSV).
generator   Seeded synthetic workloads and the fixed spec workloads.
policies    Replacement policies behind one common interface.
engine      The memory manager: frames, page tables, faults, cleanup.
analysis    Per-process working-set / fault-rate windows and warnings.
invariants  Executable consistency checks run after every reference.
metrics     Per-process and system counters, summaries.
experiments Comparison runner, FIFO-anomaly check, multi-seed sweeps.
output      CSV writers, rich terminal tables and matplotlib charts.
cli         Command-line interface (``python -m vmsim``).
"""

__version__ = "0.2.0"
