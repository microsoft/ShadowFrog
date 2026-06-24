# ShadowFrog — AI Agent Guidelines

## Project Overview

ShadowFrog is a suite of AI coding agent skills that build and maintain shadow knowledge bases for any codebase. It consists of 6 skills (`shadow-frog`, `shadow-frog-init`, `shadow-frog-update`, `shadow-frog-dream`, `shadow-frog-meditate`, `shadow-frog-viewer`) and associated hooks, all sharing a common `.shadow/` filesystem. This is a **distributable skills package** — users install it into their own projects via `install.sh`.

## Repository Structure

```
ShadowFrog/
  skills/
    shadow-frog/SKILL.md         Main entrypoint (docs, reference system, search)
    shadow-frog-init/            First-time setup (create .shadow/)
      SKILL.md                   Init instructions + fallback steps
      shadow-init.py             Python helper script
    shadow-frog-update/SKILL.md  Incremental update (after changes)
    shadow-frog-dream/           Autonomous exploration + experimentation (AFK mode)
      SKILL.md                   Dream instructions + pipeline phases
      dream-setup.sh             Worktree + branch creation
      dream-validate.py          Pre-push artifact validation
      dream-reconcile.py         Merge dream branches into main's shadow
      dream-coverage.py          Exploration coverage map
      dream-cleanup.sh           Safe per-worktree cleanup (replaces inline snippet)
      dream-gc.sh                Orphan-worktree sweep (defense-in-depth)
      _worktree_safety.py        Shared safety gate for rm-rf paths
    shadow-frog-meditate/SKILL.md Dedup, merge, and resolve conflicting discoveries
    shadow-frog-viewer/          Browse and query the shadow knowledge base
      SKILL.md                   Query instructions + shell fallbacks
      shadow-viewer.py           Python helper script
      dream-lineage.py           Dream lineage visualization
  hook-templates/
    shadow-frog-hooks.json       Copilot CLI hook config (sessionStart, preToolUse)
    claude-settings.json         Claude Code hook config (.claude/settings.json: SessionStart, PreToolUse)
    scripts/
      shadow-frog-check-init.sh  Session-start: check .shadow/ exists
      shadow-frog-pre-tool.sh    Pre-tool: shadow awareness + knowledge capture reminder
  examples/
    coupon-demo/                 Example of what `.shadow/` looks like (3 source files + .shadow/)
      cart.py                    Cart logic (coupon lookup + total calculation)
      inventory.py               Coupon validation (cross-file case mismatch)
      test_cart.py               Passing tests for existing coupons
      README.md                  Tour of the .shadow/ for this demo
      .shadow/                   Agent-discovered knowledge base (init + dream)
  eval/                          Systematic eval — see eval/README.md
    README.md                    Methodology + results
    results_dashboard.html       Interactive results dashboard
    swesmith/manifests_canonical/ SWE-Smith stacked-bug task manifests
  agent-context.md               Always-on context for project instructions
  install.sh                     Install skills + hooks into a project repo (bash)
  install.ps1                    Windows/PowerShell port of install.sh (no python3 dep)
  README.md                      User-facing documentation
  claude.md                      This file
```

## Key Principles

1. **General-purpose** — ShadowFrog works with any codebase, any language. Skills and examples must be language-agnostic. Never assume Python, JS, or any specific stack.

2. **Discoveries, not descriptions** — Shadows contain behavioral insights (edge cases, implicit contracts, non-obvious interactions), NOT code summaries or descriptions. Write "silently returns None on expired tokens" not "handles token expiration".

3. **Two sources of knowledge** — The shadow captures knowledge from autonomous code analysis (`source: exploration`) AND from user-agent conversations (`source: user`, `source: interaction`). User-shared knowledge is the highest-trust source — capture it immediately, anchored to the exact file and symbol.

4. **Symbol-level granularity** — Shadows mirror the codebase at the symbol level, not just file level. Every class, function, and method has a `##` section in its shadow file. This enables precise bidirectional lookup: code→shadow and shadow→code.

5. **Bidirectional references are the core mechanism** — The entire system rests on robust, accurate references between code and shadow. The canonical format is `file::symbol` (e.g., `src/auth.py::UserAuth.validate`). Seven invariants must hold (see `shadow-frog/SKILL.md`). When editing skills, never break reference integrity.

6. **Trust hierarchy** — `source: user` (always verified) > `source: interaction` (always verified) > `verified` exploration > `uncertain` > `refuted`.

