"""What a check reports: one problem per broken rule, with the line and the JSON pointer."""

from __future__ import annotations

from dataclasses import dataclass

MAX_MESSAGE = 240


@dataclass(frozen=True)
class Problem:
    """A rule a line or a file breaks. ``line`` is 1-based, 0 for the file as a whole."""

    line: int
    path: str
    message: str

    def __str__(self) -> str:
        where = f"line {self.line}" if self.line else "file"
        return f"{where} {self.path or '/'}: {self.message}"


class FormatError(ValueError):
    """A file or a record that does not follow its format; ``problems`` says how."""

    def __init__(self, problems: list[Problem] | tuple[Problem, ...]) -> None:
        self.problems = tuple(problems)
        shown = "; ".join(str(p) for p in self.problems[:3])
        more = f" (and {len(self.problems) - 3} more)" if len(self.problems) > 3 else ""
        super().__init__(shown + more if shown else "invalid file")


def shorten(text: str, limit: int = MAX_MESSAGE) -> str:
    """The message cut to ``limit`` characters, so a schema error never echoes a whole item."""
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 3] + "..."
