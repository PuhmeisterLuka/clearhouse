"""Turn the clean world into three messy exports plus a ledger of every defect injected.

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
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .truth import LOCAL_TZ, Order, World, ascii_fold, money, pickup_time

DEFECT_RATE = 0.2

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


@dataclass
class Ledger:
    rows: list[dict] = field(default_factory=list)

    def add(self, kind: str, order: Order, source: str, **detail) -> None:
        self.rows.append({"defect_id": f"D{len(self.rows) + 1:04d}", "kind": kind, "order_key": order.key,
                          "source": source, "expected": EXPECTED.get(kind, "fix"), **detail})

    def quarantined_keys(self) -> set[str]:
        return {r["order_key"] for r in self.rows if r["expected"] == "quarantine"}


def pick_defect(rng: random.Random, weights: dict[str, int]) -> str | None:
    if rng.random() > DEFECT_RATE:
        return None
    return rng.choices(list(weights), list(weights.values()))[0]


def sku_variant(rng: random.Random, sku: str) -> str:
    prefix, num = sku.split("-")
    return rng.choice([sku.lower(), f"{prefix}{num}", f"{prefix}-{int(num)}", f"  {sku} ", f"{prefix.lower()}_{num}"])


def unknown_sku(rng: random.Random, sku: str) -> str:
    # The catalog only uses numbers below 400, so these can never match.
    return f"{sku[:3]}-{rng.randint(400, 999):04d}"


def email_noise(rng: random.Random, email: str) -> str:
    return rng.choice([email.upper(), f" {email}", f"{email}  ", email.title()])


def mojibake(name: str) -> str | None:
    if name.isascii():
        return None
    try:
        return name.encode("utf-8").decode("cp1252")
    except UnicodeDecodeError:
        return None


def eu_number(d: Decimal) -> str:
    """1234.5 -> '1.234,50', how the till's export formats money."""
    whole, frac = f"{d:.2f}".split(".")
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    groups = []
    while whole:
        groups.insert(0, whole[-3:])
        whole = whole[:-3]
    return f"{sign}{'.'.join(groups)},{frac}"


