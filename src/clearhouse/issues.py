from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

# error:   the order can't be trusted, so it goes to quarantine
# warning: something was wrong, but the order is still usable: the value was repaired,
#          or dropped because the order can do without it
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
