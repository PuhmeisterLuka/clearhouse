from datetime import date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from clearhouse.ingest.records import RawLine, RawOrder
from clearhouse.issues import Issue
from clearhouse.parse import BUSINESS_TZ, parse_order
from clearhouse.parse.fields import (
    FieldError,
    parse_amount,
    parse_customer_code,
    parse_date,
    parse_email,
    parse_instant,
    parse_local_datetime,
    parse_name,
    parse_quantity,
    parse_sku,
)

# Field parsers


@pytest.mark.parametrize("raw, comma, scale, value, repaired", [
    ("1.234,56", True, 1, "1234.56", False),
    (" 7,00 ", True, 1, "7.00", True),
    ("23.0", False, 1, "23.00", False),
    ("1250", False, 100, "12.50", False),
    ("-2", False, 1, "-2.00", False),
])
def test_amount(raw, comma, scale, value, repaired):
    parsed = parse_amount(raw, decimal_comma=comma, scale=scale)
    assert parsed.value == Decimal(value) and str(parsed.value) == value
    assert bool(parsed.repair) == repaired


@pytest.mark.parametrize("raw, scale", [("12.5", 100), ("abc", 1), ("12.345", 1), ("1,234.50", 1)])
def test_amount_refuses(raw, scale):
    with pytest.raises(FieldError):
        parse_amount(raw, decimal_comma=False, scale=scale)


@pytest.mark.parametrize("raw, value, repaired", [
    ("2", 2, False), ("2 pcs", 2, True), ("3x", 3, True), (" 3 ", 3, True), ("-1", -1, False),
])
def test_quantity(raw, value, repaired):
    parsed = parse_quantity(raw)
    assert (parsed.value, bool(parsed.repair)) == (value, repaired)


@pytest.mark.parametrize("raw", ["2.5", "two", "", "2 boxes"])
def test_quantity_refuses(raw):
    with pytest.raises(FieldError):
        parse_quantity(raw)


@pytest.mark.parametrize("raw", ["brk_0042", "BRK0042", "BRK-42", "  BRK-0042 ", "brk-0042"])
def test_sku_variants_normalise(raw):
    parsed = parse_sku(raw)
    assert parsed.value == "BRK-0042" and parsed.repair


@pytest.mark.parametrize("raw", ["BRAKE-1", "BRK-12345", "42", "BR-0042"])
def test_sku_refuses(raw):
    with pytest.raises(FieldError):
        parse_sku(raw)


def test_canonical_values_are_not_reported_as_repairs():
    assert parse_sku("BRK-0042").repair is None
    assert parse_email("a.b@x.com").repair is None
    assert parse_customer_code("B014").repair is None
    assert parse_name("Žiga Novak").repair is None


def test_email_and_customer_code():
    assert parse_email(" Mia.Smith@Gmail.Com ").value == "mia.smith@gmail.com"
    assert parse_customer_code(" b014 ").value == "B014"
    assert parse_customer_code("B-014").value == "B014"
    for parser, raw in ((parse_email, "not-an-email"), (parse_customer_code, "X12")):
        with pytest.raises(FieldError):
            parser(raw)


@pytest.mark.parametrize("raw, value", [
    ("Å½iga Novak", "Žiga Novak"),
    ("UrÅ¡ka Mlakar", "Urška Mlakar"),
    ("Å\xa0pela Mlakar", "Špela Mlakar"),  # the second byte of Š is a non-breaking space in cp1252
    ("Jakob BabiÄ‡", "Jakob Babić"),
    ("Müller  Gruber", "Müller Gruber"),  # genuine accents survive, spacing is tidied
    ("Tomaž Černe", "Tomaž Černe"),
])
def test_name(raw, value):
    assert parse_name(raw).value == value


def test_timestamps_all_end_up_in_utc():
    expected = datetime(2025, 3, 14, 9, 22, tzinfo=ZoneInfo("UTC"))
    for raw in ("2025-03-14T09:22:00Z", "2025-03-14T10:22:00+01:00", "1741944120000"):
        assert parse_instant(raw).value == expected
    with pytest.raises(FieldError):
        parse_instant("2025-03-14T09:22:00")  # no zone, so the moment is unknown


def test_local_time_respects_daylight_saving():
    winter = parse_local_datetime("26.01.2025 10:00", fmt="%d.%m.%Y %H:%M", tz=BUSINESS_TZ).value
    summer = parse_local_datetime("30.03.2025 10:00", fmt="%d.%m.%Y %H:%M", tz=BUSINESS_TZ).value
    assert (winter.hour, summer.hour) == (9, 8)


@pytest.mark.parametrize("raw", ["32.01.2025 10:15", "00.00.0000 00:00", "15.13.2025 09:40"])
def test_local_time_refuses_impossible_dates(raw):
    with pytest.raises(FieldError):
        parse_local_datetime(raw, fmt="%d.%m.%Y %H:%M", tz=BUSINESS_TZ)


def test_compact_date():
    assert parse_date("20250314", fmt="%Y%m%d").value == date(2025, 3, 14)
    with pytest.raises(FieldError):
        parse_date("20250230", fmt="%Y%m%d")


# Field reader rules


def test_required_fields_fail_the_order_optional_ones_only_warn():
    raw = RawOrder("web", "f.json", "orders[0]", native_id="1", placed_at="2025-01-01T10:00:00Z",
                   customer_email="nope", lines=[RawLine(None, "2", "10.00", 1, "orders[0].items[0]")])
    order = parse_order(raw)
    by_code = {i.code: i for i in order.issues}
    assert by_code["sku.missing"].severity == "error"
    assert by_code["customer_email.invalid"].severity == "warning"
    assert order.customer_email is None and order.has_errors


def test_ingest_issues_carry_over_to_the_parsed_order():
    raw = RawOrder("wholesale", "f.xml", "Order[0]", native_id="WH-1", placed_at="20250101",
                   lines=[RawLine("BRK-0001", "1", "4.10", 1, "Order[0]/Line[0]")])
    raw.issues.append(Issue("ingest", "info", "wholesale.price_in_euros", "line uses unitPrice", "wholesale"))
    assert parse_order(raw).issues[0] == raw.issues[0]


# Against the generated data and its ground truth

QUARANTINES = {"bad_date", "missing_sku", "missing_qty"}
REPAIRS = {"sku_variant", "email_noise", "mojibake", "qty_text", "buyer_code_noise"}
RECOVERABLE = REPAIRS | {"euro_price_variant"}  # parsing alone must recover the true values


@pytest.fixture(scope="module")
def parsed(truth):
    return truth.parse()


def test_every_parse_level_defect_is_reported_with_the_right_code(truth, parsed):
    truth.assert_detected(parsed, QUARANTINES | REPAIRS)


def test_no_order_is_rejected_without_a_reason(truth, parsed):
    for o in parsed:
        if o.has_errors:
            assert truth.kinds[o.order_ref] & QUARANTINES, o.order_ref


def test_parsed_values_equal_the_truth(truth, parsed):
    for order in truth.comparable(parsed, RECOVERABLE):
        truth.assert_matches(order)
