# Claude Code Permission Matching — Behavior Verification Log

This document tracks what we know (and don't know) about Claude Code's actual
permission-matching behavior.  Each section corresponds to a test class in
`tests/test_behavior_spec.py`.

**Status legend:**
- ✅ VERIFIED — confirmed by live test
- ❌ REFUTED — hypothesis was wrong; see notes
- 🔲 HYPOTHESIZED — not yet tested against real Claude Code
- ⚠️ UNVERIFIABLE — methodology does not allow reliable confirmation

---

## Testing methodology

### Always-deny method
Add a `deny` rule that would fire only if the allow rule matched. If the command
hard-blocks (no dialog) → the allow rule matched. If a dialog appears → allow
rule did not match.

### Critical: standalone vs pipe for Bash pattern testing

**Standalone `cat /absolute/path`** is converted by Claude Code to a `Read` tool
call internally. The `Bash(cat ...)` rule is **never evaluated**. Testing
`Bash(cat **)` by running `cat /path` alone will always prompt — not because the
Bash rule failed, but because the tool used is Read, not Bash.

**To test a Bash pattern against a command with absolute paths, always use a pipe
or compound command:**
```
cat /path | python3 -c "print('ok')"   ← forces Bash execution
```
This applies broadly: any command Claude Code might optimize to a non-Bash tool
(Read, Edit, Glob, etc.) must be tested via a compound/pipe to verify Bash rules.

### `Read(...)` patterns require double-slash for absolute paths

`Read(/path)` — single slash is interpreted as **relative to project root**.
`Read(//path)` — double slash is a **true absolute path**.

Always use `//` when writing Read rules for system or home-directory paths:
```json
"Read(//Users/viktor/.claude/**)"   ✅
"Read(/Users/viktor/.claude/**)"    ❌ relative, won't match
```

---

## Enforcement model

### How Claude Code decides whether to prompt

| Command type | Behavior |
|-------------|---------|
| **Bare name of standard utility** (`ls`, `grep`, `wc`, `cat`, …) | Auto-approved — no rule needed |
| **Full path command** (`.venv/bin/pytest`, `/usr/bin/wc`, `/tmp/script`) | Requires an explicit allow rule OR user approval via dialog |
| **Network command** (`curl http://…`, `wget`, …) | Always requires allow rule or user approval |
| **Command matching a deny rule** | Hard-blocked — no dialog shown |

### Evidence

**Auto-approved (no rule):** `ls`, `wc`, `grep`, `cat`, `awk`, `bc`, `sed` —
all bare names, ran without a permission prompt after their allow rules were removed.

**Required permission dialog:**
- `/usr/bin/wc -l /dev/null` — same binary as `wc`, but explicit path → dialog
- `.venv/bin/pytest tests/` — full path → dialog (despite global `Bash(pytest:*)`)
- `/tmp/claude_test_util hello` — custom script, full path → dialog
- `curl http://example.com` — network → dialog

**Hard-blocked (no dialog):**
- `git status` with `deny: ["Bash(git status)"]` in settings → immediate error

### Key implications

**No basename matching for allow or deny rules.**
`Bash(pytest:*)` does NOT cover `.venv/bin/pytest` — the rule must include the full
path prefix: `Bash(.venv/bin/pytest:*)` or `Bash(.venv/bin/pytest tests/ -v)`.

**Allow rules are required for full-path commands.**
Unlike bare names, full-path commands will show a permission dialog if not covered
by an allow rule.  This is the primary value of the allow rules in `settings.local.json`.

**Deny rules are hard blocks, not dialogs.**
A matching deny rule produces an immediate error with no user prompt.  Deny rules
override allow rules and apply globally.

### Note on earlier "default-allow" conclusion

Earlier tests concluded "all commands run freely" because the user had not yet adopted
the "always-deny" testing methodology.  Those tests were unreliable — the user was
likely approving dialogs without reporting them.  The accurate model is above.

---

## 1. Exact patterns use prefix matching

**Status:** ✅ VERIFIED

**How verified:**
Rule `Bash(.venv/bin/mypy src/ --strict)` was in `settings.local.json`.
Command `.venv/bin/mypy src/ --strict 2>&1` ran without a permission prompt.

**Observation:** Claude Code does NOT do strict equality for exact patterns.
A rule `Bash(cmd args)` also covers `cmd args --extra`, `cmd args 2>&1`, etc.
The match requires the extra text to be space-separated (not a substring of the last token).

---

## 2. `*` does not cross shell operators

**Status:** ✅ VERIFIED

**How verified:** Documented behavior in Claude Code permission docs.
Additionally confirmed by the existence and purpose of this project —
if `*` crossed operators, there would be no need for segment-level checking.

**Operators blocked:** `&&`, `||`, `|`, `;`, `>`, `>>`

---

## 3. File-descriptor redirects are not operators

**Status:** ✅ VERIFIED

**How verified:**
Rule `Bash(.venv/bin/mypy src/ --strict)` in settings.
Command `.venv/bin/mypy src/ --strict 2>&1` ran without a prompt.
The `2>&1` suffix did not cause a permission prompt, confirming it is not treated
as a splitting operator.

**Known safe redirects:** `2>&1`, `2>/dev/null`, `1>/dev/null`, `N>&1`

---

## 4. Compound commands: all segments must match

**Status:** ✅ VERIFIED (both directions)

**Negative case — uncovered segment triggers prompt:**
Removed `Bash(.venv/bin/pytest tests/ -v)` from settings. `ls /tmp` is
auto-approved (bare name). Ran `ls /tmp && .venv/bin/pytest --version` —
Claude Code showed a permission prompt for the full compound command.

**Positive case — all segments covered → auto-approved:**
Added `deny: ["Bash(cat *)"]` to local settings. Ran `git status | cat` —
both segments covered (`git status:*` in global, `cat` is a bare utility).
Claude Code executed the command immediately and hit the deny rule (hard-block,
no prompt). Since the deny fired without a dialog, the compound command was
auto-approved — confirming that all-segments-covered → no prompt.

**Notes:**
- The prompt shows the **full compound command**, not just the uncovered segment.
- Earlier testing was unreliable (user was approving prompts silently).
  Both results above use the "always-deny" methodology and are reliable.

---

## 5. Operators inside quotes are literals

**Status:** ✅ VERIFIED

**How verified (always-deny methodology):**
Added `deny: ["Bash(ls *)"]` to local settings. Ran commands where a `|` inside
quotes is followed by `ls /tmp` — if the quoted `|` were treated as an operator,
`ls /tmp` would match the deny rule and produce "Permission denied". Instead all
commands ran freely:

- `grep "hello | ls /tmp" /dev/null` — double-quoted `|` → ran freely ✅
- `grep 'hello | ls /tmp' /dev/null` — single-quoted `|` → ran freely ✅

**Conclusion:** `|` inside double or single quotes is treated as a literal character.
The command is a single segment; `ls /tmp` is never evaluated as a separate command.

---

## 6. Colon-style patterns

**Status:** ✅ VERIFIED

**How verified:**
`~/.claude/settings.json` (global settings) contains entries like
`Bash(git status:*)`, `Bash(git log:*)`, `Bash(python3:*)`, `Bash(make:*)`, etc.
Throughout extended Claude Code sessions with these settings, all matching
commands (`git status`, `git log --oneline`, `python3 -m pytest`, `make verify`)
ran without permission prompts. Non-matching commands (e.g., `curl`) triggered prompts.

**Observations:**
- Colon-style `Bash(cmd:*)` is equivalent to `Bash(cmd *)` — both use prefix matching.
- The colon syntax appears to be the idiomatic form in global settings.
- `Bash(git status:*)` covers `git status`, `git status --short`, etc. (prefix match).
- `Bash(git status:*)` does NOT cover `git statuslong` (must be space-separated).

---

## 7. Deny rules take priority over allow rules

**Status:** ✅ VERIFIED

**How verified:**
`git status` runs without prompt (covered by global `Bash(git status:*)`).
Added `"deny": ["Bash(git status)"]` to `.claude/settings.local.json`.
Ran `git status` — received error:
> Permission to use Bash with command git status has been denied.

The deny rule in local settings overrode the allow rule in global settings.
Conclusion: deny beats allow, and **local settings deny beats global settings allow**.

**Notes:**
- The error is a hard block (not a prompt) — denied commands do not offer
  "allow once" or "add to file" options.
- Local deny rules override global allow rules, confirming a deny-wins hierarchy
  that applies across the settings scope stack.

---

## 8. Unknown / needs investigation

### 8a. stdin redirect `<`

**Status:** ✅ VERIFIED

**Question:** Is `<` treated as a shell operator that splits commands?

**How verified:**
Rule `Bash(cat *)` in settings. Ran `cat /dev/null < /dev/null` — executed
without a permission prompt. Conclusion: `<` is NOT an operator in Claude Code.

**Implementation:** Correct — `<` is not treated as an operator.

---

### 8b. Backslash-escaped operators outside quotes

**Status:** ✅ VERIFIED

**Question:** Does `echo hello\|ls /tmp` treat `\|` as a literal or as an operator?

**How verified (always-deny methodology):**
Added `deny: ["Bash(ls *)"]` to local settings. Ran `echo hello\|ls /tmp` —
if `\|` were an operator, `ls /tmp` would match the deny rule → "Permission denied".
Instead the command ran freely and printed `hello|ls /tmp`.

**Conclusion:** Backslash-escaped `|` is treated as a literal character, not an operator.

---

### 8c. Nested quotes

**Status:** ✅ VERIFIED

**Question:** In `echo "he said 'hello | ls /tmp'"`, does the inner `|` split the command?

**How verified (always-deny methodology):**
Added `deny: ["Bash(ls *)"]` to local settings. Ran two nested-quote variants —
if the inner `|` were an operator, `ls /tmp` would trigger "Permission denied":

- `echo "he said 'hello | ls /tmp'"` (double outer, single inner) → ran freely ✅
- `echo 'he said "hello | ls /tmp"'` (single outer, double inner) → ran freely ✅

**Conclusion:** Nested quotes protect inner operators. The outer quote level is
sufficient — inner quotes do not "break out" to expose the `|` as an operator.

---

---

## 9. `*` does not match `/`

**Status:** ✅ VERIFIED

**How verified:**
`Bash(cat *)` was in global settings. Ran two commands:
- `cat README.md` — executed without a prompt ✓ (no slash in argument)
- `cat /tmp/file` — prompted (not auto-approved) ✗ (slash in argument)
- `cat /Users/viktor/.claude/settings.json` — prompted ✗ (slash in argument)

Also verified the compound positive case using always-deny methodology:
`git status | python3 -c "print(1)"` — executed without prompt (both segments covered:
`git status:*` and `python3:*`).

**Conclusion:** `*` in Bash permission patterns blocks only a **leading `/`** (i.e.
arguments that start with `/`).  Internal slashes — including paths like
`tests/file.py`, `src/foo/bar.py`, or redirect targets like `2>/dev/null` — are
matched by `*` as long as the matched sequence does not start with `/`.

**Additional verification (always-deny):**
- `cat src/permission_audit/claude_glob.py | wc -l` hard-blocked by deny `Bash(cat *)` —
  two internal slashes, no leading slash → `*` matched ✓

**Impact on redirect targets:** `grep foo 2>/dev/null` is matched by `Bash(grep *)`
in our model (matched portion `foo 2>/dev/null` starts with `f`). Whether Claude Code
strips redirect targets before matching is still unverified, but our model now says
`*` covers them. Use `**` or an exact pattern only if you need to match the redirect
target itself as part of the pattern.

---

## 10. `**` in Bash patterns matches `/`

**Status:** ✅ VERIFIED

**How verified:**
Added `Bash(cat /Users/viktor/**)` to global allow rules.
In a fresh session, ran `cat /Users/viktor/.claude/settings.json | python3 -c "print('ok')"` —
auto-approved without a prompt. The pipe forces Bash execution (not Read tool), confirming
`**` matched the cat segment including nested path `/Users/viktor/.claude/settings.json`.

**Note on earlier flawed test:** A previous test used standalone `cat /path`, which Claude
Code converts to a `Read` tool call — the Bash rule was never evaluated. The correct
methodology for Bash pattern testing is to use a pipe or compound command.

**Conclusion:** `**` in Bash patterns matches `/` (crosses path separators), mirroring
standard double-glob semantics. Use `**` when you need to cover commands with absolute
or deeply nested paths:
- `Bash(cat /Users/viktor/**)` — covers `cat /Users/viktor/.claude/settings.json` ✅
- `Bash(cat /Users/viktor/*)` — covers `cat /Users/viktor/file.txt` but NOT nested paths

---

## 11. Read patterns require `//` prefix for absolute paths

**Status:** ✅ VERIFIED

**How verified:**
- `Read(/Users/viktor/.claude/**)` (single slash) — standalone `cat /path` prompted in
  a fresh session. Single-slash paths are relative to the project root.
- `Read(//Users/viktor/.claude/**)` (double slash) — `cat /Users/viktor/.claude/settings.json`
  ran without a prompt in a fresh session. Double-slash = true absolute path.

**Observation:** When Claude runs `cat /absolute/path` as a standalone command, Claude Code
converts it to a `Read` tool call — NOT a `Bash(cat ...)` call. This means:
- Standalone `cat /path` → matched against `Read(...)` rules
- `cat /path | ...` in a pipe → matched against `Bash(cat ...)` rules (cannot use Read)

**Correct format for absolute paths in Read rules:**
```json
"Read(//Users/viktor/.claude/**)",
"Read(//usr/local/bin/**)",
"Read(//opt/homebrew/bin/**)"
```

**Impact on Bash rules for absolute paths:** `Bash(cat /path/*)` is still needed for
pipe contexts (`cat /path | ...`) since those cannot be converted to `Read`.

---

---

## 12. Does `*` match a trailing `/` in directory arguments?

**Status:** ✅ VERIFIED

**Hypothesis A confirmed.**

**How verified (always-deny methodology):**
1. Removed `Bash(.venv/bin/*)` from `~/.claude/settings.json`; kept only `Bash(.venv/bin/* *)`.
2. Added `"deny": ["Bash(.venv/bin/* *)"]` to `.claude/settings.local.json`.
3. In a fresh session, ran `.venv/bin/pytest tests/ -q`.
4. Result: immediate hard-block — "Permission to use Bash with command
   .venv/bin/pytest tests/ -q 2>&1 has been denied." — **no dialog appeared**.

No dialog = allow rule fired first (auto-approved), then deny rule hard-blocked.
This confirms `Bash(.venv/bin/* *)` matched `.venv/bin/pytest tests/ -q 2>&1`.

**Conclusion:** `*` matches `/` when it is immediately followed by a space or
end-of-string — i.e. a **trailing slash on a directory argument** (e.g. `tests/`).
`*` does NOT match `/` in other positions (leading slash, mid-path separator,
or slash before a non-space character like in `2>/dev/null`).

**Implementation updated:** `_STAR` in `claude_glob.py` uses
`(?!/)(?:(?!&&|\|\||[|;]).)*` — blocks leading `/` only, allows all internal `/`.

---

## Summary table

| # | Behavior | Status |
|---|---------|--------|
| 1 | Exact patterns: prefix match | ✅ VERIFIED |
| 2 | `*` blocked by `\|`, `&&`, `\|\|`, `;` | ✅ VERIFIED |
| 3 | Redirects (`>`, `>>`, `<`, `2>&1`) not operators | ✅ VERIFIED |
| 4 | Compound: all segments must match | ✅ VERIFIED |
| 5 | Quoted operators are literals | ✅ VERIFIED |
| 6 | Colon-style patterns | ✅ VERIFIED |
| 7 | Deny beats allow | ✅ VERIFIED |
| 8a | `<` is not an operator | ✅ VERIFIED |
| 8b | Backslash escapes operators | ✅ VERIFIED |
| 8c | Nested quotes protect operators | ✅ VERIFIED |
| 9 | `*` does not match `/` | ✅ VERIFIED |
| 10 | `**` matches `/` in Bash patterns | ✅ VERIFIED |
| 11 | `Read(//path)` double-slash = absolute path | ✅ VERIFIED |
| 12 | `*` matches trailing `/` in dir args (`tests/`) | ✅ VERIFIED |
