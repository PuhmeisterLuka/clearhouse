import argparse
from collections import Counter
from pathlib import Path

from .generate.mess import generate
from .generate.truth import build_world


def cmd_generate(args: argparse.Namespace) -> None:
    world = build_world(seed=args.seed)
    ledger = generate(Path(args.out), world, seed=args.seed)
    by_expected = Counter(r["expected"] for r in ledger.rows)
    print(f"{len(world.orders)} true orders, {len(ledger.rows)} defects injected into {args.out}/raw")
    for expected, n in sorted(by_expected.items()):
        print(f"  {expected:<11} {n}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="clearhouse")
    sub = parser.add_subparsers(required=True)

    gen = sub.add_parser("generate", help="build the messy source files and their ground truth")
    gen.add_argument("--out", default="data")
    gen.add_argument("--seed", type=int, default=7)
    gen.set_defaults(func=cmd_generate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
