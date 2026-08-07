"""Dependency-free coverage guard for potential SQL write statements."""

from __future__ import annotations

import re
from dataclasses import dataclass

_WRITE = re.compile(r"(?im)^[ \t]*(?P<operation>INSERT|UPDATE|DELETE|MERGE|TRUNCATE)\b")
_LOCAL_SELECT_INTO = re.compile(r"(?im)^[ \t]*INTO[ \t]+[#@]")
_SINGLE_LINE_SELECT_INTO = re.compile(r"(?im)^[ \t]*SELECT\b[^\n;]*\bINTO\b")


@dataclass(frozen=True, slots=True)
class WriteMarker:
    operation: str
    line: int
    text: str

    def to_dict(self) -> dict[str, object]:
        return {"line": self.line, "operation": self.operation, "text": self.text}


def write_markers(source: str) -> tuple[WriteMarker, ...]:
    """Locate possible writes without claiming their targets or data dependencies."""
    masked = _mask_comments_and_strings(source)
    original_lines = source.splitlines()
    found: dict[tuple[int, str], WriteMarker] = {}
    for match in _WRITE.finditer(masked):
        operation = match.group("operation").casefold()
        line = masked.count("\n", 0, match.start()) + 1
        found[(line, operation)] = WriteMarker(operation, line, original_lines[line - 1].strip())
    for pattern in (_LOCAL_SELECT_INTO, _SINGLE_LINE_SELECT_INTO):
        for match in pattern.finditer(masked):
            line = masked.count("\n", 0, match.start()) + 1
            if any(key[0] == line and key[1] == "insert" for key in found):
                continue
            found[(line, "select_into")] = WriteMarker(
                "select_into",
                line,
                original_lines[line - 1].strip(),
            )
    return tuple(sorted(found.values(), key=lambda item: (item.line, item.operation)))


def _mask_comments_and_strings(source: str) -> str:
    result: list[str] = []
    index = 0
    state = "normal"
    while index < len(source):
        character = source[index]
        following = source[index + 1] if index + 1 < len(source) else ""
        if state == "normal":
            if character == "-" and following == "-":
                result.extend((" ", " "))
                index += 2
                state = "line_comment"
                continue
            if character == "/" and following == "*":
                result.extend((" ", " "))
                index += 2
                state = "block_comment"
                continue
            if character == "'":
                result.append(" ")
                index += 1
                state = "string"
                continue
            result.append(character)
            index += 1
            continue
        if state == "line_comment":
            if character == "\n":
                result.append("\n")
                state = "normal"
            else:
                result.append(" ")
            index += 1
            continue
        if state == "block_comment":
            if character == "*" and following == "/":
                result.extend((" ", " "))
                index += 2
                state = "normal"
                continue
            result.append("\n" if character == "\n" else " ")
            index += 1
            continue
        if character == "'" and following == "'":
            result.extend((" ", " "))
            index += 2
            continue
        if character == "'":
            result.append(" ")
            index += 1
            state = "normal"
            continue
        result.append("\n" if character == "\n" else " ")
        index += 1
    return "".join(result)
