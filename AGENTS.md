# AGENTS.md

## Project overview

Audit toolset for Claude Code permission rules. Three scripts that work together:

1. **`extract_bash_commands.py`** — scans `~/.claude/projects/<slug>/*.jsonl` for `tool_use` Bash blocks and writes a frequency-sorted TSV
2. **`check_permissions.py`** — reads that TSV and checks each command against `allow`/`deny` rules from `settings.local.json` and `~/.claude/settings.json`
3. **`find_redundant_rules.py`** — finds allow rules that are fully covered by another rule

Core matching logic lives in **`src/permission_audit/claude_glob.py`**.

## Running tests

```sh
python -m venv .venv && .venv/bin/pip install -e ".[dev]" -q
.venv/bin/pytest
```

## Key invariants

`*` in Claude Code permission patterns does **not** match shell operators (`&&`, `||`, `|`, `;`) **and does not match `/`**.

- `git *` matches `git status` but NOT `git status && git diff`
- `cat *` matches `cat README.md` but NOT `cat /etc/hosts` (slash in argument)
- `cat **` matches `cat /Users/viktor/.claude/settings.json` (`**` crosses `/`)
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

## What not to do

- Do not add external runtime dependencies — the tool must work with stdlib only
- Do not change `*` matching to cross shell operators — that would break the core semantics
- Do not assume `settings.local.json` exists — both settings files are optional
