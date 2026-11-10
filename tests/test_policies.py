"""Deterministic edge cases from the proposal's testing table.

Notation: "0R" = page 0 read by PID 1. H = hits, M = faults,
E = dirty eviction write-backs, C = completion flushes. Counts include cleanup.
"""

import pytest

from vmsim.engine import Simulator, simulate
from vmsim.generator import belady_trace
from vmsim.policies import MAIN_POLICIES
from vmsim.trace import Reference as R
from vmsim.trace import refs_from_compact as c

ALL = [*MAIN_POLICIES, "opt"]


def counts(refs, frames, policy):
    T = simulate(refs, frames, policy).totals
    return dict(H=T.hits, M=T.faults, E=T.eviction_writebacks, C=T.completion_flushes)


# (case name, F, trace, expected for every policy)
SAME_FOR_ALL = [
    ("one frame, clean eviction", 1, c("0R 1R 0R"), dict(H=0, M=3, E=0, C=0)),
    ("repeated page", 1, c("0R 0W 0R"), dict(H=2, M=1, E=0, C=1)),
    ("dirty eviction", 1, c("0W 1R"), dict(H=0, M=2, E=1, C=0)),
    ("separate page identities", 2, [R(1, 0, "R"), R(2, 0, "R"), R(1, 0, "R"), R(2, 0, "R")],
     dict(H=2, M=2, E=0, C=0)),
    ("completion and reuse", 1, [R(1, 0, "W"), R(2, 0, "R")], dict(H=0, M=2, E=0, C=1)),
]


@pytest.mark.parametrize("policy", ALL)
@pytest.mark.parametrize("name,frames,refs,expected", SAME_FOR_ALL, ids=[x[0] for x in SAME_FOR_ALL])
def test_proposal_table_all_policies(name, frames, refs, expected, policy):
    assert counts(refs, frames, policy) == expected


def test_separate_identities_one_fault_one_hit_each():
    res = simulate([R(1, 0, "R"), R(2, 0, "R"), R(1, 0, "R"), R(2, 0, "R")], 2, "lru")
    for pid in (1, 2):
        assert (res.processes[pid].faults, res.processes[pid].hits) == (1, 1)


def test_completion_frees_frame_before_next_process():
    """F=1; (1,0,W),(2,0,R): P1 flushes and frees its frame, so P2's first
    reference takes the free frame — no eviction, no eviction write-back."""
    res = simulate([R(1, 0, "W"), R(2, 0, "R")], 1, "fifo")
    assert res.steps[0].completed and res.steps[0].flushed == 1
    assert res.steps[1].evicted == ""
    assert res.processes[1].completion_flushes == 1
    assert res.processes[2].evictions_caused == 0


def test_fifo_versus_lru():
    refs = c("0R 1R 0R 2R 0R")
    assert counts(refs, 2, "fifo") == dict(H=1, M=4, E=0, C=0)
    assert counts(refs, 2, "lru") == dict(H=2, M=3, E=0, C=0)


def test_fifo_hit_does_not_change_queue_but_lru_does():
    sim_f = Simulator(c("0R 1R 0R 2R"), 2, "fifo", record_policy_state=True)
    sim_l = Simulator(c("0R 1R 0R 2R"), 2, "lru", record_policy_state=True)
    for _ in range(3):
        sim_f.step()
        sim_l.step()
    assert sim_f.policy.snapshot()["queue"] == ["P1:0", "P1:1"]        # hit on 0 ignored
    assert [s.split("@")[0] for s in sim_l.policy.snapshot()["lru_to_mru"]] == ["P1:1", "P1:0"]
    assert sim_f.step().evicted == "P1:0"
    assert sim_l.step().evicted == "P1:1"


def test_clock_bits_and_hand():
    """F=2; 0R,1R,0R,2R,1R. Before 2R: bits [1,1], hand 0. It clears both,
    evicts page 0, then bits [1,0], hand 1. Final 1R sets bit 1 to 1."""
    sim = Simulator(c("0R 1R 0R 2R 1R"), 2, "clock", record_policy_state=True)
    for _ in range(3):
        sim.step()
    assert sim.policy.ref_bits == [1, 1] and sim.policy.hand == 0

    rec = sim.step()  # 2R
    assert rec.evicted == "P1:0" and rec.frame == 0
    assert sim.policy.ref_bits == [1, 0] and sim.policy.hand == 1

    rec = sim.step()  # 1R (hit) — last reference, cleanup follows
    assert rec.result == "HIT"
    assert rec.policy_state == {"hand": 1, "bits": [1, 1]}  # sampled before cleanup
    T = sim.result().totals
    assert (T.faults, T.hits, T.eviction_writebacks, T.completion_flushes) == (3, 2, 0, 0)


def test_clock_hand_advances_on_free_frame_insertion_but_not_on_hit():
    sim = Simulator(c("0R 1R 2R 0R 1R"), 3, "clock")
    hands = [sim.step() and sim.policy.hand for _ in range(5)]
    assert hands[:3] == [1, 2, 0]    # every insertion moves the hand
    assert hands[3:] == [0, 0]       # hits leave it alone


def test_fifo_anomaly_trace():
    refs = belady_trace()
    assert simulate(refs, 3, "fifo").totals.faults == 9
    assert simulate(refs, 4, "fifo").totals.faults == 10
    for f in (3, 4):
        T = simulate(refs, f, "fifo").totals
        assert T.eviction_writebacks == T.completion_flushes == 0


def test_write_hit_makes_page_dirty_and_read_does_not_clean_it():
    # 0R (clean load), 0W (write hit -> dirty), 0R (still dirty), 1R evicts 0 -> one write-back
    assert counts(c("0R 0W 0R 1R"), 1, "lru") == dict(H=2, M=2, E=1, C=0)


def test_multiple_writes_cause_single_writeback():
    assert counts(c("0W 0W 0W 1R"), 1, "fifo") == dict(H=2, M=2, E=1, C=0)


@pytest.mark.parametrize("policy", MAIN_POLICIES)
def test_opt_is_never_worse(policy):
    from vmsim.generator import build_workload
    for w in ("locality", "pressure", "phase", "belady"):
        refs, _ = build_workload(w)
        for f in (1, 2, 3, 4, 8):
            assert simulate(refs, f, "opt").totals.faults <= simulate(refs, f, policy).totals.faults


def test_global_replacement_can_evict_other_process():
    # F=2: P1 loads 0, P2 loads 0, P1 loads 1 -> FIFO evicts P1:0 (oldest), then
    # P2:1 evicts P2:0, i.e. victims are chosen across processes.
    refs = [R(1, 0, "R"), R(2, 0, "R"), R(1, 1, "R"), R(2, 1, "R"), R(1, 1, "R"), R(2, 1, "R")]
    res = simulate(refs, 2, "fifo")
    assert [s.evicted for s in res.steps[:4]] == ["", "", "P1:0", "P2:0"]