7. **Cross-cutting is critical** — `_cross/` discoveries span multiple files and are stored once. Per-file shadows have `## Cross-References` back-pointers. Always maintain both directions.

8. **No backward compatibility** — When refactoring, only keep the latest code. No re-exports, deprecation wrappers, or compatibility shims.

## Important Conventions

### Discovery Format

Canonical formal spec: `/shadow-frog`. The shapes below are the minimum an agent needs to write a valid discovery from claude.md alone.

Per-file discovery (anchored by `file::symbol` heading; labels and `Also involves:` are optional):
```
- <behavioral statement>
  _(<verified|uncertain|refuted>, source: <exploration|user|interaction>[, labels: [bug, security]])_
  Also involves: `file::symbol`, `file::symbol`
```

Cross-cutting (`_cross/<slug>.md`, slug = kebab-case from title, e.g. "DB connection lifecycle" → `db-connection-lifecycle.md`):
```
# <Title>

**Category**: <pattern|behavior|edge-case|contract|performance|intent|warning|history|convention>
**Refs**:
- `file::symbol`

**Discovery**: <behavioral statement>

_(<verified|uncertain|refuted>, source: <exploration|user|interaction>)_
```

Preference (`_prefs.md` — project-wide, no file/symbol anchor):
```
- <preference or convention>
  _(source: <user|interaction>)_
```

- Labels (lowercase, comma-separated): `bug`, `performance`, `security`, `feature-gap`, `tech-debt`. Only for actionable discoveries.
- `Also involves:` always uses `file::symbol`, never bare file paths.
- `Dream report: _dreams/<dream-id>/` is optional — only for experiment-derived discoveries.

### Verification
- Observe-based: read source at `file::symbol`, trace logic, confirm claim.
- Do-based: write and run a short test/script to confirm or refute.
- `source: user` and `source: interaction` → always `verified`.

### Dedup
- Before writing, read existing discoveries at the target symbol.
- Same claim → update existing. Extends existing → merge. Contradicts → keep both, mark weaker `refuted`.
- If `_No discoveries yet._` placeholder → replace it. If discoveries already exist → append after them.

### Shadow File Headings
- Top-level symbols: `##` heading with symbol in backticks
- Nested symbols: `###` heading with symbol in backticks

Examples:
```
## `authenticate_user`
## `class UserAuth`
### `UserAuth.validate`
```
- `## Cross-References` at the bottom of every per-file shadow

### Cross-Cutting Files (`_cross/<slug>.md`)
- Use `**Refs**:` with `file::symbol` entries
- Category field values: pattern, behavior, edge-case, contract, performance, intent, warning, history, convention

### state.json Schema (canonical)
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

`total_discoveries` counts **per-file discoveries only** (excludes `_cross/`
and `_dreams/`). Cross-cutting discoveries are tracked separately via
`ls .shadow/_cross/*.md | wc -l`.

### Dream Reports (`_dreams/`)

Dream experiment reports are archived in `_dreams/` for compounding knowledge
across dream sessions. Each experiment gets a folder named `YYYYMMDD-HHMMSSZ-slug`.

Report frontmatter (YAML):
```yaml
---
dream_id: "20250417-183012Z-retry-logic"
category: feature design
verdict: useful | dead_end
base_commit: abc1234def5678
branch: "dream/myproject/20250417-183012Z-retry-logic"
parent_branch: "main"
remote: "origin"
related_symbols:
  - "src/http.py::HttpClient.send"
builds_on: []
---
```

Note: `tip_commit` is NOT stored in the report (chicken-and-egg problem).
The reconciler derives it via `git rev-parse origin/$BRANCH` and records
it in `_dreams/_index.md`.

