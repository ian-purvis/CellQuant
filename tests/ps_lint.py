"""A small static checker for the Windows installer's PowerShell scripts.

PowerShell is not available where the test suite runs, so this checks what can
be checked without it: brackets balance outside strings and comments, no
syntax that only PowerShell 7 accepts (Windows ships 5.1), and every call to a
function defined in these scripts uses parameter names that function declares.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_PAIRS = {"(": ")", "{": "}", "[": "]"}


@dataclass
class Scan:
    code: str  # the script with strings and comments blanked out
    problems: list[str] = field(default_factory=list)


def scan(text: str) -> Scan:
    """Blank out comments and string contents, keeping code inside $( ) subexpressions."""

    out: list[str] = []
    problems: list[str] = []
    index = 0
    length = len(text)

    def line_of(position: int) -> int:
        return text.count("\n", 0, position) + 1

    def read_code(position: int, closer: str | None) -> int:
        stack: list[tuple[str, int]] = []
        nonlocal out
        while position < length:
            char = text[position]
            if char == "<" and text.startswith("<#", position):
                end = text.find("#>", position + 2)
                if end < 0:
                    problems.append(f"line {line_of(position)}: unterminated block comment")
                    return length
                out.append(" " * (end + 2 - position))
                position = end + 2
                continue
            if char == "#":
                end = text.find("\n", position)
                end = length if end < 0 else end
                out.append(" " * (end - position))
                position = end
                continue
            if char == "@" and text.startswith(("@'", '@"'), position) and text[position + 2 : position + 3] in ("\n", "\r"):
                quote = text[position + 1]
                end = text.find("\n" + quote + "@", position)
                if end < 0:
                    problems.append(f"line {line_of(position)}: unterminated here-string")
                    return length
                out.append(" " * (end + 3 - position))
                position = end + 3
                continue
            if char == "'":
                position = read_single(position)
                continue
            if char == '"':
                position = read_double(position)
                continue
            if char == "`":
                out.append("  ")
                position += 2
                continue
            if char in _PAIRS:
                stack.append((char, position))
            elif char in _PAIRS.values():
                if closer is not None and not stack and char == closer:
                    out.append(char)
                    return position + 1
                if not stack or _PAIRS[stack[-1][0]] != char:
                    problems.append(f"line {line_of(position)}: unexpected '{char}'")
                else:
                    stack.pop()
            out.append(char)
            position += 1
        for opener, where in stack:
            problems.append(f"line {line_of(where)}: '{opener}' is never closed")
        if closer is not None:
            problems.append("unterminated subexpression")
        return position

    def read_single(position: int) -> int:
        position += 1
        out.append("'")
        while position < length:
            if text[position] == "'":
                if text.startswith("''", position):
                    out.append("  ")
                    position += 2
                    continue
                out.append("'")
                return position + 1
            out.append("\n" if text[position] == "\n" else " ")
            position += 1
        problems.append("unterminated single-quoted string")
        return position

    def read_double(position: int) -> int:
        start = position
        position += 1
        out.append('"')
        while position < length:
            char = text[position]
            if char == "`":
                out.append("  ")
                position += 2
                continue
            if char == '"':
                if text.startswith('""', position):
                    out.append("  ")
                    position += 2
                    continue
                out.append('"')
                return position + 1
            if char == "$" and text.startswith("$(", position):
                out.append("$(")
                position = read_code(position + 2, ")")
                continue
            out.append("\n" if char == "\n" else " ")
            position += 1
        problems.append(f"line {line_of(start)}: unterminated double-quoted string")
        return position

    index = read_code(index, None)
    return Scan("".join(out), problems)


_PS7_ONLY = [
    (re.compile(r"\?\?"), "'??' needs PowerShell 7"),
    (re.compile(r"\?\."), "'?.' needs PowerShell 7"),
    (re.compile(r"&&|\|\|"), "'&&' and '||' need PowerShell 7"),
    (re.compile(r"\s\?\s[^:\n]+\s:\s"), "the ternary operator needs PowerShell 7"),
]


def check_script(path: Path) -> list[str]:
    text = path.read_text(encoding="ascii")
    result = scan(text)
    problems = [f"{path.name}: {item}" for item in result.problems]
    for number, line in enumerate(result.code.splitlines(), start=1):
        for pattern, message in _PS7_ONLY:
            if pattern.search(line):
                problems.append(f"{path.name} line {number}: {message}")
    return problems


_FUNCTION = re.compile(r"(?im)^\s*function\s+([A-Za-z][\w-]*)\s*\{")
_PARAM_NAME = re.compile(r"\$([A-Za-z_]\w*)\s*(?:=|,|\)|$)")


def declared_functions(paths: list[Path]) -> dict[str, set[str]]:
    """Function name -> declared parameter names (lower case)."""

    functions: dict[str, set[str]] = {}
    for path in paths:
        code = scan(path.read_text(encoding="ascii")).code
        for match in _FUNCTION.finditer(code):
            body_start = match.end()
            param = re.match(r"\s*param\s*\(", code[body_start:], flags=re.IGNORECASE)
            names: set[str] = set()
            if param:
                open_at = body_start + param.end() - 1
                depth = 0
                for position in range(open_at, len(code)):
                    if code[position] == "(":
                        depth += 1
                    elif code[position] == ")":
                        depth -= 1
                        if depth == 0:
                            block = code[open_at + 1 : position]
                            break
                else:
                    block = ""
                # Parameter variables follow a type or attribute, or start a line.
                for item in re.finditer(r"(?:\]|^|,)\s*\$([A-Za-z_]\w*)", block, flags=re.MULTILINE):
                    names.add(item.group(1).lower())
            functions[match.group(1).lower()] = names
    return functions


_CALL = re.compile(r"(?<![\w$-])([A-Z][a-z]+-CellQuant[\w]*|[A-Z][a-z]+-Nvidia[\w]*|Invoke-EnvPython|Get-EnvPythonOutput|Remove-CellQuantEnvPrefix|Resolve-CondaExecutable)\b([^\n|;{}]*)")
_NAMED = re.compile(r"(?<![\w$])-([A-Za-z]\w*)")


def check_calls(paths: list[Path]) -> list[str]:
    functions = declared_functions(paths)
    problems: list[str] = []
    for path in paths:
        code = scan(path.read_text(encoding="ascii")).code
        for number, line in enumerate(code.splitlines(), start=1):
            if re.match(r"\s*function\s", line, flags=re.IGNORECASE):
                continue
            for match in _CALL.finditer(line):
                name = match.group(1).lower()
                if name not in functions:
                    problems.append(f"{path.name} line {number}: calls undefined function {match.group(1)}")
                    continue
                arguments = _own_arguments(match.group(2))
                for flag in _NAMED.findall(arguments):
                    lowered = flag.lower()
                    if lowered in {"eq", "ne", "lt", "le", "gt", "ge", "and", "or", "not", "join", "match", "like", "contains", "notcontains", "split", "replace", "f", "as", "is", "in", "notin"}:
                        continue
                    if lowered not in functions[name]:
                        problems.append(f"{path.name} line {number}: {match.group(1)} has no parameter -{flag}")
    return problems


def _own_arguments(text: str) -> str:
    """Arguments of one call: stop at a ')' that closes an enclosing group, and
    at the start of a nested command, whose flags are checked on their own."""

    depth = 0
    for position, char in enumerate(text):
        if char == "(":
            if re.match(r"\(\s*[A-Z][a-z]+-", text[position:]):
                return text[:position]
            depth += 1
        elif char == ")":
            if depth == 0:
                return text[:position]
            depth -= 1
    return text
