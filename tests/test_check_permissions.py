"""Tests for check_permissions — command splitting and permission checking."""

from permission_audit.check_permissions import _split_command, check_command, is_compound, _is_noise


# ---------------------------------------------------------------------------
# _split_command — splitting at shell operators
# ---------------------------------------------------------------------------

class TestSplitCommand:
    # --- Simple commands (no splitting) ---
    def test_simple_command(self):
        assert _split_command("git status") == ["git status"]

    def test_command_with_args(self):
        assert _split_command("grep -rn foo src/") == ["grep -rn foo src/"]

    # --- && splitting ---
    def test_double_ampersand(self):
        assert _split_command("git add . && git commit") == ["git add .", "git commit"]

    def test_triple_ampersand_chain(self):
        assert _split_command("a && b && c") == ["a", "b", "c"]

    # --- || splitting ---
    def test_double_pipe(self):
        assert _split_command("cmd1 || cmd2") == ["cmd1", "cmd2"]

    # --- Single pipe ---
    def test_single_pipe(self):
        assert _split_command("ls | grep foo") == ["ls", "grep foo"]

    def test_pipe_chain(self):
        assert _split_command("cat f | grep x | head -5") == [
            "cat f", "grep x", "head -5",
        ]

    # --- Semicolon ---
    def test_semicolon(self):
        assert _split_command("echo a ; echo b") == ["echo a", "echo b"]

    # --- Redirects (NOT operators — do not split) ---
    def test_stdout_redirect_no_split(self):
        """VERIFIED: > is a redirect, not an operator."""
        assert _split_command("echo foo > file.txt") == ["echo foo > file.txt"]

    def test_append_redirect_no_split(self):
        """VERIFIED: >> is a redirect, not an operator."""
        assert _split_command("echo foo >> file.txt") == ["echo foo >> file.txt"]

    def test_stdin_redirect_no_split(self):
        """VERIFIED: < is a redirect, not an operator."""
        assert _split_command("cat /dev/null < /dev/null") == ["cat /dev/null < /dev/null"]

    def test_2_redirect_1_no_split(self):
        assert _split_command("make verify 2>&1") == ["make verify 2>&1"]

    def test_2_redirect_devnull_no_split(self):
        assert _split_command("ls foo 2>/dev/null") == ["ls foo 2>/dev/null"]

    def test_fd_redirect_with_pipe(self):
        """2>&1 preserved, but | still splits."""
        assert _split_command("make verify 2>&1 | tail -3") == [
            "make verify 2>&1",
            "tail -3",
        ]

    def test_stdout_redirect_with_pipe(self):
        """> does not split, but | after it still splits."""
        assert _split_command("echo foo > file.txt | cat") == [
            "echo foo > file.txt",
            "cat",
        ]

    # --- Backslash escaping ---
    def test_backslash_escaped_pipe_no_split(self):
        """VERIFIED: \\| is a literal — backslash escapes the operator."""
        assert _split_command("grep foo\\|bar file") == ["grep foo\\|bar file"]

    def test_backslash_escaped_pipe_in_compound(self):
        """Backslash escapes | but unquoted | still splits."""
        assert _split_command("echo hello\\|ls /tmp | cat") == [
            "echo hello\\|ls /tmp",
            "cat",
        ]

    # --- Quoting ---
    def test_double_quotes_protect_pipe(self):
        assert _split_command('grep "a|b" file') == ['grep "a|b" file']

    def test_single_quotes_protect_pipe(self):
        assert _split_command("grep 'a|b' file") == ["grep 'a|b' file"]

    def test_double_quotes_protect_semicolon(self):
        assert _split_command('python -c "import os; print(1)"') == [
            'python -c "import os; print(1)"',
        ]

    def test_double_quotes_protect_ampersand(self):
        assert _split_command('echo "a && b"') == ['echo "a && b"']

    def test_mixed_quotes_and_operators(self):
        assert _split_command('echo "hello" && grep "a|b" file') == [
            'echo "hello"',
            'grep "a|b" file',
        ]

    # --- Empty segments ignored ---
    def test_leading_operator(self):
        assert _split_command("| grep foo") == ["grep foo"]

    def test_trailing_operator(self):
        assert _split_command("echo foo |") == ["echo foo"]


