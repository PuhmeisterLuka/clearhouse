from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# error:   the order can't be trusted and should end up in quarantine
# warning: something was wrong but has been (or can be) repaired
# info:    worth knowing, nothing to fix
Severity = Literal["error", "warning", "info"]


@dataclass(frozen=True)
class Issue:
    stage: str
    severity: Severity
    code: str
    message: str
    source: str
    locator: str = ""
    order_ref: str | None = None
    value: str | None = None

    def describe(self) -> str:
        return f"{self.code}: {self.message}" + (f" ({self.value})" if self.value else "")
