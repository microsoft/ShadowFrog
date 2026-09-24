## ShadowFrog Knowledge Base

This project uses a `.shadow/` knowledge base with verified discoveries about non-obvious code behavior. **You MUST consult the shadow before making any code change.**

1. **Check the shadow first** — before editing any file, read its shadow:
   ```
   cat .shadow/<file-path>.md
   ```
2. **Check preferences** — `cat .shadow/_prefs.md` for project conventions
3. **Check cross-cutting** — `cat .shadow/_cross/*.md` for multi-file patterns
4. **Act on what you find** — apply what you learn from the shadow to your work.
5. **After making changes** — run `/shadow-frog-update` to capture learnings

The shadow contains discoveries from code analysis and user conversations. Always consult it before making assumptions about code behavior.

Each entry's Markdown metadata includes `citation_score` (initially 0; omitted
also means 0). After deliberately consulting an entry, increment it once per
task using `shadow-cite.py` in the core `shadow-frog` skill, supplying the file,
symbol and exact claim text already read. Batch repeated `--text` arguments
for one file/section. Coordinate subagent updates through one writer.
Do not count every entry in an opened file or recount repeated reads.
The score is approximate revisit frequency, not confidence; relevance and trust
take precedence. `/shadow-frog-viewer` is for user-facing inspection, not a
required read path. There is no citation database or retrieval protocol.

### Key directories

- `.shadow/<path>.md` — per-file shadows with symbol-level discoveries
- `.shadow/_cross/` — cross-cutting discoveries spanning multiple files
- `.shadow/_prefs.md` — project-wide user preferences and conventions
- `.shadow/_dreams/` — experiment archive from dream runs (reports + implementation diffs)
- `.shadow/_meta/state.json` — tracking state (last commit, counts)
- `.shadow/_meta/naps/` — optional ideation records, not verified discoveries

Use `/shadow-frog-nap` for implementation-free feature-task ideation within a
work budget. Dream and Nap accept `mode=coherent`: each child needs a
substantive parent connection, while siblings remain free to pursue diverse
directions. A nap parent is an idea, not proof that its proposed code exists.
Nap's managed tree operations preserve proposals and record independent judge
receipts; only current accepted judgments make tasks ready for selection.
Judged planning quality is not execution-verified correctness.
