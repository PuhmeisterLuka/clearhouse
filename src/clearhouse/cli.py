import argparse
import sys
from collections import Counter
from datetime import date
from pathlib import Path

from .export import write_tables
from .generate.mess import DEFAULT_DEFECT_RATE, generate
from .generate.truth import build_world
from .ingest.records import SourceResult
from .issues import Issue
from .pipeline import Run, run_pipeline
from .report import render_report
from .score import Output, Truth, render, score


def print_sources(sources: list[SourceResult]) -> None:
    print(f"{'source':<10} {'file':<28} {'encoding':<9} {'orders':>6} {'lines':>6}")
    for r in sources:
        s = r.stats
        print(f"{s.source:<10} {s.file:<28} {s.encoding:<9} {s.orders:>6} {s.lines:>6}")


def print_run_summary(run: Run) -> None:
    status = Counter(o.status for o in run.orders)
    print(f"\n{len(run.orders)} records: {status['clean']} clean orders, {status['quarantined']} quarantined, "
          f"{status['duplicate']} duplicate copies; {len(run.repairs())} values repaired")


def print_issue_counts(issues: list[Issue]) -> None:
    counts = Counter((i.stage, i.severity, i.code) for i in issues)
    print()
    for (stage, severity, code), n in sorted(counts.items()):
        print(f"  {stage:<9} {severity:<8} {code:<28} {n:>4}")


def share(text: str) -> float:
    value = float(text)
    if not 0 <= value <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return value


def cmd_generate(args: argparse.Namespace) -> None:
    world = build_world(seed=args.seed)
    ledger = generate(Path(args.data), world, seed=args.seed, defect_rate=args.defect_rate)
    print(f"{len(world.orders)} true orders, {len(ledger.rows)} defects injected into {args.data}/raw")
    for expected, n in sorted(Counter(r["expected"] for r in ledger.rows).items()):
        print(f"  {expected:<11} {n}")


def cmd_run(args: argparse.Namespace) -> None:
    run = run_pipeline(Path(args.data), args.as_of)
    out = Path(args.out)
    write_tables(out, run)
    (out / "report.html").write_text(render_report(run), encoding="utf-8")
    print_sources(run.sources)
    print_run_summary(run)
    print_issue_counts(run.issues)
    print(f"\nwritten to {out}/: report.html, and orders, lines, customers, quarantine, duplicates and issues "
          "as CSV")


def cmd_score(args: argparse.Namespace) -> None:
    result = score(Truth.load(Path(args.data)), Output.load(Path(args.out)))
    text = render(result)
    (Path(args.out) / "score.md").write_text(text, encoding="utf-8")
    print(text)
    sys.exit(1 if result.problems() else 0)


PATHS = {
    "data": "folder holding raw/, reference/ and truth/ (default: data)",
    "out": "folder for the report, CSV tables and score (default: out)",
}


def add_paths(parser: argparse.ArgumentParser, *names: str) -> None:
    for name in names:
        parser.add_argument(f"--{name}", default=name, help=PATHS[name])


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="clearhouse",
        description="Generate messy order exports, reconcile them, and grade the result against the truth.")
    sub = parser.add_subparsers(required=True)

    gen = sub.add_parser("generate", help="build the messy source files and their ground truth")
    add_paths(gen, "data")
    gen.add_argument("--seed", type=int, default=7, help="the same seed always gives the same files (default: 7)")
    gen.add_argument("--defect-rate", type=share, default=DEFAULT_DEFECT_RATE,
                     help=f"share of orders that get a defect, 0 to 1 (default: {DEFAULT_DEFECT_RATE})")
    gen.set_defaults(func=cmd_generate)

    run = sub.add_parser("run", help="run the pipeline over data/raw and write its outputs")
    add_paths(run, "data", "out")
    run.add_argument("--as-of", type=date.fromisoformat, default=date.today(),
                     help="latest plausible order date, YYYY-MM-DD (default: today)")
    run.set_defaults(func=cmd_run)

    grade = sub.add_parser("score", help="grade a run's outputs against the generator's ground truth")
    add_paths(grade, "data", "out")
    grade.set_defaults(func=cmd_score)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
