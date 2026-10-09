from collections import Counter
from datetime import date
from decimal import Decimal

from builders import make_line, make_order

from clearhouse.issues import Issue
from clearhouse.pipeline import Run, run_pipeline
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
