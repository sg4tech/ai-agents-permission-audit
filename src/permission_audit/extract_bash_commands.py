#!/usr/bin/env python3
"""Extract Bash tool_use commands from Claude Code JSONL session history.

Scans ``~/.claude/projects/`` for JSONL files matching a project slug,
parses ``tool_use`` blocks with ``name=Bash``, and writes a
frequency-sorted TSV.

Usage::

    python extract_bash_commands.py                     # auto-detect from CWD
    python extract_bash_commands.py --project myproject  # explicit slug
    python extract_bash_commands.py --output /tmp/out.tsv
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from permission_audit.claude_glob import find_repo_root

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


def _detect_project_slug(repo_root: Path) -> str:
    """Derive the Claude Code project slug from a repository path.

    Claude Code encodes the absolute path by replacing ``/`` with ``-``
    and stripping the leading slash, e.g.
    ``/Users/me/project`` → ``-Users-me-project``.
    """
    return "-" + str(repo_root).replace("/", "-").lstrip("-")


def find_session_files(slug: str) -> list[Path]:
    """Return all JSONL session files matching *slug*."""
    results: list[Path] = []
    if not _CLAUDE_PROJECTS.is_dir():
        return results
    for entry in _CLAUDE_PROJECTS.iterdir():
        if entry.is_dir() and entry.name.startswith(slug):
            results.extend(entry.rglob("*.jsonl"))
    return sorted(results)


def extract_commands(session_files: list[Path]) -> Counter[str]:
    """Parse Bash commands from a list of JSONL session files."""
    commands: Counter[str] = Counter()
    for path in session_files:
        with open(path) as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                msg = rec.get("message", rec)
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", [])
                if isinstance(content, str):
                    continue
                for block in content or []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    if block.get("name") != "Bash":
                        continue
                    cmd = block.get("input", {}).get("command", "")
                    if cmd:
                        commands[cmd] += 1
    return commands


def write_tsv(commands: Counter[str], output: Path) -> None:
    """Write *commands* as a frequency-sorted TSV file."""
    with open(output, "w") as f:
        for cmd, count in commands.most_common():
            f.write(f"{count}\t{cmd}\n")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project",
        default=None,
        help="Project slug (auto-detected from repo root if omitted)",
    )
    parser.add_argument(
        "--output", "-o",
        type=Path,
        default=None,
        help="Output TSV path (default: claude_bash_commands.tsv in script dir)",
    )
    args = parser.parse_args(argv)

    slug = args.project or _detect_project_slug(find_repo_root())
    output = args.output or Path(__file__).resolve().parent / "claude_bash_commands.tsv"

    files = find_session_files(slug)
    if not files:
        print(f"No session files found for slug: {slug}")
        return

    commands = extract_commands(files)
    write_tsv(commands, output)
    print(
        f"Extracted {len(commands)} unique commands "
        f"({sum(commands.values())} total) -> {output}"
    )


if __name__ == "__main__":
    main()
