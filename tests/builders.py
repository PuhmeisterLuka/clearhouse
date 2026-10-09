"""Hand-built orders for unit tests. Any Order field can be overridden by keyword."""

from datetime import date
from decimal import Decimal

from clearhouse.model import Line, Order


def make_line(sku="BRK-0001", qty=1, price="10.00", n=0) -> Line:
    return Line(sku, qty, Decimal(price), f"line {n}")


def make_order(lines, total=None, **fields) -> Order:
    values = {
        "source": "web", "file": "f.json", "locator": "orders[0]", "native_id": "1",
        "placed_at": None, "placed_date": date(2025, 3, 14), "currency": "EUR",
        "total": sum((ln.total for ln in lines), Decimal("0.00")) if total is None else Decimal(total),
        "customer_name": None, "customer_email": None, "customer_code": None, "customer_country": "SI",
        "ref": None, "fulfillment": None,
    }
    return Order(**{**values, **fields}, lines=lines)
