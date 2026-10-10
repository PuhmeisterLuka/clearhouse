"""The pipeline's order record. Parsing creates it; checks and reconciliation repair and annotate it in place.

Every field is either a valid value or None, and every repair or failure is in `issues`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from .issues import Issue, Severity
from .money import money


@dataclass
class Line:
    sku: str | None
    qty: int | None
    unit_price: Decimal | None
    locator: str

    @property
    def total(self) -> Decimal | None:
        if self.unit_price is None or self.qty is None:
            return None
        return money(self.unit_price * self.qty)


@dataclass
class Order:
    source: str
    file: str
    locator: str
    native_id: str | None
    placed_at: datetime | None  # UTC; None when the source only records a date
    placed_date: date | None  # local business date
    currency: str | None
    total: Decimal | None
    customer_name: str | None
    customer_email: str | None
    customer_code: str | None
    customer_country: str | None
    ref: str | None
    fulfillment: str | None
    lines: list[Line] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    customer_id: str | None = None  # set when the customer is found in the CRM
    total_eur: Decimal | None = None
    duplicate_of: str | None = None  # order_ref of the record this one copies

    @property
    def order_ref(self) -> str:
        return f"{self.source}:{self.native_id or '?'}"

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def value_eur(self) -> Decimal:
        """The euro total, which reconciliation sets on every order that ends up clean."""
        if self.total_eur is None:
            raise ValueError(f"{self.order_ref} has no euro total")
        return self.total_eur

    @property
    def status(self) -> str:
        """Where the record ends up: a copy to drop, an order to quarantine, or a clean order."""
        if self.duplicate_of is not None:
            return "duplicate"
        return "quarantined" if self.has_errors else "clean"

    def log(self, stage: str, severity: Severity, code: str, message: str,
            locator: str | None = None, value: Any = None) -> None:
        self.issues.append(Issue(stage, severity, code, message, self.source, locator or self.locator,
                                 self.order_ref, None if value is None else str(value)))
