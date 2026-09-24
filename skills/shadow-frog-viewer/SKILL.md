---
name: shadow-frog-viewer
description: >-
  Browse and query the shadow knowledge base with bounded, citation-ranked
  retrieval: search files, symbols, or text, expand individual discoveries,
  page through preferences, or see recent discoveries.
  Invoke when the user wants to see what's in the shadow, get an
  overview, or find specific knowledge.
scripts:
  - shadow-viewer.py
  - dream-lineage.py
---

# ShadowFrog Viewer

Query and browse `.shadow/` content. Prerequisite: `.shadow/` exists.

## Primary: Python Helper Script

The companion script `shadow-viewer.py` supports Python 3.9+ and lives beside
this SKILL.md file. To find and run it:

```bash
# Project install (Copilot CLI):
python3 .github/skills/shadow-frog-viewer/shadow-viewer.py [options]
# Or for Claude Code:
python3 .claude/skills/shadow-frog-viewer/shadow-viewer.py [options]
```

### Available Views

| Command | What it shows |
|---------|--------------|
| `--summary` | Overview: counts, source/status/label breakdown, per-file table, cross-cutting titles (default) |
| `--search QUERY` | Bounded search across paths, symbols, text, cross-cutting entries, and preferences |
| `--symbol FILE::SYMBOL` | Bounded discoveries at an exact symbol, plus matching cross-cutting refs; use `File-Level` for a file-level section |
| `--get ID` | Expand one current discovery; long expansions return a revision-bound continuation |
| `--prefs` | Project-wide preferences; follow all pages before treating them as complete |
| `--recent [N]` | Most recent discovery previews by file mtime (default page size: 10) |
| `--labels LABEL` | Bounded discoveries matching labels (e.g., `bug`, `security`, `bug,performance`) |
| `--top FILE` | Hook-sized actionable previews (default: up to 3 entries, 600 characters). Includes per-file and cross-cutting findings; short symbol/ID lines omit numeric scores to leave room for content. Not an exhaustive file view. |
| `--check-invariants` | Audit structural integrity — bidirectional cross-references, label/source/category enum compliance, heading format, no-orphan-back-pointer. Exits 0 if clean, 1 with one violation per line. Run after dream reconciliation or before commit. |

No arguments defaults to `--summary`.

### Options

| Flag | Effect |
|------|--------|
| `--shadow-dir DIR` | Override .shadow/ location (default: auto-detect from CWD) |
| `--limit N` | Positive page size for search, symbol, labels, or preferences (default: 10) |
| `--max-chars N` | Hard output cap, including metadata/newline (default: 4000; minimum 256, or 0 for explicit uncapped output). Use `--top-max-chars` with `--top`. |
| `--cursor TOKEN` | Continue the same view and filters using the returned ordering snapshot |
| `--text-cursor TOKEN` | Continue the same `--get` body and logical read; copy the returned token rather than fabricating an offset |
| `--event-id ID` | Optional retry ID: each discovery counts at most once per ID within 24 hours (1-128 letters/digits or `. _ : -`) |
| `--no-record` | Do not increase citation scores; existing scores still rank results, and pagination may store a local snapshot |
| `--top-labels LABELS` | Comma-separated label filter for `--top` (default: `bug,security`). Empty string disables label filtering. |
| `--top-limit N` | Max discoveries to show in `--top` (default: 3) |
| `--top-max-chars N` | Hard cap on `--top` total output length (default: 600; minimum 256). Use 0 for no cap. |

### Examples

```bash
# Search file names, symbols, discoveries, cross-cutting entries, and preferences
python3 shadow-viewer.py --search "token expiry"

# Inspect one symbol without loading its entire shadow
python3 shadow-viewer.py --symbol src/auth.py::UserAuth.validate --limit 5

# Expand a returned id, or continue the same search with its returned cursor
python3 shadow-viewer.py --get DISCOVERY_ID
python3 shadow-viewer.py --search "token expiry" --cursor CURSOR_TOKEN

# Security and performance issues
python3 shadow-viewer.py --labels security,performance

# Broaden the per-file label filter and show up to 5 entries
python3 shadow-viewer.py --top src/auth.py --top-labels bug,security,performance --top-limit 5
```

### Citation Score and Retrieval Contract

Replace `DISCOVERY_ID` and `CURSOR_TOKEN` with the exact values returned by the helper.

Content views (`search`, `symbol`, `get`, `prefs`, `labels`, `recent`, `top`)
return an `id`; all except compact `top` also show one `citation_score`.
Every discovery starts at zero by
default, regardless of which workflow wrote it. The helper increments only
entries whose content it emits. Expanding a long claim counts as one logical
read across its continuation chunks. Scanning/matching,
summary statistics, invariant audits, and raw file reads do not count.
Displayed scores are the values **before** the current read. A citation here
measures helper exposure, not proven usefulness, correctness, or LLM influence.
Agents must not manually edit counters or add them to discovery metadata.

Exact search matches and source trust/status rank ahead of citation history;
recent views also prioritize mtime. Within a tied tier, higher scores rank
first, reserving room for a zero-score entry within tied tiers when the page
and character budget can fit multiple entries.
Popularity never overrides a refuted status or authorizes dropping a constraint.
Use targeted searches and additional pages for deduplication rather than
assuming the popular shortlist is exhaustive.

