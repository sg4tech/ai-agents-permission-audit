# AGENTS.md

## Project overview

Audit toolset for Claude Code permission rules. Three scripts that work together:

1. **`extract_bash_commands.py`** — recursively scans `~/.claude/projects/<slug>/` for JSONL session files, parses `tool_use` Bash blocks, and writes a frequency-sorted TSV with per-command approval-status breakdown (`auto` / `user` / `denied`)
2. **`check_permissions.py`** — reads that TSV and checks each command against `allow`/`deny` rules from `settings.local.json` and `~/.claude/settings.json`
3. **`find_redundant_rules.py`** — finds allow rules that are fully covered by another rule

Core matching logic lives in **`src/permission_audit/claude_glob.py`**.

## Running tests

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]" -q
.venv/bin/pytest
```

## Key invariants

`*` in Claude Code permission patterns does **not** match shell operators (`&&`, `||`, `|`, `;`).
`*` does **not** match a leading `/` (absolute paths) and does **not** cross `/` mid-path,
but **does** match a trailing `/` on a directory argument (slash immediately followed by space
or end-of-string).

- `git *` matches `git status` but NOT `git status && git diff`
- `cat *` matches `cat README.md` but NOT `cat /etc/hosts` (leading slash)
- `.venv/bin/*` matches `.venv/bin/pytest` AND `.venv/bin/sub/pytest` (internal slash allowed)
- `.venv/bin/* *` matches `.venv/bin/pytest tests/ -q` and `.venv/bin/pytest tests/spec.py` ✓
- `grep *` DOES match `grep foo 2>/dev/null` (matched portion starts with `f`, not `/`)
- `cat **` matches `cat /Users/viktor/.claude/settings.json` (`**` crosses `/` freely)
- Compound commands are split at operators; every segment must match independently
- A compound command is auto-approved only when **every** segment is covered — verified both directions:
  - Uncovered segment → prompt (verified via always-deny)
  - All segments covered → auto-approved (verified via always-deny with `git status | cat`)
- File-descriptor redirects (`2>&1`) are **not** operators — pass through fine; `2>/dev/null` contains `/` so requires `**` or exact pattern
- Operators inside quotes (`"a|b"`, `'a;b'`) or after backslash are treated as literals

These invariants are the reason the project exists and must be preserved in all changes to `claude_glob.py`.

## Read pattern paths

`Read(...)` patterns use **double-slash** for absolute paths:
- `Read(//Users/viktor/.claude/**)` — absolute path ✅
- `Read(/Users/viktor/.claude/**)` — relative to project root ❌

Standalone `cat /absolute/path` is converted by Claude Code to a `Read` tool call, not a `Bash(cat ...)` call. To cover it, use `Read(//path/**)`. For pipe contexts (`cat /path | ...`), a `Bash(cat /path/**)` rule is needed since pipes can't use the Read tool (`**` covers nested paths).

## Settings file locations

| File | Purpose |
|------|---------|
| `.claude/settings.local.json` | Project-level allow/deny rules |
| `~/.claude/settings.json` | Global allow rules (lower priority than local) |

Claude Code project slug = absolute repo path with `/` replaced by `-`, leading slash stripped:
`/Users/me/myproject` → `-Users-me-myproject`

## Behavioral specification

`tests/test_behavior_spec.py` documents what we know about Claude Code's
actual matching behavior.  Each test class is annotated with its status:

- **VERIFIED** — confirmed by live testing against Claude Code
- **HYPOTHESIZED** — inferred from docs/logic, not yet live-tested
- **UNKNOWN** — edge case with no confirmed behavior yet

`docs/behavior_verification.md` has step-by-step instructions for verifying
each hypothesis.  When you discover new behavior (especially if a live test
contradicts a hypothesis), update both files.

Known verified behaviors:
- Exact patterns use **prefix matching**: `Bash(git status)` also covers `git status --short`
- `*` does **not** cross shell operators (`&&`, `||`, `|`, `;`)
- `*` does **not** match `/` — `cat *` covers `cat file.txt` but NOT `cat /etc/hosts`
- `**` **does** match `/` — `cat **` covers `cat /Users/viktor/.claude/settings.json`
- `>`, `>>`, `<`, `2>&1` are **not** operators — `*` matches across them
- Operators inside quotes (`"a|b"`, `'a;b'`) or after backslash (`\|`) are literals
- Colon-style patterns (`Bash(git status:*)`) are equivalent to space-style (`Bash(git status *)`)
- Deny rules hard-block with no dialog; local deny beats global allow
- Standalone `cat /absolute/path` → converted to **Read tool**, not Bash — test Bash rules via pipe
- `Read(//path)` double-slash required for absolute paths in Read rules

## TSV formats

`claude_bash_commands.tsv` (output of `extract_bash_commands.py`) uses the following format:

```
# Format: total<tab>auto<tab>user<tab>denied<tab>command
5	3	2	0	git status
1	0	0	1	cat /etc/passwd
```

`check_permissions.py` and `find_redundant_rules.py` accept **both** the new 5-column format and the legacy 2-column format (`count<tab>command`) for backward compatibility.

Approval status is inferred from the delta between `tool_use` and `tool_result` timestamps in the JSONL session files:
- **auto** — delta < threshold (default 2 s); matched an allow rule
- **user** — delta ≥ threshold; user approved via dialog
- **denied** — result text matches `"Permission to use Bash with command … has been denied."`

Known limitation: slow auto-approved commands (e.g. `npm install`) will be misclassified as `user` because their execution time pushes the delta above the threshold.

## What not to do

- Do not add external runtime dependencies — the tool must work with stdlib only
- Do not change `*` matching to cross shell operators — that would break the core semantics
- Do not assume `settings.local.json` exists — both settings files are optional
- **Do not change `claude_glob.py` matching logic based on observation or hypothesis alone.**
  All changes to `_STAR`, `_DOUBLE_STAR`, or operator handling require prior always-deny
  live verification (fresh session, single rule, deny all dialogs).  Same rule applies to
  flipping UNKNOWN → HYPOTHESIZED → VERIFIED annotations in `test_behavior_spec.py`.
  Implement only after the user reports the verified result.
- **Do not rewrite any code, tests, or documentation without an explicit instruction to do so.**
  Presenting findings and waiting for approval is not the same as receiving an instruction to implement.
  When in doubt — ask, do not act.
