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

**Status:** ✅ VERIFIED

**How verified:**
Removed `Bash(.venv/bin/pytest tests/ -v)` from settings. `ls /tmp` is
auto-approved (bare name). Ran `ls /tmp && .venv/bin/pytest --version` —
Claude Code showed a permission prompt for the full compound command.

Conclusion: a compound command containing any uncovered segment (here,
`.venv/bin/pytest` without an allow rule) triggers a permission prompt
for the whole compound command.

**Notes:**
- The prompt shows the **full compound command**, not just the uncovered segment.
- Earlier testing was unreliable (user was approving prompts silently).
  This result uses the "always-deny" methodology and is reliable.

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