The `d_...` ID binds kind, canonical file/symbol anchor, parsed claim text, and
related refs. Internal whitespace is preserved, including code literals.
Filesystem aliases resolve to the same on-disk name; container-heading prefixes
are removed from symbol anchors. Metadata-only status/source/label changes keep it;
rewording, renaming, or merging claims/refs can create a new zero-score identity.
Scores are not fuzzily transferred or summed during Meditate. Removed identities
can remain in the local ledger but cannot be expanded unless their claim exists.

Scores live in a local-filesystem SQLite database under the repository's
**common Git directory** at
`shadowfrog/citations.sqlite3`, shared by its local worktrees. Different shadow
roots in the same repo have separate scopes. Outside Git, the cache lives under
`$XDG_STATE_HOME/shadowfrog/citations` (Windows: `$LOCALAPPDATA`), falling back to
`~/.local/state/shadowfrog/citations`. It contains identities/counters/events,
not discovery bodies. It is local metadata, not a tracked or multi-machine DB;
Markdown and its format remain authoritative and unchanged. Keep this database
on a local filesystem, not a network share: its WAL journal coordinates readers
and writers on one host.

Successful transactions cannot overwrite concurrent increments. Accounting is
best-effort: a busy ledger can exhaust the 100 ms wait budget. Unknown scores
display as `?`, but counting is still attempted after output; failed increments
warn explicitly that the visit was not recorded. Retry a busy database later,
not by deleting it. Failed stdout emission is not recorded.

Ordinary reads need no retained event receipt. Caller-supplied retry IDs and
generated long-read IDs retain receipts for 24 hours, up to 100,000 receipts.
New explicit receipts beyond capacity fail visibly rather than weakening retry
deduplication. Never reuse a retry ID for an unrelated visit. Expired receipts
are pruned in bounded batches. Commits use full synchronization. WAL checkpoints
run automatically after 256 pages, with a 1 MiB retained-journal limit after
reset; an active transaction can temporarily keep a larger journal. SQLite can
retain reusable free pages in the database.

Result snapshots freeze ordering despite score changes. Identical snapshots
reuse compressed storage; at most 32 snapshots / 8 MiB are retained locally.
They expire after 24 hours or capacity eviction. Keep the original view/filters
with `--cursor`; changed matching content/trust/labels require restarting.
Timestamp-only or unrelated-symbol edits do not invalidate non-recent views.

For a long claim, copy the returned `--text-cursor` continuation. It binds the
exact expanded body and metadata, preserves `--no-record`, and reuses one read
event. Changed content or an expired token requires restarting `--get`. Character
limits bound helper output, not token counts;
the helper can still scan the underlying Markdown locally. If the ledger is
unavailable, repair local-state access before relying on exhaustive pagination.
An incompatible prerelease cache must be moved aside to reset local scores;
never alter shadow content to repair telemetry.

## Dream Lineage Visualization

The companion script `dream-lineage.py` generates an interactive HTML
visualization of the dream experiment tree. It reads `_dreams/_index.md`
and experiment reports to produce a self-contained HTML file.

```bash
# (Claude Code users: replace .github/skills with .claude/skills below)

# Generate dream-lineage.html in the current directory
python3 .github/skills/shadow-frog-viewer/dream-lineage.py

# Custom output path
python3 .github/skills/shadow-frog-viewer/dream-lineage.py -o my-lineage.html

# Explicit shadow directory
python3 .github/skills/shadow-frog-viewer/dream-lineage.py --shadow-dir /path/to/.shadow
```

The HTML groups compounding chains and fresh experiments, includes a full
lineage tree, and supports expanding each experiment's report.

## Fallback: Shell One-Liners

If the Python script fails to execute (wrong Python version, missing
file, permission error, etc.), fall back to these shell commands:

### Summary

Note: shell discovery counts are approximate (may include cross-reference links).

```bash
echo "Files: $(find .shadow -name '*.md' -not -path '*/_cross/*' -not -path '*/_meta/*' -not -path '*/_dreams/*' -not -name '_index.md' -not -name '_prefs.md' | wc -l | tr -d ' ')"
echo "Discoveries: $(find .shadow -name '*.md' -not -path '*/_cross/*' -not -path '*/_meta/*' -not -path '*/_dreams/*' -not -name '_index.md' -not -name '_prefs.md' -exec grep -c '^\- ' {} \; 2>/dev/null | awk '{s+=$1} END {print s+0}')"
echo "Cross-cutting: $(ls .shadow/_cross/*.md 2>/dev/null | wc -l | tr -d ' ')"
python3 -c "import json; d=json.load(open('.shadow/_meta/state.json')); print(f'Last update: {d[\"last_update_at\"]} ({d[\"last_update_type\"]})')"
```

### Search

```bash
grep -rn "QUERY" .shadow/ --include="*.md"
```

### Preferences

```bash
cat .shadow/_prefs.md
```

### Recent

```bash
# macOS
find .shadow -name '*.md' -not -path '*/_meta/*' -exec stat -f '%m %N' {} \; | sort -rn | head -10 | while read ts f; do echo "$(date -r "$ts" '+%Y-%m-%d %H:%M') $f"; done

# Linux
find .shadow -name '*.md' -not -path '*/_meta/*' -printf '%T@ %p\n' | sort -rn | head -10 | while read ts f; do echo "$(date -d @"${ts%%.*}" '+%Y-%m-%d %H:%M') $f"; done
```

## Responding to the User

- Preserve `--top` output as-is; it is intentionally compact and pre-formatted
  for the preToolUse hook.
- If the shadow is empty or has no discoveries, suggest running
  `/shadow-frog-dream` to populate it
