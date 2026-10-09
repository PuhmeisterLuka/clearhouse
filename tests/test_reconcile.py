from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from builders import make_line, make_order

from clearhouse.pipeline import run_pipeline
from clearhouse.reconcile import convert_to_eur, drop_repeats, match_pickups, resolve_customers
from clearhouse.reference import CrmCustomer, Reference

REFERENCE = Reference(
    list_prices={},
    fx={("2025-03", "USD"): Decimal("1.10")},
    customers={"C0001": CrmCustomer("C0001", "Žiga Novak", "ziga@x.si"),
               "B001": CrmCustomer("B001", "Alpe Cycles GmbH", "orders@alpe.com")},
)
NOON = datetime(2025, 3, 14, 12, tzinfo=UTC)


def codes(order):
    return {i.code for i in order.issues}


def web_pickup(native_id="100", **fields):
    return make_order([make_line()], source="web", native_id=native_id, customer_email="ziga@x.si",
                      placed_at=NOON, fulfillment="click_and_collect", **fields)


def till_receipt(native_id="R-1", ref=None, after=timedelta(days=1), **fields):
    return make_order([make_line()], source="pos", native_id=native_id, customer_email="ziga@x.si",
                      placed_at=NOON + after, ref=ref, **fields)


# Customers


def test_customers_are_found_by_email_or_code_and_take_the_crm_name():
    by_email = make_order([], customer_email="ziga@x.si", customer_name="NOVAK ŽIGA")
    by_code = make_order([], source="wholesale", customer_code="B001", customer_name="Alpe Cycles GmbH")
    resolve_customers([by_email, by_code], REFERENCE)
    assert (by_email.customer_id, by_email.customer_name) == ("C0001", "Žiga Novak")
    assert "customer_name.from_crm" in codes(by_email)
    assert by_code.customer_id == "B001" and not by_code.issues


def test_unknown_and_anonymous_customers():
    stranger = make_order([], customer_email="who@x.si")
    walk_in = make_order([], source="pos")
    resolve_customers([stranger, walk_in], REFERENCE)
    assert stranger.customer_id is None and "customer.unknown" in codes(stranger)
    assert walk_in.customer_id is None and not walk_in.issues


# Repeats


def test_exact_repeat_is_a_copy_of_the_first_record():
    first, again = web_pickup(), web_pickup(locator="orders[9]")
    drop_repeats([first, again])
    assert (first.status, again.status) == ("clean", "duplicate")
    assert again.duplicate_of == "web:100"


def test_repeat_with_different_contents_quarantines_the_order():
    first, changed = web_pickup(), web_pickup(total="99.00", locator="orders[9]")
    drop_repeats([first, changed])
    assert (first.status, changed.status) == ("quarantined", "duplicate")
    assert "order.conflicting_copies" in codes(first)


# Pickups


def test_till_copy_matched_by_ref():
    web, receipt = web_pickup(), till_receipt(ref="100")
    match_pickups([web, receipt])
    assert receipt.duplicate_of == "web:100" and web.status == "clean"


@pytest.mark.parametrize("receipt, code", [
    (till_receipt(ref="999"), "pickup.unknown_ref"),
    (till_receipt(ref="100", total="55.00"), "pickup.ref_mismatch"),
])
def test_a_ref_that_doesnt_check_out_keeps_the_receipt_as_a_sale(receipt, code):
    match_pickups([web_pickup(), receipt])
    assert receipt.status == "clean" and code in codes(receipt)


def test_till_copy_without_ref_matched_by_customer_items_and_timing():
    web, receipt = web_pickup(), till_receipt()
    match_pickups([web, receipt])
    assert receipt.duplicate_of == "web:100"


@pytest.mark.parametrize("after", [timedelta(days=8), timedelta(hours=-1)])
def test_no_guess_outside_the_pickup_window(after):
    receipt = till_receipt(after=after)
    match_pickups([web_pickup(), receipt])
    assert receipt.status == "clean"


def test_two_equally_good_web_orders_means_no_guess():
    receipt = till_receipt()
    match_pickups([web_pickup("100"), web_pickup("101"), receipt])
    assert receipt.status == "clean" and "pickup.ambiguous" in codes(receipt)


def test_a_ref_claims_its_web_order_before_any_guess_can():
    guessed, referenced = till_receipt("R-1"), till_receipt("R-2", ref="100")
    match_pickups([web_pickup(), guessed, referenced])  # the guess comes first in the file
    assert (guessed.status, referenced.duplicate_of) == ("clean", "web:100")


def test_totals_are_converted_at_that_months_rate():
    usd = make_order([make_line(price="11.00")], currency="USD")
    convert_to_eur([usd], REFERENCE)
    assert usd.total_eur == Decimal("10.00")


# The whole pipeline against the generated data and its ground truth


@pytest.fixture(scope="module")
def orders(truth):
    return run_pipeline(truth.data, truth.as_of).orders


def test_copies_are_exactly_the_ledgers_duplicates(truth, orders):
    assert {(o.order_ref, o.duplicate_of) for o in orders if o.duplicate_of} == truth.expected_copies()


def test_every_true_order_survives_exactly_once_with_the_right_outcome(truth, orders):
    kept = [o for o in orders if o.status != "duplicate"]
    assert sorted(o.order_ref for o in kept) == sorted(truth.orders)
    for o in kept:
        assert o.status == ("quarantined" if truth.orders[o.order_ref]["expected"] == "quarantine" else "clean")


def test_clean_orders_equal_the_truth_including_customer_and_euros(truth, orders):
    for o in orders:
        if o.status != "clean":
            continue
        t = truth.orders[o.order_ref]
        truth.assert_matches(o)
        assert o.customer_id == (t["customer_id"] or None)
        assert o.total_eur == Decimal(t["total_eur"])