def write_pos(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    rows: list[list[str]] = []
    jobs = [(o.placed_at, o, False) for o in world.orders if o.channel == "pos"]
    jobs += [(pickup_time(o), o, True) for o in world.orders if o.collect_in_store]

    for when, order, is_pickup in sorted(jobs, key=lambda j: j[0]):
        receipt = order.pos_receipt if is_pickup else order.native_id
        c = order.customer
        name, email = (c.name, c.email) if c else ("", "")
        ref = ""
        defect = None
        if is_pickup:
            # The till usually records the web order number, but staff sometimes skip the field.
            has_ref = rng.random() < 0.7
            ref = order.native_id if has_ref else ""
            ledger.add("cross_channel_duplicate", order, "pos", copy_id=receipt, ref_present=has_ref)
        else:
            defect = pick_defect(rng, POS_DEFECTS)
            if defect in ("email_noise", "name_variant") and not c:
                defect = None

        datum = when.astimezone(LOCAL_TZ).strftime("%d.%m.%Y %H:%M")
        total = order.total
        if defect == "bad_date":
            datum = rng.choice(["32.01.2025 10:15", "00.00.0000 00:00", "", "15.13.2025 09:40"])
            ledger.add(defect, order, "pos", value=datum)
        elif defect == "total_mismatch":
            total = order.total + money(rng.choice([1, 5, 10, -2]) * Decimal("1.00"))
            ledger.add(defect, order, "pos", true_total=str(order.total), written=str(total))
        elif defect == "email_noise":
            email = email_noise(rng, email)
            ledger.add(defect, order, "pos", value=email)
        elif defect == "name_variant":
            first, last = name.split(" ", 1)
            name = rng.choice([ascii_fold(name), f"{last} {first}", name.upper()])
            ledger.add(defect, order, "pos", value=name)

        bad_line = rng.randrange(len(order.lines))
        order_rows = []
        for i, ln in enumerate(order.lines):
            sku, qty = ln.sku, ln.qty
            if i == bad_line:
                if defect == "negative_qty":
                    qty = -qty
                    ledger.add(defect, order, "pos", sku=sku, value=qty)
                elif defect == "sku_variant":
                    sku = sku_variant(rng, sku)
                    ledger.add(defect, order, "pos", true_sku=ln.sku, value=sku)
                elif defect == "unknown_sku":
                    sku = unknown_sku(rng, sku)
                    ledger.add(defect, order, "pos", value=sku)
            order_rows.append([receipt, datum, name, email, ref, sku, str(qty), eu_number(ln.unit_price), eu_number(total)])
        if defect == "duplicate_row":
            dup = rng.randrange(len(order_rows))
            order_rows.insert(dup + 1, list(order_rows[dup]))
            ledger.add(defect, order, "pos", sku=order.lines[dup].sku)
        rows += order_rows

    buf = io.StringIO()
    buf.write("VELO SUPPLY d.o.o. - izvoz blagajne\r\n")
    buf.write("Poslovalnica: Ljubljana BTC;Obdobje: 01.01.2025-31.12.2025\r\n\r\n")
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Racun", "Datum", "Kupec", "E-posta", "Ref", "Artikel", "Kolicina", "Cena", "Znesek racuna"])
    w.writerows(rows)
    w.writerow(["", "", "", "", "", "", "", "SKUPAJ", f"{len(rows)} vrstic"])
    path.write_bytes(buf.getvalue().encode("cp1250"))


def web_timestamp(rng: random.Random, order: Order, year_override: int | None = None) -> str | int:
    when = order.placed_at if year_override is None else order.placed_at.replace(year=year_override)
    style = rng.random()
    if style < 0.6:
        return when.strftime("%Y-%m-%dT%H:%M:%SZ")
    if style < 0.85:
        return when.astimezone(LOCAL_TZ).isoformat()
    return int(when.timestamp() * 1000)


def write_web(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    out: list[dict] = []
    retries: list[tuple[int, dict]] = []
    for order in (o for o in world.orders if o.channel == "web"):
        c = order.customer
        defect = None if order.collect_in_store else pick_defect(rng, WEB_DEFECTS)
        name, email, currency = c.name, c.email, order.currency
        total = order.total
        created = web_timestamp(rng, order)

        if defect == "mojibake":
            broken = mojibake(name)
            if broken:
                name = broken
                ledger.add(defect, order, "web", true_name=c.name, value=name)
            else:
                defect = "email_noise"
        if defect == "email_noise":
            email = email_noise(rng, email)
            ledger.add(defect, order, "web", value=email)
        elif defect == "missing_currency":
            currency = rng.choice([None, ""])
            ledger.add(defect, order, "web", country=c.country, true_currency=order.currency)
        elif defect == "total_mismatch":
            total = money(order.total * Decimal("1.1"))
            ledger.add(defect, order, "web", true_total=str(order.total), written=str(total))
        elif defect == "future_date":
            created = web_timestamp(rng, order, year_override=2031)
            ledger.add(defect, order, "web", value=created)

        items = []
        bad_line = rng.randrange(len(order.lines))
        for i, ln in enumerate(order.lines):
            price: str | float = str(ln.unit_price) if rng.random() < 0.5 else float(ln.unit_price)
            item: dict = {"sku": ln.sku, "quantity": ln.qty, "price": price}
            if i == bad_line:
                if defect == "price_cents_error":
                    item["price"] = int(ln.unit_price * 100)
                    ledger.add(defect, order, "web", sku=ln.sku, true_price=str(ln.unit_price), value=item["price"])
                elif defect == "unknown_sku":
                    item["sku"] = unknown_sku(rng, ln.sku)
                    ledger.add(defect, order, "web", value=item["sku"])
                elif defect == "missing_sku":
                    del item["sku"]
                    ledger.add(defect, order, "web", true_sku=ln.sku)
                elif defect == "qty_text":
                    item["quantity"] = rng.choice([f"{ln.qty} pcs", f"{ln.qty}x", f" {ln.qty} "])
                    ledger.add(defect, order, "web", value=item["quantity"])
            items.append(item)

        record = {
            "id": int(order.native_id) if rng.random() < 0.7 else order.native_id,
            "created_at": created,
            "customer": {"name": name, "email": email, "country": c.country},
            "currency": currency,
            "items": items,
            "total": float(total),
            "fulfillment": "click_and_collect" if order.collect_in_store else "ship",
        }
        out.append(record)
        if defect == "duplicate_order":
            # A webhook retry re-sends the same order a bit later in the stream.
            retries.append((len(out) + rng.randint(1, 30), dict(record)))
            ledger.add(defect, order, "web")

    for pos, record in sorted(retries, key=lambda r: r[0], reverse=True):
        out.insert(min(pos, len(out)), record)

    # Shuffle lightly: the API pages by update time, not creation time.
    for i in range(0, len(out) - 1, 2):
        if rng.random() < 0.3:
            out[i], out[i + 1] = out[i + 1], out[i]
    payload = {"export": {"source": "webshop", "generated_at": "2026-01-02T03:00:00Z", "count": len(out)}, "orders": out}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def write_wholesale(world: World, rng: random.Random, ledger: Ledger, path: Path) -> None:
    root = ET.Element("WholesaleExport", supplier="VELO SUPPLY d.o.o.", generated="2026-01-03")
    for order in (o for o in world.orders if o.channel == "wholesale"):
        c = order.customer
        defect = pick_defect(rng, WHOLESALE_DEFECTS)
        date = order.placed_at.astimezone(LOCAL_TZ).strftime("%Y%m%d")
        code = c.customer_id
        total_cents = int(order.total * 100)

        if defect == "bad_date":
            day_first = date[:4] + date[6:] + date[4:6]  # YYYYDDMM, only invalid when the day is above 12
            date = rng.choice([day_first if int(date[6:]) > 12 else "20251340", "2025XXXX", "0"])
            ledger.add(defect, order, "wholesale", value=date)
        elif defect == "buyer_code_noise":
            code = rng.choice([code.lower(), f" {code} ", code.replace("B", "B-")])
            ledger.add(defect, order, "wholesale", value=code)
        elif defect == "total_mismatch":
            total_cents += rng.choice([100, 1000, -500, 99])
            ledger.add(defect, order, "wholesale", true_total=str(order.total), written=str(Decimal(total_cents) / 100))

        el = ET.SubElement(root, "Order", number=order.native_id, date=date)
        buyer = ET.SubElement(el, "Buyer", code=code)
        ET.SubElement(buyer, "Company").text = c.name
        lines = ET.SubElement(el, "Lines")
        bad_line = rng.randrange(len(order.lines))
        for i, ln in enumerate(order.lines):
            attrs = {"sku": ln.sku, "qty": str(ln.qty), "unitPriceCents": str(int(ln.unit_price * 100))}
            if i == bad_line:
                if defect == "sku_variant":
                    attrs["sku"] = sku_variant(rng, ln.sku)
                    ledger.add(defect, order, "wholesale", true_sku=ln.sku, value=attrs["sku"])
                elif defect == "unknown_sku":
                    attrs["sku"] = unknown_sku(rng, ln.sku)
                    ledger.add(defect, order, "wholesale", value=attrs["sku"])
                elif defect == "missing_qty":
                    del attrs["qty"]
                    ledger.add(defect, order, "wholesale", sku=ln.sku)
                elif defect == "euro_price_variant":
                    del attrs["unitPriceCents"]
                    attrs["unitPrice"] = str(ln.unit_price)
                    ledger.add(defect, order, "wholesale", sku=ln.sku, value=attrs["unitPrice"])
            ET.SubElement(lines, "Line", attrs)
        ET.SubElement(el, "TotalCents").text = str(total_cents)
        if defect == "duplicate_order":
            root.append(ET.fromstring(ET.tostring(el)))
            ledger.add(defect, order, "wholesale")

    ET.indent(root)
    ET.ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)


def write_csv(path: Path, header: list[str], rows) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def write_reference(world: World, ref_dir: Path) -> None:
    write_csv(ref_dir / "products.csv", ["sku", "name", "category", "list_price_eur"],
              ((p.sku, p.name, p.category, p.price_eur) for p in world.products))
    write_csv(ref_dir / "crm_customers.csv", ["customer_id", "name", "email", "country", "type"],
              ((c.customer_id, c.name, c.email, c.country, "business" if c.is_business else "consumer")
               for c in world.customers))
    write_csv(ref_dir / "fx_rates.csv", ["month", "currency", "per_eur"],
              ((f"2025-{m:02d}", cur, rate) for (m, cur), rate in sorted(world.fx.items())))


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
    with (truth_dir / "defects.jsonl").open("w", encoding="utf-8") as f:
        for row in ledger.rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def generate(out_dir: Path, world: World, seed: int = 7) -> Ledger:
    rng = random.Random(seed + 1)
    dirs = {name: out_dir / name for name in ("raw", "reference", "truth")}
    for d in dirs.values():
        d.mkdir(parents=True, exist_ok=True)
    ledger = Ledger()
    write_pos(world, rng, ledger, dirs["raw"] / "pos_ljubljana_2025.csv")
    write_web(world, rng, ledger, dirs["raw"] / "webshop_orders_2025.json")
    write_wholesale(world, rng, ledger, dirs["raw"] / "wholesale_2025.xml")
    write_reference(world, dirs["reference"])
    write_truth(world, ledger, dirs["truth"])
    return ledger
