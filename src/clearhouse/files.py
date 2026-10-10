import csv
import json
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def is_number(text: str) -> bool:
    try:
        Decimal(text)
    except InvalidOperation:
        return False
    return True


def spreadsheet_safe(value: Any) -> Any:
    """Text a spreadsheet would run as a formula gets a leading quote; numbers, '-2' included, pass through."""
    if isinstance(value, str) and value.startswith(FORMULA_START) and not is_number(value):
        return "'" + value
    return value


def write_csv(path: Path, header: list[str], rows: Iterable[Iterable[Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows([spreadsheet_safe(value) for value in row] for row in rows)


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    """Decimals, dates and datetimes are written as their string form."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
