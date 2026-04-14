"""Behavioral specification for Claude Code permission matching.

Each test documents *observed or inferred* behavior of Claude Code's
permission system.  Tests are annotated with their verification status:

    VERIFIED       — confirmed by live testing against Claude Code
    HYPOTHESIZED   — inferred from docs or code analysis; not yet live-tested
    UNVERIFIABLE   — cannot be confirmed via conversational-mode live tests;
                     requires agentic-mode testing (see note below)

Methodology note
----------------
Live tests run in *conversational mode* (user watches every step) are
unreliable for verifying allow-rule matching.  In that mode most commands
run freely regardless of whether an allow rule matches — only DENY rules
and network access are consistently enforced.  Tests confirming a command
*was blocked* are reliable; tests confirming a command *ran without a prompt*
are NOT reliable for allow-rule verification.

Verification procedure
----------------------
See docs/behavior_verification.md for step-by-step instructions on how
to verify each group against real Claude Code.

When a hypothesis is confirmed or refuted, update the annotation and
add the observed result to the corresponding section in
docs/behavior_verification.md.
"""

from __future__ import annotations

from permission_audit.claude_glob import matches
from permission_audit.check_permissions import check_command, _split_command


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# ===========================================================================
# 1. EXACT PATTERNS — prefix-match semantics          [VERIFIED via live test]
# ===========================================================================
# Claude Code treats Bash(cmd args) as a prefix match:
# the rule covers "cmd args", "cmd args --extra", "cmd args 2>&1", etc.
# Verified: .venv/bin/mypy src/ --strict ran without prompt even though
# the rule was Bash(.venv/bin/mypy src/ --strict) with no wildcard.

class TestExactPatternPrefixMatch:
    """VERIFIED: exact patterns use prefix matching."""

    def test_exact_covers_exact(self):
        assert matches("git status", "git status")

    def test_exact_covers_extra_args(self):
        assert matches("git status --short", "git status")

    def test_exact_covers_fd_redirect(self):
        assert matches("git status 2>&1", "git status")

    def test_exact_covers_multiple_extra_args(self):
        assert matches("mypy src/ --strict --show-error-codes", "mypy src/ --strict")

    def test_exact_no_partial_word_match(self):
        """Extra text must be space-separated, not a substring."""
        assert not matches("git statuslong", "git status")

    def test_exact_shorter_cmd_no_match(self):
        assert not matches("git", "git status")

    def test_exact_different_cmd_no_match(self):
        assert not matches("git diff", "git status")


# ===========================================================================
# 2. WILDCARD OPERATOR BLOCKING                       [VERIFIED via live test]
# ===========================================================================
# * does not match shell operators (&&, ||, |, ;, >).
# Claude Code splits compound commands at operators and matches each
# segment independently.  Documented in Claude Code permission docs.

class TestWildcardOperatorBlocking:
    """VERIFIED: * does not cross shell operators."""

    def test_star_blocks_pipe(self):
        assert not matches("ls foo | grep bar", "ls *")

    def test_star_blocks_double_ampersand(self):
        assert not matches("git add . && git commit", "git *")

    def test_star_blocks_double_pipe(self):
        assert not matches("cmd1 || cmd2", "cmd1 *")

    def test_star_blocks_semicolon(self):
        assert not matches("echo a ; echo b", "echo *")

    def test_star_blocks_stdout_redirect(self):
        assert not matches("echo foo > file.txt", "echo *")

    def test_star_blocks_append_redirect(self):
        assert not matches("echo foo >> file.txt", "echo *")


# ===========================================================================
# 3. FD REDIRECTS ARE NOT OPERATORS                   [VERIFIED via live test]
# ===========================================================================
# 2>&1, 2>/dev/null, 1>/dev/null are fd redirects — not shell operators.
# * can match across them; they do not split the command.
# Verified: mypy src/ --strict 2>&1 ran without prompt.

class TestFdRedirectsNotOperators:
    """VERIFIED: fd redirects (2>&1, 1>/dev/null) are not operators."""

    def test_2_redirect_1(self):
        assert matches("make verify 2>&1", "make *")

    def test_2_redirect_devnull(self):
        assert matches("grep foo file 2>/dev/null", "grep *")

    def test_1_redirect_devnull(self):
        assert matches("cmd arg 1>/dev/null", "cmd *")

    def test_fd_redirect_with_exact_pattern(self):
        assert matches("mypy src/ --strict 2>&1", "mypy src/ --strict")

    def test_fd_redirect_does_not_allow_pipe(self):
        """2>&1 is safe but | after it still splits."""
        assert not matches("make verify 2>&1 | tail -3", "make *")


# ===========================================================================
# 4. COMPOUND COMMAND SEGMENT MATCHING                [UNVERIFIABLE]
# ===========================================================================
# Hypothesis: Claude Code allows a compound command if and only if EVERY
# segment matches some allow rule.
#
# UNVERIFIABLE via conversational mode: the earlier "confirmation" (ls &&
# curl prompted) was likely due to curl making a network request, not due
# to segment matching.  Tested ls && xargs, ls && bc — both ran without
# prompts despite xargs/bc not being in any allow rule.  In conversational
# mode most commands run freely.  To verify, an agentic-mode test is needed.

