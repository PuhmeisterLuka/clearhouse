"""Turn the clean world into three messy exports plus a ledger of every defect injected.

Each order is first copied into a format-neutral Draft. Defect injectors edit drafts, and the
format writers only serialise them, so any defect can be switched on for any source.

Each ledger entry says what a correct pipeline should do with the affected order:
  fix         repair it and keep it; the cleaned values must equal the truth
  quarantine  reject the whole order, it can't be trusted
  dedupe      this is an extra copy of an order that exists elsewhere; drop the copy
"""

from __future__ import annotations

import csv
import io
import json
import random
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..files import write_csv, write_jsonl
from ..text import ascii_fold
from .truth import LOCAL_TZ, YEAR, Order, World

DEFECT_RATE = 0.2
PICKUP_REF_RATE = 0.7  # how often till staff type the web order number into Ref

POS_DEFECTS = {"bad_date": 2, "negative_qty": 2, "sku_variant": 4, "unknown_sku": 2, "duplicate_row": 3,
               "total_mismatch": 3, "email_noise": 3, "name_variant": 2}
WEB_DEFECTS = {"mojibake": 3, "email_noise": 3, "missing_currency": 2, "price_cents_error": 3, "total_mismatch": 2,
               "unknown_sku": 2, "future_date": 2, "duplicate_order": 3, "missing_sku": 2, "qty_text": 3}
WHOLESALE_DEFECTS = {"buyer_code_noise": 3, "sku_variant": 3, "missing_qty": 2, "bad_date": 2, "total_mismatch": 3,
                     "unknown_sku": 2, "euro_price_variant": 3, "duplicate_order": 2}

EXPECTED = {
    "bad_date": "quarantine", "future_date": "quarantine", "negative_qty": "quarantine", "unknown_sku": "quarantine",
    "missing_sku": "quarantine", "missing_qty": "quarantine",
    "duplicate_row": "dedupe", "duplicate_order": "dedupe", "cross_channel_duplicate": "dedupe",
}  # anything not listed is "fix"

BAD_DATES = {
    "pos": ["32.01.2025 10:15", "00.00.0000 00:00", "", "15.13.2025 09:40"],
    "wholesale": ["20251340", "2025XXXX", "0", "20250230"],
}
TOTAL_ERRORS = [Decimal(x) for x in ("1", "5", "10", "-2", "0.99", "12.40")]


# Ledger and drafts

@dataclass
class Ledger:
    rows: list[dict] = field(default_factory=list)

    def add(self, kind: str, order: Order, source: str, **detail: Any) -> None:
        self.rows.append({"defect_id": f"D{len(self.rows) + 1:04d}", "kind": kind, "order_key": order.key,
                          "source": source, "expected": EXPECTED.get(kind, "fix"), **detail})

    def quarantined_keys(self) -> set[str]:
        return {r["order_key"] for r in self.rows if r["expected"] == "quarantine"}


@dataclass
class Draft:
    """One order on its way into an export. Line dicts hold sku, qty and price; a missing key is a missing field."""
    order: Order
    source: str
    native_id: str
    placed_at: datetime
    name: str
    email: str
    code: str
    currency: str | None
    total: Decimal
    lines: list[dict[str, Any]]
    ref: str = ""
    raw_date: str | None = None  # an unparseable date to write instead of placed_at
    copies: int = 1

    @classmethod
    def of(cls, order: Order, source: str) -> Draft:
        c = order.customer
        return cls(order, source, order.native_id, order.placed_at,
                   c.name if c else "", c.email if c else "", c.customer_id if c else "",
                   order.currency, order.total,
                   [{"sku": ln.sku, "qty": ln.qty, "price": ln.unit_price} for ln in order.lines])

    def date_text(self, fmt: str) -> str:
        return self.raw_date if self.raw_date is not None else self.placed_at.astimezone(LOCAL_TZ).strftime(fmt)


# Defect injectors. Each edits a draft and returns ledger details, or None when it doesn't apply.

