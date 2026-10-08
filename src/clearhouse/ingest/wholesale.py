"""Wholesale B2B feed: XML, money in cents, buyers identified by CRM code."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .records import RawLine, RawOrder, SourceResult, SourceStats, blank_to_none

LINE_ATTRS = {"sku", "qty", "unitPriceCents", "unitPrice"}
XML_ENCODING = re.compile(rb"""<\?xml[^>]*encoding=["']([\w.-]+)["']""")


def declared_encoding(data: bytes) -> str:
    m = XML_ENCODING.match(data)
    return m[1].decode().lower() if m else "utf-8"


def parse_total(el: ET.Element) -> tuple[str | None, int]:
    if (total_cents := el.findtext("TotalCents")) is not None:
        return blank_to_none(total_cents), 100
    return blank_to_none(el.findtext("Total")), 1


def parse_line(line: ET.Element, loc: str, order_ref: str, result: SourceResult) -> RawLine:
    result.flag_unknown("Line", line.attrib.keys() - LINE_ATTRS)
    if "unitPriceCents" not in line.attrib and "unitPrice" in line.attrib:
        # Older feed version, still sent by some buyers' integrations.
        result.issue("info", "wholesale.price_in_euros", "line uses unitPrice instead of unitPriceCents",
                     loc, order_ref, line.get("unitPrice"))
        price, scale = line.get("unitPrice"), 1
    else:
        price, scale = line.get("unitPriceCents"), 100
    return RawLine(blank_to_none(line.get("sku")), blank_to_none(line.get("qty")), blank_to_none(price), scale, loc)


def parse_order(el: ET.Element, loc: str, file: str, result: SourceResult) -> RawOrder:
    buyer = el.find("Buyer")
    if buyer is None:
        buyer = ET.Element("Buyer")  # an empty Buyer reads as all fields missing
    # The feed has no currency field; every wholesale contract is in euros.
    order = RawOrder("wholesale", file, loc, blank_to_none(el.get("number")), placed_at=blank_to_none(el.get("date")),
                     currency="EUR", customer_code=blank_to_none(buyer.get("code")),
                     customer_name=blank_to_none(buyer.findtext("Company")))
    order.total, order.total_scale = parse_total(el)
    order.lines = [parse_line(line, f"{loc}/Line[{j}]", order.order_ref, result)
                   for j, line in enumerate(el.findall("Lines/Line"))]
    return order


def read_wholesale(path: Path) -> SourceResult:
    data = path.read_bytes()
    result = SourceResult(SourceStats(path.name, "wholesale", declared_encoding(data)))
    try:
        root = ET.fromstring(data)
    except ET.ParseError as e:
        result.issue("error", "wholesale.bad_xml", str(e))
        return result.finish()
    for i, el in enumerate(root.iter("Order")):
        result.orders.append(parse_order(el, f"Order[{i}]", path.name, result))
    return result.finish()
