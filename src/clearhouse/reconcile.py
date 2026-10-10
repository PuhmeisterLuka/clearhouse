"""Reconciliation: decisions that need more than one order at a time. Every step works in place.

  resolve_customers  find each order's customer in the CRM, and take the CRM's spelling of the name
  drop_repeats       a second record of the same order from the same source is a copy
  match_pickups      a till receipt for a click-and-collect web order is a copy of that order
  convert_to_eur     every total in euros, at the rate for the month it was placed
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from .issues import Issue
from .model import Order
from .money import money
from .reference import Reference

STAGE = "reconcile"
REPEAT = "order.repeat"
PICKUP_BY_REF = "order.pickup_by_ref"
PICKUP_BY_SALE = "order.pickup_by_sale"
COPY_CODES = {REPEAT, PICKUP_BY_REF, PICKUP_BY_SALE}  # the issue that marks a record as a copy carries one
PICKUP_WINDOW = timedelta(days=7)  # how long the shop holds a click-and-collect order

type SaleKey = tuple[str | None, str | None, Decimal | None, tuple[tuple[str, str, str], ...]]


def sale_key(order: Order) -> SaleKey:
    """Who bought what, for how much, regardless of which system recorded it or when."""
    items = sorted((str(ln.sku), str(ln.qty), str(ln.unit_price)) for ln in order.lines)
    return order.customer_email, order.currency, order.total, tuple(items)


def same_sale(a: Order, b: Order) -> bool:
    return sale_key(a) == sale_key(b)


def same_record(a: Order, b: Order) -> bool:
    """The same sale, recorded at the same moment for the same account."""
    stamp_a = (a.placed_at, a.placed_date, a.customer_code)
    stamp_b = (b.placed_at, b.placed_date, b.customer_code)
    return same_sale(a, b) and stamp_a == stamp_b


def copy_issue(order: Order) -> Issue:
    """The issue that marked this record as a copy, which says how the copy was found."""
    return next(i for i in order.issues if i.code in COPY_CODES)


def mark_copy(copy: Order, original: Order, code: str, message: str) -> None:
    copy.duplicate_of = original.order_ref
    copy.log(STAGE, "warning", code, message)


def resolve_customers(orders: list[Order], ref: Reference) -> None:
    """Buyer codes and emails identify customers; names don't, since they're typed by hand."""
    by_email = ref.customer_by_email()
    for order in orders:
        if order.customer_code is not None:
            customer = ref.customers.get(order.customer_code)
        elif order.customer_email is not None:
            customer = by_email.get(order.customer_email)
        else:
            continue  # an anonymous walk-in sale
        if customer is None:
            order.log(STAGE, "info", "customer.unknown", "not in the CRM",
                      value=order.customer_code or order.customer_email)
            continue
        order.customer_id = customer.customer_id
        if order.customer_name != customer.name:
            order.log(STAGE, "warning", "customer_name.from_crm", f"{order.customer_name!r} -> {customer.name!r}",
                      value=order.customer_name)
            order.customer_name = customer.name


def drop_repeats(orders: list[Order]) -> None:
    """The first record of an id wins. A later one that differs means nobody can say which is right."""
    first: dict[str, Order] = {}
    for order in orders:
        if order.native_id is None:
            continue
        original = first.setdefault(order.order_ref, order)
        if original is order:
            continue
        if same_record(order, original):
            mark_copy(order, original, REPEAT, f"exact repeat of the record at {original.locator}")
        else:
            original.log(STAGE, "error", "order.conflicting_copies",
                         f"the record at {order.locator} has the same id but different contents")
            mark_copy(order, original, REPEAT, f"same id as {original.locator}, but the contents differ")


def within_pickup_window(receipt: Order, web: Order) -> bool:
    if receipt.placed_at is None or web.placed_at is None:
        return False
    return timedelta(0) <= receipt.placed_at - web.placed_at <= PICKUP_WINDOW


def match_by_ref(receipt: Order, ref: str, waiting: dict[str, Order]) -> None:
    web = waiting.get(ref)
    if web is None:
        receipt.log(STAGE, "info", "pickup.unknown_ref", "Ref isn't a click-and-collect order awaiting pickup",
                    value=ref)
    elif not same_sale(receipt, web):
        receipt.log(STAGE, "info", "pickup.ref_mismatch", f"Ref points at {web.order_ref}, but the sale differs",
                    value=ref)
    else:
        del waiting[ref]
        mark_copy(receipt, web, PICKUP_BY_REF, f"till copy of {web.order_ref}, matched by its Ref")


def match_by_sale(receipt: Order, waiting: dict[str, Order], by_sale: dict[SaleKey, list[str]]) -> None:
    # by_sale is built once, so it can still list web orders an earlier receipt has claimed.
    candidates = [(web_id, waiting[web_id]) for web_id in by_sale.get(sale_key(receipt), [])
                  if web_id in waiting and within_pickup_window(receipt, waiting[web_id])]
    if len(candidates) == 1:
        web_id, web = candidates[0]
        del waiting[web_id]
        mark_copy(receipt, web, PICKUP_BY_SALE, f"till copy of {web.order_ref}, matched by customer, items and timing")
    elif candidates:
        refs = ", ".join(web.order_ref for _, web in candidates)
        receipt.log(STAGE, "info", "pickup.ambiguous", f"could be a copy of any of {refs}, kept as its own sale")


def match_pickups(orders: list[Order]) -> None:
    """The till receipt is the copy: the sale happened online, and counting both doubles the revenue.

    Receipts with a Ref are matched first, so a guess can never take a web order a Ref points at.
    Every receipt still unmatched then gets a match by sale, so a mistyped Ref can't double count one.
    """
    waiting = {o.native_id: o for o in orders if o.native_id is not None
               and o.source == "web" and o.fulfillment == "click_and_collect" and o.duplicate_of is None}
    till = [o for o in orders if o.source == "pos" and o.duplicate_of is None]
    for receipt in till:
        if receipt.ref is not None:
            match_by_ref(receipt, receipt.ref, waiting)

    by_sale: dict[SaleKey, list[str]] = defaultdict(list)
    for web_id, web in waiting.items():
        by_sale[sale_key(web)].append(web_id)
    for receipt in till:
        if receipt.duplicate_of is None and receipt.customer_email is not None:
            match_by_sale(receipt, waiting, by_sale)


def convert_to_eur(orders: list[Order], ref: Reference) -> None:
    for order in orders:
        rate = ref.rate(order.currency, order.placed_date)
        if order.total is not None and rate is not None:
            order.total_eur = money(order.total / rate)


def reconcile(orders: list[Order], ref: Reference) -> None:
    resolve_customers(orders, ref)
    drop_repeats(orders)
    match_pickups(orders)
    convert_to_eur(orders, ref)
