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

### Key directories

- `.shadow/<path>.md` — per-file shadows with symbol-level discoveries
- `.shadow/_cross/` — cross-cutting discoveries spanning multiple files
- `.shadow/_prefs.md` — project-wide user preferences and conventions
- `.shadow/_dreams/` — experiment archive from dream runs (reports + implementation diffs)
- `.shadow/_meta/state.json` — tracking state (last commit, counts)
