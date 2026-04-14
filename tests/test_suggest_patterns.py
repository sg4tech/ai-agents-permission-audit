"""Tests for suggest_patterns — pattern suggestion and coverage logic."""

from __future__ import annotations

import json
from pathlib import Path

from permission_audit.suggest_patterns import (
    _base_command,
    _candidate_pattern,
    _is_destructive,
    _load_not_allowed,
    _unmatched_segments,
    build_suggestions,
    coverage_with,
    patch_settings,
)


# ---------------------------------------------------------------------------
# _base_command
# ---------------------------------------------------------------------------

class TestBaseCommand:
    def test_simple(self):
        assert _base_command("git status") == "git"

    def test_venv_path(self):
        assert _base_command(".venv/bin/pytest tests/") == ".venv/bin/pytest"

    def test_no_args(self):
        assert _base_command("ls") == "ls"

    def test_leading_whitespace(self):
        assert _base_command("  git status") == "git"


# ---------------------------------------------------------------------------
# _candidate_pattern
# ---------------------------------------------------------------------------

class TestCandidatePattern:
    def test_with_args(self):
        assert _candidate_pattern("git status") == "git *"

    def test_no_args(self):
        assert _candidate_pattern("ls") == "ls"

    def test_venv_path_with_args(self):
        assert _candidate_pattern(".venv/bin/pytest tests/") == ".venv/bin/pytest *"

    def test_venv_path_no_args(self):
        assert _candidate_pattern(".venv/bin/pytest") == ".venv/bin/pytest"


# ---------------------------------------------------------------------------
# _is_destructive
# ---------------------------------------------------------------------------

class TestIsDestructive:
    def test_rm(self):
        assert _is_destructive("rm *")

    def test_mv(self):
        assert _is_destructive("mv *")

    def test_sed(self):
        assert _is_destructive("sed *")

    def test_python(self):
        assert _is_destructive("python *")

    def test_find(self):
        assert _is_destructive("find *")

    def test_venv_python(self):
        assert _is_destructive(".venv/bin/python *")

    def test_safe_ls(self):
        assert not _is_destructive("ls *")

    def test_safe_grep(self):
        assert not _is_destructive("grep *")

    def test_safe_cat(self):
        assert not _is_destructive("cat *")

    def test_safe_venv_pytest(self):
        assert not _is_destructive(".venv/bin/pytest *")


# ---------------------------------------------------------------------------
# _load_not_allowed
# ---------------------------------------------------------------------------

class TestLoadNotAllowed:
    def test_missing_file(self, tmp_path: Path):
        rows = _load_not_allowed(tmp_path / "nonexistent.tsv")
        assert rows == []

    def test_parses_simple_rows(self, tmp_path: Path):
        f = tmp_path / "not_allowed.tsv"
        f.write_text(
            "# comment\n"
            "3\tSIMPLE\tgit status\tNO_MATCH\n"
            "1\tCOMPOUND\tls | grep foo\tSEGMENTS_NOT_ALLOWED: grep foo\n"
        )
        rows = _load_not_allowed(f)
        assert rows == [
            (3, "SIMPLE", "git status"),
            (1, "COMPOUND", "ls | grep foo"),
        ]

    def test_skips_blank_lines(self, tmp_path: Path):
        f = tmp_path / "not_allowed.tsv"
        f.write_text("\n\n2\tSIMPLE\tls\tNO_MATCH\n\n")
        rows = _load_not_allowed(f)
        assert len(rows) == 1


# ---------------------------------------------------------------------------
# _unmatched_segments
# ---------------------------------------------------------------------------

class TestUnmatchedSegments:
    def test_simple_unmatched(self):
        segs = _unmatched_segments("curl http://example.com", ["git *"])
        assert segs == ["curl http://example.com"]

    def test_simple_matched(self):
        segs = _unmatched_segments("git status", ["git *"])
        assert segs == []

    def test_compound_one_unmatched(self):
        segs = _unmatched_segments("git status && curl foo", ["git *"])
        assert segs == ["curl foo"]

    def test_compound_all_matched(self):
        segs = _unmatched_segments("git status && ls tmp", ["git *", "ls *"])
        assert segs == []


