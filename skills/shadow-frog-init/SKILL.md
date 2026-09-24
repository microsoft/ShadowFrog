---
name: shadow-frog-init
description: >-
  Initialize a shadow knowledge base for any codebase. Creates a .shadow/
  directory that mirrors the source tree with markdown files for AI-discovered
  insights. Run this once per repo before using other shadow-frog skills.
  Refuses to overwrite an existing .shadow/ unless --reset is passed.
scripts:
  - shadow-init.py
---

# ShadowFrog Init

Creates `.shadow/` directory with symbol-organized shadow files for every
source file. Run once per repo. If `.shadow/` exists, ask user to reset or skip.

## Primary: Python Helper Script

The companion script `shadow-init.py` lives in the same directory as
this SKILL.md file. To find and run it:

```bash
# Project install (Copilot CLI):
python3 .github/skills/shadow-frog-init/shadow-init.py [options]
# Or for Claude Code:
python3 .claude/skills/shadow-frog-init/shadow-init.py [options]
```

**Run from the repo/worktree root.** The helper auto-detects it with
`git rev-parse --show-toplevel`. If Git cannot resolve or access the root
(for example, inside a container), use `--root` to bypass detection:

```bash
# If auto-detect fails, pass the root explicitly:
python3 .github/skills/shadow-frog-init/shadow-init.py --root "$(pwd)"
```

### Options

| Flag | Effect |
|------|--------|
| `--root DIR` | Repository root (default: auto-detect via git) |
| `--reset` | Delete existing .shadow/ and recreate |
| `--dry-run` | Show what would be created without writing |

### What it does

The helper discovers sources with `git ls-files`, applies
`.shadow/.shadowignore`, extracts symbols, and creates symbol-organized
per-file shadows plus `_index.md`, `_prefs.md`, `_meta/state.json`, and
`.shadowignore`. It reports file/symbol counts and detected languages.

