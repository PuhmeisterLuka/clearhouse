"""The HTML data-quality report: what came in, where every record ended up, and why.

Everything that comes from the data goes through esc() before it reaches the page. Section builders
return HTML strings, and the page skeleton with its styles lives in template.html.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from decimal import Decimal
from html import escape
from importlib.resources import files
from string import Template

from ..issues import Issue
from ..money import euros
from ..pipeline import Run
from ..reconcile import PICKUP_BY_REF, PICKUP_BY_SALE, REPEAT, copy_issue

TEMPLATE = Template((files(__package__) / "template.html").read_text(encoding="utf-8"))

SOURCE_LABELS = {"pos": "Shop till", "web": "Webshop", "wholesale": "Wholesale feed"}
# Also the order of the bar segments and the legend, each status with its own colour.
STATUS_LABELS = {"clean": "Clean", "quarantined": "Quarantined", "duplicate": "Duplicate copy"}
COPY_LABELS = {
    REPEAT: "The same order id sent twice by one source",
    PICKUP_BY_REF: "A till receipt for a web order, matched by the web order number in its Ref",
    PICKUP_BY_SALE: "A till receipt for a web order, matched by customer, items and timing",
}
LABEL_MIN_SHARE = 0.12  # a segment narrower than this keeps its value in the tooltip and table only


def esc(value: object) -> str:
    return escape("" if value is None else str(value))


def num(n: int) -> str:
    return f"{n:,}"


def compact_eur(amount: Decimal) -> str:
    if amount >= 1_000_000:
        return f"€{amount / 1_000_000:.2f}M"
    if amount >= 1_000:
        return f"€{amount / 1_000:.1f}K"
    return euros(amount)


def source_label(source: str) -> str:
    return SOURCE_LABELS.get(source, source)


def location(file: str, locator: str) -> str:
    return f'<span class="mono">{esc(" ".join(part for part in (file, locator) if part))}</span>'


def table(headers: list[str], rows: Iterable[list[str]], classes: dict[int, str] | None = None) -> str:
    """Cells are HTML already, so data must be escaped before it gets here. `classes` styles columns by index."""
    def cls(i: int) -> str:
        return f' class="{classes[i]}"' if classes and i in classes else ""
    head = "".join(f"<th{cls(i)}>{esc(h)}</th>" for i, h in enumerate(headers))
    body = "".join("<tr>" + "".join(f"<td{cls(i)}>{c}</td>" for i, c in enumerate(row)) + "</tr>" for row in rows)
    return f'<div class="table-scroll"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def section(title: str, lede: str, *parts: str) -> str:
    return f'<section><h2>{esc(title)}</h2><p class="lede">{lede}</p>{"".join(parts)}</section>'


def by_code(issues: Iterable[Issue]) -> list[tuple[str, list[Issue]]]:
    """Issues grouped by code, most frequent first."""
    groups: dict[str, list[Issue]] = defaultdict(list)
    for issue in issues:
        groups[issue.code].append(issue)
    return sorted(groups.items(), key=lambda g: (-len(g[1]), g[0]))


def code_table(issues: list[Issue]) -> str:
    """One row per issue code: how often it happened, with a bar for scale and the first example."""
    groups = by_code(issues)
    most = max((len(found) for _, found in groups), default=1)
    rows = ([f"<code>{esc(code)}</code>", esc(found[0].stage), num(len(found)),
             f'<span class="meter" style="width:{100 * len(found) / most:.1f}%"></span>', esc(found[0].message)]
            for code, found in groups)
    return table(["Issue", "Stage", "Count", "", "Example"], rows, {2: "num", 3: "meter-cell"})


# Sections

def kpis(run: Run) -> str:
    status = Counter(o.status for o in run.orders)
    revenue = sum((o.value_eur for o in run.with_status("clean")), Decimal(0))
    tiles = [("Records read", num(len(run.orders))), ("Clean orders", num(status["clean"])),
             ("Quarantined", num(status["quarantined"])), ("Copies removed", num(status["duplicate"])),
             ("Repaired or dropped", num(len(run.repairs()))), ("Clean revenue", compact_eur(revenue))]
    return '<div class="kpis">' + "".join(
        f'<div><div class="kpi-label">{esc(label)}</div><div class="kpi-value">{esc(value)}</div></div>'
        for label, value in tiles) + "</div>"


def file_problems(run: Run) -> str:
    found = [i for i in run.file_issues if i.severity != "info"]
    if not found:
        return ""
    file_of = {s.stats.source: s.stats.file for s in run.sources}
    rows = ([esc(source_label(i.source)), location(file_of.get(i.source, ""), i.locator), f"<code>{esc(i.code)}</code>",
             esc(i.message)] for i in found)
    return section("Problems with whole files",
                   "These affect a file rather than one order. A file the pipeline can't read adds no orders, so its "
                   "source shows no records below.",
                   table(["Source", "Where", "Issue", "What happened"], rows, {1: "nowrap"}))


def outcome_bar(source: str, counts: Counter[str]) -> str:
    total = sum(counts.values())
    segments = []
    for status, label in STATUS_LABELS.items():
        n = counts[status]
        if not n:
            continue
        share = n / total
        text = f"{share:.0%}" if share >= LABEL_MIN_SHARE else ""
        tip = f"{num(n)} records ({share:.1%})"
        what = f"{source_label(source)}, {label.lower()}"
        segments.append(
            f'<span class="seg {status}{" labeled" if text else ""}" style="flex-grow:{n}" tabindex="0" '
            f'aria-label="{esc(what)}: {esc(tip)}" data-tip-value="{esc(tip)}" data-tip-label="{esc(what)}">'
            f"{text}</span>")
    return f'<span class="bar-label">{esc(source_label(source))}</span><div class="track">{"".join(segments)}</div>'


def outcomes(run: Run) -> str:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    revenue: dict[str, Decimal] = defaultdict(Decimal)
    for o in run.orders:
        counts[o.source][o.status] += 1
        if o.status == "clean":
            revenue[o.source] += o.value_eur
    sources = [s.stats for s in run.sources]

    legend = '<ul class="legend">' + "".join(
        f'<li><span class="swatch {status}"></span>{esc(label)}</li>' for status, label in STATUS_LABELS.items()
    ) + "</ul>"
    ticks = ('<div class="ticks">' + "".join(f'<span style="left:{p}%">{p}%</span>' for p in (0, 25, 50, 75, 100))
             + "</div>")
    chart = ('<div class="chart" role="group" aria-label="Share of each source\'s records by outcome">'
             + "".join(outcome_bar(s.source, counts[s.source]) for s in sources if s.orders) + ticks + "</div>")
    rows = ([esc(source_label(s.source)), f"<code>{esc(s.file)}</code>", num(s.orders),
             num(counts[s.source]["clean"]), num(counts[s.source]["quarantined"]),
             num(counts[s.source]["duplicate"]), euros(revenue[s.source])] for s in sources)
    numbers = table(["Source", "File", "Records", "Clean", "Quarantined", "Copies", "Clean revenue"], rows,
                    {0: "nowrap", 2: "num", 3: "num", 4: "num", 5: "num", 6: "num"})
    return section("Where every record ended up",
                   "Every record the pipeline read became a clean order, a quarantined order, or a copy of another "
                   "record.",
                   legend, chart, numbers)


def repairs(run: Run) -> str:
    found = run.repairs()
    return section("What was repaired or dropped",
                   f"{num(len(found))} values were wrong without making their order unusable. The pipeline repaired "
                   "each one, or dropped it when the order can do without it, and <code>issues.csv</code> keeps the "
                   "original.",
                   code_table(found))


def quarantine(run: Run) -> str:
    orders = run.with_status("quarantined")
    errors = [i for o in orders for i in o.issues if i.severity == "error"]
    rows = ([f"<code>{esc(o.order_ref)}</code>", esc(source_label(o.source)), location(o.file, o.locator),
             "<br>".join(esc(i.describe()) for i in o.issues if i.severity == "error")] for o in orders)
    listing = (f"<details><summary>All {num(len(orders))} quarantined orders</summary>"
               f"{table(['Order', 'Source', 'Where', 'Reasons'], rows, {2: 'nowrap'})}</details>")
    return section("Why orders were quarantined",
                   f"{num(len(orders))} orders had a problem the pipeline can't fix safely, so the clean tables leave "
                   "them out. <code>quarantine.csv</code> lists each one with its reasons.",
                   code_table(errors), listing)


def duplicates(run: Run) -> str:
    copies = run.with_status("duplicate")
    found_by = Counter(copy_issue(o).code for o in copies)
    summary = table(["How the copy was found", "Count"],
                    ([esc(label), num(found_by[code])] for code, label in COPY_LABELS.items()), {1: "num"})
    rows = ([f"<code>{esc(o.order_ref)}</code>", f"<code>{esc(o.duplicate_of)}</code>", location(o.file, o.locator),
             esc(copy_issue(o).message)] for o in copies)
    listing = (f"<details><summary>All {num(len(copies))} copies</summary>"
               f"{table(['Copy', 'Copy of', 'Where', 'How it was found'], rows, {2: 'nowrap'})}</details>")
    return section("Duplicate copies removed",
                   "Counting these would double the revenue of the orders they copy. <code>duplicates.csv</code> "
                   "pairs each one with its original.",
                   summary, listing)


def notes(run: Run) -> str:
    found = [i for i in run.issues if i.severity == "info"]
    if not found:
        return section("Worth knowing", "Nothing else to report.")
    return section("Worth knowing", "Things that needed no fix but say something about the sources.", code_table(found))


def subtitle(run: Run) -> str:
    day = run.as_of
    return (f"{len(run.sources)} source files, {num(len(run.orders))} records, "
            f"checked as of {day.day} {day:%B %Y}")


def render_report(run: Run) -> str:
    body = "\n".join([kpis(run), file_problems(run), outcomes(run), repairs(run), quarantine(run), duplicates(run),
                      notes(run)])
    return TEMPLATE.substitute(subtitle=esc(subtitle(run)), body=body)
