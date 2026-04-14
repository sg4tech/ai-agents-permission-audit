#!/usr/bin/env python3
"""Suggest allow-rule patterns based on commands_not_allowed.tsv.

Reads the audit report and proposes minimal ``Bash(...)`` patterns ranked
by the number of commands and invocations they would cover.  Optionally
patches ``.claude/settings.local.json`` with the accepted suggestions.

Usage::

    python suggest_patterns.py
    python suggest_patterns.py --apply          # patch settings.local.json
    python suggest_patterns.py --min-count 2    # only suggest if covers >= 2 commands
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

from permission_audit.claude_glob import find_repo_root, matches
from permission_audit.check_permissions import _split_command, _extract_bash_patterns


# ---------------------------------------------------------------------------
# Reading the report
# ---------------------------------------------------------------------------

def _load_not_allowed(path: Path) -> list[tuple[int, str, str]]:
    """Return ``[(count, kind, command), ...]`` from commands_not_allowed.tsv."""
    rows: list[tuple[int, str, str]] = []
    if not path.exists():
        return rows
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split("\t", 3)
            if len(parts) >= 3:
                rows.append((int(parts[0]), parts[1], parts[2]))
    return rows


# ---------------------------------------------------------------------------
# Pattern generation
# ---------------------------------------------------------------------------

def _base_command(cmd: str) -> str:
    """Extract the executable name from a command string.

    Returns the first whitespace-delimited token, e.g.::

        ".venv/bin/pytest tests/ -v"  →  ".venv/bin/pytest"
        "git status"                  →  "git"
    """
    return cmd.strip().split()[0] if cmd.strip() else cmd


def _candidate_pattern(cmd: str) -> str:
    """Return a simple ``<base> *`` pattern for *cmd*.

    If the command has no arguments, returns an exact pattern.
    """
    base = _base_command(cmd)
    rest = cmd.strip()[len(base):].strip()
    return f"{base} *" if rest else base


def _unmatched_segments(cmd: str, existing: list[str]) -> list[str]:
    """Return segments of *cmd* not matched by any existing allow pattern."""
    segments = _split_command(cmd)
    return [s for s in segments if not any(matches(s, p) for p in existing)]


# ---------------------------------------------------------------------------
# Destructive command safelist
# ---------------------------------------------------------------------------

# Base executables that should never be auto-suggested.
# They are shown separately so the user can review and add manually.
_DESTRUCTIVE = frozenset({
    # File removal / overwrite
    "rm", "rmdir", "dd", "truncate", "shred",
    # File mutation
    "mv", "cp", "touch", "mkdir",
    # In-place text editing
    "sed", "awk", "perl",
    # Permissions / ownership
    "chmod", "chown", "chgrp",
    # Process control
    "kill", "pkill", "killall",
    # Disk / partition
    "mkfs", "fdisk", "parted",
    # Privilege escalation
    "sudo", "su",
    # Arbitrary code execution
    "python", "python3", "python2",
    "node", "ruby", "php", "bash", "sh", "zsh",
    # Arbitrary command execution via find -exec
    "find",
})


def _is_destructive(pattern: str) -> bool:
    """Return True if the pattern's base command is considered destructive."""
    base = _base_command(pattern).lstrip("./").split("/")[-1]
    return base in _DESTRUCTIVE


# ---------------------------------------------------------------------------
# Core suggestion logic
# ---------------------------------------------------------------------------

def build_suggestions(
    rows: list[tuple[int, str, str]],
    existing_allow: list[str],
) -> list[tuple[str, int, int]]:
    """Return suggested patterns ranked by coverage.

    Each entry is ``(pattern, unique_commands, total_invocations)``.
    Patterns that duplicate existing rules are excluded.
    """
    # pattern → set of commands it would newly cover
    pattern_cmds: dict[str, set[str]] = defaultdict(set)
    pattern_inv: dict[str, int] = defaultdict(int)

    for count, kind, cmd in rows:
        if kind == "SIMPLE":
            pat = _candidate_pattern(cmd)
            if not any(matches(cmd, p) for p in existing_allow):
                pattern_cmds[pat].add(cmd)
                pattern_inv[pat] += count
        else:
            # COMPOUND: find which segments need coverage
            unmatched = _unmatched_segments(cmd, existing_allow)
            for seg in unmatched:
                pat = _candidate_pattern(seg)
                pattern_cmds[pat].add(cmd)
                pattern_inv[pat] += count

    # Exclude patterns already covered by existing rules.
    suggestions = [
        (pat, len(cmds), pattern_inv[pat])
        for pat, cmds in pattern_cmds.items()
        if not any(matches(_base_command(pat), p) for p in existing_allow)
    ]

    # Sort: most invocations first, then most unique commands, then alphabetical.
    suggestions.sort(key=lambda x: (-x[2], -x[1], x[0]))
    return suggestions


