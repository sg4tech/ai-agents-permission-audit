"""Tests for extract_bash_commands with approval-status tracking."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from permission_audit.extract_bash_commands import (
    CommandStats,
    _classify_status,
    _get_tool_result_text,
    _parse_ts,
    extract_commands_with_status,
    write_tsv,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ts(offset_s: float = 0.0) -> str:
    """Return an ISO-8601 timestamp at *offset_s* seconds past epoch."""
    from datetime import datetime, timezone, timedelta
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(seconds=offset_s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _tool_use_record(
    tool_id: str,
    command: str,
    ts_offset: float = 0.0,
) -> dict:
    return {
        "type": "assistant",
        "timestamp": _ts(ts_offset),
        "message": {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_id,
                    "name": "Bash",
                    "input": {"command": command},
                }
            ],
        },
    }


def _tool_result_record(
    tool_id: str,
    result_text: str,
    ts_offset: float = 0.5,
) -> dict:
    return {
        "type": "user",
        "timestamp": _ts(ts_offset),
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": result_text,
                    "is_error": False,
                }
            ],
        },
    }


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")


# ---------------------------------------------------------------------------
# Unit tests: _parse_ts
# ---------------------------------------------------------------------------

class TestParseTs:
    def test_z_suffix(self):
        """Timestamps with Z suffix (Claude Code's format) are parsed correctly."""
        result = _parse_ts("2026-01-01T00:00:00.000Z")
        assert result is not None
        assert result.year == 2026
        assert result.tzinfo is not None

    def test_explicit_offset(self):
        """Timestamps with explicit timezone offset are parsed."""
        result = _parse_ts("2026-01-01T05:00:00+05:00")
        assert result is not None
        assert result.hour == 5

    def test_malformed_returns_none(self):
        """Unparseable timestamp strings return None, not raise."""
        assert _parse_ts("not-a-date") is None

    def test_epoch_string_returns_none(self):
        """Numeric string (epoch) is not valid ISO-8601."""
        assert _parse_ts("1714000000") is None

    def test_none_input(self):
        assert _parse_ts(None) is None

    def test_empty_string(self):
        assert _parse_ts("") is None


# ---------------------------------------------------------------------------
# Unit tests: _classify_status
# ---------------------------------------------------------------------------

class TestClassifyStatus:
    def test_denied_message(self):
        assert _classify_status(None, None, "Permission to use Bash with command cat has been denied.") == "denied"

    def test_denied_partial_match(self):
        assert _classify_status(None, None, "Permission to use Bash with command git status has been denied.") == "denied"

    def test_fast_delta_is_auto(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1.0)
        assert _classify_status(t0, t1, "output") == "auto"

    def test_slow_delta_is_user(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=5.0)
        assert _classify_status(t0, t1, "output") == "user"

    def test_exactly_at_threshold_is_user(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=2.0)
        assert _classify_status(t0, t1, "output") == "user"

    def test_just_below_threshold_is_auto(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1.999)
        assert _classify_status(t0, t1, "output") == "auto"

    def test_missing_timestamps_defaults_to_user(self):
        assert _classify_status(None, None, "some output") == "user"

    def test_denied_takes_priority_over_fast_delta(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=0.1)
        assert _classify_status(t0, t1, "Permission to use Bash with command ls has been denied.") == "denied"

    def test_custom_threshold(self):
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 + timedelta(seconds=1.5)
        assert _classify_status(t0, t1, "output", threshold_s=1.0) == "user"
        assert _classify_status(t0, t1, "output", threshold_s=2.0) == "auto"

    def test_negative_delta_classified_as_auto(self):
        """Result timestamp before use timestamp (clock skew) → delta < 0 < threshold → auto."""
        from datetime import datetime, timezone, timedelta
        t0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
        t1 = t0 - timedelta(seconds=2.0)  # result is 2 s before the request
        assert _classify_status(t0, t1, "output") == "auto"


# ---------------------------------------------------------------------------
# Unit tests: _get_tool_result_text
# ---------------------------------------------------------------------------

class TestGetToolResultText:
    def test_string_content(self):
        block = {"type": "tool_result", "content": "hello world"}
        assert _get_tool_result_text(block) == "hello world"

    def test_list_content(self):
        block = {
            "type": "tool_result",
            "content": [{"type": "text", "text": "hello"}, {"type": "text", "text": "world"}],
        }
        assert _get_tool_result_text(block) == "hello world"

    def test_empty_content(self):
        block = {"type": "tool_result", "content": ""}
        assert _get_tool_result_text(block) == ""

    def test_missing_content(self):
        block = {"type": "tool_result"}
        assert _get_tool_result_text(block) == ""


# ---------------------------------------------------------------------------
# Integration tests: extract_commands_with_status
# ---------------------------------------------------------------------------

class TestExtractCommandsWithStatus:
    def test_auto_approved(self, tmp_path):
        """Delta < threshold → auto."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "git status", ts_offset=0.0),
            _tool_result_record("id1", "On branch master", ts_offset=0.5),
        ])
        stats = extract_commands_with_status([f])
        assert "git status" in stats
        s = stats["git status"]
        assert s.auto == 1
        assert s.user == 0
        assert s.denied == 0
        assert s.total == 1

    def test_user_approved(self, tmp_path):
        """Delta >= threshold → user."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "git status", ts_offset=0.0),
            _tool_result_record("id1", "On branch master", ts_offset=5.0),
        ])
        stats = extract_commands_with_status([f])
        s = stats["git status"]
        assert s.user == 1
        assert s.auto == 0
        assert s.denied == 0

    def test_denied(self, tmp_path):
        """Denied message → denied."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "cat /etc/passwd", ts_offset=0.0),
            _tool_result_record(
                "id1",
                "Permission to use Bash with command cat has been denied.",
                ts_offset=0.1,
            ),
        ])
        stats = extract_commands_with_status([f])
        s = stats["cat /etc/passwd"]
        assert s.denied == 1
        assert s.auto == 0
        assert s.user == 0

    def test_no_result_not_counted(self, tmp_path):
        """tool_use without matching tool_result → command not in output."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "git status", ts_offset=0.0),
            # no tool_result
        ])
        stats = extract_commands_with_status([f])
        assert "git status" not in stats

    def test_multiple_invocations_mixed_status(self, tmp_path):
        """Same command run 3×: auto, user, denied."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "ls", ts_offset=0.0),
            _tool_result_record("id1", "file.txt", ts_offset=0.3),      # auto
            _tool_use_record("id2", "ls", ts_offset=10.0),
            _tool_result_record("id2", "file.txt", ts_offset=15.0),     # user
            _tool_use_record("id3", "ls", ts_offset=20.0),
            _tool_result_record(
                "id3",
                "Permission to use Bash with command ls has been denied.",
                ts_offset=20.1,
            ),                                                            # denied
        ])
        stats = extract_commands_with_status([f])
        s = stats["ls"]
        assert s.auto == 1
        assert s.user == 1
        assert s.denied == 1
        assert s.total == 3

    def test_multiple_session_files(self, tmp_path):
        """Commands are aggregated across session files."""
        f1 = tmp_path / "s1.jsonl"
        f2 = tmp_path / "s2.jsonl"
        _write_jsonl(f1, [
            _tool_use_record("id1", "git status", ts_offset=0.0),
            _tool_result_record("id1", "clean", ts_offset=0.2),
        ])
        _write_jsonl(f2, [
            _tool_use_record("id2", "git status", ts_offset=0.0),
            _tool_result_record("id2", "clean", ts_offset=0.2),
        ])
        stats = extract_commands_with_status([f1, f2])
        assert stats["git status"].total == 2

    def test_non_bash_tool_ignored(self, tmp_path):
        """Non-Bash tool_use records are ignored."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            {
                "type": "assistant",
                "timestamp": _ts(0.0),
                "message": {
                    "role": "assistant",
                    "content": [{"type": "tool_use", "id": "id1", "name": "Read", "input": {"path": "/tmp/x"}}],
                },
            },
            _tool_result_record("id1", "content", ts_offset=0.2),
        ])
        stats = extract_commands_with_status([f])
        assert len(stats) == 0

    def test_custom_threshold(self, tmp_path):
        """Threshold can be overridden."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [
            _tool_use_record("id1", "git status", ts_offset=0.0),
            _tool_result_record("id1", "clean", ts_offset=1.5),
        ])
        stats_default = extract_commands_with_status([f])          # threshold=2.0
        stats_strict = extract_commands_with_status([f], threshold_s=1.0)

        assert stats_default["git status"].auto == 1   # 1.5 < 2.0
        assert stats_strict["git status"].user == 1    # 1.5 >= 1.0

    def test_result_text_from_list_content(self, tmp_path):
        """tool_result.content as list of text blocks — still detects denied."""
        f = tmp_path / "s.jsonl"
        rec = {
            "type": "user",
            "timestamp": _ts(0.1),
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "id1",
                        "content": [
                            {"type": "text", "text": "Permission to use Bash with command rm has been denied."}
                        ],
                        "is_error": False,
                    }
                ],
            },
        }
        _write_jsonl(f, [_tool_use_record("id1", "rm -rf /tmp/x", ts_offset=0.0), rec])
        stats = extract_commands_with_status([f])
        assert stats["rm -rf /tmp/x"].denied == 1

    def test_denied_via_tool_use_result_fallback(self, tmp_path):
        """block.content is empty but top-level toolUseResult carries the denial message."""
        f = tmp_path / "s.jsonl"
        rec = {
            "type": "user",
            "timestamp": _ts(0.1),
            "toolUseResult": "Permission to use Bash with command cat has been denied.",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": "id1",
                        "content": "",  # empty — triggers toolUseResult fallback
                        "is_error": False,
                    }
                ],
            },
        }
        _write_jsonl(f, [_tool_use_record("id1", "cat /etc/passwd", ts_offset=0.0), rec])
        stats = extract_commands_with_status([f])
        assert stats["cat /etc/passwd"].denied == 1

    def test_malformed_jsonl_lines_skipped(self, tmp_path, capsys):
        """Malformed JSON lines are skipped; valid lines still processed."""
        f = tmp_path / "s.jsonl"
        valid_use = json.dumps(_tool_use_record("id1", "git status", ts_offset=0.0))
        valid_result = json.dumps(_tool_result_record("id1", "ok", ts_offset=0.5))
        f.write_text(f"NOT JSON\n{valid_use}\n{{broken\n{valid_result}\n")

        stats = extract_commands_with_status([f])
        assert stats["git status"].auto == 1
        err = capsys.readouterr().err
        assert "skipped 2 malformed JSON lines" in err

    def test_missing_tool_use_id_skipped(self, tmp_path):
        """tool_use block without 'id' field is silently skipped, not crash."""
        f = tmp_path / "s.jsonl"
        # A tool_use block with no "id" key
        bad_record = {
            "type": "assistant",
            "timestamp": _ts(0.0),
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
                ],
            },
        }
        _write_jsonl(f, [bad_record])
        stats = extract_commands_with_status([f])
        assert len(stats) == 0  # no crash, no data

    def test_unresolved_pending_warning(self, tmp_path, capsys):
        """tool_use with no matching tool_result emits a warning."""
        f = tmp_path / "s.jsonl"
        _write_jsonl(f, [_tool_use_record("orphan1", "echo hello", ts_offset=0.0)])
        stats = extract_commands_with_status([f])
        assert len(stats) == 0
        err = capsys.readouterr().err
        assert "1 tool_use entries had no matching tool_result" in err

    def test_nonexistent_file_skipped(self, tmp_path, capsys):
        """Non-existent session file is skipped with a warning."""
        missing = tmp_path / "does_not_exist.jsonl"
        stats = extract_commands_with_status([missing])
        assert len(stats) == 0
        err = capsys.readouterr().err
        assert "warning" in err.lower()


# ---------------------------------------------------------------------------
# write_tsv
# ---------------------------------------------------------------------------

class TestWriteTsv:
    def test_format(self, tmp_path):
        stats = {
            "git status": CommandStats(auto=3, user=1, denied=0),  # total=4
            "rm -rf /": CommandStats(auto=0, user=0, denied=2),    # total=2
        }
        out = tmp_path / "out.tsv"
        write_tsv(stats, out)
        lines = [ln for ln in out.read_text().splitlines() if not ln.startswith("#")]
        # sorted by total desc: git (4) first, then rm (2)
        assert lines[0].startswith("4\t3\t1\t0\tgit status")
        assert lines[1].startswith("2\t0\t0\t2\trm -rf /")

    def test_sort_stability_equal_totals(self, tmp_path):
        """Commands with equal totals preserve insertion order (stable sort)."""
        stats = {
            "aaa": CommandStats(auto=1, user=0, denied=0),  # total=1
            "zzz": CommandStats(auto=0, user=1, denied=0),  # total=1
        }
        out = tmp_path / "out.tsv"
        write_tsv(stats, out)
        lines = [ln for ln in out.read_text().splitlines() if not ln.startswith("#")]
        assert lines[0].endswith("aaa")
        assert lines[1].endswith("zzz")

    def test_columns(self, tmp_path):
        stats = {"echo hi": CommandStats(auto=1, user=2, denied=3)}
        out = tmp_path / "out.tsv"
        write_tsv(stats, out)
        lines = [ln for ln in out.read_text().splitlines() if not ln.startswith("#")]
        parts = lines[0].split("\t")
        assert parts[0] == "6"   # total
        assert parts[1] == "1"   # auto
        assert parts[2] == "2"   # user
        assert parts[3] == "3"   # denied
        assert parts[4] == "echo hi"


# ---------------------------------------------------------------------------
# Backward-compat: check_permissions and find_redundant read new TSV format
# ---------------------------------------------------------------------------

class TestCheckPermissionsReadsNewFormat:
    def test_default_filters_to_user_approved(self, tmp_path, capsys):
        """By default only user-approved commands (user > 0) are checked."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        # git status: user=1 → included; ls -la: user=0 (auto only) → filtered out
        tsv.write_text(
            "# Format: total\tauto\tuser\tdenied\tcommand\n"
            "4\t3\t1\t0\tgit status\n"
            "2\t2\t0\t0\tls -la\n"
        )

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": [], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        # git status has user=1 → checked → not allowed → in output
        assert "git status" in not_allowed
        # ls -la has user=0 → filtered out → not checked → not in output
        assert "ls -la" not in not_allowed
        # summary mentions the filter
        assert "user-approved" in capsys.readouterr().out

    def test_all_flag_includes_auto_commands(self, tmp_path, capsys):
        """--all includes all commands regardless of status."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text(
            "# Format: total\tauto\tuser\tdenied\tcommand\n"
            "4\t3\t1\t0\tgit status\n"
            "2\t2\t0\t0\tls -la\n"
        )

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(git status)"], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
            "--all",
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        assert "ls -la" in not_allowed
        assert "git status" not in not_allowed
        # no status note when --all is used
        assert "user-approved" not in capsys.readouterr().out

    def test_denied_only_skipped_by_default(self, tmp_path):
        """Commands with denied>0 but user=0 are skipped by default filter.

        These commands were always rejected — the user never approved them.
        Use --all to include them in the audit.
        """
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text(
            "# Format: total\tauto\tuser\tdenied\tcommand\n"
            "2\t0\t0\t2\trm -rf /\n"   # denied only → skipped
            "1\t0\t1\t0\tgit status\n"  # user=1 → included
        )

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": [], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        assert "git status" in not_allowed
        assert "rm -rf /" not in not_allowed  # intentionally skipped; use --all to see

    def test_all_flag_includes_denied_only_commands(self, tmp_path):
        """--all includes commands with denied>0, user=0."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text(
            "# Format: total\tauto\tuser\tdenied\tcommand\n"
            "2\t0\t0\t2\trm -rf /\n"
        )

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": [], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
            "--all",
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        assert "rm -rf /" in not_allowed

    def test_empty_after_filter(self, tmp_path, capsys):
        """TSV with all user=0 rows → 0 commands checked, no crash."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text(
            "# Format: total\tauto\tuser\tdenied\tcommand\n"
            "3\t3\t0\t0\tnpm install\n"
            "1\t1\t0\t0\tgit fetch\n"
        )

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": [], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
        ])

        out = capsys.readouterr().out
        assert "Commands checked: 0 unique" in out
        assert (out_dir / "commands_not_allowed.tsv").exists()

    def test_reads_old_tsv(self, tmp_path, capsys):
        """Legacy count\tcmd format: status filter skipped, all commands included."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text("4\tgit status\n2\tls -la\n")

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(git status)"], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        assert "ls -la" in not_allowed
        assert "git status" not in not_allowed
        assert "legacy format" in capsys.readouterr().out

    def test_legacy_with_all_flag(self, tmp_path, capsys):
        """Legacy format + --all: legacy_format takes priority in status_note."""
        from permission_audit.check_permissions import main as check_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text("4\tgit status\n2\tls -la\n")

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({"permissions": {"allow": ["Bash(git status)"], "deny": []}}))

        out_dir = tmp_path / "out"
        check_main([
            "--input", str(tsv),
            "--settings", str(settings),
            "--global-settings", str(settings),
            "--output-dir", str(out_dir),
            "--all",
        ])

        not_allowed = (out_dir / "commands_not_allowed.tsv").read_text()
        # all commands included (legacy bypasses filter even without --all)
        assert "ls -la" in not_allowed
        # legacy_format takes priority over --all in status_note
        assert "legacy format" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# CommandStats: validation and increment method
