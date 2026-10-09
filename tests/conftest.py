"""A generated dataset plus its ground truth, shared by the stage tests."""

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from clearhouse.generate.mess import generate
from clearhouse.generate.truth import build_world
from clearhouse.ingest import ingest_dir
from clearhouse.model import Order
from clearhouse.parse import BUSINESS_TZ, parse_orders


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@dataclass
class Truth:
    data: Path
    ledger: list[dict]  # every injected defect
    kinds: dict[str, set[str]]  # order key -> defect kinds injected into it
    orders: dict[str, dict[str, str]]
    lines: dict[str, list[tuple[str, int, Decimal]]]
    crm: dict[str, dict[str, str]]
    as_of: date = date(2026, 1, 3)  # the day the exports were generated

    @classmethod
    def load(cls, data: Path) -> "Truth":
        ledger = [json.loads(line) for line in (data / "truth/defects.jsonl").open()]
        kinds = defaultdict(set)
        for row in ledger:
            kinds[row["order_key"]].add(row["kind"])
        lines = defaultdict(list)
        for row in read_csv(data / "truth/lines.csv"):
            lines[row["order_key"]].append((row["sku"], int(row["qty"]), Decimal(row["unit_price"])))
        return cls(data, ledger, kinds,
                   {row["order_key"]: row for row in read_csv(data / "truth/orders.csv")},
                   lines,
                   {row["customer_id"]: row for row in read_csv(data / "reference/crm_customers.csv")})

    def parse(self) -> list[Order]:
        results, _ = ingest_dir(self.data / "raw")
        return parse_orders(order for r in results for order in r.orders)

    def comparable(self, orders: list[Order], recoverable: set[str]) -> list[Order]:
        """Orders whose defects are all ones the stage should have undone. Fails if any such order is missing."""
        expected = {key for key in self.orders if self.kinds[key] <= recoverable}
        found = [o for o in orders if o.order_ref in expected]
        assert {o.order_ref for o in found} == expected
        return found

    def assert_matches(self, order: Order) -> None:
        t = self.orders[order.order_ref]
        moment = datetime.fromisoformat(t["placed_at_utc"])
        assert [(ln.sku, ln.qty, ln.unit_price) for ln in order.lines] == self.lines[order.order_ref]
        assert (order.total, order.currency) == (Decimal(t["total"]), t["currency"])
        assert order.placed_date == moment.astimezone(BUSINESS_TZ).date()
        if order.source != "wholesale":
            assert order.placed_at == moment

        customer = self.crm.get(t["customer_id"])
        if order.source == "wholesale":
            assert order.customer_code == t["customer_id"]
        elif customer is None:
            assert order.customer_email is None  # walk-in sale at the till
        else:
            assert (order.customer_email, order.customer_name) == (customer["email"], customer["name"])


@pytest.fixture(scope="session", params=[3, 11])
def truth(request, tmp_path_factory) -> Truth:
    data = tmp_path_factory.mktemp(f"seed{request.param}")
    generate(data, build_world(seed=request.param), seed=request.param)
    return Truth.load(data)
