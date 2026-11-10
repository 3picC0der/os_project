"""Seeded workload generation: reproducible, round-robin, parameters recorded."""

import json

import pytest

from vmsim.generator import (ProcessSpec, belady_trace, build_workload, generate,
                             phase_change_trace, save_workload)
from vmsim.trace import load_trace


def test_same_seed_same_trace():
    a, _ = build_workload("pressure", seed=11)
    b, _ = build_workload("pressure", seed=11)
    assert a == b


def test_different_seeds_differ():
    a, _ = build_workload("pressure", seed=11)
    b, _ = build_workload("pressure", seed=23)
    assert a != b


def test_round_robin_ascending_pid_skips_exhausted():
    specs = [ProcessSpec(pid=3, refs=1, ws_size=1), ProcessSpec(pid=1, refs=3, ws_size=1),
             ProcessSpec(pid=2, refs=2, ws_size=1)]
    pids = [r.pid for r in generate(specs, seed=1, write_prob=0)]
    assert pids == [1, 2, 3, 1, 2, 1]


def test_cyclic_working_set_with_full_locality():
    refs = generate([ProcessSpec(pid=1, refs=7, ws_size=3, locality=1.0)], seed=5, write_prob=0)
    assert [r.page for r in refs] == [0, 1, 2, 0, 1, 2, 0]


def test_write_probability_extremes():
    spec = [ProcessSpec(pid=1, refs=50, ws_size=2)]
    assert all(r.op == "R" for r in generate(spec, seed=1, write_prob=0.0))
    assert all(r.op == "W" for r in generate(spec, seed=1, write_prob=1.0))


def test_write_probability_roughly_respected():
    refs, _ = build_workload("pressure", seed=11, write_prob=0.30)
    share = sum(r.is_write for r in refs) / len(refs)
    assert 0.2 < share < 0.4


def test_process_trace_is_independent_of_other_processes():
    """Adding a process must not change another process's own reference stream."""
    p1 = ProcessSpec(pid=1, refs=40, ws_size=3, locality=0.7)
    alone = [r for r in generate([p1], seed=9) if r.pid == 1]
    mixed = [r for r in generate([p1, ProcessSpec(pid=2, refs=10, ws_size=2)], seed=9) if r.pid == 1]
    assert alone == mixed


@pytest.mark.parametrize("bad", [
    dict(pid=0, refs=1, ws_size=1),
    dict(pid=1, refs=1, ws_size=0),
    dict(pid=1, refs=1, ws_size=20, address_space=10),
    dict(pid=1, refs=1, ws_size=1, locality=1.5),
])
def test_invalid_specs(bad):
    with pytest.raises(ValueError):
        generate([ProcessSpec(**bad)])


def test_duplicate_pid_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        generate([ProcessSpec(1, 1, 1), ProcessSpec(1, 2, 1)])


def test_saved_trace_and_metadata(tmp_path):
    refs, meta = build_workload("locality", seed=23, write_prob=0.3)
    csv_path, meta_path = save_workload(refs, meta, tmp_path / "loc.csv")
    assert load_trace(csv_path) == refs
    saved = json.loads(meta_path.read_text())
    assert saved["seed"] == 23 and saved["write_prob"] == 0.3
    assert saved["references"] == len(refs) and len(saved["processes"]) == 3


def test_phase_change_trace_shape():
    refs = phase_change_trace()
    assert len(refs) == 320 and all(r.op == "R" and r.pid == 1 for r in refs)
    assert {r.page for r in refs[:80]} == {0, 1}
    assert {r.page for r in refs[80:240]} == set(range(8))
    assert {r.page for r in refs[240:]} == {0, 1}


def test_belady_trace():
    assert [r.page for r in belady_trace()] == [1, 2, 3, 4, 1, 2, 5, 1, 2, 3, 4, 5]


def test_unknown_workload():
    with pytest.raises(ValueError, match="unknown workload"):
        build_workload("nope")
