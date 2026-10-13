"""How a command says what happened.

Every command produces one result object. `--json` prints it; otherwise a
renderer turns it into something a person reads. Both come from the same value,
so the two views cannot describe different outcomes.

Results go to stdout and failures to stderr, so `contextctl search … | jq` works
and an error never lands in the pipe.
"""

import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TextIO

# Long text is cut in a table, never in JSON: a terminal has a width and a
# pipeline does not.
COLUMN_LIMIT = 60
_EMPTY = "—"


@dataclass(frozen=True, slots=True)
class Result:
    """One command's outcome, in both the forms `contextctl` can print.

    `data` is what `--json` emits and what a test asserts on. `lines` is what a
    person reads. A command builds both from the same values rather than
    formatting twice.
    """

    data: Mapping[str, Any]
    lines: Sequence[str] = field(default_factory=tuple)


def render(result: Result, *, as_json: bool, stream: TextIO | None = None) -> None:
    """Print one result in the form that was asked for."""
    out = stream if stream is not None else sys.stdout
    if as_json:
        print(json.dumps(result.data, indent=2, default=str, sort_keys=False), file=out)
        return
    for line in result.lines:
        print(line, file=out)


def render_error(
    message: str,
    *,
    as_json: bool,
    exit_code: int,
    hint: str | None = None,
    stream: TextIO | None = None,
) -> None:
    """Report a failure on stderr, in the same form the results take."""
    err = stream if stream is not None else sys.stderr
    if as_json:
        payload: dict[str, Any] = {"error": message, "exit_code": exit_code}
        if hint:
            payload["hint"] = hint
        print(json.dumps(payload, indent=2), file=err)
        return
    print(f"error: {message}", file=err)
    if hint:
        print(f"hint: {hint}", file=err)


def table(headers: Sequence[str], rows: Sequence[Sequence[object]]) -> list[str]:
    """Lay out rows in aligned columns, or say that there are none.

    Columns are sized to their contents so short tables stay narrow, and a
    value long enough to wrap is cut rather than allowed to break the layout.
    """
    if not rows:
        return ["(none)"]
    cells = [[_cell(value) for value in row] for row in rows]
    widths = [
        max(len(header), *(len(row[index]) for row in cells))
        for index, header in enumerate(headers)
    ]
    lines = ["  ".join(header.ljust(widths[i]) for i, header in enumerate(headers))]
    lines.append("  ".join("-" * width for width in widths))
    lines.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in cells)
    return [line.rstrip() for line in lines]


def fields(pairs: Mapping[str, object]) -> list[str]:
    """Lay out one record as aligned `name: value` lines."""
    if not pairs:
        return ["(none)"]
    width = max(len(name) for name in pairs)
    return [f"{name.ljust(width)}  {_cell(value)}" for name, value in pairs.items()]


def _cell(value: object) -> str:
    """Render one value for a terminal."""
    if value is None:
        return _EMPTY
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | tuple):
        rendered = ", ".join(str(item) for item in value)
    elif isinstance(value, float):
        rendered = f"{value:.4f}"
    else:
        rendered = str(value)
    if not rendered:
        return _EMPTY
    rendered = " ".join(rendered.split())
    if len(rendered) > COLUMN_LIMIT:
        return f"{rendered[: COLUMN_LIMIT - 1]}…"
    return rendered
