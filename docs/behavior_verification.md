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

**Status:** 🔲 HYPOTHESIZED

**How to verify:**

1. Add to `.claude/settings.local.json`:
   ```json
   { "permissions": { "allow": ["Bash(git *)", "Bash(ls *)"] } }
   ```
2. Ask Claude to run: `git status && ls /tmp`
   - Expected if hypothesis correct: **runs without prompt**
3. Ask Claude to run: `git status && curl http://example.com`
   - Expected if hypothesis correct: **prompts for permission** (curl not covered)
4. Ask Claude to run: `cd /tmp && git status` with rule `Bash(cd * && git *)`
   - Expected: **runs without prompt** (full-string match wins)

**Record result here.**

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

**Status:** 🔲 HYPOTHESIZED

**How to verify:**

1. Add to `~/.claude/settings.json` (global):
   ```json
   { "permissions": { "allow": ["Bash(git log:*)"] } }
   ```
2. Ask Claude to run: `git log`
   - Expected: **runs without prompt**
3. Ask Claude to run: `git log --oneline`
   - Expected: **runs without prompt**
4. Ask Claude to run: `git logger`
   - Expected: **prompts** (not a valid space-separated suffix)
5. Ask Claude to run: `git status`
   - Expected: **prompts** (different command)

**Record result here.**

---

## 7. Deny rules take priority over allow rules

**Status:** 🔲 HYPOTHESIZED

**How to verify:**

1. Add to `.claude/settings.local.json`:
   ```json
   {
     "permissions": {
       "allow": ["Bash(git *)"],
       "deny":  ["Bash(git push --force*)"]
     }
   }
   ```
2. Ask Claude to run: `git push --force origin main`
   - Expected if hypothesis correct: **prompts / blocked** (deny wins)
3. Ask Claude to run: `git push origin main`
   - Expected: **runs without prompt** (allow covers it, no deny match)

**Record result here.**

---

## 8. Unknown / needs investigation

### 8a. stdin redirect `<`

**Status:** 🔲 UNKNOWN

**Question:** Is `<` treated as a shell operator that splits commands?

**How to verify:**
1. Add `Bash(git *)` to allow.
2. Ask Claude to run: `git log < /dev/null`
   - If **runs without prompt** → `<` is not an operator (our impl is correct)
   - If **prompts** → `<` is an operator (our impl needs fixing)

**Current implementation:** `<` is NOT treated as an operator.

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
| 4 | Compound: all segments must match | 🔲 HYPOTHESIZED |
| 5 | Quoted operators are literals | 🔲 HYPOTHESIZED |
| 6 | Colon-style patterns | 🔲 HYPOTHESIZED |
| 7 | Deny beats allow | 🔲 HYPOTHESIZED |
| 8a | `<` is not an operator | 🔲 UNKNOWN |
| 8b | Backslash escapes operators | 🔲 UNKNOWN |
| 8c | Nested quotes protect operators | 🔲 UNKNOWN |
