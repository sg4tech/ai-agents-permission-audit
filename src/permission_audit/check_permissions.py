#!/usr/bin/env python3
"""Check bash commands against Claude Code permission rules.

Reads a TSV of bash commands (output of ``extract_bash_commands.py``)
and checks each against ``allow`` / ``deny`` rules from
``.claude/settings.local.json`` and ``~/.claude/settings.json``.

Claude Code matching semantics
------------------------------
``*`` in permission patterns does NOT match shell operators
(``&&``, ``||``, ``|``, ``;``, ``>``).  Claude Code splits compound
commands at shell operators and matches each segment independently.
A compound command is allowed only when **every** segment is allowed.

Usage::

    python extract_bash_commands.py   # refresh commands first
    python check_permissions.py
    python check_permissions.py --settings .claude/settings.local.json
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from permission_audit.claude_glob import find_repo_root, matches

# ---------------------------------------------------------------------------
# Pattern loading
# ---------------------------------------------------------------------------

def _extract_bash_patterns(perms: dict, key: str) -> list[str]:
    """Extract glob patterns from ``Bash(...)`` permission rules."""
    patterns: list[str] = []
    for rule in perms.get(key, []):
        m = re.match(r"^Bash\((.+)\)$", rule)
        if m:
            patterns.append(m.group(1))
    return patterns


def load_rules(
    settings_local: Path,
    settings_global: Path,
) -> tuple[list[str], list[str], list[str]]:
    """Return ``(deny, allow_local, allow_global)`` pattern lists."""
    local = json.loads(settings_local.read_text()) if settings_local.exists() else {}
    glb = json.loads(settings_global.read_text()) if settings_global.exists() else {}

    deny = _extract_bash_patterns(local.get("permissions", {}), "deny")
    allow_local = _extract_bash_patterns(local.get("permissions", {}), "allow")
    allow_global = _extract_bash_patterns(glb.get("permissions", {}), "allow")
    return deny, allow_local, allow_global


# ---------------------------------------------------------------------------
# Command splitting
# ---------------------------------------------------------------------------

def _split_command(cmd: str) -> list[str]:
    """Split *cmd* at unquoted shell operators, respecting quotes.

    Recognized operators: ``&&``, ``||``, ``|``, ``;``, ``>``, ``>>``.
    File-descriptor redirects (``2>&1``, ``2>/dev/null``) are **not**
    treated as operators.
    """
    segments: list[str] = []
    current: list[str] = []
    in_single = False
    in_double = False
    i = 0

    def _flush() -> None:
        seg = "".join(current).strip()
        if seg:
            segments.append(seg)
        current.clear()

    while i < len(cmd):
        c = cmd[i]

        # Track quoting state.
        if c == "'" and not in_double:
            in_single = not in_single
            current.append(c)
            i += 1
            continue
        if c == '"' and not in_single:
            in_double = not in_double
            current.append(c)
            i += 1
            continue
        if in_single or in_double:
            current.append(c)
            i += 1
            continue

        # && or ||
        if c in ("&", "|") and i + 1 < len(cmd) and cmd[i + 1] == c:
            _flush()
            i += 2
            continue

        # Single |
        if c == "|":
            _flush()
            i += 1
            continue

        # ;
        if c == ";":
            _flush()
            i += 1
            continue

        # > or >> — but NOT fd redirects (digit before >).
        if c == ">":
            if current and current[-1].isdigit():
                current.append(c)
                i += 1
                continue
            _flush()
            i += 1
            if i < len(cmd) and cmd[i] == ">":
                i += 1
            continue

        current.append(c)
        i += 1

    _flush()
    return segments


# ---------------------------------------------------------------------------
# Permission check
# ---------------------------------------------------------------------------

def check_command(
    cmd: str,
    deny: list[str],
    allow_local: list[str],
    allow_global: list[str],
) -> tuple[bool, str]:
    """Check whether *cmd* is allowed under Claude Code semantics.

    Algorithm:

    1. Check deny rules against the full command string.
    2. Check deny rules against each segment.
    3. Try matching the full command string against all allow patterns.
    4. If no full-string match, check that every segment individually
       matches some allow rule.

    Returns ``(is_allowed, reason)``.
    """
    # Deny: full string.
    for p in deny:
        if matches(cmd, p):
            return False, f"DENY:{p}"

    # Deny: per segment.
    segments = _split_command(cmd)
    for seg in segments:
        for p in deny:
            if matches(seg, p):
                return False, f"DENY:{p} (segment: {seg[:60]})"

    # Allow: full string.
    for p in allow_local:
        if matches(cmd, p):
            return True, f"local:{p}"
    for p in allow_global:
        if matches(cmd, p):
            return True, f"global:{p}"

    # Allow: every segment must match.
    if len(segments) <= 1:
        return False, "NO_MATCH"

    all_allow = allow_local + allow_global
    unmatched = [s for s in segments if not any(matches(s, p) for p in all_allow)]
    if not unmatched:
        return True, "all_segments_allowed"
    return False, f"SEGMENTS_NOT_ALLOWED: {'; '.join(s[:60] for s in unmatched)}"


def is_compound(cmd: str) -> bool:
    """Return True if *cmd* contains unquoted shell operators."""
    return len(_split_command(cmd)) > 1


def _is_noise(cmd: str) -> bool:
    """Return True for commands that are not real shell invocations."""
    stripped = cmd.lstrip()
    return stripped.startswith("#") or stripped in ("\\", "")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    repo_root = find_repo_root()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=repo_root / "audit-output" / "claude_bash_commands.tsv",
        help="Input TSV from extract_bash_commands.py",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=repo_root / ".claude" / "settings.local.json",
        help="Project settings.local.json path",
    )
    parser.add_argument(
        "--global-settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
        help="Global ~/.claude/settings.json path",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "audit-output",
        help="Directory for output TSV files",
    )
    args = parser.parse_args(argv)

    deny, allow_local, allow_global = load_rules(args.settings, args.global_settings)

    commands: list[tuple[int, str]] = []
    with open(args.input) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 1)
            if len(parts) == 2:
                commands.append((int(parts[0]), parts[1]))

    not_allowed: list[tuple[int, str, bool, str]] = []
    denied: list[tuple[int, str, str]] = []
    compound_not_allowed: list[tuple[int, str, str]] = []

    for count, cmd in commands:
        if _is_noise(cmd):
            continue
        allowed, reason = check_command(cmd, deny, allow_local, allow_global)
        compound = is_compound(cmd)
        if not allowed:
            if reason.startswith("DENY"):
                denied.append((count, cmd, reason))
            else:
                not_allowed.append((count, cmd, compound, reason))
                if compound:
                    compound_not_allowed.append((count, cmd, reason))

    not_allowed.sort(key=lambda x: -x[0])
    denied.sort(key=lambda x: -x[0])
    compound_not_allowed.sort(key=lambda x: -x[0])

    out_dir = args.output_dir
    out_dir.mkdir(exist_ok=True)
    _write_not_allowed(out_dir / "commands_not_allowed.tsv", not_allowed)
    _write_denied(out_dir / "commands_denied.tsv", denied)
    _write_compound(out_dir / "commands_compound_not_allowed.tsv", compound_not_allowed)

    total_inv = sum(c for c, _, _, _ in not_allowed)
    simple_count = sum(1 for _, _, comp, _ in not_allowed if not comp)
    compound_inv = sum(c for c, _, _ in compound_not_allowed)

    print(f"Commands checked: {len(commands)} unique")
    print(f"Not allowed:  {len(not_allowed)} unique ({total_inv} invocations)")
    print(f"  simple:     {simple_count}")
    print(f"  compound:   {len(compound_not_allowed)} ({compound_inv} invocations)")
    print(f"Denied:       {len(denied)} unique")
    print(f"\nOutput: {out_dir}/commands_*.tsv")


def _write_not_allowed(
    path: Path, rows: list[tuple[int, str, bool, str]]
) -> None:
    total = sum(c for c, _, _, _ in rows)
    with open(path, "w") as f:
        f.write("# Commands not matching any allow rule\n")
        f.write(f"# Unique: {len(rows)}, Invocations: {total}\n")
        f.write("# Format: count<tab>SIMPLE|COMPOUND<tab>command<tab>reason\n")
        for count, cmd, compound, reason in rows:
            tag = "COMPOUND" if compound else "SIMPLE"
            f.write(f"{count}\t{tag}\t{cmd}\t{reason}\n")


def _write_denied(path: Path, rows: list[tuple[int, str, str]]) -> None:
    with open(path, "w") as f:
        f.write("# Commands matching DENY rules\n")
        f.write(f"# Unique: {len(rows)}\n")
        f.write("# Format: count<tab>command<tab>deny_rule\n")
        for count, cmd, reason in rows:
            f.write(f"{count}\t{cmd}\t{reason}\n")


def _write_compound(path: Path, rows: list[tuple[int, str, str]]) -> None:
    total = sum(c for c, _, _ in rows)
    with open(path, "w") as f:
        f.write("# Compound commands not matching any allow rule\n")
        f.write(f"# Unique: {len(rows)}, Invocations: {total}\n")
        f.write("# Format: count<tab>command<tab>reason\n")
        for count, cmd, reason in rows:
            f.write(f"{count}\t{cmd}\t{reason}\n")


if __name__ == "__main__":
    main()
