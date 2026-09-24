## ShadowFrog Knowledge Base

This project uses a `.shadow/` knowledge base with verified discoveries about non-obvious code behavior. **You MUST consult the shadow before making any code change.**

1. **Read preferences first** — `.shadow/_prefs.md` contains project conventions.
2. **Navigate directly** — before editing `<file>`, read `.shadow/<file>.md`
   and its relevant symbol sections using native file reads/searches.
3. **Follow cross-cutting links** — inspect the relevant `.shadow/_cross/` entries.
4. **Act on what you find** — apply what you learn from the shadow to your work.
5. **After making changes** — run `/shadow-frog-update` to capture learnings

The shadow contains discoveries from code analysis and user conversations. Always consult it before making assumptions about code behavior.

File/symbol paths are sufficient; no viewer or database is required to read the
knowledge. For large sections, the optional `shadow-read.py` in the core
`shadow-frog` skill can retrieve a known file/symbol or search and page results.
Use the user-facing `/shadow-frog-viewer` when the **user** asks to browse or visualize knowledge,
not as the mandatory read path for code work.

Helper reads update one local `citation_score` atomically across worktrees;
continuation chunks share one logical read. Native reads are normal and uncounted.
Never edit counters in Markdown or reread only to increase them. Scores measure
exposure, not correctness or usefulness. A shortlist is not exhaustive: inspect
the specific claim before adding duplicate knowledge.

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
