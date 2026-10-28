"""Read-only access to workbook / CSV contents. Original files are never opened for writing."""
from __future__ import annotations

import csv
import datetime as dt
from pathlib import Path
from typing import Any

import openpyxl

Rows = list[tuple]


def list_sheets(path: Path) -> list[str]:
    """Worksheet names (cheap: reads workbook metadata only). CSV files expose one pseudo-sheet."""
    if path.suffix.lower() == ".csv":
        return [path.stem]
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return list(wb.sheetnames)
    finally:
        wb.close()


def read_all_sheets(path: Path) -> dict[str, Rows]:
    """Return {sheet name: list of row tuples}. Values are as stored (timedeltas stay timedeltas)."""
    if path.suffix.lower() == ".csv":
        return {path.stem: read_csv_rows(path)}
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {name: [tuple(r) for r in wb[name].iter_rows(values_only=True)] for name in wb.sheetnames}
    finally:
        wb.close()


def _coerce(text: str) -> Any:
    """Convert a CSV cell from text to int/float where it looks numeric; empty text becomes None."""
    if text == "":
        return None
    try:
        return float(text) if any(c in text for c in ".eE") else int(text)
    except ValueError:
        return text


def read_csv_rows(path: Path) -> Rows:
    """Read a CSV with delimiter sniffing; numeric-looking cells become numbers."""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [tuple(_coerce(c) for c in row) for row in csv.reader(fh, dialect)]


def to_seconds(value: Any) -> float | None:
    """Convert an Excel time cell (timedelta / time / day-fraction number) to seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, dt.time):
        return value.hour * 3600 + value.minute * 60 + value.second + value.microsecond / 1e6
    if isinstance(value, (int, float)):
        return float(value) * 86400.0  # Excel stores durations as fractions of a day
    return None


def find_header_row(rows: Rows, markers: set[str], max_scan: int = 15) -> int | None:
    """Index of the first row whose first non-empty cell is one of `markers`."""
    for i, row in enumerate(rows[:max_scan]):
        first = next((c for c in row if c not in (None, "")), None)
        if isinstance(first, str) and first.strip() in markers:
            return i
    return None


def key_value_rows(rows: Rows, stop_at_blank_after: int = 0) -> dict[str, Any]:
    """Parse 'Label:' / value pairs from the top block of an Info-style sheet."""
    out: dict[str, Any] = {}
    for row in rows:
        if len(row) >= 2 and isinstance(row[0], str) and row[0].strip().endswith(":") and row[1] not in (None, ""):
            out[row[0].strip().rstrip(":")] = row[1]
    return out
