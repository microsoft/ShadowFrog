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
source file. Run once per repo. For existing knowledge, use update or a
read-only backend migration preview; never reset merely to change extractors.

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
| `--symbol-backend regex\|astred` | Regex by default; existing shadows inherit `state.json` configuration |
| `--astred-command PATH` | Astred executable; also configurable with `SHADOWFROG_ASTRED_COMMAND` |
| `--astred-timeout SECONDS` | Positive batch timeout; default 180 |
| `--symbols-only` | Print the shared extraction inventory as JSON; do not write `.shadow/` |
| `--preview-migration` | Print inventory plus proposed address changes; do not write `.shadow/` |

### What it does

The helper discovers sources with `git ls-files`, applies
`.shadow/.shadowignore`, extracts symbols, and creates symbol-organized
per-file shadows plus `_index.md`, `_prefs.md`, `_meta/state.json`, and
`.shadowignore`. It reports file/symbol counts and detected languages.

After creating `.shadow/`, complete [Post-init steps](#post-init-steps).
The helper does not choose the version-control mode.

### Optional Astred backend

Astred is an **optional Python declaration backend**, not an LLM analysis
replacement or a required ShadowFrog dependency. Install a pinned, approved
`Astred.Tools` release separately from the authenticated internal feed; see
[setup and authentication](../../README.md#optional-astred-symbol-extraction).
Do not copy Astred source or binaries into the skills package.

For a **new** shadow, after configuring the executable:

```powershell
python .github\skills\shadow-frog-init\shadow-init.py --symbol-backend astred
```

This batches Python files through Astred's `treeerr -- bljson` command chain:
the AST error check must succeed before the shared graph is exported as bljson v2.
Checking export error blocks alone is insufficient because the AST can contain
errors absent from the graph. This is not Python compiler validation:
Tree-sitter may recover missing tokens without producing an error node (for
example, `def broken(:` can still export `broken`). Review source changes and
migration candidates rather than treating an inventory as proof of valid syntax.
The inventory includes named
classes, functions/methods (with full lexical scope), and direct module-level
uppercase assignment targets, including tuple unpacking. Parameters, local
variables, anonymous functions, references, docstring examples, and discard
targets such as `_` are not symbol addresses. Repeated declarations are retained;
they do not acquire artificial graph-ID suffixes.

Other languages continue using the existing regex extractors, explicitly
reported on stderr and in `symbol_backend.fallback_languages`. Python failures
**do not** silently fall back to regex. Missing tools, timeouts, incompatible or
malformed exports, graph errors, incomplete file coverage, and source changes
fail extraction. Astred preflight completes before init creates or resets a
shadow. Temporary graphs are deleted; error-report uploads are disabled.

Astred initialization records this optional metadata in `_meta/state.json`:

```json
"symbol_backend": {
  "name": "astred",
  "normalizer_version": 1,
  "astred_version": "<reported version>",
  "astred_release": "<reported release including build identity>",
  "fallback_languages": ["<non-Python languages present>"]
}
```

The machine-local executable path and credentials are **not** persisted.
`_meta/symbols.json` caches the validated inventory, keyed by source SHA-256,
backend release, and normalizer version. Updates reuse unchanged Python files
and batch only cache misses. A full cache hit does not invoke Astred; on a miss,
the installed tool's reported release must match the recorded metadata.
Corrupt caches fail visibly; remove only `_meta/symbols.json` to regenerate,
not the knowledge tree.

### Preview an existing shadow without losing discoveries

```powershell
python .github\skills\shadow-frog-init\shadow-init.py --preview-migration --symbol-backend astred
```

The JSON contains the new inventory and `migration.files`: scope-change
**candidates**, added addresses, `retained_unmatched` existing sections,
ambiguous matches, and orphaned files. Unique leaf-name matches are suggestions,
not proof of a rename. This command bypasses the old cache/version pin so it can
also preview a tool or normalizer upgrade. It never changes headings, discoveries,
trust metadata, citation scores, cross-references, cache, or state.

Review and apply confirmed changes through
[/shadow-frog-update](../shadow-frog-update/SKILL.md), including every affected
shadow even if its source file is unchanged. Keep unmatched/ambiguous discoveries
and historical anchors until resolved; do not delete them because the new
extractor no longer emits a heading. There is deliberately no automatic
migration/apply command in this first integration. `--reset` deletes knowledge
and is **not** a migration mechanism.

## Fallback: Manual Init

If the Python script fails (wrong Python version, missing file, etc.),
follow these steps manually, then complete [Post-init steps](#post-init-steps).
An explicitly selected Astred backend must be repaired or deliberately changed
after migration review; do not use this fallback to conceal extraction failure.

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
  are lost. `dream-setup.py` detects this and refuses to start with a clear
  error rather than failing silently.

- If gitignored: add `.shadow/` to `.gitignore`.

### Report

Print: files discovered, languages detected, total symbols.
Tell the user to edit `.shadow/.shadowignore` to exclude unwanted paths,
such as vendored/generated files or tool configs. Suggest `/shadow-frog-update`
for deeper analysis or after code changes, and `/shadow-frog-dream` for
autonomous exploration if `.shadow/` is committed.
