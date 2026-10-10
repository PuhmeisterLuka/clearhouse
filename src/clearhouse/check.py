"""Order checks: rules that need a whole order or the reference data, run after every field is parsed.

Validations only look and report, and run on every order so the issue log shows every problem.
Repairs change the order, and stop as soon as it has an error: an order headed for quarantine isn't
worth repairing, and its numbers can't be trusted to repair from. Repair order matters too: prices
are fixed before duplicate lines are judged, and both before the header total is compared.

Rules skip values that are already None, since parsing has reported those.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .model import Line, Order
from .money import money
from .reference import Reference

STAGE = "check"

EUROZONE = {"AT", "BE", "CY", "DE", "EE", "ES", "FI", "FR", "GR", "HR", "IE", "IT", "LT", "LU", "LV", "MT", "NL",
            "PT", "SI", "SK"}
# The webshop charges customers outside the eurozone in their own currency, where it supports one.
WEB_CURRENCY = {"GB": "GBP", "US": "USD"}

CENTS_SLIP = (Decimal(50), Decimal(200))  # price/list ratio that means the price was written in cents
PLAUSIBLE = (Decimal("0.5"), Decimal(2))  # anything outside this is worth a human look


@dataclass(frozen=True)
class CheckContext:
    reference: Reference
    as_of: date  # nothing can have been ordered after this


Rule = Callable[[Order, CheckContext], None]


def lines_total(lines: Iterable[Line]) -> Decimal | None:
    """The sum of the line totals, or None when any line is missing its price or quantity."""
    total = Decimal("0.00")
    for line in lines:
        amount = line.total
        if amount is None:
            return None
        total += amount
    return total


def currency_for_country(country: str | None) -> str | None:
    if country is None:
        return None
    return "EUR" if country in EUROZONE else WEB_CURRENCY.get(country)


def check_not_future(order: Order, ctx: CheckContext) -> None:
    if order.placed_date is not None and order.placed_date > ctx.as_of:
        order.log(STAGE, "error", "placed_at.future", f"dated {order.placed_date}, after {ctx.as_of}",
                  value=order.placed_date)


def resolve_currency(order: Order, ctx: CheckContext) -> None:
    if order.currency is None:
        inferred = currency_for_country(order.customer_country)
        if inferred is None:
            order.log(STAGE, "error", "currency.unknown", "no currency, and none follows from the customer's country",
                      value=order.customer_country)
            return
        order.currency = inferred
        order.log(STAGE, "warning", "currency.inferred",
                  f"no currency given, used {inferred} for a customer in {order.customer_country}")
    if order.placed_date is not None and ctx.reference.rate(order.currency, order.placed_date) is None:
        order.log(STAGE, "error", "currency.no_rate", f"no {order.currency} rate for {order.placed_date:%Y-%m}",
                  value=order.currency)


def check_known_skus(order: Order, ctx: CheckContext) -> None:
    for line in order.lines:
        if line.sku is not None and ctx.reference.list_price(line.sku) is None:
            order.log(STAGE, "error", "sku.unknown", "not in the product catalog", line.locator, line.sku)


def check_quantities(order: Order, ctx: CheckContext) -> None:
    for line in order.lines:
        if line.qty is not None and line.qty <= 0:
            order.log(STAGE, "error", "qty.not_positive", "quantity must be at least 1", line.locator, line.qty)


def check_prices(order: Order, ctx: CheckContext) -> None:
    """Compare each price, in euros, with the list price. A ~100x price was written in cents."""
    rate = ctx.reference.rate(order.currency, order.placed_date)
    for line in order.lines:
        list_price = ctx.reference.list_price(line.sku)
        # A free catalog item has no price ratio to judge, and a zero would divide.
        if not rate or not list_price or line.unit_price is None:
            continue
        ratio = line.unit_price / rate / list_price
        if CENTS_SLIP[0] <= ratio <= CENTS_SLIP[1]:
            fixed = money(line.unit_price / 100)
            order.log(STAGE, "warning", "unit_price.cents",
                      f"{ratio:.0f}x the list price, read as cents: {fixed}", line.locator, line.unit_price)
            line.unit_price = fixed
        elif not PLAUSIBLE[0] <= ratio <= PLAUSIBLE[1]:
            order.log(STAGE, "info", "unit_price.unusual", f"{ratio:.2f}x the list price", line.locator,
                      line.unit_price)


def drop_duplicate_lines(order: Order, ctx: CheckContext) -> None:
    """Identical lines are dropped only when the order's own total says they shouldn't be there.

    A customer can legitimately buy the same thing twice on separate lines, so a repeat alone
    proves nothing. A repeat that is also the exact difference between the lines and the total does.
    """
    unique: list[Line] = []
    repeats: list[Line] = []
    seen: set[tuple[str | None, int | None, Decimal | None]] = set()
    for line in order.lines:
        key = (line.sku, line.qty, line.unit_price)
        (repeats if key in seen else unique).append(line)
        seen.add(key)
    if not repeats or order.total is None or lines_total(unique) != order.total:
        return
    for line in repeats:
        order.log(STAGE, "warning", "lines.duplicate", "repeat of an earlier line, and the total excludes it",
                  line.locator, line.sku)
    order.lines = unique


def reconcile_total(order: Order, ctx: CheckContext) -> None:
    """Lines are the more detailed record, so when the header total disagrees the lines win."""
    computed = lines_total(order.lines)
    if computed is None or computed == order.total:
        return
    if order.total is None:
        order.log(STAGE, "warning", "total.missing", f"no order total, used the sum of the lines: {computed}")
    else:
        order.log(STAGE, "warning", "total.mismatch", f"header says {order.total}, lines add up to {computed}",
                  value=order.total)
    order.total = computed


VALIDATIONS: list[Rule] = [check_not_future, check_known_skus, check_quantities]
REPAIRS: list[Rule] = [resolve_currency, check_prices, drop_duplicate_lines, reconcile_total]


def check_orders(orders: list[Order], ctx: CheckContext) -> None:
    """Validates every order, then repairs the ones still sound. Works in place."""
    for order in orders:
        for rule in VALIDATIONS:
            rule(order, ctx)
        for rule in REPAIRS:
            if order.has_errors:
                break
            rule(order, ctx)
