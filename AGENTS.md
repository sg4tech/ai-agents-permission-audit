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

## Key invariant

`*` in Claude Code permission patterns does **not** match shell operators (`&&`, `||`, `|`, `;`, `>`).

- `git *` matches `git status` but NOT `git status && git diff`
- Compound commands are split at operators; every segment must match independently
- File-descriptor redirects (`2>&1`, `2>/dev/null`) are **not** operators — they pass through fine
- Operators inside quotes (`"a|b"`, `'a;b'`) or after backslash are treated as literals

This invariant is the reason the project exists and must be preserved in all changes to `claude_glob.py`.

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
- `*` does **not** cross shell operators (`&&`, `||`, `|`, `;`, `>`)
- `2>&1` and other fd redirects are **not** operators — `*` matches across them

## What not to do

- Do not add external runtime dependencies — the tool must work with stdlib only
- Do not change `*` matching to cross shell operators — that would break the core semantics
- Do not assume `settings.local.json` exists — both settings files are optional