# ---------------------------------------------------------------------------
# build_suggestions
# ---------------------------------------------------------------------------

class TestBuildSuggestions:
    def test_simple_command_suggests_glob(self):
        rows = [(2, "SIMPLE", "ls foo")]
        suggestions = build_suggestions(rows, [])
        patterns = [p for p, _, _ in suggestions]
        assert "ls *" in patterns

    def test_compound_suggests_unmatched_segment(self):
        rows = [(1, "COMPOUND", "git status && curl foo")]
        suggestions = build_suggestions(rows, ["git *"])
        patterns = [p for p, _, _ in suggestions]
        assert "curl *" in patterns
        assert "git *" not in patterns  # already covered

    def test_already_covered_not_suggested(self):
        rows = [(1, "SIMPLE", "git status")]
        suggestions = build_suggestions(rows, ["git *"])
        assert suggestions == []

    def test_sorted_by_invocations(self):
        rows = [
            (5, "SIMPLE", "ls foo"),
            (1, "SIMPLE", "cat file"),
            (3, "SIMPLE", "echo hello"),
        ]
        suggestions = build_suggestions(rows, [])
        invocations = [inv for _, _, inv in suggestions]
        assert invocations == sorted(invocations, reverse=True)

    def test_count_aggregates_across_commands(self):
        rows = [
            (2, "SIMPLE", "ls foo"),
            (3, "SIMPLE", "ls bar"),
        ]
        suggestions = build_suggestions(rows, [])
        ls_entry = next((p, u, inv) for p, u, inv in suggestions if p == "ls *")
        assert ls_entry[1] == 2   # 2 unique commands
        assert ls_entry[2] == 5   # 5 total invocations


# ---------------------------------------------------------------------------
# coverage_with
# ---------------------------------------------------------------------------

class TestCoverageWith:
    ROWS = [
        (1, "SIMPLE", "ls foo"),
        (1, "SIMPLE", "cat file"),
        (1, "COMPOUND", "git status && curl foo"),
    ]

    def test_no_new_patterns(self):
        covered, total = coverage_with(self.ROWS, [], [])
        assert covered == 0
        assert total == 3

    def test_covers_simple(self):
        covered, total = coverage_with(self.ROWS, [], ["ls *"])
        assert covered == 1
        assert total == 3

    def test_covers_compound_via_segments(self):
        covered, total = coverage_with(self.ROWS, [], ["git *", "curl *"])
        assert covered == 1

    def test_covers_all(self):
        covered, total = coverage_with(self.ROWS, [], ["ls *", "cat *", "git *", "curl *"])
        assert covered == 3
        assert total == 3

    def test_existing_rules_count(self):
        covered, total = coverage_with(self.ROWS, ["ls *"], ["cat *"])
        assert covered == 2


# ---------------------------------------------------------------------------
# patch_settings
# ---------------------------------------------------------------------------

class TestPatchSettings:
    def test_creates_file_if_missing(self, tmp_path: Path):
        path = tmp_path / "settings.local.json"
        patch_settings(path, ["git *"])
        data = json.loads(path.read_text())
        assert "Bash(git *)" in data["permissions"]["allow"]

    def test_appends_to_existing(self, tmp_path: Path):
        path = tmp_path / "settings.local.json"
        path.write_text(json.dumps({"permissions": {"allow": ["Bash(ls *)"]}}))
        patch_settings(path, ["git *"])
        data = json.loads(path.read_text())
        allow = data["permissions"]["allow"]
        assert "Bash(ls *)" in allow
        assert "Bash(git *)" in allow

    def test_no_duplicate_rules(self, tmp_path: Path):
        path = tmp_path / "settings.local.json"
        path.write_text(json.dumps({"permissions": {"allow": ["Bash(git *)"]}}))
        patch_settings(path, ["git *"])
        data = json.loads(path.read_text())
        assert data["permissions"]["allow"].count("Bash(git *)") == 1

    def test_multiple_patterns(self, tmp_path: Path):
        path = tmp_path / "settings.local.json"
        patch_settings(path, ["ls *", "cat *", "echo *"])
        data = json.loads(path.read_text())
        allow = data["permissions"]["allow"]
        assert len(allow) == 3
