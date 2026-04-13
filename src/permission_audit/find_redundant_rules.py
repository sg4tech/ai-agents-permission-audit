#!/usr/bin/env python3
"""Find redundant allow rules in settings.local.json.

Rule A is redundant if another rule B (B != A) matches every string
that A matches.  We test with both real commands from history and
synthetic samples generated from the pattern.

Usage::

    python extract_bash_commands.py   # refresh commands first
    python find_redundant_rules.py
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from permission_audit.claude_glob import find_repo_root, matches


def load_bash_allow(settings_path: Path) -> list[str]:
    """Return the list of Bash allow-rule glob patterns."""
    data = json.loads(settings_path.read_text())
    rules: list[str] = []
    for rule in data.get("permissions", {}).get("allow", []):
        m = re.match(r"^Bash\((.+)\)$", rule)
        if m:
            rules.append(m.group(1))
    return rules


def _generate_test_strings(pattern: str) -> list[str]:
    """Generate synthetic strings that should match *pattern*."""
    fillers = ["foo", "/some/path", "--verbose -x", "bar baz"]
    return [pattern.replace("*", f) for f in fillers]


def main(argv: list[str] | None = None) -> None:
    repo_root = find_repo_root()
    script_dir = Path(__file__).resolve().parent

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--settings",
        type=Path,
        default=repo_root / ".claude" / "settings.local.json",
        help="Path to settings.local.json",
    )
    parser.add_argument(
        "--input", "-i",
        type=Path,
        default=script_dir / "claude_bash_commands.tsv",
        help="Input TSV from extract_bash_commands.py",
    )
    args = parser.parse_args(argv)

    rules = load_bash_allow(args.settings)

    # Load real commands.
    real_commands: list[str] = []
    if args.input.exists():
        with open(args.input) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    real_commands.append(parts[1])

    # For each rule, find real commands it matches.
    rule_to_cmds: dict[str, list[str]] = {}
    for rule in rules:
        rule_to_cmds[rule] = [cmd for cmd in real_commands if matches(cmd, rule)]

    # Check redundancy: rule A is redundant if rule B covers all of A's matches.
    redundant: list[tuple[str, str, int]] = []
    for i, rule_a in enumerate(rules):
        test_strings = rule_to_cmds[rule_a] + _generate_test_strings(rule_a)
        if not test_strings:
            continue
        for j, rule_b in enumerate(rules):
            if i == j:
                continue
            if all(matches(t, rule_b) for t in test_strings):
                redundant.append((rule_a, rule_b, len(rule_to_cmds[rule_a])))
                break

    if not redundant:
        print("No redundant rules found.")
        return

    print(f"Found {len(redundant)} potentially redundant rules:\n")
    for rule_a, rule_b, num_cmds in redundant:
        print(f"  REDUNDANT:  Bash({rule_a})")
        print(f"  COVERED BY: Bash({rule_b})")
        print(f"  (matched {num_cmds} real commands)")
        print()


if __name__ == "__main__":
    main()
