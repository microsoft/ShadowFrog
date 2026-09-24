---
name: shadow-frog
description: >-
  Use a shadow knowledge base to understand any codebase. The .shadow/
  directory mirrors the source tree with markdown files containing
  behavioral insights — known bugs, edge cases, implicit contracts,
  and user preferences. Always check the shadow before editing,
  debugging, or investigating code. When the user shares important
  context, write it to the shadow immediately. Invoke shadow-frog-init
  to create it, shadow-frog-update to refresh it, shadow-frog-dream
  for autonomous experiments, shadow-frog-nap for lightweight feature-task
  ideation, shadow-frog-meditate for shadow hygiene,
  or shadow-frog-viewer for user-facing browsing and visualization.
scripts:
  - shadow-read.py
---

# ShadowFrog

`.shadow/` mirrors the source tree. Each source file has a `.md` shadow organized
by symbol. Each symbol section contains discoveries — behavioral insights anchored
to that code location.

## Required Actions

**Every time you work on code in a repo with `.shadow/`:**

1. **Read `.shadow/_prefs.md` first** — it contains project-wide conventions,
   user preferences, and things to avoid.
2. **Read relevant `_cross/` discoveries** — list `_cross/` and read entries
   whose titles relate to the current area, including cross-file contracts
   and interactions.
3. **Check `_dreams/_index.md`** and read relevant experiment reports,
   especially when investigating bugs or unfamiliar code. They may contain
   findings not yet distilled into per-file shadows.
4. **Before editing a file**, navigate directly to `.shadow/<path>.md` and its
   symbol headings, then follow relevant `_cross/` back-pointers. Use native
   file reads/searches; for unusually large sections, the optional core helper
   below can return bounded selections. A shortlist is not a complete file review.
   `_index.md` counts may be stale; inspect the actual shadows and `_cross/`.
5. **When the user explains something about code** (gotcha, design intent,
   warning, history): write a `source: user` discovery to the shadow
   immediately. Resolve its `file::symbol` anchor from `_index.md`, shadows,
   and session context; do not ask where to put it.
6. **When the user states a preference or convention** (not tied to any
   specific file): write it to `_prefs.md` immediately.
7. **After code changes**: run `/shadow-frog-update`

## Optional Agent Retrieval

**File/symbol navigation is primary.** The source path already locates its
shadow; no viewer, retrieval service, or citation database is required to read it.
`/shadow-frog-viewer` is the user-facing browsing/visualization skill, not the
agent's required knowledge interface.

For large files/symbol sections or a targeted search, use `shadow-read.py`
beside this skill. Known paths are read directly, not rediscovered by global
search. Examples below use the Copilot install; Claude Code uses `.claude/skills/`.

```text
python .github/skills/shadow-frog/shadow-read.py src/auth.py
python .github/skills/shadow-frog/shadow-read.py src/auth.py::UserAuth.validate --limit 5
python .github/skills/shadow-frog/shadow-read.py --search "token expiry" --max-chars 1800
```

The optional helper can rank/paginate large sections and expand returned IDs.
Keep file/symbol anchors as the navigation address; IDs only identify individual
entries within this helper. Full-file reads include file-level discoveries and
cross-cutting refs; `--top` is only a compact hint.

Native file reads remain normal and uncounted. Helper-emitted entries increment
one local `citation_score` atomically; do not edit counters in Markdown or run a
second retrieval just to inflate them. Scores measure exposure, not correctness
or proven usefulness. Never omit user constraints because they are unpopular.
Use targeted searches and follow pages when completeness matters.

Read [the helper reference](retrieval.md) when using its budgets, continuation,
or citation options. If optional telemetry fails, keep using the knowledge and
heed the diagnostic; do not substitute score availability for reference integrity.

## Directory Layout

```
.shadow/
  .shadowignore      Gitignore-syntax file for excluding paths from the shadow
  _index.md          File list with symbol counts and discovery counts
  _prefs.md          Project-wide user preferences (not tied to any file/symbol)
  _cross/            Cross-cutting discoveries (span multiple files)
    <slug>.md        One file per cross-cutting discovery (descriptive kebab-case name)
  _meta/
    state.json       Last commit, timestamps, counts
  _dreams/           Dream experiment archive (detailed reports + diffs)
    _index.md        Table of all experiments with verdicts
    <YYYYMMDD-HHMMSSZ-slug>/      One folder per experiment
      report.md      Structured report with YAML frontmatter
      patch.diff     Full implementation diff against base_commit
  <mirrored tree>/   Per-file shadows
    file.py.md       Organized by symbol
```