After creating `.shadow/`, complete [Post-init steps](#post-init-steps).
The helper does not choose the version-control mode.

## Fallback: Manual Init

If the Python script fails (wrong Python version, missing file, etc.),
follow these steps manually, then complete [Post-init steps](#post-init-steps).

### 1. Check preconditions

```bash
git rev-parse --is-inside-work-tree  # must be a git repo
test -d .shadow && echo "exists"     # if exists, ask user: reset or skip
```

### 2. Discover source files

```bash
git ls-files --cached --others --exclude-standard
```

Include patterns (auto-detect from repo contents):
`*.py`, `*.js`, `*.ts`, `*.tsx`, `*.jsx`, `*.java`, `*.go`, `*.rs`, `*.rb`,
`*.cpp`, `*.c`, `*.h`, `*.cs`, `*.swift`, `*.kt`, `*.scala`, `*.php`,
`*.sh`, `*.bash`, `*.zsh`, `*.yaml`, `*.yml`, `*.toml`, `*.json`,
`Makefile`, `Dockerfile`, `docker-compose*.yml`

Default excludes (always applied): `node_modules/`, `vendor/`, `venv/`,
`.venv/`, `__pycache__/`, `*.min.js`, `*.min.css`, `*.map`, `*.lock`,
`dist/`, `build/`, `target/`, `out/`, `.shadow/`, binary files

After discovering files, filter them through `.shadow/.shadowignore`
(if it exists). The ignore file uses `.gitignore` syntax.

### 3. Create directories

```bash
mkdir -p .shadow/_cross .shadow/_meta .shadow/_dreams
# For each source file, create parent dirs: mkdir -p .shadow/<dir>/
```

### 4. Create `.shadow/.shadowignore`

Uses `.gitignore` syntax. Seed with sensible defaults:

```gitignore
# Directories
node_modules/
vendor/
venv/
.venv/
__pycache__/
dist/
build/
target/
out/

# Generated / minified
*.min.js
*.min.css
*.map
*.lock

# Binary
*.png
*.jpg
*.gif
*.ico
*.woff
*.woff2
*.ttf
*.eot
*.pdf
*.zip
*.tar.gz

# The shadow itself
.shadow/

# ShadowFrog's own install artifacts (project install copies these here)
.github/skills/shadow-frog*/
.github/hooks/scripts/shadow-frog-*
.claude/skills/shadow-frog*/
.claude/hooks/scripts/shadow-frog-*
```

### 5. Create `_prefs.md`

```markdown
# Preferences

_No preferences recorded yet._
```

This file stores project-wide user preferences and conventions that are
not tied to any specific file or symbol. It is populated by
`/shadow-frog-update` when the user shares general directives.
New preference/discovery entries use `citation_score: 0`; empty placeholders
are not knowledge entries and do not have scores.

### 6. Create `_meta/state.json`

```json
{
  "version": 1,
  "initialized_at": "<ISO timestamp>",
  "last_update_at": "<ISO timestamp>",
  "last_commit": "<full 40-char HEAD SHA>",
  "last_update_type": "init|auto|manual|dream|meditate",
  "total_files": 0,
  "total_symbols": 0,
  "total_discoveries": 0,
  "dream_cycles_completed": 0
}
```

### 7. Generate per-file shadows

For each source file, extract symbols and create a shadow with this structure:

```markdown
# Shadow: <path/to/file.py>

**Language**: <lang> | **Lines**: <N> | **Last modified**: <date>

## File-Level

_No discoveries yet._

## `class <ClassName>`

### `<ClassName.method>`

_No discoveries yet._

## `<function_name>`

_No discoveries yet._

## Cross-References

_No cross-cutting discoveries yet._
```

Rules:
- Every class, function, method gets a `##`/`###` heading
- Symbol name is the stable anchor — no line numbers in headings
- `## Cross-References` section is always last
- Extract symbols using language-appropriate static analysis (AST, tree-sitter, regex)
- If static analysis is not feasible, create `## File-Level` only; other skills fill in symbols later

### 8. Generate `_index.md`

```markdown
# Shadow Index

> Generated by shadow-frog-init on <date>
> Total files: N | Symbols: M | Discoveries: 0 | Cross-cutting: 0

| File | Language | Symbols | Discoveries |
|------|----------|---------|-------------|
| src/auth.py | Python | 5 (UserAuth, authenticate_user, ...) | 0 |
```

## Post-init Steps

After creating `.shadow/` with either path:

### Version-Control Mode

Ask the user: "Should `.shadow/` be **committed** (shared with your team via
git) or **gitignored** (local to your machine only)?"

Before they decide, make the trade-off explicit:

**Committed (shared) — full functionality:**
- The whole team shares one knowledge base; discoveries compound across people.
- `shadow-frog-dream` works — autonomous experiments commit `.shadow/`
  artifacts onto dream branches, push them, and reconcile merges them back.
- `shadow-frog-meditate` and the viewer work normally.

**Gitignored (local only) — reduced functionality:**
- Your shadow stays private to your machine and never leaves the repo.
- `shadow-frog-init`, `shadow-frog-update`, `shadow-frog-meditate`, and the
  viewer all still work (they operate on the local filesystem).
- **`shadow-frog-dream` will NOT work.** Dreams move `.shadow/` through git
  (commit → push → reconcile from the remote); a gitignored `.shadow/` is
  silently skipped by `git add`, so discoveries never reach the remote and
  are lost. `dream-setup.sh` detects this and refuses to start with a clear
  error rather than failing silently.

- If gitignored: add `.shadow/` to `.gitignore`.

### Report

Print: files discovered, languages detected, total symbols.
Tell the user to edit `.shadow/.shadowignore` to exclude unwanted paths,
such as vendored/generated files or tool configs. Suggest `/shadow-frog-update`
for deeper analysis or after code changes, and `/shadow-frog-dream` for
autonomous exploration if `.shadow/` is committed.
