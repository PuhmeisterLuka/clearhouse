"""Till export: one CSV row per line item, with the order's header fields repeated on every row."""

from __future__ import annotations

import csv
import io
import re
from itertools import islice
from pathlib import Path
from typing import Any

from ..text import ascii_fold
from .records import RawLine, RawOrder, SourceResult, SourceStats, blank_to_none, decode

# The till runs on a Central European Windows box, so anything that isn't UTF-8 is cp1250.
FALLBACK_ENCODINGS = ("cp1250",)
DELIMITERS = ";,\t|"
HEADER_SEARCH_ROWS = 50
MIN_KNOWN_COLUMNS = 5

COLUMNS = {
    "racun": "native_id", "datum": "placed_at", "kupec": "customer_name", "e-posta": "customer_email",
    "ref": "ref", "artikel": "sku", "kolicina": "qty", "cena": "unit_price", "znesek racuna": "total",
}
REQUIRED = {"native_id", "sku", "qty", "unit_price"}
ORDER_FIELDS = ("placed_at", "customer_name", "customer_email", "ref", "total")
FOOTER_WORDS = {"skupaj", "total", "sum"}
FOOTER_COUNT = re.compile(r"(\d+)\s*(vrstic|rows|lines)", re.IGNORECASE)


def norm(cell: str) -> str:
    return ascii_fold(cell).strip().strip('"').lower()


def is_header(row: list[str]) -> bool:
    return sum(norm(c) in COLUMNS for c in row) >= MIN_KNOWN_COLUMNS


def open_table(text: str) -> tuple[Any, list[str], str] | None:
    """Find the header row under whichever delimiter makes it parse.

    Returns the csv reader positioned just after the header, the header itself and the delimiter.
    """
    for delim in DELIMITERS:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delim)
        for row in islice(reader, HEADER_SEARCH_ROWS):
            if is_header(row):
                return reader, row, delim
    return None


def map_columns(result: SourceResult, header: list[str], loc: str) -> dict[int, str] | None:
    """Column position -> field name. None when a required column is missing."""
    columns = {i: COLUMNS[norm(c)] for i, c in enumerate(header) if norm(c) in COLUMNS}
    for c in header:
        if norm(c) not in COLUMNS:
            result.issue("info", "pos.unknown_column", "column ignored", loc, value=c)
    missing = REQUIRED - set(columns.values())
    if missing:
        result.issue("error", "pos.missing_columns", f"header lacks {sorted(missing)}", loc)
        return None
    return columns


def check_footer(result: SourceResult, row: list[str], data_rows: int, loc: str) -> None:
    claimed = next((int(m[1]) for c in row if (m := FOOTER_COUNT.search(c))), None)
    if claimed is not None and claimed != data_rows:
        result.issue("error", "pos.footer_count",
                     f"footer says {claimed} rows but {data_rows} were read, the file may be truncated", loc)


def add_line(result: SourceResult, orders: dict[str, RawOrder], cell: dict[str, str | None],
             file: str, loc: str) -> None:
    """Rows sharing a receipt number become one order. Its header fields come from the first row."""
    receipt = cell["native_id"]
    order = orders.get(receipt)
    if order is None:
        # The till has no currency column because it only ever takes euros.
        order = orders[receipt] = RawOrder("pos", file, loc, receipt, currency="EUR",
                                           **{f: cell.get(f) for f in ORDER_FIELDS})
    else:
        for f in ORDER_FIELDS:
            if cell.get(f) != getattr(order, f):
                message = f"{f} differs from the receipt's first row, kept the first"
                result.issue("warning", "pos.header_disagrees", message, loc, order.order_ref, cell.get(f))
    order.lines.append(RawLine(cell["sku"], cell["qty"], cell["unit_price"], 1, loc))


def read_rows(result: SourceResult, reader: Any, header: list[str], columns: dict[int, str],
              delim: str, file: str) -> list[RawOrder]:
    orders: dict[str, RawOrder] = {}
    data_rows = 0
    for row in reader:
        loc = f"line {reader.line_num}"
        if not any(c.strip() for c in row):
            continue
        # Short rows read as missing values in their trailing columns.
        cell = {name: blank_to_none(row[i]) if i < len(row) else None for i, name in columns.items()}
        if cell["native_id"] is None and any(norm(c) in FOOTER_WORDS for c in row):
            check_footer(result, row, data_rows, loc)
            continue

        data_rows += 1
        if cell["native_id"] is None:
            result.issue("error", "pos.no_receipt", "row has no receipt number", loc, value=delim.join(row))
            continue
        if len(row) != len(header):
            result.issue("error", "pos.row_width", f"row has {len(row)} cells, header has {len(header)}", loc,
                         f"pos:{cell['native_id']}", delim.join(row))
        add_line(result, orders, cell, file, loc)
    return list(orders.values())


def read_pos(path: Path) -> SourceResult:
    text, encoding = decode(path.read_bytes(), FALLBACK_ENCODINGS)
    result = SourceResult(SourceStats(path.name, "pos", encoding))
    if not encoding.startswith("utf-8"):
        result.issue("info", "pos.legacy_encoding", f"file is not UTF-8, decoded as {encoding}")

    table = open_table(text)
    if table is None:
        result.issue("error", "pos.no_header", "could not find a header row naming the expected columns")
        return result.finish()
    reader, header, delim = table

    columns = map_columns(result, header, f"line {reader.line_num}")
    if columns is not None:
        result.orders = read_rows(result, reader, header, columns, delim, path.name)
    return result.finish()