## Reference Notation

Canonical format: `file_path::symbol_name`

Examples: `src/auth.py::authenticate_user`, `src/auth.py::UserAuth.validate`,
`src/auth.py` (file-level, no symbol)

The symbol name is the stable anchor.

## Per-File Shadow Format

```markdown
# Shadow: src/auth.py

**Language**: Python | **Lines**: 142 | **Last modified**: 2025-01-15

## File-Level

- This module has no __all__ — all top-level names are public.
  _(verified, source: exploration)_

## `class UserAuth`

### `UserAuth.validate`

- Catches ALL exceptions and returns False — swallows
  connection errors, making network failures look like invalid tokens.
  _(verified, source: exploration)_

## `authenticate_user`

- Silently returns None on expired tokens. Callers must check.
  _(verified, source: exploration, labels: [bug])_
  Also involves: `src/middleware.py::require_auth`

## Cross-References

- [db-connection-lifecycle](../_cross/db-connection-lifecycle.md)
  (involves `src/db/connection.py::ConnectionPool`, `src/api/routes.py::get_user`)
```

Heading format (**hard rule — parsers depend on this**):
- Top-level symbols (classes, functions, constants): `##` heading with symbol in backticks
- Nested symbols (methods): `###` heading with symbol in backticks
- `## Cross-References` — always last section

The viewer parser only matches the backtick form. A heading written as
`### UserAuth.validate` (no backticks) will have its discoveries
silently dropped from search/top output. Always wrap the symbol in
backticks, including for nested symbols.

Examples:
```
## `authenticate_user`
## `class UserAuth`
### `UserAuth.validate`
```

## Cross-Cutting File Format (`_cross/<slug>.md`)

```markdown
# Database connection lifecycle

**Category**: pattern
**Refs**:
- `src/db/connection.py::ConnectionPool.get`
- `src/auth.py::authenticate_user`
- `src/api/routes.py::get_user`

**Discovery**: All database access goes through a connection pool that
silently reconnects on failure. First request after DB restart is slow (~2s).

_(verified, source: exploration)_
```

## Preferences File (`_prefs.md`)

Project-wide user preferences and conventions that are not tied to any
specific file or symbol. These guide all agent work across the codebase.

```markdown
# Preferences

- No backward compatibility — only keep the latest code, no shims or aliases.
  _(source: user)_

- Use snake_case for all Python function and variable names.
  _(source: user)_

- Prefer small, focused PRs over large sweeping changes.
  _(source: interaction)_
```

Format:
```
- <preference or convention>
  _(source: <user|interaction>)_
```

Preferences are always trusted (same rank as `source: user`). They don't
need `verified/uncertain/refuted` — if the user said it, it's a directive.

When to write to `_prefs.md` vs per-file shadow vs `_cross/`:
- Applies to the whole repo, no specific file → `_prefs.md`
- Applies to a specific file or symbol → per-file shadow
- Applies to 3+ specific files → `_cross/<slug>.md`

## Discovery Format

Per-file discoveries (no stored IDs — anchored by their `file::symbol` heading):
```
- <behavioral statement>
  _(<verified|uncertain|refuted>, source: <exploration|user|interaction>)_
  Also involves: `file::symbol`, `file::symbol`
```

With labels (optional — only when the discovery is actionable):
```
- <behavioral statement>
  _(<verified|uncertain|refuted>, source: <exploration|user|interaction>, labels: [bug, security])_
  Also involves: `file::symbol`
```

With dream report link (optional — only for experiment-derived discoveries):
```
- <behavioral statement>
  _(<verified|uncertain|refuted>, source: <exploration|user|interaction>)_
  Dream report: `_dreams/<dream-id>/`
```

Cross-cutting discoveries (one per `_cross/<slug>.md` file):
```
# <Title>

**Category**: <category>
**Refs**:
- `file::symbol`

**Discovery**: <behavioral statement>

_(<verified|uncertain|refuted>, source: <exploration|user|interaction>)_
```

Slug naming: use descriptive kebab-case derived from the title.
Example: title "Database connection lifecycle" → filename `db-connection-lifecycle.md`

### Labels

