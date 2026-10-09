"""Field parsing: validate and clean every raw value in one pass, using each source's format rules."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from ..ingest.records import RawLine, RawOrder
from ..issues import Issue, Severity
from . import fields
from .records import ParsedLine, ParsedOrder

type Parser[T] = Callable[[str], fields.Parsed[T]]

BUSINESS_TZ = ZoneInfo("Europe/Ljubljana")  # where the books are kept, and where the till stands

parse_currency = partial(fields.parse_code, length=3)
parse_country = partial(fields.parse_code, length=2)
parse_fulfillment = partial(fields.parse_choice, choices=("ship", "click_and_collect"))


@dataclass(frozen=True)
class SourceFormat:
    timestamp: Parser[datetime | date]
    decimal_comma: bool

    def amount(self, scale: int) -> Parser[Decimal]:
        return partial(fields.parse_amount, decimal_comma=self.decimal_comma, scale=scale)


FORMATS = {
    "pos": SourceFormat(partial(fields.parse_local_datetime, fmt="%d.%m.%Y %H:%M", tz=BUSINESS_TZ), decimal_comma=True),
    "web": SourceFormat(fields.parse_instant, decimal_comma=False),
    "wholesale": SourceFormat(partial(fields.parse_date, fmt="%Y%m%d"), decimal_comma=False),
}


@dataclass
class FieldReader:
    """Runs parsers over one raw order and logs every repair and failure against it.

    A required field that is missing or invalid makes the order unusable (error). An optional field
    that fails to parse is dropped with a warning, because the order is still sound without it.
    """

    order: RawOrder
    issues: list[Issue] = field(default_factory=list)

    def read[T](self, name: str, raw: str | None, parser: Parser[T], *,
             required: bool = False, locator: str | None = None) -> T | None:
        loc = locator or self.order.locator
        if raw is None:
            if required:
                self.log("error", f"{name}.missing", f"{name} is missing", loc)
            return None
        try:
            parsed = parser(raw)
        except fields.FieldError as e:
            self.log("error" if required else "warning", f"{name}.invalid", str(e), loc, raw)
            return None
        if parsed.repair:
            self.log("warning", f"{name}.repaired", parsed.repair, loc, raw)
        return parsed.value

    def log(self, severity: Severity, code: str, message: str, locator: str, value: Any = None) -> None:
        self.issues.append(Issue("parse", severity, code, message, self.order.source, locator,
                                 self.order.order_ref, value))


def business_date(placed: datetime | date | None) -> date | None:
    if isinstance(placed, datetime):
        return placed.astimezone(BUSINESS_TZ).date()
    return placed


def parse_line(reader: FieldReader, line: RawLine, fmt: SourceFormat) -> ParsedLine:
    loc = line.locator
    return ParsedLine(
        sku=reader.read("sku", line.sku, fields.parse_sku, required=True, locator=loc),
        qty=reader.read("qty", line.qty, fields.parse_quantity, required=True, locator=loc),
        unit_price=reader.read("unit_price", line.unit_price, fmt.amount(line.price_scale), required=True, locator=loc),
        locator=loc,
    )


def parse_order(raw: RawOrder) -> ParsedOrder:
    fmt = FORMATS[raw.source]
    reader = FieldReader(raw, list(raw.issues))  # ingest issues stay with the order
    if not raw.lines:
        reader.log("error", "lines.missing", "order has no lines", raw.locator)
    placed = reader.read("placed_at", raw.placed_at, fmt.timestamp, required=True)
    return ParsedOrder(
        source=raw.source,
        file=raw.file,
        locator=raw.locator,
        native_id=reader.read("native_id", raw.native_id, fields.parse_identifier, required=True),
        placed_at=placed if isinstance(placed, datetime) else None,
        placed_date=business_date(placed),
        currency=reader.read("currency", raw.currency, parse_currency),
        total=reader.read("total", raw.total, fmt.amount(raw.total_scale)),
        customer_name=reader.read("customer_name", raw.customer_name, fields.parse_name),
        customer_email=reader.read("customer_email", raw.customer_email, fields.parse_email),
        customer_code=reader.read("customer_code", raw.customer_code, fields.parse_customer_code),
        customer_country=reader.read("customer_country", raw.customer_country, parse_country),
        ref=reader.read("ref", raw.ref, fields.parse_identifier),
        fulfillment=reader.read("fulfillment", raw.fulfillment, parse_fulfillment),
        lines=[parse_line(reader, line, fmt) for line in raw.lines],
        issues=reader.issues,
    )


def parse_orders(raw_orders: Iterable[RawOrder]) -> list[ParsedOrder]:
    return [parse_order(raw) for raw in raw_orders]
