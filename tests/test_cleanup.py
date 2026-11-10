"""Policy reset and process-cleanup tests (reviewer feedback #4).

Trace (F = 3), with P1 finishing in the middle so its freed frames get reused:

    t  ref       Clock: frame  hand after  bits after
    0  (2,0,R)          0      1           [1,0,0]
    1  (1,0,W)          1      2           [1,1,0]
    2  (1,1,R)          2      0           [1,1,1]
       -- P1 completes: flush P1:0 (dirty), free frames 1 and 2 --
                                0 (kept)    [1,0,0]
    3  (2,1,R)          1      2           [1,1,0]    lowest free frame is 1, hand was 0
    4  (2,2,R)          2      0           [1,1,1]
    5  (2,3,R)  full: clear all, evict frame 0 (P2:0)  -> hand 1, bits [1,0,0]
    6  (2,0,R)  full: frame 1 has bit 0, evict P2:1     -> hand 2, bits [1,1,0]

Step 3 is the interesting one: after cleanup the lowest free frame (1) is
not under the hand (0). Our rule ``hand = inserted_frame + 1`` gives 2.
"""

import pytest

from vmsim.engine import Simulator, simulate
from vmsim.policies import MAIN_POLICIES, make_policy
from vmsim.trace import PageKey
from vmsim.trace import Reference as R

TRACE = [R(2, 0, "R"), R(1, 0, "W"), R(1, 1, "R"), R(2, 1, "R"),
         R(2, 2, "R"), R(2, 3, "R"), R(2, 0, "R")]


def test_clock_hand_and_bits_through_cleanup_and_reuse():
    sim = Simulator(TRACE, 3, "clock")
    expected = [  # (frame used, evicted, hand after, bits after) per step
        (0, "", 1, [1, 0, 0]),
        (1, "", 2, [1, 1, 0]),
        (2, "", 0, [1, 0, 0]),          # after P1's cleanup: frames 1,2 freed, hand kept at 0
        (1, "", 2, [1, 1, 0]),          # reuse lowest free frame 1
        (2, "", 0, [1, 1, 1]),
        (0, "P2:0", 1, [1, 0, 0]),
        (1, "P2:1", 2, [1, 1, 0]),
    ]
    for t, (frame, evicted, hand, bits) in enumerate(expected):
        rec = sim.step()
        if t == 6:
            break  # last step: P2 cleanup clears everything; checked below via policy_state
        assert (rec.frame, rec.evicted, sim.policy.hand, sim.policy.ref_bits) == (frame, evicted, hand, bits), t
    assert (rec.frame, rec.evicted) == (1, "P2:1")
    # After the final cleanup all frames are free, bits cleared, hand retained.
    assert sim.policy.ref_bits == [0, 0, 0] and sim.policy.hand == 2
    assert sim.frames == [None, None, None]


def test_cleanup_at_step_2_frees_frames_and_flushes_dirty():
    sim = Simulator(TRACE, 3, "clock")
    for _ in range(3):
        rec = sim.step()
    assert rec.completed and rec.flushed == 1
    assert sim.frames == [PageKey(2, 0), None, None]
    assert sim.page_tables[1] == {}
    assert PageKey(1, 0) not in sim.policy.tracked_keys()


@pytest.mark.parametrize("policy", [*MAIN_POLICIES, "opt"])
def test_per_process_totals_after_cleanup(policy):
    res = simulate(TRACE, 3, policy)
    p1, p2 = res.processes[1], res.processes[2]
    assert (p1.references, p1.hits, p1.faults, p1.completion_flushes, p1.eviction_writebacks) == (2, 0, 2, 1, 0)
    assert (p1.first_ref, p1.last_ref) == (1, 2)
    assert p2.references == 5 and p2.completion_flushes == 0 and p2.eviction_writebacks == 0
    # P1's pages were never evicted: they were freed by cleanup instead.
    assert p1.pages_evicted == 0
    # per-process counts sum to the system totals
    for field in ("references", "hits", "faults", "page_ins", "eviction_writebacks", "completion_flushes"):
        assert getattr(p1, field) + getattr(p2, field) == getattr(res.totals, field)


@pytest.mark.parametrize("policy", ["fifo", "lru"])
def test_fifo_lru_metadata_removed_on_cleanup(policy):
    sim = Simulator(TRACE, 3, policy)
    for _ in range(3):
        sim.step()
    assert sim.policy.tracked_keys() == {PageKey(2, 0)}
    rec = sim.step()       # P2:1 must reuse lowest free frame 1, not evict P2:0
    assert rec.frame == 1 and rec.evicted == ""


def test_fifo_order_after_reuse():
    """After cleanup, the reused frame's page is the *newest* in the FIFO queue."""
    sim = Simulator(TRACE, 3, "fifo")
    recs = [sim.step() for _ in range(6)]
    assert recs[5].evicted == "P2:0"  # P2:0 loaded at t=0 is still the oldest


@pytest.mark.parametrize("policy", [*MAIN_POLICIES, "opt"])
def test_replacement_state_resets_between_runs(policy):
    """Reusing one policy object for two runs gives identical results."""
    pol = make_policy(policy)
    a = Simulator(TRACE, 3, pol).run()
    b = Simulator(TRACE, 3, pol).run()
    assert a.totals == b.totals
    assert [s.evicted for s in a.steps] == [s.evicted for s in b.steps]


def test_final_process_flushed_at_end_of_trace():
    res = simulate([R(1, 0, "W"), R(1, 1, "W")], 4, "lru")
    assert res.totals.completion_flushes == 2
    assert res.totals.eviction_writebacks == 0
