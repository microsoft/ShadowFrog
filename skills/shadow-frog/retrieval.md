# Optional Agent Retrieval Reference

Direct `.shadow/<source-path>.md` reads and symbol navigation remain the default.
Use the core `shadow-read.py` when a known section is too large for context or
when a targeted search is useful. It supports Python 3.9+ and does not require
the user-facing Viewer. Neither tool changes the canonical Markdown format.

```text
python .github/skills/shadow-frog/shadow-read.py src/auth.py
python .github/skills/shadow-frog/shadow-read.py src/auth.py::UserAuth.validate --limit 5
python .github/skills/shadow-frog/shadow-read.py --search "token expiry"
python .github/skills/shadow-frog/shadow-read.py --get DISCOVERY_ID
```

Use `.claude/skills/` for Claude Code. Replace `DISCOVERY_ID` and cursor values
with values returned by the helper. A known file or `file::symbol` can be passed
positionally; the equivalent explicit selectors are `--file` and `--symbol`.
Do not combine a positional target with another view.

## Views and Options

| View | Behavior |
|------|----------|
| `--file FILE` | Bounded per-file knowledge, including file-level sections and related cross-cutting entries; does not search unrelated per-file shadows |
| `--symbol FILE::SYMBOL` | Exact symbol and cross-cutting refs; `File-Level` selects its file-level section |
| `--search QUERY` | Search paths, symbols, claim text, preferences, and cross-cutting entries |
| `--get ID` | Expand one current entry; long content returns a revision-bound continuation |
| `--prefs` | Preferences; follow every page when relying on the full set of directives |
| `--labels LABELS` | Comma-separated actionable label filter |
| `--recent [N]` | Most recent previews by source-shadow mtime (default: 10 per page) |
| `--top FILE` | Compact actionable hints (default labels: `bug,security`, up to 3 entries / 600 characters); not exhaustive or pageable |
| `--check-invariants` | Structural audit; no citation recording |
| `--summary` | Statistics/overview; no citation recording |

| Option | Contract |
|--------|----------|
| `--shadow-dir DIR` | Override the shadow root; otherwise locate it from the working directory |
| `--limit N` | Positive page size for file/search/symbol/labels/preferences (default: 10) |
| `--max-chars N` | Output cap including metadata/newline (default: 4000; minimum 256, or 0 for explicit uncapped output) |
| `--cursor TOKEN` | Continue the same view and filters with its frozen result ordering |
| `--text-cursor TOKEN` | Continue the same unchanged `--get` body as one logical read |
| `--event-id ID` | Retry ID: each entry counts once per ID within 24 hours; 1-128 letters/digits or `. _ : -` |
| `--no-record` | Do not increment scores; existing scores still rank results, and pagination can store a local snapshot |
| `--top-labels LABELS` | Labels for `--top`; an empty string removes its label filter |
| `--top-limit N` | Positive result limit for `--top` (default: 3) |
| `--top-max-chars N` | Cap for `--top` instead of `--max-chars` (default: 600; minimum 256, or 0 for no cap) |

For a truncated result set, repeat the same view with the returned `--cursor`.
For a long expanded claim, copy its `--get ... --text-cursor ...` continuation.
Character limits constrain output, not token counts or local parsing work.
Errors use stderr and exit 1 (invalid arguments: 2). Optional telemetry warnings
do not hide knowledge; raw reads remain available independently.

## Citation and Identity Semantics

There is one `citation_score`, initially zero regardless of the creation workflow.
The user Viewer and the optional core helper share identities and the same ledger.
Only emitted claim content increments scores. Parsing, matching, summaries,
structural audits, and native file reads do not. Do not manually add score fields
to Markdown or treat partial instrumentation as a complete access history.

Displayed scores precede the current read. Long expansion chunks share one
logical-read event. Compact `--top` lines omit numeric scores to preserve room for
content. A citation measures exposure, not correctness or influence on reasoning.
Relevance and source trust/status rank ahead of scores; tied tiers reserve room
for zero-score entries when page and character budgets allow multiple results.
Popularity never overrides refutation or authorizes dropping a user constraint.

The `d_...` fingerprint binds kind, canonical file/symbol anchor, parsed claim
text, and related refs. Internal whitespace is preserved, including code literals.
Filesystem aliases share on-disk identity and container prefixes are removed
from symbol anchors. Metadata-only source/status/label changes retain identity;
rewriting, moving, or merging claims/refs can produce a new zero-score identity.
Duplicate labels are unioned. No fuzzy score transfer is performed by Meditate.
An ID is an optional handle, not a replacement for the file/symbol address.

## Local State and Concurrency

SQLite lives at `shadowfrog/citations.sqlite3` under the repository's **common Git
directory**, shared by local worktrees. Different shadow roots have separate
scopes. Standalone shadows use `$XDG_STATE_HOME/shadowfrog/citations`
(Windows: `$LOCALAPPDATA`), falling back to `~/.local/state/shadowfrog/citations`.
The cache stores IDs, scores, retry receipts, hashed requests and ordered ID
snapshots, not discovery bodies. It is local metadata, not a Git-synchronized DB.
WAL requires a local filesystem, not a network share.

Successful transactions are atomic. Busy reads/writes retry within a 100 ms
budget; accounting is best-effort, and a timed-out increment warns that the visit
was not recorded. Unknown scores display as `?`, but recording can still recover
after output. Retry a busy database later rather than deleting it.

Ordinary reads retain no event receipt. Explicit retry and generated logical-read
receipts expire after 24 hours and are capped at 100,000; excess new receipts fail
visibly without weakening deduplication. Expired receipts are pruned in bounded
batches. Do not reuse one event ID for unrelated visits.

Identical page snapshots reuse compressed storage, with at most 32 snapshots /
8 MiB retained. They expire after 24 hours or capacity eviction. Score changes
do not change a cursor's order. Relevant matching content/metadata changes require
restarting; mtime-only or unrelated-symbol edits do not invalidate non-recent views.
Text cursors bind the exact expanded body and metadata, preserve `--no-record`,
and expire after 24 hours; changed or expired content requires restarting `--get`.

Commits use full synchronization. WAL checkpoints run after 256 pages with a
1 MiB retained-journal limit after reset; active transactions can keep a larger
journal temporarily. SQLite can retain reusable free pages. Score rows grow with
distinct identities. An incompatible prerelease cache emits reset guidance; move
that cache aside, not the shadow, if deliberately resetting local telemetry.
