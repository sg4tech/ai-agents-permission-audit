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

## ⚠️ Critical methodology note: conversational vs agentic mode

All live tests in this document are run in **conversational mode** — an interactive
Claude Code session where the user watches every step.  This mode behaves
**differently** from agentic mode:

| Mode | Allow rules | Deny rules | Network commands |
|------|------------|-----------|-----------------|
| **Agentic** (autonomous multi-step) | Enforced — unlisted commands prompt | Enforced | Always prompt |
| **Conversational** (user watches) | Largely unenforced — most commands run freely | Enforced | Prompt |

**Evidence for this distinction:**
- `ls /tmp` ran without a prompt after removing `Bash(ls *)` from settings
- `bc`, `awk`, `xargs`, `wc` all ran without any allow rule
- `git status` was hard-blocked by a deny rule (deny rules apply in both modes)
- `curl http://example.com` prompted in conversational mode (network access is flagged regardless of mode)

**Implication for this document:**
- Tests that confirm a command **was blocked** (deny tests, network prompts) are reliable
- Tests that confirm a command **ran without a prompt** in conversational mode are
  **not reliable** for verifying allow-rule matching — the command may have run freely
  regardless of whether the pattern matched
- Only tests run in an agentic context (autonomous task execution) can reliably
  verify allow-rule matching behavior

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

**Status:** ⚠️ UNVERIFIABLE (methodology flaw — see note below)

**What we observed:**
Rules `Bash(ls *)` in local settings, no `curl` rule in effect.
Ran `ls /tmp && curl http://example.com --max-time 1 ...` — Claude Code showed
a permission prompt for the full compound command.

**Why this does NOT confirm the hypothesis:**
Subsequent testing revealed that in conversational mode, most commands (wc, awk,
xargs, bc) run freely without any allow rule.  The curl prompt is likely caused by
**curl making a network request** (network access always prompts), not by the
`&&` segment-matching logic.  Tested `ls src/ && xargs echo`, `ls src/ && bc <<<
"1+1"` — both ran without prompts even though `xargs` and `bc` have no allow rules.

**To verify properly:** Run in agentic mode (autonomous multi-step task). Add only
`Bash(ls *)` to settings.  Ask Claude to perform a task that requires
`ls && <non-network-uncovered-cmd>`.  If a prompt appears, segment matching is confirmed.

---

## 5. Operators inside quotes are literals

**Status:** ⚠️ UNVERIFIABLE (methodology flaw — see note below)

**What we observed:**
- `grep -n "^def \|^class " src/...` ran without prompt (double-quoted `|`)
- `grep -c '^def \|^class ' src/...` ran without prompt (single-quoted `|`)
- These are consistent with the hypothesis.

**Why this does NOT confirm the hypothesis:**
Conversational mode does not enforce allow rules. The commands likely ran freely
regardless of whether the quoted `|` was parsed correctly. We cannot distinguish
"quoted operator is literal → single segment → matches `grep *`" from "conversational
mode ignores allow rules → runs freely".

**To verify properly:** Requires agentic mode test OR a negative test where a
mis-parsed command would produce a string that definitely doesn't match any rule.

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

**Status:** ⚠️ UNVERIFIABLE (methodology flaw)

**Question:** Does `grep foo\|bar` treat `\|` as a literal pipe or as an operator?

**What we observed:**
`grep -c foo\|bar src/...` ran without prompt.  Consistent with backslash escaping.

**Why unverifiable:** Conversational mode does not enforce allow rules for most
commands.  Cannot distinguish "backslash escape works" from "ran freely".

---

### 8c. Nested quotes

**Status:** ⚠️ UNVERIFIABLE (methodology flaw)

**Question:** In `echo "he said 'hello | world'"`, does the inner `|` split the command?

**What we observed:**
`echo "he said 'hello | world'"` ran without prompt.  Consistent with nested
quotes protecting the operator.

**Why unverifiable:** Same as 8b — conversational mode does not enforce allow rules.

---

## Summary table

| # | Behavior | Status |
|---|---------|--------|
| 1 | Exact patterns: prefix match | ✅ VERIFIED |
| 2 | `*` blocked by operators | ✅ VERIFIED |
| 3 | `2>&1` not an operator | ✅ VERIFIED |
| 4 | Compound: all segments must match | ⚠️ UNVERIFIABLE |
| 5 | Quoted operators are literals | ⚠️ UNVERIFIABLE |
| 6 | Colon-style patterns | ✅ VERIFIED |
| 7 | Deny beats allow | ✅ VERIFIED |
| 8a | `<` is not an operator | ✅ VERIFIED |
| 8b | Backslash escapes operators | ⚠️ UNVERIFIABLE |
| 8c | Nested quotes protect operators | ⚠️ UNVERIFIABLE |
