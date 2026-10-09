"""The whole pipeline over one data directory: ingest, parse, check, reconcile."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from .check import CheckContext, check_orders
from .ingest import ingest_dir
from .ingest.records import SourceResult
from .issues import Issue
from .model import Order
from .parse import parse_orders
from .reconcile import COPY_CODES, reconcile
from .reference import load_reference


@dataclass
class Run:
    sources: list[SourceResult]
    orders: list[Order]  # every record read, each with its final status
    file_issues: list[Issue]  # about whole files rather than any one order
    as_of: date

    @property
    def issues(self) -> list[Issue]:
        return self.file_issues + [i for o in self.orders for i in o.issues]

    def with_status(self, status: str) -> list[Order]:
        return [o for o in self.orders if o.status == status]

    def repairs(self) -> list[Issue]:
        """Values the pipeline changed. Marking a record as a copy removes it rather than repairing it."""
        return [i for i in self.issues if i.severity == "warning" and i.code not in COPY_CODES]


def run_pipeline(data: Path, as_of: date) -> Run:
    sources, unclaimed = ingest_dir(data / "raw")
    orders = parse_orders(order for source in sources for order in source.orders)
    reference = load_reference(data / "reference")
    check_orders(orders, CheckContext(reference, as_of))
    reconcile(orders, reference)
    return Run(sources, orders, unclaimed + [i for source in sources for i in source.issues], as_of)
