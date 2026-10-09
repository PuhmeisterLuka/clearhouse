import argparse
from collections import Counter
from dataclasses import asdict
from datetime import date
from pathlib import Path

from .check import CheckContext, check_orders
from .files import write_csv, write_jsonl
from .generate.mess import generate
from .generate.truth import build_world
from .ingest import ingest_dir
from .ingest.records import SourceResult
from .issues import Issue
from .parse import parse_orders
from .parse.records import ParsedOrder
from .reference import load_reference

ISSUE_COLUMNS = ["stage", "severity", "code", "source", "order_ref", "locator", "value", "message"]


def print_sources(results: list[SourceResult]) -> None:
    print(f"{'source':<10} {'file':<28} {'encoding':<9} {'orders':>6} {'lines':>6}")
    for r in results:
        s = r.stats
        print(f"{s.source:<10} {s.file:<28} {s.encoding:<9} {s.orders:>6} {s.lines:>6}")


def print_order_summary(orders: list[ParsedOrder]) -> None:
    rejected = sum(o.has_errors for o in orders)
    repairs = sum(i.severity == "warning" for o in orders for i in o.issues)
    print(f"\n{len(orders)} orders: {len(orders) - rejected} usable, {rejected} with errors, {repairs} repairs")


def print_issue_counts(issues: list[Issue]) -> None:
    counts = Counter((i.stage, i.severity, i.code) for i in issues)
    print()
    for (stage, severity, code), n in sorted(counts.items()):
        print(f"  {stage:<7} {severity:<8} {code:<28} {n:>4}")


def cmd_generate(args: argparse.Namespace) -> None:
    world = build_world(seed=args.seed)
    ledger = generate(Path(args.data), world, seed=args.seed)
    print(f"{len(world.orders)} true orders, {len(ledger.rows)} defects injected into {args.data}/raw")
    for expected, n in sorted(Counter(r["expected"] for r in ledger.rows).items()):
        print(f"  {expected:<11} {n}")


def cmd_run(args: argparse.Namespace) -> None:
    data = Path(args.data)
    results, unclaimed = ingest_dir(data / "raw")
    orders = parse_orders(order for r in results for order in r.orders)
    check_orders(orders, CheckContext(load_reference(data / "reference"), args.as_of))

    file_issues = unclaimed + [i for r in results for i in r.issues]
    issues = file_issues + [i for o in orders for i in o.issues]
    out = Path(args.out)
    write_jsonl(out / "orders.jsonl", (asdict(o) for o in orders))
    write_csv(out / "issues.csv", ISSUE_COLUMNS, ([getattr(i, c) for c in ISSUE_COLUMNS] for i in issues))

    print_sources(results)
    print_order_summary(orders)
    print_issue_counts(issues)
    print(f"\nwritten to {out}/: orders.jsonl, issues.csv")


def main() -> None:
    parser = argparse.ArgumentParser(prog="clearhouse")
    sub = parser.add_subparsers(required=True)

    gen = sub.add_parser("generate", help="build the messy source files and their ground truth")
    gen.add_argument("--data", default="data")
    gen.add_argument("--seed", type=int, default=7)
    gen.set_defaults(func=cmd_generate)

    run = sub.add_parser("run", help="run the pipeline over data/raw")
    run.add_argument("--data", default="data")
    run.add_argument("--out", default="out")
    run.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                     help="latest plausible order date, YYYY-MM-DD (default: today)")
    run.set_defaults(func=cmd_run)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
