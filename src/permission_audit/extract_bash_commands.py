#!/usr/bin/env python3
"""Extract Bash tool_use commands from Claude Code JSONL session history.

Scans ``~/.claude/projects/`` for JSONL files matching a project slug,
parses ``tool_use`` blocks with ``name=Bash``, and writes a
frequency-sorted TSV with per-command approval-status breakdown.

Approval status is inferred from the time delta between tool_use and
tool_result timestamps:

* **auto**   — delta < threshold (default 2 s); Claude Code approved
  the command instantly via a matching allow rule.
* **user**   — delta >= threshold; the user saw the approval dialog
  and clicked "Allow" manually.
* **denied** — the tool_result text contains the Claude Code permission
  denial message (``"Permission to use Bash with command … has been denied."``).

Note: slow commands that were auto-approved (e.g. ``npm install``) will
be classified as **user** because their execution time pushes the delta
above the threshold.  This is a known limitation of the heuristic.

Output TSV columns::

    total<tab>auto<tab>user<tab>denied<tab>command

Usage::

    python extract_bash_commands.py                     # auto-detect from CWD
    python extract_bash_commands.py --project myproject  # explicit slug
    python extract_bash_commands.py --output /tmp/out.tsv
    python extract_bash_commands.py --threshold 3.0     # custom delta threshold
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from permission_audit.claude_glob import find_repo_root

_CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"

# Regex matching the Claude Code permission denial message.
_DENIED_RE = re.compile(
    r"Permission to use Bash with command .+ has been denied\.",
    re.IGNORECASE,
)

# Default threshold: deltas below this are classified as auto-approved.
_AUTO_THRESHOLD_S: float = 2.0


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CommandStats:
    """Per-command approval-status counters."""

    auto: int = 0    # approved instantly by allow rule
    user: int = 0    # user approved manually (slow delta)
    denied: int = 0  # blocked by permission check

    @property
    def total(self) -> int:
        return self.auto + self.user + self.denied


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _parse_ts(ts_str: str | None) -> datetime | None:
    """Parse an ISO-8601 timestamp string; return None on failure."""
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except ValueError:
        return None


def _get_tool_result_text(block: dict) -> str:
    """Extract plain text from a tool_result block's ``content`` field."""
    content = block.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            item.get("text", "") for item in content if isinstance(item, dict)
        )
    return ""


def _classify_status(
    use_ts: datetime | None,
    result_ts: datetime | None,
    result_text: str,
    threshold_s: float = _AUTO_THRESHOLD_S,
) -> str:
    """Return ``'auto'``, ``'user'``, or ``'denied'``."""
    if _DENIED_RE.search(result_text):
        return "denied"
    if use_ts is None or result_ts is None:
        return "user"
    delta = (result_ts - use_ts).total_seconds()
    return "auto" if delta < threshold_s else "user"


# ---------------------------------------------------------------------------
# Project slug / session-file discovery
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_commands_with_status(
    session_files: list[Path],
    threshold_s: float = _AUTO_THRESHOLD_S,
) -> dict[str, CommandStats]:
    """Parse Bash commands with approval status from JSONL session files.

    Each file is processed in order.  For each ``tool_use`` / ``tool_result``
    pair the delta between their timestamps determines the approval status.
    Commands whose ``tool_use`` has no matching ``tool_result`` in the file
    are silently skipped (conversation ended before the result was recorded).
    """
    stats: dict[str, CommandStats] = {}

    for path in session_files:
        # Map tool_use_id → (command, use_timestamp) for unresolved tool calls.
        pending: dict[str, tuple[str, datetime | None]] = {}

        try:
            fh = open(path)
        except OSError:
            continue

        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rec_ts = _parse_ts(rec.get("timestamp"))
                msg = rec.get("message", rec)
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content", [])
                if isinstance(content, str):
                    continue

                for block in content or []:
                    if not isinstance(block, dict):
                        continue

                    btype = block.get("type")

                    if btype == "tool_use" and block.get("name") == "Bash":
                        cmd = block.get("input", {}).get("command", "")
                        if cmd:
                            pending[block["id"]] = (cmd, rec_ts)

                    elif btype == "tool_result":
                        tool_id = block.get("tool_use_id", "")
                        if tool_id not in pending:
                            continue
                        cmd, use_ts = pending.pop(tool_id)

                        result_text = _get_tool_result_text(block)
                        if not result_text:
                            result_text = rec.get("toolUseResult", "")

                        status = _classify_status(use_ts, rec_ts, result_text, threshold_s)

                        entry = stats.setdefault(cmd, CommandStats())
                        if status == "auto":
                            entry.auto += 1
                        elif status == "user":
                            entry.user += 1
                        else:
                            entry.denied += 1

    return stats


# ---------------------------------------------------------------------------
# TSV output
# ---------------------------------------------------------------------------

def write_tsv(stats: dict[str, CommandStats], output: Path) -> None:
    """Write *stats* as a frequency-sorted TSV file.

    Columns: ``total<tab>auto<tab>user<tab>denied<tab>command``
    """
    rows = sorted(stats.items(), key=lambda x: -x[1].total)
    with open(output, "w") as f:
        f.write("# Format: total\tauto\tuser\tdenied\tcommand\n")
        for cmd, s in rows:
            f.write(f"{s.total}\t{s.auto}\t{s.user}\t{s.denied}\t{cmd}\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

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
        help="Output TSV path (default: claude_bash_commands.tsv in audit-output/)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=_AUTO_THRESHOLD_S,
        help=f"Delta threshold in seconds for auto vs user classification (default: {_AUTO_THRESHOLD_S})",
    )
    args = parser.parse_args(argv)

    repo_root = find_repo_root()
    slug = args.project or _detect_project_slug(repo_root)
    out_dir = repo_root / "audit-output"
    out_dir.mkdir(exist_ok=True)
    output = args.output or out_dir / "claude_bash_commands.tsv"

    files = find_session_files(slug)
    if not files:
        print(f"No session files found for slug: {slug}")
        return

    stats = extract_commands_with_status(files, threshold_s=args.threshold)
    write_tsv(stats, output)

    total_invocations = sum(s.total for s in stats.values())
    print(
        f"Extracted {len(stats)} unique commands "
        f"({total_invocations} total) -> {output}"
    )
    auto = sum(s.auto for s in stats.values())
    user = sum(s.user for s in stats.values())
    denied = sum(s.denied for s in stats.values())
    print(f"  auto: {auto}  user: {user}  denied: {denied}")


if __name__ == "__main__":
    main()