- `_dreams/_index.md` — table of all experiments (dream_id, category, verdict, title, branch, parent, tip_commit). The `parent` column is a **branch name** (the parent dream's branch, or `main` if rooted at the base branch) — never a dream_id. Both the reconciler (writer) and `dream-lineage.py` (reader) treat it as a branch name; meditate's index repair resolves to and writes the parent row's branch.
- `_dreams/<dream-id>/report.md` — structured report with frontmatter (mirrored from dream branch)
- `_dreams/<dream-id>/patch.diff` — code-only diff against `base_commit` (excludes `.shadow/`)
- `_dreams/<dream-id>/manifest.json` — machine-readable discovery manifest (on dream branch)
- Per-file discoveries cross-reference with `Dream report: _dreams/<dream-id>/`
- `_dreams/` is excluded from discovery counts and viewer file listings

## SKILL.md Format

Each skill has a `SKILL.md` with YAML frontmatter:

```yaml
---
name: skill-name
description: >-
  One-paragraph description. This is what the agent matches
  against to decide when to load the skill.
scripts:        # optional — list script filenames in this directory
  - my-script.py
---

# Skill Title

Markdown instructions for the agent.
```

The `description` field is critical — it determines when the agent auto-loads the skill. Make it specific and action-oriented.

The `scripts` field (optional) lists executable scripts bundled with the skill. Scripts live in the same directory as SKILL.md. With a project install, agents can find them via:
```bash
python3 .github/skills/<skill-name>/<script>.py
# or, for Claude Code:
python3 .claude/skills/<skill-name>/<script>.py
```

## Hook Format

Two agent platforms, two hook-config shapes, **one set of shared scripts**:

**Copilot CLI** — `hook-templates/shadow-frog-hooks.json` (installed to
`.github/hooks/hooks.json`):
- `sessionStart` / `preToolUse` events; handler uses `bash:` + `timeoutSec`
- Reads context from the top-level `additionalContext` output key. For
  `preToolUse`, support for `additionalContext` is undocumented in the 2026
  hooks reference but explicitly confirmed in the copilot-cli v1.0.24
  changelog. If Copilot ever removes this, the `sessionStart` reminder
  remains; only the pre-edit injection silently no-ops.

**Claude Code** — `hook-templates/claude-settings.json` (merged into
`.claude/settings.json`):
- `SessionStart` / `PreToolUse` events (PascalCase), matcher-group nesting,
  `command:` + `timeout`, scripts referenced via `${CLAUDE_PROJECT_DIR}`
- Reads context from the nested `hookSpecificOutput.additionalContext` key

**Shared script contract** (both `check-init.sh` and `pre-tool.sh`):
- Receive JSON on stdin; parse both camelCase (Copilot `toolName`/`toolInput`)
  and snake_case (Claude `tool_name`/`tool_input`) field names
- Emit JSON carrying BOTH output shapes so one payload drives both agents
- Use `python3 -c "import json,sys; ..."` for JSON parsing (not grep/cut)
- Keep hooks fast (< 5 second timeout)
- **Fail-open — the hooks are advisory and MUST always exit 0.** Copilot CLI
  ≥ 1.0.57 denies the tool call when a `preToolUse` command hook exits
  non-zero. The scripts therefore use a **multi-layer defense** (interactive
  scripts like `install.sh` and `dream-setup.sh` are the opposite — they
  fail-fast):

  1. **No `set -e`/`-u`/`pipefail`** — failing sub-steps don't abort the script.
  2. **Trap pyramid** — separate `trap 'exit 0' EXIT` AND
     `trap 'exit 0' TERM HUP INT`. EXIT alone returns 143/-15 under SIGTERM
     (empirically verified on bash 3.2 macOS / bash 5+ Linux), which the
     runner's `timeoutSec` enforcement triggers; the TERM trap converts it to 0.
  3. **Every external call bounded** — `git`, `python3`, and viewer
     subprocesses MUST run inside Python `subprocess.run(timeout=...)`
     wrappers. Bash queues signals while waiting for a foreground child, so
     the trap pyramid cannot save us from an unbounded hang. Total bounded
     work budget is ~3.5s, leaving ≥1.5s headroom under the hook's 5s
     `timeoutSec`. The previously-unbounded `git rev-parse --show-toplevel`
     in pre-tool.sh was reproduced as a 31s hang in production.
  4. **`state.json` read inside Python** (`json.load(open(...))`) rather than
     a shell `< redirect`, so a missing file is a caught exception instead of
     an stderr leak.
  5. **Static enforcement** — `hook-templates/check-hook-failopen.py` blocks changes
     that re-introduce any of: short/long-form strict-mode flags, `source`/`.`
     of external files, missing EXIT or TERM trap,
     comment-masquerading-as-trap, or unbounded `git` calls at bash level.

## Development

- SKILL.md files ARE the product — edit them directly
- Test by running skills in Copilot CLI / Claude Code
- Hook scripts are bash with python3 for JSON — keep them simple and fast
- Use `install.sh --project <repo>` (with `--agent copilot|claude`) to copy
  skills, hooks, and context into a project repo
- The `examples/coupon-demo/.shadow/` must stay consistent with skill docs (same formats, same field names, matching counts)
- After any format change, audit ALL files for consistency (skills, examples, hooks, README, claude.md)
