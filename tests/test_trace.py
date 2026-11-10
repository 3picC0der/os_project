"""Strict CSV validation: invalid inputs are rejected before simulation."""

import pytest

from vmsim.trace import Reference, TraceError, load_trace, parse_lines, save_trace


def parse(text: str):
    return parse_lines(text.splitlines(keepends=True), source="test")


def test_valid_trace():
    refs = parse("pid,page,op\n1,0,R\n2,0,W\n1,1,R\n")
    assert refs == [Reference(1, 0, "R"), Reference(2, 0, "W"), Reference(1, 1, "R")]
    assert refs[0].key != refs[1].key  # (pid, page) identity


def test_header_only_is_valid_empty_trace():
    assert parse("pid,page,op\n") == []


def test_crlf_line_endings_accepted(tmp_path):
    p = tmp_path / "t.csv"
    p.write_bytes(b"pid,page,op\r\n1,0,R\r\n")
    assert load_trace(p) == [Reference(1, 0, "R")]


def test_zero_byte_file_rejected(tmp_path):
    p = tmp_path / "empty.csv"
    p.write_bytes(b"")
    with pytest.raises(TraceError, match="missing header"):
        load_trace(p)


@pytest.mark.parametrize("header", ["pid,page", "PID,page,op", "page,pid,op", "pid, page, op", "1,0,R"])
def test_wrong_or_missing_header(header):
    with pytest.raises(TraceError, match="header"):
        parse(f"{header}\n1,0,R\n")


@pytest.mark.parametrize("row,fragment", [
    ("1,0", "expected 3 fields"),
    ("1,0,R,extra", "expected 3 fields"),
    ("0,0,R", "pid must be a positive integer"),
    ("-1,0,R", "pid must be a positive integer"),
    ("1.5,0,R", "pid must be a positive integer"),
    ("abc,0,R", "pid must be a positive integer"),
    ("+1,0,R", "pid must be a positive integer"),
    (" 1,0,R", "pid must be a positive integer"),
    ("1,-1,R", "page must be a non-negative integer"),
    ("1,2.0,R", "page must be a non-negative integer"),
    ("1,x,R", "page must be a non-negative integer"),
    ("1,0,X", "op must be uppercase"),
    ("1,0,r", "op must be uppercase"),
    ("1,0,w", "op must be uppercase"),
    ("1,0,", "op must be uppercase"),
    (",,", "blank data row"),
])
def test_invalid_rows(row, fragment):
    with pytest.raises(TraceError, match=fragment):
        parse(f"pid,page,op\n1,0,R\n{row}\n")


def test_blank_data_row_rejected():
    with pytest.raises(TraceError, match="line 3: blank"):
        parse("pid,page,op\n1,0,R\n\n2,0,R\n")


def test_error_names_the_line_and_collects_all_errors():
    with pytest.raises(TraceError) as exc:
        parse("pid,page,op\n1,0,R\n0,0,R\n1,0,Q\n")
    assert len(exc.value.errors) == 2
    assert exc.value.errors[0].startswith("line 3")
    assert exc.value.errors[1].startswith("line 4")


def test_missing_file():
    with pytest.raises(TraceError, match="not found"):
        load_trace("does/not/exist.csv")


def test_save_and_reload_roundtrip(tmp_path):
    refs = [Reference(1, 0, "R"), Reference(3, 7, "W")]
    p = save_trace(refs, tmp_path / "sub" / "t.csv")
    assert p.read_text() == "pid,page,op\n1,0,R\n3,7,W\n"
    assert load_trace(p) == refs


@pytest.mark.parametrize("frames", [0, -1, 2.5, "4", True])
def test_invalid_frame_count_rejected_before_simulation(frames):
    from vmsim.engine import Simulator
    with pytest.raises(ValueError, match="frame count"):
        Simulator([Reference(1, 0, "R")], frames, "lru")
