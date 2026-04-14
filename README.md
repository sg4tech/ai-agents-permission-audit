# claude-permission-audit

Audit tools for [Claude Code](https://claude.ai/code) permission rules.

Claude Code's `*` wildcard in `Bash(...)` permission patterns does **not** match shell operators (`&&`, `||`, `|`, `;`). This means a rule like `Bash(git *)` does **not** cover `git status && git diff` — Claude Code splits compound commands at operators and matches each segment independently.

This toolset helps you:

- See which commands from your session history are **not covered** by your current allow rules
- Understand **how each command was approved** — automatically by a rule, manually by you, or denied
- Get **pattern suggestions** ranked by how many commands they would unblock
- Find allow rules that are **redundant** (fully covered by another rule)

## Installation

```sh
pip install git+https://github.com/sg4tech/ai-agents-permission-audit.git
```

Or clone and install in editable mode:

```sh
git clone https://github.com/sg4tech/ai-agents-permission-audit.git
cd ai-agents-permission-audit
pip install -e .
```

## Usage

Run all commands from within your project directory (or any subdirectory). Results are written to `audit-output/` at the repo root.

### 1. Extract commands from session history

```sh
claude-audit-extract
```

Scans `~/.claude/projects/<slug>/` for JSONL session files, parses every `Bash` tool invocation, and writes `audit-output/claude_bash_commands.tsv`.

Each command is tagged with an approval status inferred from the time delta between the tool request and its result:

| Status | Meaning |
|--------|---------|
| `auto` | Delta &lt; 2 s — Claude Code approved instantly via a matching allow rule |
| `user` | Delta ≥ 2 s — you saw the approval dialog and clicked Allow manually |
| `denied` | Result contained the Claude Code permission denial message |

Commands you approved manually (`user`) are the highest-priority candidates for new allow rules.

Use `--threshold N` to adjust the auto/user cutoff (default: 2.0 s):

```sh
claude-audit-extract --threshold 3.0
```

> **Note:** slow commands auto-approved by a rule (e.g. `npm install`) will be classified as `user` because their execution time pushes the delta above the threshold. This is a known limitation.

Console output includes a status summary:

```
Extracted 424 unique commands (468 total) -> audit-output/claude_bash_commands.tsv
  auto: 293  user: 155  denied: 20
```

### 2. Check commands against your permission rules

```sh
claude-audit-check
```

Reads the TSV and checks each command against `allow`/`deny` rules from `.claude/settings.local.json` and `~/.claude/settings.json`.

Output files written to `audit-output/`:

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

### 3. Get pattern suggestions

```sh
claude-audit-suggest
```

Analyzes `commands_not_allowed.tsv` and proposes `Bash(...)` allow rules ranked by coverage:

```
Suggested allow rules (8 total):

  Bash(cat *     # 6 cmd(s), 11 invocation(s)
  Bash(head *    # 7 cmd(s),  9 invocation(s)
  Bash(echo *    # 5 cmd(s),  9 invocation(s)
  Bash(ls *      # 5 cmd(s),  7 invocation(s)
  ...

With these rules: 26/31 uncovered commands would be resolved.
```

Add `--apply` to patch `.claude/settings.local.json` directly:

```sh
claude-audit-suggest --apply
```

Use `--min-count N` to only suggest patterns that cover at least N commands:

```sh
claude-audit-suggest --min-count 2
```

### 4. Find redundant allow rules

```sh
claude-audit-redundant
```

Reports rules where every command matched by rule A is also matched by rule B — safe to remove.

Example output:

```
Found 1 potentially redundant rules:

  REDUNDANT:  Bash(git log --oneline)
  COVERED BY: Bash(git log*)
  (matched 4 real commands)
```

## How matching works

`*` in a Claude Code permission pattern matches any sequence of characters **except** unquoted shell operators. To allow a compound command, you need either:

- A pattern that explicitly contains the operator: `Bash(cd * && git *)`
- Individual patterns covering each segment: `Bash(cd *)` + `Bash(git *)`

Operators inside quotes (`"a|b"`) or after backslash (`\|`) are treated as literals and pass through fine. File-descriptor redirects (`2>&1`, `2>/dev/null`) are not operators.

## Running tests

```sh
pytest
```

Unit tests covering the glob matcher, permission checker, approval-status extraction, and behavioral specification.

## License

Apache 2.0
