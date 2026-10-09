"""Reference data that checks and reconciliation compare orders against. It's maintained, so it's trusted as given."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path


@dataclass(frozen=True)
class CrmCustomer:
    customer_id: str
    name: str
    email: str


@dataclass(frozen=True)
class Reference:
    list_prices: dict[str, Decimal]  # sku -> list price in EUR
    fx: dict[tuple[str, str], Decimal]  # ("2025-03", "USD") -> units per 1 EUR
    customers: dict[str, CrmCustomer]  # customer_id -> customer

    def customer_by_email(self) -> dict[str, CrmCustomer]:
        return {c.email: c for c in self.customers.values()}

    def list_price(self, sku: str | None) -> Decimal | None:
        return self.list_prices.get(sku) if sku else None

    def rate(self, currency: str | None, day: date | None) -> Decimal | None:
        """Units of `currency` per euro in the month of `day`, or None when there's no rate."""
        if currency == "EUR":
            return Decimal(1)
        if currency is None or day is None:
            return None
        return self.fx.get((f"{day:%Y-%m}", currency))


def read_rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load_reference(ref_dir: Path) -> Reference:
    products = read_rows(ref_dir / "products.csv")
    rates = read_rows(ref_dir / "fx_rates.csv")
    crm = read_rows(ref_dir / "crm_customers.csv")
    return Reference(
        list_prices={row["sku"]: Decimal(row["list_price_eur"]) for row in products},
        fx={(row["month"], row["currency"]): Decimal(row["per_eur"]) for row in rates},
        customers={row["customer_id"]: CrmCustomer(row["customer_id"], row["name"], row["email"]) for row in crm},
    )
