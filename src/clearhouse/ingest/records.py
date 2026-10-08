"""The common raw layer every adapter produces.

Values stay as the source wrote them (strings, unstripped), so later stages can see and log
exactly what was wrong. The only interpretation done here is mapping each format's fields onto
these names and recording what scale prices were written in.
"""

from __future__ import annotations

import codecs
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..issues import Issue, Severity


@dataclass
class RawLine:
    sku: str | None
    qty: str | None
    unit_price: str | None
    price_scale: int  # divide by this to get currency units, 100 when the source writes cents
    locator: str


@dataclass
class RawOrder:
    source: str
    file: str
    locator: str
    native_id: str | None = None
    placed_at: str | None = None
    currency: str | None = None
    total: str | None = None
    total_scale: int = 1
    customer_name: str | None = None
    customer_email: str | None = None
    customer_code: str | None = None
    customer_country: str | None = None
    ref: str | None = None
    fulfillment: str | None = None
    lines: list[RawLine] = field(default_factory=list)

    @property
    def order_ref(self) -> str:
        return f"{self.source}:{self.native_id or '?'}"


@dataclass
class SourceStats:
    file: str
    source: str
    encoding: str
    orders: int = 0
    lines: int = 0


@dataclass
class SourceResult:
    stats: SourceStats
    orders: list[RawOrder] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    unknown_fields: Counter[str] = field(default_factory=Counter)

    def issue(self, severity: Severity, code: str, message: str, locator: str = "",
              order_ref: str | None = None, value: Any = None) -> None:
        self.issues.append(Issue("ingest", severity, code, message, self.stats.source, locator, order_ref,
                                 None if value is None else str(value)))

    def flag_unknown(self, where: str, names: Iterable[str]) -> None:
        """Count fields outside the expected schema, usually the first sign an upstream system changed."""
        self.unknown_fields.update(f"{where}.{name}" for name in names)

    def finish(self) -> SourceResult:
        for name, n in self.unknown_fields.items():
            self.issue("info", f"{self.stats.source}.unknown_field", f"not in the expected schema, seen {n} times",
                       value=name)
        self.stats.orders = len(self.orders)
        self.stats.lines = sum(len(o.lines) for o in self.orders)
        return self


def blank_to_none(value: str | None) -> str | None:
    return None if value is None or not value.strip() else value


def decode(data: bytes, fallbacks: tuple[str, ...] = ()) -> tuple[str, str]:
    """Strict UTF-8 first, since a legacy codepage will 'successfully' decode almost anything."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8"), "utf-8-sig"
    for encoding in ("utf-8", *fallbacks):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1"), "latin-1"
