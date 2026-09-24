---
name: shadow-frog-viewer
description: >-
  Browse and query the shadow knowledge base: overview, search for files
  or symbols or text, view preferences, or see recent discoveries.
  Invoke when the user wants to see what's in the shadow, get an
  overview, or find specific knowledge.
scripts:
  - shadow-viewer.py
  - dream-lineage.py
---

# ShadowFrog Viewer

Query and browse `.shadow/` content. Prerequisite: `.shadow/` exists.

## Primary: Python Helper Script

The companion script `shadow-viewer.py` lives in the same directory as
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
| `--search QUERY` | Universal search — matches file names, symbol names, and discovery text. Includes cross-cutting and preferences |
| `--prefs` | Project-wide preferences |
| `--recent [N]` | N most recent discoveries with full content (default: 10) |
| `--labels LABEL` | Discoveries filtered by label (e.g., `bug`, `security`, `bug,performance`) |
| `--top FILE` | Top actionable discoveries for FILE — concise output (default: 3 entries, ~600 chars) suitable for the preToolUse hook. Includes both per-file shadow entries and any `_cross/` discoveries that reference FILE. Verified discoveries rank first. |
| `--check-invariants` | Audit structural integrity — bidirectional cross-references, label/source/category enum compliance, heading format, no-orphan-back-pointer. Exits 0 if clean, 1 with one violation per line. Run after dream reconciliation or before commit. |

No arguments defaults to `--summary`.

### Options

| Flag | Effect |
|------|--------|
| `--shadow-dir DIR` | Override .shadow/ location (default: auto-detect from CWD) |
| `--top-labels LABELS` | Comma-separated label filter for `--top` (default: `bug,security`). Empty string disables label filtering. |
| `--top-limit N` | Max discoveries to show in `--top` (default: 3) |
| `--top-max-chars N` | Hard cap on `--top` total output length (default: 600). Use 0 for no cap. |

### Examples

```bash
# Search file names, symbols, discoveries, cross-cutting entries, and preferences
python3 shadow-viewer.py --search "token expiry"

# Security and performance issues
python3 shadow-viewer.py --labels security,performance

# Broaden the per-file label filter and show up to 5 entries
python3 shadow-viewer.py --top src/auth.py --top-labels bug,security,performance --top-limit 5
```

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
Experiment names and test-count metadata render as literal text, not HTML;
escaping happens at rendering time without changing the stored metadata.

Pass stderr back to the agent even when generation exits 0. Required directory,
index, or output failures emit `ERROR` and exit 1; malformed optional metadata
or omitted rows emit `WARNING` with the affected path/field and produce a
partial view. Repair the indicated input or permissions and rerun the same
command. Missing optional artifacts and safely escaped text are not errors.
The helper does not invoke a model or retry automatically.

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