Labels mark actionable discoveries so agents can quickly scan for specific
types. Most discoveries are just knowledge — labels are only for findings
that call for action.

| Label | Use when |
|-------|----------|
| `bug` | A defect that should be fixed |
| `performance` | A bottleneck or inefficiency |
| `security` | A vulnerability or unsafe pattern |
| `feature-gap` | Missing functionality or improvement opportunity |
| `tech-debt` | Code smell, duplication, refactoring opportunity |

A discovery can have multiple labels: `labels: [bug, security]`.
Omit labels entirely for pure observational knowledge.

Labels go in the metadata line:
```
_(verified, source: exploration, labels: [bug])_
```

Cross-cutting discoveries can also have labels — add them to the metadata line.

### Fields

- `verified|uncertain|refuted` — verification status
- `source: exploration` — agent discovered via code analysis
- `source: user` — human stated it in conversation
- `source: interaction` — emerged from collaborative work (debugging, refactoring)
- `labels: [...]` — optional, actionable labels (see table above)
- `Also involves:` — `file::symbol` refs to other code locations (required if discovery touches other files)
- `Dream report:` — optional, `_dreams/<dream-id>/` link for experiment-derived discoveries
- `Category` (cross-cutting only): pattern, behavior, edge-case, contract, performance, intent, warning, history, convention

## Trust Order

1. `source: user` — highest trust, always `verified`
2. `source: interaction` — always `verified`
3. `verified` from exploration
4. `uncertain` — not yet confirmed
5. `refuted` — skip

## Five Reference Links (all must be maintained)

1. **File mapping**: `src/auth.py` ↔ `.shadow/src/auth.py.md`
2. **Symbol anchoring**: every source symbol has a `##`/`###` heading in its shadow
3. **Also involves**: per-file discoveries list other `file::symbol` locations
4. **Cross-ref back-pointers**: per-file `## Cross-References` links to `_cross/<slug>.md` entries
5. **Cross-cutting refs**: `_cross/<slug>.md` `**Refs**:` lists all involved `file::symbol` locations

Links 4 and 5 are bidirectional: if `_cross/db-connection-lifecycle.md` references
`src/auth.py::fn`, then `src/auth.py.md` must list it in `## Cross-References`, and vice versa.

## Seven Invariants

1. Every included source file has exactly one shadow at `.shadow/<path>.md`
2. Every symbol in source has a `##`/`###` heading in its shadow
3. Per-file discoveries touching other files have `Also involves:` with `file::symbol`
4. Cross-ref back-pointers match: `_cross/<slug>.md` refs ↔ per-file `## Cross-References`
5. Every entry in `## Cross-References` has a corresponding `_cross/<slug>.md` file
6. Cross-cutting filenames are unique (enforced by filesystem)
7. No duplicate discoveries (same behavioral claim at same symbol)

To audit a shadow for structural drift (invariant 3 format, invariants 4–5,
plus enum and heading-format guards), the optional core helper also supports:

```bash
READER=""
for DIR in .github/skills/shadow-frog .claude/skills/shadow-frog; do
    [ -f "$DIR/shadow-read.py" ] && READER="$DIR/shadow-read.py" && break
done
python3 "$READER" --check-invariants
```

Exits 0 if clean, 1 with one violation per line otherwise. Invariant 3 is
checked for anchor *format* only (not existence of the referenced file or
symbol); invariants 1, 2, and 7 require source parsing / semantic match and
are not statically checked; invariant 6 is filesystem-enforced.

## Lookup Commands

```bash
# File's shadow
cat .shadow/src/auth.py.md

# Specific symbol's knowledge
grep -A 20 "## \`authenticate_user\`" .shadow/src/auth.py.md

# Cross-cutting discoveries for a file
grep -rl "src/auth.py::" .shadow/_cross/

# Search by topic
grep -rl "error.handling\|exception" .shadow/ --include="*.md"

# All user-shared knowledge
grep -r "source: user" .shadow/ --include="*.md"

# Project-wide preferences
cat .shadow/_prefs.md

# List all cross-cutting discovery files
ls .shadow/_cross/
```

## Verification

Two methods, use whichever fits the claim:

**Observe-based** (for simpler claims — code reading suffices):
1. Read the source code at the relevant `file::symbol`
2. Trace the logic: does the behavioral claim hold?
3. If confirmed → `verified`. If contradicted → `refuted`. If unclear → `uncertain`.

