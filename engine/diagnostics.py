# engine/diagnostics.py
from dataclasses import dataclass
from typing import Optional, Literal

Severity = Literal["error", "warning"]
Phase = Literal["parser", "structure", "semantic", "graph", "runtime"]

@dataclass(frozen=True)
class Diagnostic:
    file: str
    tag: str
    line: int           # 1-based
    column: int         # 0-based
    length: Optional[int]
    severity: Severity
    phase: Phase
    code: str
    message: str


    def format_console(self):
        severity = self.severity.upper()
        file = self.file or "?"
        tag = _display_tag(self.tag or "?")
        line = self.line if self.line is not None else "?"
        return f"{severity} {file}:{tag}, line={line}, '{self.message}'"


def _display_tag(tag: str) -> str:
    if tag.startswith("__sys__auto__"):
        return "<auto>"
    return tag
