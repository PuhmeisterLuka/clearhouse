import argparse
import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path

from .generate.mess import generate
from .generate.truth import build_world
from .ingest import ingest_dir
from .ingest.records import SourceResult
from .issues import Issue


def print_sources(results: list[SourceResult]) -> None:
    print(f"{'source':<10} {'file':<28} {'encoding':<9} {'orders':>6} {'lines':>6} {'issues':>6}")
    for r in results:
        s = r.stats
        print(f"{s.source:<10} {s.file:<28} {s.encoding:<9} {s.orders:>6} {s.lines:>6} {len(r.issues):>6}")


def print_issue_counts(issues: list[Issue]) -> None:
    for (severity, code), n in sorted(Counter((i.severity, i.code) for i in issues).items()):
        print(f"  {severity:<8} {code:<30} {n}")


def write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def cmd_generate(args: argparse.Namespace) -> None:
    world = build_world(seed=args.seed)
    ledger = generate(Path(args.out), world, seed=args.seed)
    print(f"{len(world.orders)} true orders, {len(ledger.rows)} defects injected into {args.out}/raw")
    for expected, n in sorted(Counter(r["expected"] for r in ledger.rows).items()):
        print(f"  {expected:<11} {n}")


def cmd_ingest(args: argparse.Namespace) -> None:
    results, unclaimed = ingest_dir(Path(args.data) / "raw")
    print_sources(results)
    issues = unclaimed + [i for r in results for i in r.issues]
    if issues:
        print()
        print_issue_counts(issues)
    if args.dump:
        write_jsonl(Path(args.dump), (asdict(order) for r in results for order in r.orders))
        print(f"\nraw orders written to {args.dump}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="clearhouse")
    sub = parser.add_subparsers(required=True)

    gen = sub.add_parser("generate", help="build the messy source files and their ground truth")
    gen.add_argument("--out", default="data")
    gen.add_argument("--seed", type=int, default=7)
    gen.set_defaults(func=cmd_generate)

    ing = sub.add_parser("ingest", help="read every raw export into the common raw layer")
    ing.add_argument("--data", default="data")
    ing.add_argument("--dump", help="write the raw orders as JSON lines to this path")
    ing.set_defaults(func=cmd_ingest)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
