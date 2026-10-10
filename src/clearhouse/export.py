"""The pipeline's deliverables as CSV: clean tables, plus a record of everything that was set aside and why."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator
from decimal import Decimal
from pathlib import Path
from typing import Any

from .files import write_csv
from .issues import Issue
from .model import Order
from .pipeline import Run
from .reconcile import copy_issue

ORDER_COLUMNS = ["order_ref", "source", "native_id", "placed_at", "placed_date", "customer_id", "customer_name",
                 "currency", "total", "total_eur", "fulfillment", "file", "locator"]
LINE_COLUMNS = ["order_ref", "line_no", "sku", "qty", "unit_price", "line_total", "locator"]
CUSTOMER_COLUMNS = ["customer_id", "name", "orders", "revenue_eur", "first_order", "last_order", "sources"]
QUARANTINE_COLUMNS = ["order_ref", "source", "file", "locator", "placed_date", "total", "error_codes", "reasons"]
DUPLICATE_COLUMNS = ["order_ref", "duplicate_of", "source", "file", "locator", "reason"]
ISSUE_COLUMNS = ["stage", "severity", "code", "source", "order_ref", "locator", "value", "message"]


def order_rows(clean: list[Order]) -> Iterator[list[Any]]:
    for o in clean:
        yield [o.order_ref, o.source, o.native_id, o.placed_at.isoformat() if o.placed_at else None, o.placed_date,
               o.customer_id, o.customer_name, o.currency, o.total, o.total_eur, o.fulfillment, o.file, o.locator]


def line_rows(clean: list[Order]) -> Iterator[list[Any]]:
    for o in clean:
        for n, line in enumerate(o.lines, start=1):
            yield [o.order_ref, n, line.sku, line.qty, line.unit_price, line.total, line.locator]


def customer_rows(clean: list[Order]) -> Iterator[list[Any]]:
    """One row per CRM customer with clean orders. Anonymous walk-in sales have no customer to report."""
    by_customer: dict[str, list[Order]] = defaultdict(list)
    for o in clean:
        if o.customer_id is not None:
            by_customer[o.customer_id].append(o)
    for customer_id in sorted(by_customer):
        orders = by_customer[customer_id]
        dates = sorted(o.placed_date for o in orders if o.placed_date is not None)
        yield [customer_id, orders[0].customer_name, len(orders), sum((o.value_eur for o in orders), Decimal(0)),
               dates[0], dates[-1], " ".join(sorted({o.source for o in orders}))]


def quarantine_rows(quarantined: list[Order]) -> Iterator[list[Any]]:
    for o in quarantined:
        errors = [i for i in o.issues if i.severity == "error"]
        yield [o.order_ref, o.source, o.file, o.locator, o.placed_date, o.total,
               " ".join(sorted({i.code for i in errors})), "; ".join(i.describe() for i in errors)]


def duplicate_rows(copies: list[Order]) -> Iterator[list[Any]]:
    for o in copies:
        yield [o.order_ref, o.duplicate_of, o.source, o.file, o.locator, copy_issue(o).message]


def issue_rows(issues: Iterable[Issue]) -> Iterator[list[Any]]:
    for issue in issues:
        yield [getattr(issue, column) for column in ISSUE_COLUMNS]


def write_tables(out: Path, run: Run) -> None:
    clean = run.with_status("clean")
    write_csv(out / "orders.csv", ORDER_COLUMNS, order_rows(clean))
    write_csv(out / "lines.csv", LINE_COLUMNS, line_rows(clean))
    write_csv(out / "customers.csv", CUSTOMER_COLUMNS, customer_rows(clean))
    write_csv(out / "quarantine.csv", QUARANTINE_COLUMNS, quarantine_rows(run.with_status("quarantined")))
    write_csv(out / "duplicates.csv", DUPLICATE_COLUMNS, duplicate_rows(run.with_status("duplicate")))
    write_csv(out / "issues.csv", ISSUE_COLUMNS, issue_rows(run.issues))