def inject_sku_variant(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    true_sku = line["sku"]
    prefix, num = true_sku.split("-")
    line["sku"] = rng.choice([true_sku.lower(), f"{prefix}{num}", f"{prefix}-{int(num)}", f"  {true_sku} ",
                              f"{prefix.lower()}_{num}"])
    return {"true_sku": true_sku, "value": line["sku"]}


def inject_unknown_sku(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    # The catalog only uses numbers below 400, so these can never match.
    line["sku"] = f"{line['sku'][:3]}-{rng.randint(400, 999):04d}"
    return {"value": line["sku"]}


def inject_missing_sku(d: Draft, rng: random.Random) -> dict | None:
    return {"true_sku": rng.choice(d.lines).pop("sku")}


def inject_missing_qty(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    del line["qty"]
    return {"sku": line["sku"]}


def inject_negative_qty(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    line["qty"] = -line["qty"]
    return {"sku": line["sku"], "value": line["qty"]}


def inject_qty_text(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    line["qty"] = rng.choice([f"{line['qty']} pcs", f"{line['qty']}x", f" {line['qty']} "])
    return {"value": line["qty"]}


def inject_price_cents_error(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    true_price = line["price"]
    line["price"] = true_price * 100
    return {"sku": line["sku"], "true_price": str(true_price), "value": str(line["price"])}


def inject_euro_price_variant(d: Draft, rng: random.Random) -> dict | None:
    line = rng.choice(d.lines)
    line["price_in_euros"] = True
    return {"sku": line["sku"], "value": str(line["price"])}


def inject_duplicate_row(d: Draft, rng: random.Random) -> dict | None:
    i = rng.randrange(len(d.lines))
    d.lines.insert(i + 1, dict(d.lines[i]))
    return {"sku": d.lines[i]["sku"]}


def inject_duplicate_order(d: Draft, rng: random.Random) -> dict | None:
    d.copies = 2
    return {}


def inject_total_mismatch(d: Draft, rng: random.Random) -> dict | None:
    true_total = d.total
    d.total += rng.choice(TOTAL_ERRORS)
    return {"true_total": str(true_total), "written": str(d.total)}


def inject_bad_date(d: Draft, rng: random.Random) -> dict | None:
    d.raw_date = rng.choice(BAD_DATES[d.source])
    return {"value": d.raw_date}


def inject_future_date(d: Draft, rng: random.Random) -> dict | None:
    d.placed_at = d.placed_at.replace(year=YEAR + 6)
    return {"value": d.placed_at.isoformat()}


def inject_missing_currency(d: Draft, rng: random.Random) -> dict | None:
    true_currency = d.currency
    d.currency = rng.choice([None, ""])
    return {"true_currency": true_currency}


def inject_email_noise(d: Draft, rng: random.Random) -> dict | None:
    if not d.email:
        return None
    d.email = rng.choice([d.email.upper(), f" {d.email}", f"{d.email}  ", d.email.title()])
    return {"value": d.email}


def inject_name_variant(d: Draft, rng: random.Random) -> dict | None:
    if not d.name:
        return None
    first, last = d.name.split(" ", 1)
    d.name = rng.choice([ascii_fold(d.name), f"{last} {first}", d.name.upper()])
    return {"value": d.name}


def inject_mojibake(d: Draft, rng: random.Random) -> dict | None:
    """UTF-8 bytes read back as Windows-1252, the classic 'Å½iga' for 'Žiga'."""
    if d.name.isascii():
        return None
    try:
        broken = d.name.encode("utf-8").decode("cp1252")
    except UnicodeDecodeError:  # some UTF-8 bytes have no cp1252 character
        return None
    true_name, d.name = d.name, broken
    return {"true_name": true_name, "value": broken}


def inject_buyer_code_noise(d: Draft, rng: random.Random) -> dict | None:
    d.code = rng.choice([d.code.lower(), f" {d.code} ", d.code.replace("B", "B-")])
    return {"value": d.code}


INJECTORS: dict[str, Callable[[Draft, random.Random], dict | None]] = {
    "sku_variant": inject_sku_variant,
    "unknown_sku": inject_unknown_sku,
    "missing_sku": inject_missing_sku,
    "missing_qty": inject_missing_qty,
    "negative_qty": inject_negative_qty,
    "qty_text": inject_qty_text,
    "price_cents_error": inject_price_cents_error,
    "euro_price_variant": inject_euro_price_variant,
    "duplicate_row": inject_duplicate_row,
    "duplicate_order": inject_duplicate_order,
    "total_mismatch": inject_total_mismatch,
    "bad_date": inject_bad_date,
    "future_date": inject_future_date,
    "missing_currency": inject_missing_currency,
    "email_noise": inject_email_noise,
    "name_variant": inject_name_variant,
    "mojibake": inject_mojibake,
    "buyer_code_noise": inject_buyer_code_noise,
}


def maybe_inject(d: Draft, rng: random.Random, weights: dict[str, int], ledger: Ledger) -> None:
    if rng.random() > DEFECT_RATE:
        return
    kind = rng.choices(list(weights), list(weights.values()))[0]
    detail = INJECTORS[kind](d, rng)
    if detail is not None:
        ledger.add(kind, d.order, d.source, **detail)


# Till CSV

POS_HEADER = ["Racun", "Datum", "Kupec", "E-posta", "Ref", "Artikel", "Kolicina", "Cena", "Znesek racuna"]


def eu_number(d: Decimal) -> str:
    """1234.5 -> '1.234,50', how the till formats money."""
    whole, frac = f"{abs(d):.2f}".split(".")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{'-' if d < 0 else ''}{'.'.join(groups)},{frac}"


def pickup_draft(order: Order, rng: random.Random, ledger: Ledger) -> Draft:
    """The till's copy of a click-and-collect web order, rung up when the customer collects it."""
    d = Draft.of(order, "pos")
    d.native_id, d.placed_at = order.pos_receipt, order.pickup_at
    has_ref = rng.random() < PICKUP_REF_RATE
    d.ref = order.native_id if has_ref else ""
    ledger.add("cross_channel_duplicate", order, "pos", copy_id=d.native_id, ref_present=has_ref)
    return d


def pos_rows(d: Draft) -> list[list[str]]:
    datum = d.date_text("%d.%m.%Y %H:%M")
    rows = [[d.native_id, datum, d.name, d.email, d.ref, ln.get("sku", ""), str(ln.get("qty", "")),
             eu_number(ln["price"]), eu_number(d.total)] for ln in d.lines]
    return rows * d.copies


def write_pos(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    drafts = []
    for order in world.orders:
        if order.channel == "pos":
            d = Draft.of(order, "pos")
            maybe_inject(d, rng, POS_DEFECTS, ledger)
            drafts.append(d)
        elif order.collect_in_store:
            drafts.append(pickup_draft(order, rng, ledger))
    rows = [row for d in sorted(drafts, key=lambda d: d.placed_at) for row in pos_rows(d)]

    buf = io.StringIO()
    buf.write("VELO SUPPLY d.o.o. - izvoz blagajne\r\n")
    buf.write(f"Poslovalnica: Ljubljana BTC;Obdobje: 01.01.{YEAR}-31.12.{YEAR}\r\n\r\n")
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(POS_HEADER)
    writer.writerows(rows)
    writer.writerow([""] * 7 + ["SKUPAJ", f"{len(rows)} vrstic"])
    path.write_bytes(buf.getvalue().encode("cp1250"))


# Webshop JSON

def web_timestamp(when: datetime, rng: random.Random) -> str | int:
    """The API has changed timestamp style over the years, and old records were never migrated."""
    style = rng.random()
    if style < 0.6:
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    if style < 0.85:
        return when.astimezone(LOCAL_TZ).isoformat()
    return int(when.timestamp() * 1000)


def web_item(line: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    item = {json_key: line[key] for key, json_key in (("sku", "sku"), ("qty", "quantity")) if key in line}
    item["price"] = str(line["price"]) if rng.random() < 0.5 else float(line["price"])
    return item


def web_record(d: Draft, rng: random.Random) -> dict[str, Any]:
    return {
        "id": int(d.native_id) if rng.random() < 0.7 else d.native_id,
        "created_at": d.raw_date if d.raw_date is not None else web_timestamp(d.placed_at, rng),
        "customer": {"name": d.name, "email": d.email, "country": d.order.customer.country},
        "currency": d.currency,
        "items": [web_item(ln, rng) for ln in d.lines],
        "total": float(d.total),
        "fulfillment": "click_and_collect" if d.order.collect_in_store else "ship",
    }


def swap_neighbours(items: list, rng: random.Random, p: float) -> None:
    for i in range(0, len(items) - 1, 2):
        if rng.random() < p:
            items[i], items[i + 1] = items[i + 1], items[i]


def write_web(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    records: list[dict] = []
    retries: list[tuple[int, dict]] = []
    for order in (o for o in world.orders if o.channel == "web"):
        d = Draft.of(order, "web")
        # Pickups stay clean here so their till copy is the only thing to reconcile.
        if not order.collect_in_store:
            maybe_inject(d, rng, WEB_DEFECTS, ledger)
        records.append(web_record(d, rng))
        for _ in range(d.copies - 1):
            # A webhook retry re-sends the same order a bit later in the stream.
            retries.append((len(records) + rng.randint(1, 30), records[-1]))

    for position, record in sorted(retries, key=lambda r: r[0], reverse=True):
        records.insert(min(position, len(records)), record)
    swap_neighbours(records, rng, 0.3)  # the API pages by update time, not creation time

    payload = {"export": {"source": "webshop", "generated_at": f"{YEAR + 1}-01-02T03:00:00Z", "count": len(records)},
               "orders": records}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# Wholesale XML

def cents(amount: Decimal) -> int:
    return int(amount * 100)


def wholesale_line(line: dict[str, Any]) -> dict[str, str]:
    attrs = {key: str(line[key]) for key in ("sku", "qty") if key in line}
    if line.get("price_in_euros"):
        attrs["unitPrice"] = str(line["price"])
    else:
        attrs["unitPriceCents"] = str(cents(line["price"]))
    return attrs


def wholesale_element(d: Draft) -> ET.Element:
    el = ET.Element("Order", number=d.native_id, date=d.date_text("%Y%m%d"))
    buyer = ET.SubElement(el, "Buyer", code=d.code)
    ET.SubElement(buyer, "Company").text = d.name
    lines = ET.SubElement(el, "Lines")
    for line in d.lines:
        ET.SubElement(lines, "Line", wholesale_line(line))
    ET.SubElement(el, "TotalCents").text = str(cents(d.total))
    return el


def write_wholesale(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    root = ET.Element("WholesaleExport", supplier="VELO SUPPLY d.o.o.", generated=f"{YEAR + 1}-01-03")
    for order in (o for o in world.orders if o.channel == "wholesale"):
        d = Draft.of(order, "wholesale")
        maybe_inject(d, rng, WHOLESALE_DEFECTS, ledger)
        root.extend(wholesale_element(d) for _ in range(d.copies))
    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


# Reference data and ground truth

def write_reference(world: World, ref_dir: Path) -> None:
    write_csv(ref_dir / "products.csv", ["sku", "name", "category", "list_price_eur"],
              ((p.sku, p.name, p.category, p.price_eur) for p in world.products))
    write_csv(ref_dir / "crm_customers.csv", ["customer_id", "name", "email", "country", "type"],
              ((c.customer_id, c.name, c.email, c.country, "business" if c.is_business else "consumer")
               for c in world.customers))
    write_csv(ref_dir / "fx_rates.csv", ["month", "currency", "per_eur"],
              ((f"{YEAR}-{month:02d}", currency, rate) for (month, currency), rate in sorted(world.fx.items())))


def write_truth(world: World, ledger: Ledger, truth_dir: Path) -> None:
    quarantined = ledger.quarantined_keys()
    write_csv(truth_dir / "orders.csv",
              ["order_key", "channel", "native_id", "customer_id", "placed_at_utc", "currency", "total", "total_eur",
               "pos_receipt", "expected"],
              ((o.key, o.channel, o.native_id, o.customer.customer_id if o.customer else "",
                o.placed_at.strftime("%Y-%m-%dT%H:%M:%SZ"), o.currency, o.total, world.total_eur(o),
                o.pos_receipt or "", "quarantine" if o.key in quarantined else "clean")
               for o in world.orders))
    write_csv(truth_dir / "lines.csv", ["order_key", "sku", "qty", "unit_price"],
              ((o.key, ln.sku, ln.qty, ln.unit_price) for o in world.orders for ln in o.lines))
    write_jsonl(truth_dir / "defects.jsonl", ledger.rows)


def generate(out_dir: Path, world: World, seed: int = 7) -> Ledger:
    rng = random.Random(seed + 1)
    raw, reference, truth = (out_dir / name for name in ("raw", "reference", "truth"))
    for directory in (raw, reference, truth):
        directory.mkdir(parents=True, exist_ok=True)

    ledger = Ledger()
    write_pos(world, rng, ledger, raw / f"pos_ljubljana_{YEAR}.csv")
    write_web(world, rng, ledger, raw / f"webshop_orders_{YEAR}.json")
    write_wholesale(world, rng, ledger, raw / f"wholesale_{YEAR}.xml")
    write_reference(world, reference)
    write_truth(world, ledger, truth)
    return ledger