**Do-based** (for harder claims — requires execution):
1. Write a short verification script (test, assertion, or probe) that would
   confirm or refute the claim
2. Run it
3. Based on the result → `verified` or `refuted`

Prefer do-based for claims about runtime behavior, performance, error handling
paths, or race conditions. Prefer observe-based for claims about code structure,
types, or static properties.

## Dedup and Writing Rules

Before writing any discovery, follow this procedure:

1. **Read before write**: Inspect the target `file::symbol` in its shadow and
   search for the specific claim. For a large section, use the optional bounded
   helper, expanding candidates and following pages when needed.
   Do not infer absence from a citation-ranked shortlist. If an existing discovery makes the same behavioral
   claim (even if worded differently) → update the existing one. If the
   new one extends an existing one → merge into a single richer entry.
   If they conflict → investigate the code, keep the correct one, mark
   the other `refuted`.
2. **Append, don't replace**: If the symbol already has discoveries,
   append your new one after them. If there is a `_No discoveries yet._`
   placeholder, remove it and write your discovery. Never use the
   placeholder as an edit anchor if it's already gone — read the file first.
3. Write in canonical format (see Discovery Format above)
4. **Fix bad format**: if you see any existing content that doesn't
   follow the canonical format, fix it in place

For cross-cutting, search `_cross/` for overlapping `**Refs**:` sets
before creating a new entry. Only create `_cross/<slug>.md` for
discoveries spanning **3+ files** — otherwise use per-file entries with
`Also involves:` references.

## Dream and Nap Parent Connections

Both skills accept `mode=broad` (default) or `mode=coherent`. Broad mode
explores distinct opportunities. Coherent mode regularizes **parent-child
edges**, not an entire tree: every child has its own `goal`, and siblings
are encouraged to pursue diverse worthwhile directions. There is no fixed
tree-wide goal, same-feature requirement, or sibling file-disjointness rule.

A child explains which parent capability, observation, limitation, or decision
motivates it. Extending, integrating, challenging, replacing, simplifying, and
offering an alternative are all valid. Technical independence is not itself
disqualifying; a substantive connection matters more than shared vocabulary.
Challenges are optional, not a quota.

Use this `parent_connection` object in coherent child dream manifests and nap
nodes (the parent identifier is stored separately, not duplicated here):

```json
{
  "relation": "replace",
  "basis": "The parent's offset checkpoint can replay output after a crash.",
  "delta": "Use idempotent chunk commits while retaining streaming delivery.",
  "preserves": ["Bounded memory", "The user-facing resume contract"],
  "supersedes": ["Offset-only checkpoint design"]
}
```

`relation` is `extend`, `integrate`, `challenge`, `replace`, `simplify`, or
`alternative`. `basis` and `delta` are nonempty text. `preserves` and
`supersedes` are lists of nonempty strings; empty lists are allowed except
that `replace` must name at least one superseded decision. Roots use null
or omit the connection. Broad mode does not require a connection, but an
explicit connection must still follow this format.

The shared Python helper `_coherence.py` checks structure only. The agent
must judge actual relevance, evidence, and task quality. Superseding a design
does not retroactively refute a historical fact or authorize rewriting user
requirements. Keep historical reports; use meditate to resolve contradictory
shadow claims rather than inventing unsupported dream-manifest operations.

Nap parents are ideas/evidence, not implemented APIs. Task exports must pin a
real code baseline separately from idea lineage. Export a root-to-leaf path
for a trajectory, not stacked siblings; a final task uses its final active
requirements rather than all superseded ancestor designs.

Nap task export requires current acceptance from a strong independent judge;
see `/shadow-frog-nap` for the record and review workflow. The host runs the
judge; the helper checks approval bindings, not reviewer authenticity or
semantic truth. Approval is planning confidence, not execution proof.

## Related Skills

- `/shadow-frog-init` — create `.shadow/` for a new repo
- `/shadow-frog-update` — refresh shadows after changes or from conversation
- `/shadow-frog-dream` — autonomous exploration and experimentation while user is AFK
- `/shadow-frog-nap` — implementation-free, source-grounded feature-task ideation within a work budget
- `/shadow-frog-meditate` — deduplicate, merge, and resolve conflicting discoveries
- `/shadow-frog-viewer` — user-facing CLI browsing and lineage visualization
