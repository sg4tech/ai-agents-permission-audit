"""Tests for claude_glob — Claude Code permission glob matcher."""

from permission_audit.claude_glob import (
    _mask_quoted_operators,
    glob_to_regex,
    matches,
    pattern_contains_operators,
)


# ---------------------------------------------------------------------------
# glob_to_regex basics
# ---------------------------------------------------------------------------

class TestGlobToRegex:
    def test_literal_match(self):
        assert glob_to_regex("git status").match("git status")

    def test_literal_no_match(self):
        assert not glob_to_regex("git status").match("git diff")

    def test_star_matches_simple_suffix(self):
        assert glob_to_regex("git *").match("git status")

    def test_star_matches_long_suffix(self):
        assert glob_to_regex("git *").match("git log --oneline --graph")

    def test_star_matches_middle(self):
        assert glob_to_regex("cd * && git *").match("cd mydir && git status")

    def test_star_empty_match(self):
        """* can match the empty string."""
        assert glob_to_regex("git *").match("git ")


# ---------------------------------------------------------------------------
# Operator blocking — * must NOT cross real shell operators
# ---------------------------------------------------------------------------

class TestOperatorBlocking:
    def test_star_blocks_double_ampersand(self):
        assert not glob_to_regex("git *").match("git status && git diff")

    def test_star_blocks_double_pipe(self):
        assert not glob_to_regex("git *").match("git status || git diff")

    def test_star_blocks_single_pipe(self):
        assert not glob_to_regex("ls *").match("ls foo | grep bar")

    def test_star_blocks_semicolon(self):
        assert not glob_to_regex("echo *").match("echo foo ; echo bar")

    def test_stdout_redirect_not_blocked(self):
        """VERIFIED: > is a redirect, not an operator — * crosses it."""
        assert glob_to_regex("echo *").match("echo foo > file.txt")

    def test_append_redirect_not_blocked(self):
        """VERIFIED: >> is a redirect, not an operator — * crosses it."""
        assert glob_to_regex("echo *").match("echo foo >> file.txt")


# ---------------------------------------------------------------------------
# fd redirects (2>&1, 2>/dev/null) — NOT operators
# ---------------------------------------------------------------------------

class TestFdRedirects:
    def test_2_redirect_stdout(self):
        assert glob_to_regex("make *").match("make verify 2>&1")

    def test_2_redirect_devnull_requires_double_star(self):
        """2>/dev/null contains a slash — * won't match it, ** will."""
        assert not glob_to_regex("grep *").match("grep foo file 2>/dev/null")
        assert glob_to_regex("grep **").match("grep foo file 2>/dev/null")

    def test_1_redirect_requires_double_star(self):
        """1>/dev/null contains a slash — * won't match it, ** will."""
        assert not glob_to_regex("cmd *").match("cmd arg 1>/dev/null")
        assert glob_to_regex("cmd **").match("cmd arg 1>/dev/null")

    def test_mixed_fd_and_pipe(self):
        """2>&1 is ok but | still blocks."""
        assert not glob_to_regex("make *").match("make verify 2>&1 | tail -3")


# ---------------------------------------------------------------------------
# Slash blocking — * must NOT cross /; ** must cross /
# ---------------------------------------------------------------------------

class TestSlashBlocking:
    """VERIFIED: * does not match / in Claude Code permission patterns.

    Confirmed: ``cat README.md`` auto-approved by ``Bash(cat *)``;
    ``cat /tmp/file`` prompted (not auto-approved) with the same rule.
    ``**`` is HYPOTHESIZED to allow slashes (mirrors standard glob semantics).
    """

    def test_star_does_not_match_absolute_path(self):
        """VERIFIED: * does not match / — absolute path arg not covered by cat *."""
        assert not glob_to_regex("cat *").match("cat /etc/hosts")

    def test_star_matches_relative_path(self):
        """VERIFIED: * matches relative path (no slash)."""
        assert glob_to_regex("cat *").match("cat README.md")

    def test_star_does_not_match_slash_in_middle(self):
        assert not glob_to_regex("ls *").match("ls /tmp")

    def test_double_star_matches_absolute_path(self):
        """HYPOTHESIZED: ** matches / — covers absolute path args."""
        assert glob_to_regex("cat **").match("cat /etc/hosts")

    def test_double_star_matches_nested_path(self):
        """HYPOTHESIZED: ** matches multiple path components."""
        assert glob_to_regex("cat **").match("cat /Users/viktor/.claude/settings.json")

    def test_double_star_still_blocks_operators(self):
        """** does not cross shell operators."""
        assert not glob_to_regex("cat **").match("cat file | grep foo")

    def test_star_matches_path_component_after_slash(self):
        """* matches a single path component (no slash in it)."""
        assert glob_to_regex(".venv/bin/*").match(".venv/bin/pytest")

    def test_star_does_not_match_nested_path_component(self):
        """* does not cross / — nested paths require **."""
        assert not glob_to_regex(".venv/bin/*").match(".venv/bin/python3/nested")


# ---------------------------------------------------------------------------
# _mask_quoted_operators
# ---------------------------------------------------------------------------

