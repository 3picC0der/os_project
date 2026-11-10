"""Invariants hold on every step of every workload — and the checker really
catches violations when the state is corrupted on purpose."""

import pytest

from vmsim.engine import Simulator
from vmsim.generator import DEFAULT_SEEDS, build_workload
from vmsim.invariants import InvariantError, check_invariants
from vmsim.trace import PageKey
from vmsim.trace import refs_from_compact as c

POLICIES = ["fifo", "lru", "clock", "opt"]


@pytest.mark.parametrize("policy", POLICIES)
@pytest.mark.parametrize("workload", ["locality", "pressure", "phase", "belady"])
@pytest.mark.parametrize("frames", [1, 2, 3, 4, 8])
def test_invariants_hold_on_all_workloads(policy, workload, frames):
    refs, _ = build_workload(workload)
    res = Simulator(refs, frames, policy, check=True).run()   # checks after every step
    T = res.totals
    assert T.hits + T.faults == T.references == len(refs)
    assert T.page_ins == T.faults


@pytest.mark.parametrize("seed", DEFAULT_SEEDS)
def test_invariants_hold_across_seeds(seed):
    refs, _ = build_workload("pressure", seed=seed)
    for p in POLICIES:
        Simulator(refs, 3, p).run()


def _sim_mid_run():
    sim = Simulator(c("0W 1R 2R 0R 3R 1W"), 3, "clock", check=False)
    for _ in range(4):
        sim.step()
    check_invariants(sim)  # baseline: consistent
    return sim


def test_detects_duplicate_resident_page():
    sim = _sim_mid_run()
    sim.frames[1] = sim.frames[0]
    with pytest.raises(InvariantError, match=r"\[I5\]|\[I6\]"):
        check_invariants(sim)


def test_detects_counter_mismatch():
    sim = _sim_mid_run()
    sim.stats[1].hits += 1
    with pytest.raises(InvariantError, match=r"\[I1\]"):
        check_invariants(sim)


def test_detects_totals_drift():
    sim = _sim_mid_run()
    sim.totals.eviction_writebacks += 1
    with pytest.raises(InvariantError, match=r"\[I3\]"):
        check_invariants(sim)


def test_detects_leaked_policy_metadata():
    sim = _sim_mid_run()
    sim.policy._resident[PageKey(9, 9)] = 0
    with pytest.raises(InvariantError, match=r"\[I8\]"):
        check_invariants(sim)


def test_detects_clean_page_write():
    sim = _sim_mid_run()
    for entry in sim.page_tables[1].values():
        entry.dirty = True  # pages that were only read now look dirty
    with pytest.raises(InvariantError, match=r"\[I10\]"):
        check_invariants(sim)


def test_detects_bad_clock_bit_on_empty_frame():
    sim = Simulator(c("0R"), 3, "clock", check=False)
    sim.step()  # process done -> all frames free
    sim.policy.ref_bits[2] = 1
    with pytest.raises(InvariantError, match=r"\[I9\]"):
        check_invariants(sim)