# ---------------------------------------------------------------------------

class TestCommandStats:
    def test_total_property(self):
        assert CommandStats(auto=3, user=2, denied=1).total == 6

    def test_negative_auto_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            CommandStats(auto=-1)

    def test_negative_user_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            CommandStats(user=-1)

    def test_negative_denied_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            CommandStats(denied=-1)

    def test_increment_auto(self):
        s = CommandStats()
        s.increment("auto")
        assert s.auto == 1 and s.user == 0 and s.denied == 0

    def test_increment_user(self):
        s = CommandStats()
        s.increment("user")
        assert s.user == 1 and s.auto == 0 and s.denied == 0

    def test_increment_denied(self):
        s = CommandStats()
        s.increment("denied")
        assert s.denied == 1 and s.auto == 0 and s.user == 0

    def test_increment_unknown_raises(self):
        s = CommandStats()
        with pytest.raises(ValueError, match="Unknown status"):
            s.increment("unknown")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# find_redundant_rules TSV format compatibility
# ---------------------------------------------------------------------------

class TestFindRedundantReadsNewFormat:
    def test_reads_new_tsv(self, tmp_path, capsys):
        """find_redundant_rules reads the new TSV format and finds redundant rules."""
        from permission_audit.find_redundant_rules import main as redundant_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text("# Format: total\tauto\tuser\tdenied\tcommand\n3\t3\t0\t0\tgit status\n")

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(git status)", "Bash(git *)"], "deny": []}
        }))

        redundant_main(["--settings", str(settings), "--input", str(tsv)])
        out = capsys.readouterr().out
        # git status is covered by both rules → at least one is redundant
        assert "REDUNDANT" in out or "redundant" in out.lower()

    def test_reads_old_tsv(self, tmp_path, capsys):
        """find_redundant_rules handles legacy count\tcmd format."""
        from permission_audit.find_redundant_rules import main as redundant_main

        tsv = tmp_path / "commands.tsv"
        tsv.write_text("3\tgit status\n")

        settings = tmp_path / "settings.json"
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(git status)", "Bash(git *)"], "deny": []}
        }))

        redundant_main(["--settings", str(settings), "--input", str(tsv)])
        out = capsys.readouterr().out
        assert "REDUNDANT" in out or "redundant" in out.lower()
