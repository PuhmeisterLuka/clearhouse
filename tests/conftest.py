"""A generated dataset plus its ground truth, shared by the stage tests."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import pytest

from clearhouse import score
from clearhouse.generate.mess import generate
from clearhouse.generate.truth import build_world
from clearhouse.ingest import ingest_dir
from clearhouse.model import Order
from clearhouse.parse import parse_orders


@dataclass
class Truth(score.Truth):
    """The scorer's view of the truth, plus what the stage tests need to hold in-memory orders against it."""

    as_of: date = date(2026, 1, 3)  # the day the exports were generated

    def parse(self) -> list[Order]:
        results, _ = ingest_dir(self.data / "raw")
        return parse_orders(order for r in results for order in r.orders)

    def assert_detected(self, orders: list[Order], kinds: set[str]) -> None:
        """Every injected defect of these kinds shows on its order with one of the codes that signal it."""
        codes = defaultdict(set)
        for o in orders:
            codes[o.order_ref] |= {i.code for i in o.issues}
        for defect in self.ledger:
            if defect["kind"] in kinds:
                assert codes[defect["order_key"]] & score.SIGNALS[defect["kind"]], defect

    def comparable(self, orders: list[Order], recoverable: set[str]) -> list[Order]:
        """Orders whose defects are all ones the stage should have undone. Fails if any such order is missing."""
        expected = {key for key in self.orders if self.kinds[key] <= recoverable}
        found = [o for o in orders if o.order_ref in expected]
        assert {o.order_ref for o in found} == expected
        return found

    def assert_matches(self, order: Order) -> None:
        t = self.orders[order.order_ref]
        assert [(ln.sku, ln.qty, ln.unit_price) for ln in order.lines] == self.lines[order.order_ref]
        assert (order.total, order.currency) == (Decimal(t["total"]), t["currency"])
        assert str(order.placed_date) == t["placed_date"]
        if order.source != "wholesale":
            assert order.placed_at == datetime.fromisoformat(t["placed_at_utc"])

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
