import shutil
from datetime import date

import pytest

from clearhouse.export import write_tables
from clearhouse.files import write_csv
from clearhouse.generate.mess import INJECTORS, generate
from clearhouse.generate.truth import build_world
from clearhouse.pipeline import run_pipeline
from clearhouse.score import SIGNALS, Output, Truth, read_csv, render, score


@pytest.fixture(scope="module")
def out(truth, tmp_path_factory):
    out = tmp_path_factory.mktemp("out")
    write_tables(out, run_pipeline(truth.data, truth.as_of))
    return out


def tampered(out, tmp_path, file, change):
    """A copy of the run's outputs with one CSV rewritten by `change`."""
    copy = tmp_path / "out"
    shutil.copytree(out, copy)
    rows = read_csv(copy / file)
    header = list(rows[0])
    write_csv(copy / file, header, ([row[h] for h in header] for row in change(rows)))
    return Output.load(copy)


def test_the_scorer_knows_how_every_defect_kind_shows_up():
    assert set(SIGNALS) == set(INJECTORS) | {"cross_channel_duplicate"}


def test_a_correct_run_scores_perfectly(truth, out):
    result = score(truth, Output.load(out))
    assert result.problems() == {}
    assert result.right_outcome == result.true_orders
    assert result.defects_handled == result.defects_injected == len(truth.ledger)
    assert "### Problems\n\nNone." in render(result)


def test_dense_defects_still_score_perfectly(tmp_path):
    """Four times the default defect rate, so collisions between defects get their chance to happen."""
    data, out = tmp_path / "data", tmp_path / "out"
    ledger = generate(data, build_world(seed=5), seed=5, defect_rate=0.8)
    write_tables(out, run_pipeline(data, date(2026, 1, 3)))
    result = score(Truth.load(data), Output.load(out))
    assert len(ledger.rows) > 800
    assert result.problems() == {}


def false_alarm(rows):
    return [*rows, {**rows[0], "order_ref": "web:999999", "code": "sku.repaired", "severity": "warning"}]


@pytest.mark.parametrize("file, change, problem", [
    ("quarantine.csv", lambda rows: rows[1:], "lost"),
    ("orders.csv", lambda rows: [*rows, rows[0]], "counted twice"),
    ("orders.csv", lambda rows: [*rows, {**rows[0], "order_ref": "pos:R-1"}], "not real orders"),
    ("duplicates.csv", lambda rows: rows[1:], "copies missed"),
    ("orders.csv", lambda rows: [{**rows[0], "total_eur": "0.01"}, *rows[1:]], "wrong total_eur"),
    ("orders.csv", lambda rows: [{**rows[0], "total_eur": "0.01"}, *rows[1:]], "revenue"),
    ("issues.csv", lambda rows: [r for r in rows if r["code"] != "sku.repaired"], "sku_variant not handled"),
    ("issues.csv", false_alarm, "false alarms"),
])
def test_the_scorer_notices_when_the_output_is_wrong(truth, out, tmp_path, file, change, problem):
    result = score(truth, tampered(out, tmp_path, file, change))
    assert problem in result.problems()
    assert f"**{problem}**" in render(result)
