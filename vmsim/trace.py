"""Reference traces: the data model, strict CSV validation, and saving.

A trace is an ordered list of memory references. Each CSV row is one
reference with the exact header ``pid,page,op``:

* ``pid``  — positive integer (1, 2, ...)
* ``page`` — non-negative virtual page number (0, 1, ...)
* ``op``   — uppercase ``R`` (read) or ``W`` (write)

Page identity is the pair ``(pid, page)``: page 0 of process 1 and page 0 of
process 2 are different pages. Rows execute in file order, one at a time.

Validation is all-or-nothing: if any row is bad the whole trace is rejected
*before* simulation starts, so a run never produces partial results.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, NamedTuple

HEADER = ["pid", "page", "op"]
VALID_OPS = ("R", "W")

# Plain ASCII digits only: rejects "+1", "1.0", " 1", "1e3", "١" (Arabic digit) etc.
_UINT_RE = re.compile(r"[0-9]+")

# Stop collecting after this many errors so a huge broken file stays readable.
MAX_REPORTED_ERRORS = 20


class PageKey(NamedTuple):
    """Globally unique identity of a virtual page: (process id, page number)."""

    pid: int
    page: int

    def __str__(self) -> str:  # e.g. "P1:3"
        return f"P{self.pid}:{self.page}"


@dataclass(frozen=True)
class Reference:
    """One memory reference from the trace."""

    pid: int
    page: int
    op: str  # "R" or "W"

    @property
    def key(self) -> PageKey:
        return PageKey(self.pid, self.page)

    @property
    def is_write(self) -> bool:
        return self.op == "W"


class TraceError(ValueError):
    """Raised when a trace file (or an in-memory trace) is invalid.

    ``errors`` holds one human-readable message per problem found, each naming
    the offending line so the user can fix the file.
    """

    def __init__(self, source: str, errors: list[str]):
        self.source = source
        self.errors = errors
        shown = errors[:MAX_REPORTED_ERRORS]
        more = len(errors) - len(shown)
        msg = f"invalid trace {source!r}:\n  " + "\n  ".join(shown)
        if more > 0:
            msg += f"\n  ... and {more} more error(s)"
        super().__init__(msg)


def _parse_row(row: list[str], line_no: int) -> tuple[Reference | None, list[str]]:
    """Validate one CSV data row. Returns (reference or None, list of errors)."""
    where = f"line {line_no}"
    if len(row) == 0 or all(cell.strip() == "" for cell in row):
        return None, [f"{where}: blank data row (blank rows are not allowed)"]
    if len(row) != 3:
        return None, [f"{where}: expected 3 fields (pid,page,op), got {len(row)}: {row!r}"]

    pid_s, page_s, op = row
    errors: list[str] = []

    if not _UINT_RE.fullmatch(pid_s) or int(pid_s) <= 0:
        errors.append(f"{where}: pid must be a positive integer, got {pid_s!r}")
    if not _UINT_RE.fullmatch(page_s):
        errors.append(f"{where}: page must be a non-negative integer, got {page_s!r}")
    if op not in VALID_OPS:
        errors.append(f"{where}: op must be uppercase 'R' or 'W', got {op!r}")

    if errors:
        return None, errors
    return Reference(int(pid_s), int(page_s), op), []


def parse_lines(lines: Iterable[str], source: str = "<memory>") -> list[Reference]:
    """Parse CSV text lines into references, raising TraceError on any problem."""
    reader = csv.reader(lines)
    try:
        header = next(reader)
    except StopIteration:
        # Zero-byte file: there is not even a header.
        raise TraceError(source, ["file is empty: missing header 'pid,page,op'"]) from None

    if header != HEADER:
        raise TraceError(
            source,
            [f"line 1: header must be exactly 'pid,page,op', got {','.join(header)!r}"],
        )

    refs: list[Reference] = []
    errors: list[str] = []
    for row in reader:
        ref, row_errors = _parse_row(row, reader.line_num)
        if row_errors:
            errors.extend(row_errors)
        elif ref is not None:
            refs.append(ref)

    if errors:
        raise TraceError(source, errors)
    return refs


def load_trace(path: str | Path) -> list[Reference]:
    """Load and strictly validate a trace CSV file. Header-only files are valid."""
    path = Path(path)
    try:
        # newline="" lets the csv module handle \n and \r\n line endings itself.
        with path.open("r", newline="", encoding="utf-8") as fh:
            return parse_lines(fh, source=str(path))
    except FileNotFoundError:
        raise TraceError(str(path), ["file not found"]) from None
    except UnicodeDecodeError as exc:
        raise TraceError(str(path), [f"file is not valid UTF-8 text ({exc.reason})"]) from None


def save_trace(refs: Iterable[Reference], path: str | Path) -> Path:
    """Write references to ``path`` in the canonical trace format."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, lineterminator="\n")
        writer.writerow(HEADER)
        for r in refs:
            writer.writerow([r.pid, r.page, r.op])
    return path


def refs_from_compact(spec: str, pid: int = 1) -> list[Reference]:
    """Build a single-process trace from the proposal's shorthand, e.g. "0R 1R 0W".

    Used heavily by the tests so expected-result tables read like the proposal.
    """
    refs = []
    for token in spec.replace(",", " ").split():
        page, op = token[:-1], token[-1]
        refs.append(Reference(pid, int(page), op))
    return refs


def validate_refs(refs: Iterable[Reference], source: str = "<memory>") -> list[Reference]:
    """Check an in-memory trace with the same rules as the CSV loader."""
    refs = list(refs)
    errors = []
    for i, r in enumerate(refs):
        if not isinstance(r.pid, int) or r.pid <= 0:
            errors.append(f"reference {i}: pid must be a positive integer, got {r.pid!r}")
        if not isinstance(r.page, int) or r.page < 0:
            errors.append(f"reference {i}: page must be a non-negative integer, got {r.page!r}")
        if r.op not in VALID_OPS:
            errors.append(f"reference {i}: op must be 'R' or 'W', got {r.op!r}")
    if errors:
        raise TraceError(source, errors)
    return refs
