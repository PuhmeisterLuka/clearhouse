"""Typed orders: every field is either a valid value or None, and every repair or failure is in `issues`."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..issues import Issue, Severity
from ..money import money


@dataclass
class ParsedLine:
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
class ParsedOrder:
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
    lines: list[ParsedLine] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def order_ref(self) -> str:
        return f"{self.source}:{self.native_id or '?'}"

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    def log(self, stage: str, severity: Severity, code: str, message: str,
            locator: str | None = None, value: Any = None) -> None:
        self.issues.append(Issue(stage, severity, code, message, self.source, locator or self.locator,
                                 self.order_ref, None if value is None else str(value)))
