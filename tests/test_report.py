from collections import Counter
from datetime import date
from decimal import Decimal

from builders import make_line, make_order

from clearhouse.generate.mess import generate
from clearhouse.generate.truth import build_world
from clearhouse.issues import Issue
from clearhouse.pipeline import Run, run_pipeline
from clearhouse.reconcile import REPEAT
from clearhouse.report import render_report


def test_the_report_shows_every_outcome_and_every_set_aside_order(truth):
    run = run_pipeline(truth.data, truth.as_of)
    html = render_report(run)
    for n in Counter(o.status for o in run.orders).values():
        assert f'<div class="kpi-value">{n:,}</div>' in html
    for order in run.with_status("quarantined") + run.with_status("duplicate"):
        assert f"<code>{order.order_ref}</code>" in html


def test_values_from_the_data_are_escaped():
    order = make_order([make_line()], source="web", total_eur=Decimal("10.00"))
    hostile = "<img src=x onerror=alert(1)>"
    order.issues.append(Issue("parse", "warning", "customer_name.repaired", hostile, "web", "orders[0]", "web:1"))
    html = render_report(Run(sources=[], orders=[order], file_issues=[], as_of=date(2026, 1, 3)))
    assert hostile not in html
    assert "&lt;img src=x onerror=alert(1)&gt;" in html


def test_a_file_that_cant_be_read_still_shows_in_the_report(tmp_path):
    generate(tmp_path, build_world(seed=3), seed=3)
    (tmp_path / "raw/wholesale_2025.xml").write_text("<WholesaleExport><Order></WholesaleExport>")
    html = render_report(run_pipeline(tmp_path, date(2026, 1, 3)))
    assert "<code>wholesale.bad_xml</code>" in html
    assert "Wholesale feed" in html  # the source keeps its row, with no records


def test_repairs_leave_out_copy_markers_and_file_warnings():
    order = make_order([make_line()], total_eur=Decimal("10.00"))
    order.log("parse", "warning", "sku.repaired", "'brk1' -> 'BRK-0001'")
    order.log("reconcile", "warning", REPEAT, "exact repeat")
    unclaimed = Issue("ingest", "warning", "ingest.unclaimed_file", "no adapter matches this file", "", "notes.txt")
    run = Run(sources=[], orders=[order], file_issues=[unclaimed], as_of=date(2026, 1, 3))
    assert [i.code for i in run.repairs()] == ["sku.repaired"]
