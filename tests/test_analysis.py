"""Working-set windows and the thrashing-warning heuristic.

Hand-calculated boundary tests from the proposal: 19 vs 20 references,
D equal to F vs above F, fault rate below vs equal to 0.50, the full
phase-change workload, and removal of finished processes from demand.
"""

import pytest

from vmsim.analysis import WorkingSetTracker, warning_intervals
from vmsim.engine import simulate
from vmsim.generator import phase_change_trace
from vmsim.policies import MAIN_POLICIES
from vmsim.trace import Reference as R


def feed(tracker, pid, pages_and_faults, t0=0):
    tracker.activate(pid)
    for i, (page, fault) in enumerate(pages_and_faults):
        tracker.record(pid, page, fault)
        tracker.evaluate(t0 + i)


# ---------------------------------------------------------------- tracker unit tests

def test_19_vs_20_references():
    tr = WorkingSetTracker(num_frames=1)
    feed(tr, 1, [(p, True) for p in range(19)])
    assert tr.ref_count(1) == 19 and tr.demand() == 19 > 1
    assert 1 not in tr.warned                        # only 19 references: never warned
    feed(tr, 1, [(19, True)], t0=19)
    assert 1 in tr.warned                            # 20th reference turns it on


def test_demand_equal_to_frames_does_not_warn():
    tr = WorkingSetTracker(num_frames=4)
    feed(tr, 1, [(i % 4, True) for i in range(20)])  # WS = 4 = F, fault rate 1.0
    assert tr.demand() == 4 and 1 not in tr.warned


def test_demand_above_frames_warns():
    tr = WorkingSetTracker(num_frames=3)
    feed(tr, 1, [(i % 4, True) for i in range(20)])  # WS = 4 > F = 3
    assert 1 in tr.warned


@pytest.mark.parametrize("faults,warned", [(9, False), (10, True)])
def test_fault_rate_below_vs_equal_threshold(faults, warned):
    tr = WorkingSetTracker(num_frames=1)
    feed(tr, 1, [(i % 2, i < faults) for i in range(20)])  # WS = 2 > 1
    assert tr.window_fault_rate(1) == faults / 20
    assert (1 in tr.warned) is warned


def test_window_is_per_process_not_global():
    tr = WorkingSetTracker(num_frames=8)
    tr.activate(1)
    tr.activate(2)
    tr.record(1, 0, True)
    for i in range(30):
        tr.record(2, i, True)                        # P2's references never touch P1's window
    assert tr.ws_size(1) == 1 and tr.ref_count(1) == 1
    assert tr.ws_size(2) == 20                      # only the last 20 of P2's own refs


def test_other_process_can_trigger_warning_and_completion_clears_it():
    """P1 is faulting but fits alone; P2's growth pushes D over F, which warns
    P1 even though P1 issued no reference. When P2 finishes, D drops and P1's
    warning is re-evaluated with post-cleanup demand."""
    tr = WorkingSetTracker(num_frames=4)
    feed(tr, 1, [(i % 3, i % 2 == 0) for i in range(20)])  # WS 3, fault rate 0.5
    assert 1 not in tr.warned                                # D = 3 <= 4
    tr.activate(2)
    tr.record(2, 0, True)
    tr.evaluate(20)                                          # D = 4: still fine
    assert 1 not in tr.warned
    tr.record(2, 1, True)
    tr.evaluate(21)                                          # D = 5 > 4 -> P1 warned
    assert 1 in tr.warned and 2 not in tr.warned             # P2 has < 20 references
    assert tr.events[-1].pid == 1 and tr.events[-1].state == "ON"

    post = tr.finish(2, t=21)
    assert post == 3 and 1 not in tr.warned
    assert tr.events[-1].cause == "completion" and tr.events[-1].state == "OFF"


def test_finished_process_removed_from_demand_in_engine():
    refs = [R(1, p % 3, "R") for p in range(5)] + [R(2, 0, "R")] * 3
    res = simulate(refs, 8, "lru")
    last_p1 = res.steps[4]
    assert last_p1.completed
    assert last_p1.demand_pre == 3 and last_p1.demand_post == 0
    assert res.steps[5].demand_pre == 1


# ---------------------------------------------------------------- phase-change workload

@pytest.mark.parametrize("policy", MAIN_POLICIES)
def test_phase_change_workload(policy):
    refs = phase_change_trace()
    assert len(refs) == 320
    res = simulate(refs, 4, policy)
    iv = warning_intervals(res.warnings, 1)
    assert len(iv) == 1, "exactly one warning episode"
    on, off = iv[0]
    assert 80 < on < 240, "turns on during the 8-page phase"
    assert 240 <= off < 260, "clears soon after recovery"
    # no steady warning in phase 1
    assert not any(s.system_warning for s in res.steps[:80])
    # 20 references into the final phase the working set is 2 and the warning is off
    s = res.steps[240 + 19]
    assert s.ws_size == 2 and not s.system_warning
    # sustained warning in the middle of the pressure phase
    assert all(s.system_warning for s in res.steps[120:240])


def test_phase_change_exact_onset_for_report():
    """Pin the numbers quoted in the report: ON 11 refs into phase 2, OFF 12 into phase 3."""
    res = simulate(phase_change_trace(), 4, "lru")
    assert warning_intervals(res.warnings, 1) == [(91, 252)]


def test_empty_trace_has_no_warnings_and_na_rates():
    res = simulate([], 4, "lru")
    assert res.totals.references == 0
    assert res.totals.hit_rate is None and res.totals.fault_rate is None
    assert res.warnings == [] and res.utilization_mean is None


def test_window_and_threshold_are_configurable():
    refs = phase_change_trace()
    on10 = warning_intervals(simulate(refs, 4, "lru", window=10).warnings, 1)[0][0]
    on40 = warning_intervals(simulate(refs, 4, "lru", window=40).warnings, 1)[0][0]
    assert on10 < 91 < on40
    assert simulate(refs, 4, "lru", threshold=1.0).warnings  # rate 1.0 still reaches threshold 1.0


@pytest.mark.parametrize("bad", [{"window": 0}, {"threshold": 1.5}, {"threshold": -0.1}])
def test_invalid_analysis_parameters(bad):
    with pytest.raises(ValueError):
        WorkingSetTracker(num_frames=4, **bad)
