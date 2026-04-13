"""Claude Code permission glob matcher.

Claude Code's ``*`` wildcard in permission patterns does NOT match shell
operators (``&&``, ``||``, ``|``, ``;``, ``>``).  Operators inside quotes
(single or double) or preceded by a backslash are treated as ordinary
characters, not as operators.

File-descriptor redirects (``2>&1``, ``2>/dev/null``) are also not
treated as operators — only ``>`` without a preceding digit counts.
"""

from __future__ import annotations

import re
from pathlib import Path

# Detects unquoted shell operators in a raw string.
# > is only an operator when NOT preceded by a digit (fd redirects are safe).
_HAS_OPERATORS = re.compile(r"&&|\|\||(?<!\|)\|(?!\|)|(?<!\d)>|;")

# Placeholder characters used by _mask_quoted_operators.
_MASK = {"|": "\x01", ";": "\x02", "&": "\x03", ">": "\x04"}


def _mask_quoted_operators(cmd: str) -> str:
    """Replace ``|;&>`` inside quotes or after backslash with placeholders.

    This lets the glob regex treat quoted/escaped operators as ordinary
    characters while still blocking unquoted ones.
    """
    result: list[str] = []
    in_single = False
    in_double = False
    i = 0
    while i < len(cmd):
        c = cmd[i]

        # Backslash escapes the next character (not inside single quotes).
        if c == "\\" and not in_single and i + 1 < len(cmd):
            result.append(c)
            i += 1
            result.append(_MASK.get(cmd[i], cmd[i]))
            i += 1
            continue

        if c == "'" and not in_double:
            in_single = not in_single
        elif c == '"' and not in_single:
            in_double = not in_double
        elif (in_single or in_double) and c in _MASK:
            result.append(_MASK[c])
            i += 1
            continue

        result.append(c)
        i += 1
    return "".join(result)


# Regex fragment for ``*``: matches any character sequence that does NOT
# contain unquoted shell operators.
_STAR = r"(?:(?!&&|\|\||[|;]|(?<!\d)>).)*"


def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Compile a Claude Code glob *pattern* to a regular expression.

    The ``*`` wildcard matches any character sequence that does not
    contain shell operators (``&&``, ``||``, ``|``, ``;``, ``>``).
    File-descriptor redirects (digit before ``>``) are allowed through.
    """
    parts = pattern.split("*")
    escaped = [re.escape(p) for p in parts]
    return re.compile("^" + _STAR.join(escaped) + "$", re.DOTALL)


def matches(cmd: str, pattern: str) -> bool:
    """Return True if *cmd* matches a Claude Code permission *pattern*.

    Supports:
    - Glob patterns with ``*`` (operator-aware, quote-aware)
    - Colon-style patterns from global settings (``git log:*``)
    """
    # Colon-style: "git log:*" matches "git log" and "git log --oneline".
    if ":" in pattern and not pattern.startswith("/"):
        prefix, suffix = pattern.split(":", 1)
        if suffix == "*":
            return cmd == prefix or cmd.startswith(prefix + " ")

    return bool(glob_to_regex(pattern).match(_mask_quoted_operators(cmd)))


def pattern_contains_operators(pattern: str) -> bool:
    """Return True if *pattern* itself contains literal shell operators."""
    return bool(_HAS_OPERATORS.search(pattern))


def find_repo_root() -> Path:
    """Walk up from CWD to find the nearest ``.git`` directory."""
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    return cwd
