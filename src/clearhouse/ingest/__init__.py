"""One adapter per source format, all producing the raw layer defined in records.py."""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatch
from pathlib import Path

from ..issues import Issue
from .pos import read_pos
from .records import SourceResult
from .web import read_web
from .wholesale import read_wholesale

SOURCES: dict[str, Callable[[Path], SourceResult]] = {
    "pos_*.csv": read_pos,
    "webshop_*.json": read_web,
    "wholesale_*.xml": read_wholesale,
}


def adapter_for(path: Path) -> Callable[[Path], SourceResult] | None:
    return next((reader for pattern, reader in SOURCES.items() if fnmatch(path.name, pattern)), None)


def ingest_dir(raw_dir: Path) -> tuple[list[SourceResult], list[Issue]]:
    """Run the matching adapter on every file. Files nobody claims are reported, not skipped silently."""
    results, unclaimed = [], []
    for path in sorted(p for p in raw_dir.iterdir() if p.is_file() and not p.name.startswith(".")):
        reader = adapter_for(path)
        if reader is None:
            unclaimed.append(Issue("ingest", "warning", "ingest.unclaimed_file", "no adapter matches this file",
                                   source="", locator=path.name))
        else:
            results.append(reader(path))
    return results, unclaimed
