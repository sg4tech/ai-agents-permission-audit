# claude-permission-audit

Audit tools for [Claude Code](https://claude.ai/code) permission rules.

Claude Code's `*` wildcard in `Bash(...)` permission patterns does **not** match shell operators (`&&`, `||`, `|`, `;`, `>`). This means a rule like `Bash(git *)` does **not** cover `git status && git diff` — Claude Code splits compound commands at operators and matches each segment independently.

This toolset helps you:

- See which commands from your session history are **not covered** by your current allow rules
- Find allow rules that are **redundant** (fully covered by another rule)

## Requirements

Python 3.9+. No external runtime dependencies.

## Usage

Run all three scripts from within your project directory (or any subdirectory).

### 1. Extract commands from session history

```sh
cd /your/project
python /path/to/permission_audit/extract_bash_commands.py
```

Scans `~/.claude/projects/<slug>/` for JSONL session files, counts every `Bash` tool invocation, and writes `permission_audit/claude_bash_commands.tsv`.

### 2. Check commands against your permission rules

```sh
python /path/to/permission_audit/check_permissions.py
```

Reads the TSV and checks each command against `allow`/`deny` rules from `.claude/settings.local.json` and `~/.claude/settings.json`.

Output files written to `permission_audit/`:

| File | Contents |
|------|---------|
| `commands_not_allowed.tsv` | Commands not matching any allow rule |
| `commands_denied.tsv` | Commands matching a deny rule |
| `commands_compound_not_allowed.tsv` | Compound commands not fully covered |

Example console output:

```
Commands checked: 312 unique
Not allowed:  47 unique (183 invocations)
  simple:     31
  compound:   16 (94 invocations)
Denied:       2 unique
```

### 3. Find redundant allow rules

```sh
python /path/to/permission_audit/find_redundant_rules.py
```

Reports rules where every command matched by rule A is also matched by rule B — safe to remove.

Example output:

```
Found 1 potentially redundant rules:

  REDUNDANT:  Bash(git log --oneline)
  COVERED BY: Bash(git log*)
  (matched 4 real commands)
```

## Options

Each script accepts `--help`. Common overrides:

```sh
# Use a different settings file
python check_permissions.py --settings /other/.claude/settings.local.json

# Explicit project slug instead of auto-detect
python extract_bash_commands.py --project -Users-me-myproject
```

## How matching works

`*` in a Claude Code permission pattern matches any sequence of characters **except** unquoted shell operators. To allow a compound command, you need either:

- A pattern that explicitly contains the operator: `Bash(cd * && git *)`
- Individual patterns covering each segment: `Bash(cd *)` + `Bash(git *)`

Operators inside quotes (`"a|b"`) or after backslash (`\|`) are treated as literals and pass through fine. File-descriptor redirects (`2>&1`, `2>/dev/null`) are not operators.

## Running tests

```sh
pytest permission_audit/
```

93 unit tests covering the glob matcher and permission checker.

## License

Apache 2.0
