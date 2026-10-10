"""Grade a pipeline run against the generator's ground truth.

The scorer reads files only: the truth and defect ledger from data/truth, the CRM from data/reference,
and the CSVs a run wrote to out/. It never imports pipeline code, so it judges what was delivered
rather than how it was produced.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Self

from .files import read_csv, read_jsonl
from .money import euros

# The issue codes that show the pipeline noticed each kind of injected defect.
SIGNALS: dict[str, set[str]] = {
    "bad_date": {"placed_at.invalid", "placed_at.missing"},
    "future_date": {"placed_at.future"},
    "negative_qty": {"qty.not_positive"},
    "unknown_sku": {"sku.unknown"},
    "missing_sku": {"sku.missing"},
    "missing_qty": {"qty.missing"},
    "sku_variant": {"sku.repaired"},
    "email_noise": {"customer_email.repaired"},
    "mojibake": {"customer_name.repaired"},
    "qty_text": {"qty.repaired"},
    "buyer_code_noise": {"customer_code.repaired"},
    "euro_price_variant": {"wholesale.price_in_euros"},
    "missing_currency": {"currency.inferred"},
    "price_cents_error": {"unit_price.cents"},
    "total_mismatch": {"total.mismatch"},
    "name_variant": {"customer_name.from_crm"},
    "duplicate_row": {"lines.duplicate"},
    "duplicate_order": {"order.repeat"},
    "cross_channel_duplicate": {"order.pickup_by_ref", "order.pickup_by_sale"},
}
COPY_KINDS = {"duplicate_order", "cross_channel_duplicate"}  # defects that add a whole extra record
FIELDS = ("placed", "customer_id", "customer_name", "currency", "total", "total_eur", "lines")

type LineKey = tuple[str, int, Decimal]
type Defect = dict[str, Any]  # one row of the generator's defect ledger


def decimal(text: str) -> Decimal | None:
    return Decimal(text) if text else None


def group_lines(rows: list[dict[str, str]], key: str) -> dict[str, list[LineKey]]:
    lines: dict[str, list[LineKey]] = defaultdict(list)
    for row in rows:
        lines[row[key]].append((row["sku"], int(row["qty"]), Decimal(row["unit_price"])))
    return lines


def flagged_ref(defect: Defect) -> str:
    """The record whose issues should show the defect: the till receipt for a pickup copy, else the order."""
    return f"{defect['source']}:{defect['copy_id']}" if "copy_id" in defect else defect["order_key"]


@dataclass
class Truth:
    data: Path
    ledger: list[Defect]
    kinds: dict[str, set[str]]  # order key -> defect kinds injected into it
    orders: dict[str, dict[str, str]]
    lines: dict[str, list[LineKey]]
    crm: dict[str, dict[str, str]]

    @classmethod
    def load(cls, data: Path) -> Self:
        ledger = read_jsonl(data / "truth/defects.jsonl")
        kinds: dict[str, set[str]] = defaultdict(set)
        for defect in ledger:
            kinds[defect["order_key"]].add(defect["kind"])
        return cls(data, ledger, kinds,
                   {row["order_key"]: row for row in read_csv(data / "truth/orders.csv")},
                   group_lines(read_csv(data / "truth/lines.csv"), "order_key"),
                   {row["customer_id"]: row for row in read_csv(data / "reference/crm_customers.csv")})

    def expected_copies(self) -> set[tuple[str, str]]:
        """(copy, original) pairs a correct run removes as duplicate records."""
        return {(flagged_ref(d), d["order_key"]) for d in self.ledger if d["kind"] in COPY_KINDS}

    def compare(self, row: dict[str, str], lines: list[LineKey]) -> dict[str, bool]:
        """Field by field, does a clean output order match the true order with the same ref?"""
        t = self.orders[row["order_ref"]]
        same_moment = not row["placed_at"] or (
            datetime.fromisoformat(row["placed_at"]) == datetime.fromisoformat(t["placed_at_utc"]))
        return {
            "placed": row["placed_date"] == t["placed_date"] and same_moment,
            "customer_id": row["customer_id"] == t["customer_id"],
            "customer_name": row["customer_name"] == self.crm.get(t["customer_id"], {}).get("name", ""),
            "currency": row["currency"] == t["currency"],
            "total": decimal(row["total"]) == Decimal(t["total"]),
            "total_eur": decimal(row["total_eur"]) == Decimal(t["total_eur"]),
            "lines": lines == self.lines[row["order_ref"]],
        }


@dataclass
class Output:
    clean: list[dict[str, str]]
    lines: dict[str, list[LineKey]]
    quarantined: list[dict[str, str]]
    copies: set[tuple[str, str]]  # (copy, original)
    issues: list[dict[str, str]]

    @classmethod
    def load(cls, out: Path) -> Self:
        return cls(read_csv(out / "orders.csv"),
                   group_lines(read_csv(out / "lines.csv"), "order_ref"),
                   read_csv(out / "quarantine.csv"),
                   {(row["order_ref"], row["duplicate_of"]) for row in read_csv(out / "duplicates.csv")},
                   read_csv(out / "issues.csv"))

    def outcomes(self) -> dict[str, list[str]]:
        """Every outcome each ref was given. A correct run gives each one exactly one."""
        outcomes: dict[str, list[str]] = defaultdict(list)
        for row in self.clean:
            outcomes[row["order_ref"]].append("clean")
        for row in self.quarantined:
            outcomes[row["order_ref"]].append("quarantined")
        return outcomes

    def codes(self) -> dict[str, set[str]]:
        codes: dict[str, set[str]] = defaultdict(set)
        for issue in self.issues:
            codes[issue["order_ref"]].add(issue["code"])
        return codes


@dataclass
class KindResult:
    expected: str  # fix, quarantine or dedupe, as the ledger says
    injected: int = 0
    misses: list[str] = field(default_factory=list)  # refs where the defect wasn't handled

    @property
    def handled(self) -> int:
        return self.injected - len(self.misses)


@dataclass
class Score:
    true_orders: int
    right_outcome: int
    lost: list[str]  # true orders in neither the clean nor the quarantine output
    wrong_outcome: list[str]  # good orders quarantined, or bad ones passed as clean
    counted_twice: list[str]
    extra: list[str]  # output records that aren't true orders at all
    copies_expected: int
    copies_missed: list[str]
    copies_wrong: list[str]  # removed but not a copy, or paired with the wrong original
    defects: dict[str, KindResult]
    false_alarms: list[str]  # errors and warnings with no defect behind them
    compared: int  # clean orders compared field by field
    field_misses: dict[str, list[str]]  # field -> refs where it differs from the truth
    revenue_true: Decimal
    revenue_reported: Decimal

    @property
    def defects_injected(self) -> int:
        return sum(r.injected for r in self.defects.values())

    @property
    def defects_handled(self) -> int:
        return sum(r.handled for r in self.defects.values())

    def problems(self) -> dict[str, list[str]]:
        """Everything that went wrong, by name. Empty means a perfect run."""
        found = {"lost": self.lost, "wrong outcome": self.wrong_outcome, "counted twice": self.counted_twice,
                 "not real orders": self.extra, "copies missed": self.copies_missed,
                 "wrongly removed as copies": self.copies_wrong, "false alarms": self.false_alarms}
        found |= {f"{kind} not handled": r.misses for kind, r in self.defects.items()}
        found |= {f"wrong {name}": refs for name, refs in self.field_misses.items()}
        if self.revenue_reported != self.revenue_true:
            found["revenue"] = [f"reported {euros(self.revenue_reported)}, true {euros(self.revenue_true)}"]
        return {name: items for name, items in found.items() if items}


def judge_outcomes(truth: Truth, outcomes: dict[str, list[str]]) -> tuple[int, list[str], list[str], list[str]]:
    right, lost, wrong, twice = 0, [], [], []
    for ref, true_order in truth.orders.items():
        expected = "quarantined" if true_order["expected"] == "quarantine" else "clean"
        got = outcomes.get(ref, [])
        if not got:
            lost.append(ref)
        elif len(got) > 1:
            twice.append(ref)
        elif got[0] == expected:
            right += 1
        else:
            wrong.append(ref)
    return right, lost, wrong, twice


def judge_defect(defect: Defect, output: Output, outcomes: dict[str, list[str]], codes: dict[str, set[str]],
                 comparisons: dict[str, dict[str, bool]]) -> bool:
    """Handled means noticed with the right code and given the right treatment, with nothing else off."""
    if defect["kind"] in COPY_KINDS:
        return (flagged_ref(defect), defect["order_key"]) in output.copies
    ref = defect["order_key"]
    noticed = bool(codes[ref] & SIGNALS[defect["kind"]])
    if defect["expected"] == "quarantine":
        return noticed and outcomes.get(ref) == ["quarantined"]
    return noticed and outcomes.get(ref) == ["clean"] and all(comparisons[ref].values())


def judge_defects(truth: Truth, output: Output, outcomes: dict[str, list[str]],
                  comparisons: dict[str, dict[str, bool]]) -> dict[str, KindResult]:
    codes = output.codes()
    results: dict[str, KindResult] = {}
    for defect in truth.ledger:
        result = results.setdefault(defect["kind"], KindResult(defect["expected"]))
        result.injected += 1
        if not judge_defect(defect, output, outcomes, codes, comparisons):
            result.misses.append(defect["order_key"])
    return dict(sorted(results.items()))


def find_false_alarms(truth: Truth, output: Output) -> list[str]:
    explained = {(flagged_ref(d), code) for d in truth.ledger for code in SIGNALS[d["kind"]]}
    return sorted({f"{i['order_ref']} {i['code']}" for i in output.issues
                   if i["order_ref"] and i["severity"] != "info" and (i["order_ref"], i["code"]) not in explained})


def pairs(copies: set[tuple[str, str]]) -> list[str]:
    return sorted(f"{copy} -> {original}" for copy, original in copies)


def score(truth: Truth, output: Output) -> Score:
    outcomes = output.outcomes()
    comparisons = {row["order_ref"]: truth.compare(row, output.lines.get(row["order_ref"], []))
                   for row in output.clean if row["order_ref"] in truth.orders}
    right, lost, wrong, twice = judge_outcomes(truth, outcomes)
    expected_copies = truth.expected_copies()
    return Score(
        true_orders=len(truth.orders),
        right_outcome=right,
        lost=lost,
        wrong_outcome=wrong,
        counted_twice=twice,
        extra=sorted(ref for ref in outcomes if ref not in truth.orders),
        copies_expected=len(expected_copies),
        copies_missed=pairs(expected_copies - output.copies),
        copies_wrong=pairs(output.copies - expected_copies),
        defects=judge_defects(truth, output, outcomes, comparisons),
        false_alarms=find_false_alarms(truth, output),
        compared=len(comparisons),
        field_misses={name: [ref for ref, c in comparisons.items() if not c[name]] for name in FIELDS},
        revenue_true=sum((Decimal(t["total_eur"]) for t in truth.orders.values() if t["expected"] == "clean"),
                         Decimal(0)),
        revenue_reported=sum((decimal(row["total_eur"]) or Decimal(0) for row in output.clean), Decimal(0)),
    )


# Rendering

def count(n: int, of: int) -> str:
    return f"{n} / {of}"


def summary_rows(s: Score) -> list[tuple[str, str]]:
    field_matches = sum(s.compared - len(refs) for refs in s.field_misses.values())
    return [
        ("True orders given the right outcome, clean or quarantined", count(s.right_outcome, s.true_orders)),
        ("True orders lost", str(len(s.lost))),
        ("Orders counted more than once", str(len(s.counted_twice))),
        ("Output records that aren't real orders", str(len(s.extra))),
        ("Duplicate copies removed and paired with their original",
         count(s.copies_expected - len(s.copies_missed), s.copies_expected)),
        ("Records wrongly removed as copies", str(len(s.copies_wrong))),
        ("Injected defects handled as expected", count(s.defects_handled, s.defects_injected)),
        ("Errors and warnings with no defect behind them", str(len(s.false_alarms))),
        ("Fields on clean orders matching the truth", count(field_matches, s.compared * len(FIELDS))),
        ("Clean revenue in EUR, reported vs true", f"{euros(s.revenue_reported)} vs {euros(s.revenue_true)}"),
    ]


def render(s: Score) -> str:
    out = ["## Score", "", "| Check | Result |", "|---|---|"]
    out += [f"| {label} | {value} |" for label, value in summary_rows(s)]
    out += ["", "### By defect kind", "", "| Defect | Expected handling | Injected | Handled |", "|---|---|--:|--:|"]
    out += [f"| `{kind}` | {r.expected} | {r.injected} | {r.handled} |" for kind, r in s.defects.items()]
    out += ["", "### Problems", ""]
    problems = s.problems()
    if not problems:
        out.append("None. Every true order, copy and defect got the treatment the ledger expects.")
    for name, items in problems.items():
        shown = ", ".join(f"`{item}`" for item in items[:10])
        out.append(f"- **{name}** ({len(items)}): {shown}" + (", ..." if len(items) > 10 else ""))
    return "\n".join(out) + "\n"
