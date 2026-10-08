import csv
import json

from clearhouse.generate.mess import eu_number, generate
from clearhouse.generate.truth import build_world


def test_same_seed_gives_identical_files(tmp_path):
    for name in ("a", "b"):
        generate(tmp_path / name, build_world(seed=3), seed=3)
    for rel in ("raw/pos_ljubljana_2025.csv", "raw/webshop_orders_2025.json", "raw/wholesale_2025.xml",
                "truth/defects.jsonl"):
        assert (tmp_path / "a" / rel).read_bytes() == (tmp_path / "b" / rel).read_bytes()


def test_ledger_points_at_real_orders(tmp_path):
    generate(tmp_path, build_world(seed=3), seed=3)
    with (tmp_path / "truth/orders.csv").open() as f:
        truth = {row["order_key"]: row for row in csv.DictReader(f)}
    defects = [json.loads(line) for line in (tmp_path / "truth/defects.jsonl").open()]
    assert defects
    for d in defects:
        assert d["order_key"] in truth
        if d["expected"] == "quarantine":
            assert truth[d["order_key"]]["expected"] == "quarantine"


def test_pos_export_is_not_utf8(tmp_path):
    generate(tmp_path, build_world(seed=3), seed=3)
    raw = (tmp_path / "raw/pos_ljubljana_2025.csv").read_bytes()
    assert "Račun" not in raw.decode("cp1250")  # headers are ascii, names aren't
    assert any(ch in raw.decode("cp1250") for ch in "ČŠŽčšž")


def test_eu_number():
    from decimal import Decimal
    assert eu_number(Decimal("1234567.5")) == "1.234.567,50"
    assert eu_number(Decimal("-12.3")) == "-12,30"
    assert eu_number(Decimal("0.07")) == "0,07"