class TestCompoundSegmentMatching:
    """UNVERIFIABLE (conversational mode): all segments must match — our implementation."""

    def test_compound_all_segments_allowed(self):
        ok, _ = check_command("git status && ls /", [], ["git *", "ls *"], [])
        assert ok

    def test_compound_one_segment_not_allowed(self):
        ok, _ = check_command("git status && curl foo", [], ["git *"], [])
        assert not ok

    def test_compound_full_string_pattern_wins(self):
        """If full string matches a pattern, it is allowed even without per-segment rules."""
        ok, _ = check_command("cd /tmp && git status", [], ["cd * && git *"], [])
        assert ok

    def test_compound_three_segments_all_must_match(self):
        ok, _ = check_command(
            "git add . && git commit -m 'x' && git push",
            [], ["git *"], [],
        )
        assert ok

    def test_compound_three_segments_last_not_covered(self):
        ok, _ = check_command(
            "git add . && git commit -m 'x' && curl foo",
            [], ["git *"], [],
        )
        assert not ok


# ===========================================================================
# 5. QUOTED OPERATORS ARE LITERALS                    [UNVERIFIABLE]
# ===========================================================================
# Hypothesis: operators inside single or double quotes are treated as
# literal characters — they do not split the command and * can match them.
#
# Commands like grep -n "^def \|^class " ran without prompts, consistent
# with the hypothesis.  But conversational mode does not enforce allow rules,
# so we cannot confirm whether the quoted | was parsed as literal or the
# command just ran freely.

class TestQuotedOperatorsAreLiterals:
    """UNVERIFIABLE (conversational mode): operators inside quotes — our implementation."""

    def test_pipe_in_double_quotes(self):
        assert matches('grep -n "^def \\|^class " src/foo.py', "grep *")

    def test_semicolon_in_double_quotes(self):
        assert matches('python -c "import os; print(1)"', "python *")

    def test_pipe_in_single_quotes(self):
        assert matches("grep -n '^def |^class ' src/foo.py", "grep *")

    def test_semicolon_in_single_quotes(self):
        assert matches("python -c 'import os; print(1)'", "python *")

    def test_ampersand_in_double_quotes(self):
        assert matches('echo "a && b"', "echo *")

    def test_backslash_escaped_pipe(self):
        assert matches("grep foo\\|bar file", "grep *")

    def test_unquoted_pipe_still_blocks(self):
        """Even with quoted pipe present, an unquoted pipe still splits."""
        assert not matches('grep "a|b" file | head -5', "grep *")


# ===========================================================================
# 6. COLON-STYLE PATTERNS                             [VERIFIED via live test]
# ===========================================================================
# Claude Code global settings support "cmd:*" as a prefix-match pattern.
# ~/.claude/settings.json uses entries like Bash(git status:*), Bash(make:*),
# Bash(python3:*) and all matching commands ran without prompts in extended
# sessions.  Colon-style is idiomatic in global settings; space-style also works.

class TestColonStylePatterns:
    """VERIFIED: colon-style patterns from global settings."""

    def test_colon_exact(self):
        assert matches("git log", "git log:*")

    def test_colon_with_args(self):
        assert matches("git log --oneline", "git log:*")

    def test_colon_no_match_different_cmd(self):
        assert not matches("git status", "git log:*")

    def test_colon_no_partial_word(self):
        """'git log:*' must not match 'git logger'."""
        assert not matches("git logger", "git log:*")


# ===========================================================================
# 7. DENY RULES TAKE PRIORITY                         [VERIFIED via live test]
# ===========================================================================
# Deny rules beat allow rules — even across settings scopes.
# Verified: "git status" is covered by global Bash(git status:*).
# Adding deny=["Bash(git status)"] to local settings.local.json caused
# Claude Code to hard-block the command ("Permission denied") despite the
# global allow rule.  Local deny > global allow.

class TestDenyPriority:
    """VERIFIED: deny beats allow (and local deny beats global allow)."""

    def test_deny_beats_allow(self):
        ok, reason = check_command(
            "git push --force origin",
            ["git push --force*"], ["git *"], [],
        )
        assert not ok
        assert "DENY" in reason

    def test_allow_without_deny(self):
        ok, _ = check_command("git push origin", [], ["git *"], [])
        assert ok

    def test_deny_on_segment(self):
        """Deny applied to a segment of a compound command."""
        ok, reason = check_command(
            "git status && git push --force origin",
            ["git push --force*"], ["git *"], [],
        )
        assert not ok
        assert "DENY" in reason


# ===========================================================================
# 8. UNKNOWN / NEEDS INVESTIGATION
# ===========================================================================

class TestNeedsInvestigation:
    """Edge cases where Claude Code behavior is unknown.

    These tests assert our *current* implementation behavior.
    They may be wrong — verify before relying on them.
    """

    def test_stdin_redirect_not_an_operator(self):
        """< is NOT an operator — verified by live test.

        VERIFIED: allow=[Bash(cat *)], ran "cat /dev/null < /dev/null"
        — executed without a permission prompt.
        Our implementation is correct: < does not split the command.
        """
        segments = _split_command("cat /dev/null < /dev/null")
        assert segments == ["cat /dev/null < /dev/null"]  # single segment

        assert matches("cat /dev/null < /dev/null", "cat *")

    def test_nested_quotes_behavior(self):
        """UNKNOWN: how are nested quotes handled?

        e.g., echo "he said 'hello | world'" — does | inside inner quotes split?
        """
        # Current behavior: inner single quotes inside double quotes protect |
        assert matches('echo "he said \'hello | world\'"', "echo *")

    def test_backslash_outside_quotes(self):
        """UNKNOWN: does backslash-escaped operator count as literal?

        e.g., grep foo\\|bar — is \\| an operator or literal?
        """
        # Current behavior: backslash escapes the operator
        assert matches("grep foo\\|bar file", "grep *")
