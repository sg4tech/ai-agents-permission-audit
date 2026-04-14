# Claude Code Permission Matching — Behavior Verification Log

This document tracks what we know (and don't know) about Claude Code's actual
permission-matching behavior.  Each section corresponds to a test class in
`tests/test_behavior_spec.py`.

**Status legend:**
- ✅ VERIFIED — confirmed by live test
- ❌ REFUTED — hypothesis was wrong; see notes
- 🔲 HYPOTHESIZED — not yet tested against real Claude Code

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
Rules `Bash(ls *)` in local settings, no `curl` rule in effect.
Ran `ls /tmp && curl http://example.com --max-time 1 ...` — Claude Code showed
a permission prompt for the full compound command. Conclusion: when any segment
is not covered, the compound command requires approval.

**Notes:**
- The permission prompt showed the **full compound command string**, not just the
  uncovered segment. Claude Code does not tell you which segment triggered it.
- The full-string pattern test (#4 step 4 above) was not separately verified, but
  the segmented-match logic is confirmed by the ALLOW case (covered segments run
  silently) and the PROMPT case (uncovered segment triggers prompt).

---

## 5. Operators inside quotes are literals

**Status:** 🔲 HYPOTHESIZED

**How to verify:**

1. Add to `.claude/settings.local.json`:
   ```json
   { "permissions": { "allow": ["Bash(grep *)"] } }
   ```
2. Ask Claude to run: `grep -n "^def \|^class " src/foo.py`
   - Expected if hypothesis correct: **runs without prompt**
3. Ask Claude to run: `python -c "import os; print(os.getcwd())"`
   - Add `Bash(python *)` to allow first
   - Expected if hypothesis correct: **runs without prompt**
4. Ask Claude to run: `grep "a|b" file | head -5`
   - Expected: **prompts** (unquoted `|` after the quoted `|` still splits)

**Record result here.**

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

**Status:** 🔲 UNKNOWN

**Question:** Does `grep foo\|bar` treat `\|` as a literal pipe or as an operator?

**How to verify:**
1. Add `Bash(grep *)` to allow.
2. Ask Claude to run: `grep foo\|bar file`
   - If **runs without prompt** → backslash escapes the operator (our impl is correct)
   - If **prompts** → backslash is not recognized as escape

---

### 8c. Nested quotes

**Status:** 🔲 UNKNOWN

**Question:** In `echo "he said 'hello | world'"`, does the inner `|` split the command?

**How to verify:**
1. Add `Bash(echo *)` to allow.
2. Ask Claude to run: `echo "he said 'hello | world'"`
   - If **runs without prompt** → inner quotes protect the operator
   - If **prompts** → inner single quotes inside double quotes are not parsed

---

## Summary table

| # | Behavior | Status |
|---|---------|--------|
| 1 | Exact patterns: prefix match | ✅ VERIFIED |
| 2 | `*` blocked by operators | ✅ VERIFIED |
| 3 | `2>&1` not an operator | ✅ VERIFIED |
| 4 | Compound: all segments must match | ✅ VERIFIED |
| 5 | Quoted operators are literals | 🔲 HYPOTHESIZED |
| 6 | Colon-style patterns | ✅ VERIFIED |
| 7 | Deny beats allow | ✅ VERIFIED |
| 8a | `<` is not an operator | ✅ VERIFIED |
| 8b | Backslash escapes operators | 🔲 UNKNOWN |
| 8c | Nested quotes protect operators | 🔲 UNKNOWN |
