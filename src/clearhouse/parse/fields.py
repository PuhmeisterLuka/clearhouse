"""Reusable field parsers.

Each parser takes the raw string an adapter produced and either returns a typed value, with a
note when it had to repair something to get there, or raises FieldError when the value can't be
interpreted safely. Parsers know nothing about sources; source formats pick and configure them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from ..money import CENT
from ..text import email_key

AMOUNT = re.compile(r"^-?\d+(\.\d+)?$")
EU_AMOUNT = re.compile(r"^-?(\d{1,3}(\.\d{3})+|\d+)(,\d+)?$")  # a dot only ever separates thousands
QUANTITY = re.compile(r"^\s*(-?\d+)\s*(pcs|pc|x|kos)?\s*$", re.IGNORECASE)
SKU = re.compile(r"^([A-Z]{3})[-_ ]?(\d{1,4})$")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$")
CUSTOMER_CODE = re.compile(r"^([BC])[-\s]?(\d{3,4})$")
EPOCH_MS = re.compile(r"^\d{13}$")
EXAMPLE_MOMENT = datetime(2025, 12, 31, 14, 30)  # shows a date format in error messages


class FieldError(ValueError):
    """The raw value can't be interpreted without guessing."""


@dataclass(frozen=True)
class Parsed[T]:
    value: T
    repair: str | None = None  # what had to change to get a valid value


def note_change(raw: str, canonical: str) -> str | None:
    return None if raw == canonical else f"{raw!r} -> {canonical!r}"


def parse_identifier(raw: str) -> Parsed[str]:
    value = raw.strip()
    return Parsed(value, note_change(raw, value))


def parse_choice(raw: str, choices: tuple[str, ...]) -> Parsed[str]:
    value = raw.strip().lower()
    if value not in choices:
        raise FieldError(f"expected one of {', '.join(choices)}")
    return Parsed(value, note_change(raw, value))


def parse_code(raw: str, length: int) -> Parsed[str]:
    """Upper-case letter codes such as countries (2) and currencies (3)."""
    value = raw.strip().upper()
    if len(value) != length or not value.isalpha():
        raise FieldError(f"expected a {length}-letter code")
    return Parsed(value, note_change(raw, value))


def parse_amount(raw: str, *, decimal_comma: bool, scale: int) -> Parsed[Decimal]:
    """'1.234,56' or '1234.56', or whole cents when scale is 100."""
    text = raw.strip()
    if decimal_comma:
        # '12.50' isn't a thousands grouping, so reading it as 1250 would be a guess.
        if not EU_AMOUNT.match(text):
            raise FieldError("not a number like 1.234,56")
        text = text.replace(".", "").replace(",", ".")
    elif not AMOUNT.match(text):
        raise FieldError("not a number")
    if scale != 1 and "." in text:
        raise FieldError(f"expected a whole number of 1/{scale} units")
    value = Decimal(text) / scale
    if value != value.quantize(CENT):
        raise FieldError("more than two decimal places")
    return Parsed(value.quantize(CENT), "trimmed whitespace" if raw != raw.strip() else None)


def parse_quantity(raw: str) -> Parsed[int]:
    """Whole units. A trailing unit word ('2 pcs', '2x') is dropped; '2.5' is refused."""
    m = QUANTITY.match(raw)
    if not m:
        raise FieldError("not a whole quantity")
    return Parsed(int(m[1]), note_change(raw, m[1]))


def parse_sku(raw: str) -> Parsed[str]:
    """Canonical form is ABC-0042. Case, separators and zero padding are fixable; anything else isn't."""
    m = SKU.match(raw.strip().upper())
    if not m:
        raise FieldError("not a SKU like ABC-0042")
    value = f"{m[1]}-{int(m[2]):04d}"
    return Parsed(value, note_change(raw, value))


def parse_email(raw: str) -> Parsed[str]:
    value = email_key(raw)
    if not EMAIL.match(value):
        raise FieldError("not an email address")
    return Parsed(value, note_change(raw, value))


def parse_customer_code(raw: str) -> Parsed[str]:
    m = CUSTOMER_CODE.match(raw.strip().upper())
    if not m:
        raise FieldError("not a customer code like B014")
    value = f"{m[1]}{m[2]}"
    return Parsed(value, note_change(raw, value))


def fix_mojibake(text: str) -> str:
    """Undo UTF-8 that was decoded as Windows-1252 ('Å½iga' -> 'Žiga').

    A genuine name almost never survives this round trip: its accented letters would need to be
    followed by the exact continuation bytes UTF-8 expects. A successful decode is the signal.
    """
    if text.isascii():
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def parse_name(raw: str) -> Parsed[str]:
    # Mojibake first: 'Š' breaks into 'Å' plus a non-breaking space, which whitespace tidying would destroy.
    value = " ".join(fix_mojibake(raw).split())
    return Parsed(value, note_change(raw, value))


def parse_local_datetime(raw: str, *, fmt: str, tz: ZoneInfo) -> Parsed[datetime]:
    """A wall-clock time in a known zone, returned in UTC."""
    try:
        local = datetime.strptime(raw.strip(), fmt)
    except ValueError as e:
        raise FieldError(f"not a date like {EXAMPLE_MOMENT:{fmt}}") from e
    return Parsed(local.replace(tzinfo=tz).astimezone(UTC))


def parse_instant(raw: str) -> Parsed[datetime]:
    """ISO 8601 with a zone ('Z' or an offset), or epoch milliseconds. Returned in UTC."""
    text = raw.strip()
    if EPOCH_MS.match(text):
        return Parsed(datetime(1970, 1, 1, tzinfo=UTC) + timedelta(milliseconds=int(text)))
    try:
        moment = datetime.fromisoformat(text)
    except ValueError as e:
        raise FieldError("not an ISO 8601 timestamp or epoch milliseconds") from e
    if moment.tzinfo is None:
        raise FieldError("timestamp has no time zone, so the moment is ambiguous")
    return Parsed(moment.astimezone(UTC))


def parse_date(raw: str, *, fmt: str) -> Parsed[date]:
    try:
        return Parsed(datetime.strptime(raw.strip(), fmt).date())
    except ValueError as e:
        raise FieldError(f"not a date like {EXAMPLE_MOMENT:{fmt}}") from e
