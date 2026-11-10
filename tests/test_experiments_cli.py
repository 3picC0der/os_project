"""Comparison runner, anomaly check, sweeps, and the command-line interface."""

import pytest

from vmsim.cli import main
from vmsim.experiments import compare, detect_anomalies, onset_sweep, seed_sweep
from vmsim.generator import belady_trace, build_workload, save_workload
from vmsim.trace import save_trace


def test_comparison_flags_fifo_anomaly():
    rows = compare(belady_trace(), [2, 3, 4, 8], ["fifo", "lru", "clock"])
    fifo = [a for a in detect_anomalies(rows) if a["policy"] == "fifo"]
    assert fifo == [{"policy": "fifo", "frames_from": 3, "frames_to": 4, "faults_from": 9, "faults_to": 10}]


@pytest.mark.parametrize("workload", ["locality", "pressure", "phase", "belady"])
def test_stack_algorithms_never_anomalous(workload):
    """LRU and OPT are stack algorithms: more frames can never mean more faults."""
    refs, _ = build_workload(workload)
    rows = compare(refs, range(1, 11), ["lru", "opt"])
    assert detect_anomalies(rows) == []


def test_compare_uses_same_trace_for_every_policy():
    refs, _ = build_workload("pressure")
    rows = compare(refs, [2, 4], ["fifo", "lru", "clock"])
    assert len(rows) == 6 and {r["references"] for r in rows} == {len(refs)}


def test_seed_sweep_summary():
    raw, summary = seed_sweep("locality", [11, 23, 37], [2, 4], ["lru"])
    assert len(raw) == 6 and len(summary) == 2
    s = summary[0]
    assert s["n_seeds"] == 3
    assert s["fault_rate_min"] <= s["fault_rate_mean"] <= s["fault_rate_max"]
    assert s["fault_rate_std"] >= 0


def test_onset_sweep_no_warning_when_ws_fits():
    rows = onset_sweep(high_ws_sizes=(3, 4, 5, 8), num_frames=4, policies=["lru"])
    by_ws = {r["high_ws"]: r for r in rows}
    assert by_ws[3]["onset_delay"] is None and by_ws[4]["onset_delay"] is None
    assert by_ws[5]["onset_delay"] is not None and by_ws[8]["onset_delay"] == 11


def test_onset_scales_with_window():
    rows = onset_sweep(high_ws_sizes=(8,), policies=["lru"], windows=(10, 20, 40))
    delays = [r["onset_delay"] for r in sorted(rows, key=lambda r: r["window"])]
    assert delays == sorted(delays) and delays[0] < delays[-1]


# ------------------------------------------------------------------------ CLI

@pytest.fixture
def trace_file(tmp_path):
    refs, meta = build_workload("locality", seed=11)
    return save_workload(refs, meta, tmp_path / "loc.csv")[0]


def test_cli_run_writes_outputs(trace_file, tmp_path):
    out = tmp_path / "out"
    assert main(["run", str(trace_file), "-p", "lru", "-f", "4", "-o", str(out)]) == 0
    for name in ("lru_F4_steps.csv", "lru_F4_summary.csv", "lru_F4_warnings.csv"):
        assert (out / name).exists()
    header = (out / "lru_F4_summary.csv").read_text().splitlines()[0]
    # disk traffic is labelled separately (reviewer feedback #5)
    assert "page_ins" in header and "eviction_writebacks" in header and "completion_flushes" in header


def test_cli_compare(trace_file, tmp_path):
    out = tmp_path / "cmp"
    assert main(["compare", str(trace_file), "--opt", "-o", str(out)]) == 0
    assert (out / "compare.csv").exists() and (out / "faults_vs_frames.png").exists()
    assert (out / "anomalies.csv").read_text().startswith("policy,frames_from")


def test_cli_empty_trace(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_text("pid,page,op\n")
    out = tmp_path / "o"
    assert main(["run", str(p), "-f", "2", "-o", str(out)]) == 0
    assert "N/A" in (out / "lru_F2_summary.csv").read_text()


def test_cli_invalid_trace_produces_no_output(tmp_path, capsys):
    p = tmp_path / "bad.csv"
    p.write_text("pid,page,op\n1,0,R\n1,0,r\n")
    out = tmp_path / "o"
    assert main(["run", str(p), "-f", "2", "-o", str(out)]) == 2
    assert not out.exists()  # no partial results
    assert "line 3" in capsys.readouterr().out


@pytest.mark.parametrize("frames", ["0", "-2", "2.5", "four"])
def test_cli_invalid_frame_count(trace_file, frames):
    with pytest.raises(SystemExit) as exc:
        main(["run", str(trace_file), "-f", frames])
    assert exc.value.code == 2


def test_cli_generate(tmp_path):
    out = tmp_path / "g.csv"
    assert main(["generate", "pressure", "--seed", "23", "-o", str(out)]) == 0
    assert out.exists() and out.with_suffix(".meta.json").exists()


def test_cli_unknown_policy(trace_file):
    with pytest.raises(SystemExit):
        main(["run", str(trace_file), "-f", "2", "-p", "random"])
