from datetime import date
from decimal import Decimal

import pytest
from builders import make_line, make_order

from clearhouse.check import CheckContext, check_orders
from clearhouse.reference import Reference, load_reference

REFERENCE = Reference(
    list_prices={"BRK-0001": Decimal("10.00"), "CHN-0002": Decimal("4.00")},
    fx={("2025-03", "USD"): Decimal("1.10")},
    customers={},
)
CTX = CheckContext(REFERENCE, as_of=date(2025, 12, 31))


def checked(o):
    check_orders([o], CTX)
    return {i.code: i for i in o.issues}


# Validations


def test_future_dates_are_rejected():
    assert checked(make_order([make_line()], placed_date=date(2031, 3, 14)))["placed_at.future"].severity == "error"
    assert not checked(make_order([make_line()], placed_date=CTX.as_of))


def test_unknown_skus_and_non_positive_quantities_are_rejected():
    lines = [make_line(sku="BRK-0999", n=0), make_line(qty=0, n=1), make_line(qty=-2, n=2)]
    codes = checked(make_order(lines, total="0"))
    assert {"sku.unknown", "qty.not_positive"} <= set(codes)


# Repairs


def test_missing_currency_is_inferred_from_the_customer_country():
    o = make_order([make_line(price="11.00")], currency=None, customer_country="US")
    assert checked(o)["currency.inferred"].severity == "warning"
    assert o.currency == "USD"


@pytest.mark.parametrize("country", [None, "CH"])
def test_currency_that_cant_be_inferred_is_an_error(country):
    codes = checked(make_order([make_line()], currency=None, customer_country=country))
    assert codes["currency.unknown"].severity == "error"


def test_currency_without_a_rate_for_the_month_is_an_error():
    assert "currency.no_rate" in checked(make_order([make_line()], currency="USD", placed_date=date(2025, 7, 1)))


def test_price_written_in_cents_is_scaled_back():
    o = make_order([make_line(price="1100.00")], total="11.00", currency="USD")  # list 10 EUR at 1.10 USD/EUR
    codes = checked(o)
    assert o.lines[0].unit_price == Decimal("11.00")
    assert "unit_price.cents" in codes and "total.mismatch" not in codes


def test_an_odd_but_not_hundredfold_price_is_flagged_and_kept():
    o = make_order([make_line(price="35.00")])
    assert checked(o)["unit_price.unusual"].severity == "info"
    assert o.lines[0].unit_price == Decimal("35.00")


def test_repeated_line_is_dropped_only_when_the_total_excludes_it():
    lines = [make_line(n=0), make_line(sku="CHN-0002", price="4.00", n=1), make_line(n=2)]
    duplicated = make_order(lines, total="14.00")
    assert "lines.duplicate" in checked(duplicated)
    assert [ln.locator for ln in duplicated.lines] == ["line 0", "line 1"]

    bought_twice = make_order([make_line(n=0), make_line(n=1)])  # total 20.00 includes both
    assert "lines.duplicate" not in checked(bought_twice)
    assert len(bought_twice.lines) == 2


def test_header_total_gives_way_to_the_lines():
    wrong = make_order([make_line(qty=2)], total="25.00")
    assert checked(wrong)["total.mismatch"].value == "25.00"
    assert wrong.total == Decimal("20.00")

    missing = make_order([make_line(qty=2)])
    missing.total = None
    assert "total.missing" in checked(missing)
    assert missing.total == Decimal("20.00")


def test_orders_with_errors_are_not_repaired():
    o = make_order([make_line(qty=-1)], total="10.00")
    codes = checked(o)
    assert "qty.not_positive" in codes and "total.mismatch" not in codes
    assert o.total == Decimal("10.00")


# Against the generated data and its ground truth

QUARANTINES = {"future_date", "negative_qty", "unknown_sku"}
REPAIRS = {"missing_currency", "duplicate_row", "total_mismatch", "price_cents_error"}
RECOVERABLE = REPAIRS | {"sku_variant", "email_noise", "mojibake", "qty_text", "buyer_code_noise", "euro_price_variant"}


@pytest.fixture(scope="module")
def orders(truth):
    orders = truth.parse()
    check_orders(orders, CheckContext(load_reference(truth.data / "reference"), truth.as_of))
    return orders


def test_every_check_level_defect_is_reported_with_the_right_code(truth, orders):
    truth.assert_detected(orders, QUARANTINES | REPAIRS)


def test_rejected_orders_are_exactly_the_ones_the_ledger_quarantines(truth, orders):
    for o in orders:
        expected = truth.orders.get(o.order_ref, {}).get("expected", "clean")  # till copies aren't in the truth
        assert o.has_errors == (expected == "quarantine"), o.order_ref


def test_checked_values_equal_the_truth(truth, orders):
    for order in truth.comparable(orders, RECOVERABLE):
        truth.assert_matches(order)
