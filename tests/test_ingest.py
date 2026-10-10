import json

import pytest

from clearhouse.generate.mess import generate
from clearhouse.generate.truth import build_world
from clearhouse.ingest import ingest_dir
from clearhouse.ingest.pos import read_pos
from clearhouse.ingest.web import read_web
from clearhouse.ingest.wholesale import read_wholesale
from clearhouse.score import COPY_KINDS


def issues(result):
    return result.issues + [i for order in result.orders for i in order.issues]


def codes(result):
    return [i.code for i in issues(result)]


POS_CSV = (
    "Some till export\r\n"
    "\r\n"
    "Artikel;Račun;Datum;Kupec;E-posta;Ref;Kolicina;Cena;Znesek racuna;Blagajnik\r\n"
    "BRK-0001;R-1;01.02.2025 10:00;Žiga Černe;ziga@x.si;;2;10,50;21,00;Mojca\r\n"
    "CHN-0002;R-1;01.02.2025 10:00;Žiga Černe;ziga@x.si;;1;5,00;26,00;Mojca\r\n"
    ";;;;;;;;;\r\n"
    "TYR-0003;R-2;01.02.2025 11:00;;;100234;1;7,00;7,00\r\n"
    ";;;;;;;SKUPAJ;4 vrstic;\r\n"
)


def test_pos_finds_header_by_name_and_groups_rows(tmp_path):
    path = tmp_path / "pos.csv"
    path.write_bytes(POS_CSV.encode("cp1250"))
    result = read_pos(path)

    assert result.stats.encoding == "cp1250"
    r1, r2 = result.orders
    assert r1.native_id == "R-1" and r1.customer_name == "Žiga Černe"
    assert [ln.sku for ln in r1.lines] == ["BRK-0001", "CHN-0002"]
    assert r1.lines[0].unit_price == "10,50"  # still raw, cleaning happens later
    assert r2.ref == "100234" and r2.customer_email is None

    # R-1's second row disagrees on the total, R-2 is one cell short, and there's an extra column.
    assert "pos.header_disagrees" in codes(result)
    assert "pos.row_width" in codes(result)
    assert "pos.unknown_column" in codes(result)
    assert "pos.footer_count" in codes(result)  # footer claims 4 rows, there are 3


def test_pos_reads_utf8_with_commas(tmp_path):
    path = tmp_path / "pos.csv"
    path.write_text("Racun,Datum,Kupec,Artikel,Kolicina,Cena\nR-9,02.02.2025 09:00,Špela,LGT-0001,1,\"3,20\"\n",
                    encoding="utf-8")
    result = read_pos(path)
    assert result.stats.encoding == "utf-8"
    assert result.orders[0].customer_name == "Špela"
    assert result.orders[0].lines[0].unit_price == "3,20"


def test_pos_without_header_is_an_error(tmp_path):
    path = tmp_path / "pos.csv"
    path.write_text("a;b;c\n1;2;3\n")
    assert codes(read_pos(path)) == ["pos.no_header"]


def test_web_keeps_number_literals_and_survives_bad_entries(tmp_path):
    path = tmp_path / "web.json"
    path.write_text(json.dumps({
        "export": {"count": 4},
        "orders": [
            {"id": 7, "created_at": 1736000000000, "total": 23.0, "currency": "EUR",
             "customer": {"name": "Ana", "email": "a@x.si"},
             "items": [{"sku": "BRK-0001", "quantity": "2 pcs", "price": 11.5}, "oops", {"quantity": 1}],
             "coupon": "SPRING"},
            "not an order",
            {"id": "8", "customer": "Bob", "items": None},
        ],
    }))
    result = read_web(path)

    a, b = result.orders
    assert a.native_id == "7" and a.placed_at == "1736000000000" and a.total == "23.0"
    assert [(ln.sku, ln.qty, ln.unit_price) for ln in a.lines] == [("BRK-0001", "2 pcs", "11.5"), (None, "1", None)]
    assert b.customer_name is None and b.lines == []
    assert set(codes(result)) == {"web.count_mismatch", "web.bad_item", "web.not_an_object", "web.bad_customer",
                                  "web.no_items", "web.unknown_field"}


def test_web_bad_json(tmp_path):
    path = tmp_path / "web.json"
    path.write_text('{"orders": [ {"id": 1,, ] }')
    assert codes(read_web(path)) == ["web.bad_json"]


def test_wholesale_handles_both_price_attributes(tmp_path):
    path = tmp_path / "wh.xml"
    path.write_text("""<?xml version="1.0" encoding="utf-8"?>
<WholesaleExport>
  <Order number="WH-1" date="20250301">
    <Buyer code=" b001 "><Company>Alpe Cycles GmbH</Company></Buyer>
    <Lines>
      <Line sku="BRK-0001" qty="10" unitPriceCents="1250"/>
      <Line sku="CHN-0002" unitPrice="4.10" colour="red"/>
    </Lines>
    <TotalCents>16600</TotalCents>
  </Order>
</WholesaleExport>""")
    result = read_wholesale(path)
    (order,) = result.orders
    assert order.customer_code == " b001 " and order.total == "16600" and order.total_scale == 100
    first, second = order.lines
    assert (first.unit_price, first.price_scale) == ("1250", 100)
    assert (second.qty, second.unit_price, second.price_scale) == (None, "4.10", 1)
    assert set(codes(result)) == {"wholesale.price_in_euros", "wholesale.unknown_field"}


def test_wholesale_bad_xml(tmp_path):
    path = tmp_path / "wh.xml"
    path.write_text("<WholesaleExport><Order></WholesaleExport>")
    assert codes(read_wholesale(path)) == ["wholesale.bad_xml"]


@pytest.fixture(scope="module")
def generated(tmp_path_factory):
    out = tmp_path_factory.mktemp("data")
    world = build_world(seed=11)
    ledger = generate(out, world, seed=11)
    return out, world, ledger


def test_every_true_order_and_every_extra_copy_is_read(generated):
    out, world, ledger = generated
    (out / "raw" / "notes.txt").write_text("someone dropped this here")
    results, stray = ingest_dir(out / "raw")

    copies = sum(r["kind"] in COPY_KINDS for r in ledger.rows)
    assert sum(r.stats.orders for r in results) == len(world.orders) + copies
    assert [i.code for i in stray] == ["ingest.unclaimed_file"]

    # The generator never breaks file structure, so nothing at error level should come out of ingestion.
    assert not [i for r in results for i in issues(r) if i.severity == "error"]