class TestMaskQuotedOperators:
    def test_pipe_in_double_quotes(self):
        masked = _mask_quoted_operators('grep "a|b" file')
        assert "|" not in masked.split('"')[1]

    def test_semicolon_in_double_quotes(self):
        masked = _mask_quoted_operators('python -c "import foo; print(1)"')
        assert ";" not in masked.split('"')[1]

    def test_pipe_in_single_quotes(self):
        masked = _mask_quoted_operators("grep 'a|b' file")
        assert "|" not in masked.split("'")[1]

    def test_ampersand_in_double_quotes(self):
        masked = _mask_quoted_operators('echo "a && b"')
        assert "&" not in masked.split('"')[1]

    def test_redirect_in_double_quotes(self):
        """> is not an operator so quoting it has no effect on matching."""
        masked = _mask_quoted_operators('echo "a > b"')
        assert ">" in masked  # > is not masked — it's not an operator

    def test_backslash_escaped_pipe(self):
        masked = _mask_quoted_operators("grep foo\\|bar file")
        assert masked.count("|") == 0

    def test_operators_outside_quotes_unchanged(self):
        masked = _mask_quoted_operators("cmd1 && cmd2 | cmd3")
        assert "&&" in masked
        assert "|" in masked

    def test_backslash_in_single_quotes_literal(self):
        """In single quotes, backslash is literal — not an escape."""
        masked = _mask_quoted_operators("echo '\\|'")
        assert "|" not in masked


# ---------------------------------------------------------------------------
# matches() — full integration
# ---------------------------------------------------------------------------

class TestMatches:
    # --- Basic glob ---
    def test_simple_match(self):
        assert matches("git status", "git *")

    def test_simple_no_match(self):
        assert not matches("ls foo", "git *")

    def test_exact_match(self):
        assert matches("git status", "git status")

    # --- Exact pattern prefix matching (no wildcard) ---
    def test_exact_prefix_with_extra_args(self):
        """Exact pattern matches command with additional arguments."""
        assert matches("git status --short", "git status")

    def test_exact_prefix_with_fd_redirect(self):
        """Exact pattern matches command with 2>&1 appended."""
        assert matches("mypy src/ --strict 2>&1", "mypy src/ --strict")

    def test_exact_prefix_no_partial_word(self):
        """Exact pattern does NOT match if extra text is not space-separated."""
        assert not matches("git statuslong", "git status")

    def test_exact_prefix_shorter_cmd_no_match(self):
        """Shorter command does not match longer exact pattern."""
        assert not matches("git", "git status")

    # --- Operator blocking ---
    def test_ampersand_blocks(self):
        assert not matches("git status && git diff", "git *")

    def test_pipe_blocks(self):
        assert not matches("ls | grep foo", "ls *")

    def test_semicolon_blocks(self):
        assert not matches("echo a ; echo b", "echo *")

    def test_redirect_passes(self):
        """VERIFIED: > is not an operator — * matches across it."""
        assert matches("echo foo > out.txt", "echo *")

    # --- fd redirects pass through ---
    def test_2_redirect_1(self):
        assert matches("make verify 2>&1", "make *")

    def test_2_redirect_devnull_requires_double_star(self):
        assert not matches("ls foo 2>/dev/null", "ls *")
        assert matches("ls foo 2>/dev/null", "ls **")

    # --- Quoted operators pass through ---
    def test_pipe_in_double_quotes(self):
        assert matches('grep -n "^def \\|^class " foo.py', "grep *")

    def test_semicolon_in_double_quotes(self):
        assert matches('.venv/bin/python -c "import foo; print(1)"', ".venv/bin/*")

    def test_pipe_in_single_quotes(self):
        assert matches("grep -n '^def |^class ' foo.py", "grep *")

    def test_semicolon_in_single_quotes(self):
        assert matches(".venv/bin/python -c 'import foo; print(1)'", ".venv/bin/*")

    def test_ampersand_in_double_quotes(self):
        assert matches('echo "a && b"', "echo *")

    def test_backslash_escaped_pipe(self):
        assert matches("grep foo\\|bar file", "grep *")

    # --- Compound patterns with literal operators ---
    def test_compound_pattern(self):
        assert matches("cd mydir && git status", "cd * && git *")

    def test_compound_pattern_with_absolute_path(self):
        """cd /tmp requires ** since * does not match /."""
        assert not matches("cd /tmp && git status", "cd * && git *")
        assert matches("cd /tmp && git status", "cd ** && git *")

    def test_compound_pattern_no_match(self):
        assert not matches("cd mydir && ls", "cd * && git *")

    def test_compound_three_parts(self):
        assert matches(
            "cd mydir && git add . && git commit -m 'x'",
            "cd * && git * && git *",
        )

    # --- Colon-style patterns ---
    def test_colon_exact(self):
        assert matches("git log", "git log:*")

    def test_colon_with_args(self):
        assert matches("git log --oneline", "git log:*")

    def test_colon_no_match(self):
        assert not matches("git status", "git log:*")

    def test_colon_prefix_no_space(self):
        """'git log:*' should not match 'git logger'."""
        assert not matches("git logger", "git log:*")

    # --- Colon in path (not a colon-style pattern) ---
    def test_colon_in_path_not_pattern(self):
        assert matches("/usr/bin/foo", "/usr/bin/*")


# ---------------------------------------------------------------------------
# pattern_contains_operators
# ---------------------------------------------------------------------------

class TestPatternContainsOperators:
    def test_plain_pattern(self):
        assert not pattern_contains_operators("git *")

    def test_pattern_with_ampersand(self):
        assert pattern_contains_operators("cd * && git *")

    def test_pattern_with_pipe(self):
        assert pattern_contains_operators("ls * | grep *")

    def test_pattern_with_semicolon(self):
        assert pattern_contains_operators("echo * ; echo *")

    def test_pattern_with_redirect(self):
        """> is not an operator, so a pattern with > does not contain operators."""
        assert not pattern_contains_operators("git show * > /tmp/*")