def coverage_with(
    rows: list[tuple[int, str, str]],
    existing: list[str],
    new_patterns: list[str],
) -> tuple[int, int]:
    """Return ``(newly_covered_count, total_count)`` if *new_patterns* are added."""
    all_allow = existing + new_patterns
    covered = sum(
        1 for _, _, cmd in rows
        if any(matches(cmd, p) for p in all_allow)
        or all(
            any(matches(s, p) for p in all_allow)
            for s in _split_command(cmd)
        )
    )
    return covered, len(rows)


# ---------------------------------------------------------------------------
# Settings patching
# ---------------------------------------------------------------------------

def _load_settings(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def patch_settings(path: Path, new_patterns: list[str]) -> None:
    """Append *new_patterns* as ``Bash(...)`` rules to *path*."""
    data = _load_settings(path)
    perms = data.setdefault("permissions", {})
    allow: list[str] = perms.setdefault("allow", [])

    existing_rules = {r for r in allow}
    added = 0
    for pat in new_patterns:
        rule = f"Bash({pat})"
        if rule not in existing_rules:
            allow.append(rule)
            existing_rules.add(rule)
            added += 1

    path.write_text(json.dumps(data, indent=2) + "\n")
    print(f"Patched {path}: added {added} rule(s).")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    repo_root = find_repo_root()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=repo_root / "audit-output" / "commands_not_allowed.tsv",
        help="commands_not_allowed.tsv from check_permissions.py",
    )
    parser.add_argument(
        "--settings",
        type=Path,
        default=repo_root / ".claude" / "settings.local.json",
        help="Project settings.local.json",
    )
    parser.add_argument(
        "--global-settings",
        type=Path,
        default=Path.home() / ".claude" / "settings.json",
        help="Global ~/.claude/settings.json",
    )
    parser.add_argument(
        "--min-count", "-n",
        type=int,
        default=1,
        help="Only suggest patterns covering at least N unique commands (default: 1)",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Patch settings.local.json with all suggestions above --min-count",
    )
    args = parser.parse_args(argv)

    rows = _load_not_allowed(args.input)
    if not rows:
        print(f"No data in {args.input}. Run check_permissions.py first.")
        return

    local = _load_settings(args.settings).get("permissions", {})
    glb = _load_settings(args.global_settings).get("permissions", {})
    existing = (
        _extract_bash_patterns(local, "allow")
        + _extract_bash_patterns(glb, "allow")
    )

    all_suggestions = build_suggestions(rows, existing)
    all_suggestions = [(p, u, inv) for p, u, inv in all_suggestions if u >= args.min_count]

    if not all_suggestions:
        print("No suggestions (all commands already covered or below --min-count).")
        return

    safe = [(p, u, inv) for p, u, inv in all_suggestions if not _is_destructive(p)]
    destructive = [(p, u, inv) for p, u, inv in all_suggestions if _is_destructive(p)]

    if safe:
        print(f"Suggested allow rules ({len(safe)} total):\n")
        for pat, unique, inv in safe:
            print(f"  Bash({pat}){'':>{50 - len(pat)}}  # {unique} cmd(s), {inv} invocation(s)")

    if destructive:
        print(f"\nSkipped — destructive, review manually ({len(destructive)} total):\n")
        for pat, unique, inv in destructive:
            print(f"  Bash({pat}){'':>{50 - len(pat)}}  # {unique} cmd(s), {inv} invocation(s)")

    covered, total = coverage_with(rows, existing, [p for p, _, _ in safe])
    print(f"\nWith suggested rules: {covered}/{total} uncovered commands would be resolved.")

    if args.apply:
        patch_settings(args.settings, [p for p, _, _ in safe])


if __name__ == "__main__":
    main()