# ---------------------------------------------------------------------------
# is_compound
# ---------------------------------------------------------------------------

class TestIsCompound:
    def test_simple_not_compound(self):
        assert not is_compound("git status")

    def test_pipe_is_compound(self):
        assert is_compound("ls | grep foo")

    def test_and_is_compound(self):
        assert is_compound("cd /tmp && ls")

    def test_2_redirect_not_compound(self):
        assert not is_compound("make verify 2>&1")

    def test_stdout_redirect_not_compound(self):
        assert not is_compound("echo foo > file.txt")

    def test_append_redirect_not_compound(self):
        assert not is_compound("echo foo >> file.txt")

    def test_stdin_redirect_not_compound(self):
        assert not is_compound("cat /dev/null < /dev/null")

    def test_quoted_pipe_not_compound(self):
        assert not is_compound('grep "a|b" file')


# ---------------------------------------------------------------------------
# _is_noise
# ---------------------------------------------------------------------------

class TestIsNoise:
    def test_comment(self):
        assert _is_noise("# this is a comment")

    def test_indented_comment(self):
        assert _is_noise("  # indented comment")

    def test_backslash(self):
        assert _is_noise("\\")

    def test_empty_string(self):
        assert _is_noise("")

    def test_normal_command(self):
        assert not _is_noise("git status")


# ---------------------------------------------------------------------------
# check_command
# ---------------------------------------------------------------------------

class TestCheckCommand:
    DENY = ["git push --force*", "rm -rf /*"]
    ALLOW = ["git *", "make *", "ls *", "grep *", "cd * && git *"]

    def test_simple_allowed(self):
        ok, reason = check_command("git status", self.DENY, self.ALLOW, [])
        assert ok
        assert "git *" in reason

    def test_simple_denied(self):
        ok, reason = check_command("git push --force origin", self.DENY, self.ALLOW, [])
        assert not ok
        assert "DENY" in reason

    def test_simple_no_match(self):
        ok, reason = check_command("curl http://example.com", self.DENY, self.ALLOW, [])
        assert not ok
        assert "NO_MATCH" in reason

    def test_compound_full_string_match(self):
        """Full-string pattern 'cd * && git *' matches."""
        ok, _ = check_command("cd /tmp && git status", self.DENY, self.ALLOW, [])
        assert ok

    def test_compound_per_segment_match(self):
        """All segments match individual rules."""
        ok, _ = check_command("git add . && git commit -m 'x'", self.DENY, self.ALLOW, [])
        assert ok

    def test_compound_segment_denied(self):
        ok, reason = check_command(
            "ls /tmp && git push --force origin", self.DENY, self.ALLOW, [],
        )
        assert not ok
        assert "DENY" in reason

    def test_compound_segment_not_allowed(self):
        ok, reason = check_command("git status | curl foo", self.DENY, self.ALLOW, [])
        assert not ok
        assert "SEGMENTS_NOT_ALLOWED" in reason

    def test_global_allow(self):
        ok, reason = check_command("curl http://example.com", self.DENY, [], ["curl *"])
        assert ok
        assert "global" in reason

    def test_deny_beats_allow(self):
        """Deny is checked before allow."""
        ok, reason = check_command("rm -rf /important", self.DENY, ["rm *"], [])
        assert not ok
        assert "DENY" in reason

    def test_2_redirect_allowed(self):
        ok, _ = check_command("make verify 2>&1", self.DENY, self.ALLOW, [])
        assert ok

    def test_quoted_semicolon_allowed(self):
        ok, _ = check_command('grep "a;b" file', self.DENY, self.ALLOW, [])
        assert ok
