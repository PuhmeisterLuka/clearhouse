import pytest

from clearhouse.files import read_csv, spreadsheet_safe, write_csv


@pytest.mark.parametrize("value, written", [
    ('=HYPERLINK("http://evil","click")', '\'=HYPERLINK("http://evil","click")'),
    ("@SUM(A1:A9)", "'@SUM(A1:A9)"),
    ("+1 555 0100", "'+1 555 0100"),
    ("-2", "-2"),  # a number, not a formula
    ("Žiga Novak", "Žiga Novak"),
])
def test_formula_like_text_is_neutralised(value, written):
    assert spreadsheet_safe(value) == written


def test_csv_round_trip_keeps_numbers(tmp_path):
    write_csv(tmp_path / "x.csv", ["qty", "name"], [[-2, "=1+1"]])
    assert read_csv(tmp_path / "x.csv") == [{"qty": "-2", "name": "'=1+1"}]
