---
name: shadow-frog-viewer
description: >-
  Help users browse and visualize their collected shadow knowledge in the
  terminal or as an interactive dream-lineage report. Show an overview,
  search results, preferences, recent discoveries, and structural audits.
  Invoke when the user asks to inspect the shadow. For agents' own code work,
  direct file/symbol navigation is primary; optional retrieval helpers live
  in the core shadow-frog skill.
scripts:
  - shadow-viewer.py
  - dream-lineage.py
---

# ShadowFrog Viewer

**User-facing inspection and visualization** of `.shadow/`. Prerequisite:
`.shadow/` exists. An agent can run these views on the user's behalf, but this
skill is not the agent's required knowledge interface. Agent work starts with
the mirrored file/symbol locations; the core `shadow-read.py` is optional for
large sections or targeted searches.

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
| `--file FILE` | Browse one known source file's shadow, including file-level and cross-cutting knowledge |
| `--symbol FILE::SYMBOL` | Bounded discoveries at an exact symbol, plus matching cross-cutting refs; use `File-Level` for a file-level section |
| `--get ID` | Expand one current discovery; long expansions return a revision-bound continuation |
| `--prefs` | Project-wide preferences; follow all pages before treating them as complete |
| `--recent [N]` | Most recent discovery previews by file mtime (default page size: 10) |
| `--labels LABEL` | Bounded discoveries matching labels (e.g., `bug`, `security`, `bug,performance`) |
| `--top FILE` | Compact actionable previews (default: up to 3 entries, 600 characters). Not an exhaustive file view. Agent hooks use the separate core reader. |
| `--check-invariants` | Audit structural integrity — bidirectional cross-references, label/source/category enum compliance, heading format, no-orphan-back-pointer. Exits 0 if clean, 1 with one violation per line. Run after dream reconciliation or before commit. |

No arguments defaults to `--summary`.

### Options

| Flag | Effect |
|------|--------|
| `--shadow-dir DIR` | Override .shadow/ location (default: auto-detect from CWD) |
| `--limit N` | Positive page size for file, search, symbol, labels, or preferences (default: 10) |
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

### Scores and Continuation

Replace `DISCOVERY_ID` and `CURSOR_TOKEN` with exact returned values. Content
views display the same IDs and local `citation_score` as the optional core
reader, recording only the entries they show. Scores measure helper exposure,
not correctness or proven use; direct file reads remain normal and uncounted.
Knowledge is still plain Markdown at its file/symbol location, not in the ledger.

Use returned cursors to continue, `--get` to inspect a claim, and `--no-record`
when browsing should not affect scores. Forward any stderr diagnostics to the
user; optional telemetry failures must not hide knowledge. See the shared
[retrieval reference](../shadow-frog/retrieval.md) for exact identity, budget,
retry, and local storage contracts.

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

- Preserve the meaning of returned trust/status and citation information;
  scores are not confidence estimates. Expand or page results on user request.
- If the shadow is empty or has no discoveries, suggest running
  `/shadow-frog-dream` to populate it
