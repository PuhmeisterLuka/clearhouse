"""Webshop API dump: nested orders with an items array."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .records import RawLine, RawOrder, SourceResult, SourceStats, blank_to_none, decode

ORDER_KEYS = {"id", "created_at", "customer", "currency", "items", "total", "fulfillment"}
CUSTOMER_KEYS = {"name", "email", "country"}
ITEM_KEYS = {"sku", "quantity", "price"}


def text(value: Any) -> str | None:
    """Numbers already arrive as their literal text (see parse_float/parse_int in load_records)."""
    if value is None:
        return None
    if isinstance(value, str):
        return blank_to_none(value)
    if isinstance(value, bool):
        return str(value).lower()
    return json.dumps(value, ensure_ascii=False)


def load_records(raw: str, result: SourceResult) -> list[Any]:
    try:
        # Numbers as strings keep "23.0" vs 23 visible and avoid float rounding.
        doc = json.loads(raw, parse_float=str, parse_int=str)
    except json.JSONDecodeError as e:
        result.issue("error", "web.bad_json", str(e), f"line {e.lineno} col {e.colno}")
        return []

    records = doc.get("orders") if isinstance(doc, dict) else doc
    if not isinstance(records, list):
        result.issue("error", "web.no_orders", "expected a list of orders, or an object with an 'orders' list")
        return []

    export = doc.get("export") if isinstance(doc, dict) else None
    declared = export.get("count") if isinstance(export, dict) else None
    if declared is not None and str(declared).isdigit() and int(declared) != len(records):
        result.issue("error", "web.count_mismatch", f"export header says {declared} orders, found {len(records)}")
    return records


def parse_customer(value: Any, result: SourceResult) -> tuple[dict[str, Any], bool]:
    """The customer object, and whether it was unusable."""
    if value is None:
        return {}, False
    if not isinstance(value, dict):
        return {}, True
    result.flag_unknown("customer", value.keys() - CUSTOMER_KEYS)
    return value, False


def parse_items(items: Any, loc: str, order: RawOrder, result: SourceResult) -> list[RawLine]:
    if not isinstance(items, list):
        result.issue("error", "web.no_items", "order has no items list", loc, text(items), order)
        return []
    lines = []
    for j, item in enumerate(items):
        item_loc = f"{loc}.items[{j}]"
        if not isinstance(item, dict):
            result.issue("error", "web.bad_item", "item is not an object", item_loc, text(item), order)
            continue
        result.flag_unknown("item", item.keys() - ITEM_KEYS)
        lines.append(RawLine(text(item.get("sku")), text(item.get("quantity")), text(item.get("price")), 1, item_loc))
    return lines


def parse_order(rec: Any, loc: str, file: str, result: SourceResult) -> RawOrder | None:
    if not isinstance(rec, dict):
        result.issue("error", "web.not_an_object", "order entry is not an object", loc, value=text(rec))
        return None
    result.flag_unknown("order", rec.keys() - ORDER_KEYS)
    customer, unusable = parse_customer(rec.get("customer"), result)
    order = RawOrder("web", file, loc, text(rec.get("id")),
                     placed_at=text(rec.get("created_at")), currency=text(rec.get("currency")),
                     total=text(rec.get("total")), customer_name=text(customer.get("name")),
                     customer_email=text(customer.get("email")), customer_country=text(customer.get("country")),
                     fulfillment=text(rec.get("fulfillment")))
    if unusable:
        result.issue("warning", "web.bad_customer", "customer is not an object", loc, text(rec["customer"]), order)
    order.lines = parse_items(rec.get("items"), loc, order, result)
    return order


def read_web(path: Path) -> SourceResult:
    raw, encoding = decode(path.read_bytes())
    result = SourceResult(SourceStats(path.name, "web", encoding))
    for i, rec in enumerate(load_records(raw, result)):
        order = parse_order(rec, f"orders[{i}]", path.name, result)
        if order is not None:
            result.orders.append(order)
    return result.finish()
