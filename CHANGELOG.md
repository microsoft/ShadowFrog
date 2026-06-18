# Changelog

All notable changes to ShadowFrog are documented here.

ShadowFrog is a suite of AI coding agent skills that build and maintain
shadow knowledge bases for any codebase.

---

## 2026-06-03

### Changed
- **`preToolUse.matcher` now filters at the CLI layer.** The hook used to
  fire on every tool call (Read, Bash, glob, …) and filter mutations
  internally in bash. With the matcher fix in Copilot CLI 1.0.36, the CLI
  itself can now skip the hook for non-mutating tools, eliminating the
  per-call subprocess overhead for the majority of tool invocations.
  Matcher pattern covers both Copilot CLI lowercase
  (`edit`/`create`/`str_replace`/`write`/`multiedit`/`notebookedit`) and
  Claude Code PascalCase (`Edit`/`Write`/`MultiEdit`/`NotebookEdit`).
  Backwards-compatible: on Copilot CLI < 1.0.36 the matcher field is
  ignored and the hook fires on every call (same as before).

---

## 2026-06-02

### Added (additional hook hardening)
Follow-up coverage pass before release surfaced sixteen further fixes
across the hook scripts, static guard, and test matrix. Categorized as 1
BLOCKING, 6 HIGH, 9 MEDIUM:

- **BLOCKING — Per-test dedup isolation.** The 70-cell fault matrix shared
  a single PPID+lstart-keyed dedup directory across all parametrized cells.
  The first cell to touch `a.py` marked it `.injected`; every subsequent
  cell skipped the entire viewer subprocess (pre-tool.sh line 95 guard).
  Result: the viewer-branch `git rev-parse` + viewer invocation were
  exercised exactly ONCE per pytest run — the very code paths the matrix
  was added to defend. Reproduced empirically: replacing the bounded
  `_git(['rev-parse',...], 0.5)` with an unbounded call passed `70 passed`
  even though production would hang for 31s. Fix: `_run()` injects a
  unique `SHADOWFROG_TMP_DIR=<cwd>/_sf_dedup` per test.
- **HIGH — Behavioral SIGTERM coverage.** `subprocess.run(timeout=)` sends
  SIGKILL, not SIGTERM, so the matrix had zero behavioral coverage of the
  TERM trap. Removing `trap 'exit 0' TERM` was invisible to the entire
  test suite. Added `TestPreToolSigterm` using `Popen` + `os.kill(SIGTERM)`
  at varied delays, including a "SIGTERM during hung subprocess" case
  proving bounded `subprocess.run(timeout=)` ensures the queued signal is
  delivered within the 5s deny threshold.
- **HIGH — PascalCase tool name coverage.** Claude Code emits
  `Edit`/`Write`/`MultiEdit`/`NotebookEdit`; Copilot CLI emits lowercase.
  Removing `.lower()` from TOOL_NAME normalization silently downgraded all
  Claude Code mutations to the base reminder — undetected. Added
  `TestPreToolPascalCaseToolNames` with 11 spellings asserting the
  file-specific actionable branch fires for each.
- **HIGH — Static guard evasion patterns.** The `FORBIDDEN_SET_RE` anchor
  `^\s*set\s+` was bypassed by `[[ X ]] && set -e`, `eval 'set -e'`,
  `; set -e`, and `set \\\n -e` (line continuation). Substring scanning +
  pre-joining line continuations now catch all four. Signal aliases
  `SIGTERM` and numeric `15` are normalized to TERM-equivalent. Added 8
  evasion regression tests and 3 alias acceptance tests.
- **HIGH — Strict production wall-clock budget.** The matrix's 7s
  wall-clock cap tolerates CI cold-start variance, but happy-path runs
  must clear Copilot's strict 5s deny threshold. Added
  `test_happy_path_meets_strict_production_budget` (best-of-3 < 5s).
- **HIGH — PATH-empty / python-crash stderr leaks.** With `PATH=""`,
  bash itself emits `cat: No such file or directory` to stderr before any
  hook code runs. Defensive PATH append + `cat 2>/dev/null` + stderr
  redirects on final emitters close the leaks.
- **HIGH — Raw bash-level `python3` script invocations.** Static guard now
  flags `python3 path.py` / `python3 -m module` at bash level (same bug
  class as raw `git`). Three separate `python3 -c` calls in both hooks
  consolidated into single invocations to reduce blast radius.

Plus 9 MEDIUM cleanups: dedup tmp-dir TTL GC at sessionStart; file_path
precedence over path (was inverted); oversized path cap (1KB);
SIGPIPE in trap pyramid; final emitters silenced; state.json edge-case
coverage (symlink-to-/dev/null, directory-shaped, huge file, future
schema, NUL bytes, deep nesting); readonly-tmpdir test now uses chmod
0500 instead of `/proc` so macOS exercises it; matrix-setup regression
guard that asserts the viewer-branch is actually reachable.

Test count: **908 passed** (+46 from 862 baseline). New tests include 11
PascalCase tool variants, 2 SIGTERM behavioral, 6 state.json edge cases,
17 static guard evasion/alias/raw-python detection, file_path precedence,
strict 5s budget, regression-guard, and cross-platform readonly-tmpdir.

### Added
- **Multi-layer fail-open defense for advisory hooks** — the original
  `trap 'exit 0' EXIT` fix was necessary but insufficient. Three
  additional layers are now in place:
  1. **Trap pyramid**: separate `trap 'exit 0' EXIT` and `trap 'exit 0' TERM
     HUP INT` traps. EXIT alone returns 143/-15 under SIGTERM (empirically
     verified on bash 3.2 macOS and bash 5+ Linux), which the runner's
     timeout-kill triggers; the TERM trap converts that to exit 0.
  2. **Every external call bounded**: the previously-unbounded
     `git rev-parse --show-toplevel` in the pre-tool viewer-discovery branch
     was hanging for 30+ seconds on locked/NFS/fsmonitor-corrupted repos
     (reproduced as a 31s hang in Opus-4.8's review), causing runner
     SIGTERM-kill → tool denial. All git and viewer subprocesses now run
     inside a single consolidated Python block with per-call
     `subprocess.run(timeout=...)` wrappers. Total bounded work budget is
     ~3.5s, leaving >=1.5s headroom under the hook's 5s `timeoutSec`.
  3. **Static structural enforcement**: `hook-templates/check-hook-failopen.py`
     blocks PRs that re-introduce ANY of the regression vectors the panel
     identified — short-form `set -e`/`-u`, long-form `set -o
     errexit|nounset|pipefail|errtrace`, `source`/`.` of external files,
     missing EXIT or TERM trap, comment-masquerading-as-trap, or unbounded
     `git`/external calls at the bash level.
- **Fault-injection test matrix** (`tests/hooks/test_hook_fault_injection.py`)
  — parametrized cells that systematically perturb the hooks across four
  axes (stubbed binary failures, malformed/malicious JSON payloads,
  filesystem & git state, environment). Now also seeds `.shadow/<target>.md`
  in setup so the pre-tool viewer-discovery branch is actually exercised
  under fault injection — closing the matrix blind spot that let the
  unbounded `git rev-parse` bug ship in the first place. Every cell now
  asserts wall-clock < 4.5s (matching the production 5s `timeoutSec`).
- **Shellcheck / release automation** — runs shellcheck (info-level on hook
  scripts, warning-level on installer/skill scripts) and invokes the
  fail-open contract checker described above.
- **Unit tests for the fail-open checker**
  (`tests/hooks/test_check_hook_failopen.py`) — 17 adversarial cases
  asserting the checker correctly flags every regression vector and accepts
  every legitimate pattern (including `git` inside Python heredocs and the
  combined `trap '...' EXIT TERM HUP INT` form).

### Fixed
- **`preToolUse` hook denied tool calls under Copilot CLI ≥ 1.0.57.** As of
  v1.0.57, a `preToolUse` command hook that exits non-zero now **denies** the
  tool call (previously such errors were silently ignored). Both hook scripts
  ran under `set -euo pipefail`, so any failing sub-step — most commonly the
  staleness check's `git diff … | wc -l | tr` pipeline when the shadow was
  behind HEAD and `git diff` returned non-zero — exited the script non-zero and
  surfaced as *"Denied by preToolUse hook (hook errored)"*. The advisory hooks
  are now **fail-open** via the multi-layer defense described above. Affects
  `hook-templates/scripts/shadow-frog-pre-tool.sh` and
  `hook-templates/scripts/shadow-frog-check-init.sh`.
- **Unbounded `git rev-parse --show-toplevel` in viewer-discovery branch**
  (pre-tool.sh:94, pre-fix). On any system where this git call hangs (NFS,
  `.git/index.lock` held by `gitk`/`vscode`/`gh`, fsmonitor/Watchman, large
  monorepos), the hook exceeded the 5s `timeoutSec` and the runner SIGTERM-
  killed it → tool deny. Now consolidated into one Python subprocess with
  `timeout=0.5`, covering the same fallback resolution paths.
- **stderr leak on missing `state.json`.** `state.json` was read via a shell
  `< redirect`, which printed `No such file or directory` to stderr (before
  `2>/dev/null` applied) when the file was absent. The file is now opened
  inside Python so a missing file is a caught exception — no stderr noise.

### Changed
- Tightened all bounded subprocess timeouts to leave ≥1.5s of headroom under
  the 5s `timeoutSec` budget: viewer 1.5s → 1.0s; staleness rev-parse 1.0s →
  0.5s × 2; staleness diff 1.5s → 1.0s. Sum was exactly 5.0s with zero
  headroom; now 3.5s.
- Removed redundant `signal.alarm(2)` from the viewer subprocess wrapper —
  `subprocess.run(timeout=1.0)` already handles cancellation, and the layered
  alarm could leave orphaned grandchildren under load (per Opus-4.7-xhigh
  review).
- `_init_minimal_shadow` test helper now seeds `.shadow/<target>.md` by
  default so the viewer branch is reachable in tests. Pass `seed_target=None`
  to opt out.
- Replaced hardcoded `/tmp/PWNED` injection-test sentinels with `tmp_path`-
  scoped paths to eliminate flakiness from stale files across runs.
- Repurposed the `shadow-read-only` cwd-fault cell to make the dedup tmpdir
  read-only instead of `state.json` (the latter is read-only by design and
  the hook never writes to it, so the original cell was a no-op).
- Fixed `SC2295` quoting bugs (`${VAR#"$PREFIX"}`) in
  `hook-templates/scripts/shadow-frog-pre-tool.sh` and `install.sh` — the unquoted
  form treats `$PREFIX` as a glob pattern, which could mangle paths
  containing `*`, `?`, or `[` characters. Surfaced by shellcheck.

### Notes
- **`additionalContext` on `preToolUse` is undocumented but functional on
  Copilot CLI.** The 2026 hooks reference documents only `permissionDecision`/
  `permissionDecisionReason`/`modifiedArgs` for `preToolUse` output, but the
  copilot-cli v1.0.24 changelog explicitly notes that `preToolUse` hooks
  "respect modifiedArgs/updatedInput, and additionalContext fields." The
  current dual-shape output (top-level `additionalContext` for Copilot,
  nested `hookSpecificOutput.additionalContext` for Claude Code) works on
  both platforms. If Copilot ever removes this undocumented support, the
  shadow-aware reminder would silently no-op on Copilot — the `sessionStart`
  reminder would remain.

---

## 2026-05-30

### Removed
- **Personal (global) install mode** — `install.sh` no longer symlinks skills
  into `~/.copilot/skills/` or `~/.claude/skills/`. ShadowFrog now installs
  **only into a specific repository** via the now-required `--project <repo>`
  flag. A global install would auto-engage the shadow-edit hooks across every
  repository the developer touches, firing unexpected shadow writes (and a
  potential information-leakage risk) in projects that never opted in. Per-repo
  install also keeps skills committed to the fork, which is what fork-based
  dreaming needs. Updated `install.sh` (required `--project`, dropped the
  personal block + help text), `README.md` (single "Install into your repo"
  section), `claude.md`, the `pre-tool` hook's viewer-resolution loop, every
  SKILL.md path-resolution loop and usage example (project paths only), and the
  install test suite.

---

Pre-release hardening for the public MIT release: a five-model independent
audit (Opus 4.6 / 4.7 / 4.8, GPT-5.5) drove correctness fixes across the dream
pipeline, the reconciler, and the viewer, plus licensing/transparency docs and
a round of helper-script slimming.

### Added
- **LICENSE (MIT)** and **`RESPONSIBLE_AI.md`** — standard Microsoft MIT
  license plus a transparency note covering intended uses, out-of-scope uses,
  evaluation results, limitations, and best practices. README links to both.
- **gitignored-`.shadow/` guard for dream** — when `shadow-frog-init`'s
  "local only" option gitignores `.shadow/`, the dream workflow's
  `git add -A` silently skipped the shadow content, so nothing reached the
  remote and every discovery was lost without warning. `dream-setup.sh` now
  fails fast via `git check-ignore .shadow` with a clear fix message;
  `shadow-frog-init` step 9 documents the committed-vs-gitignored trade-off;
  the dream SKILL adds a "`.shadow/` must be git-tracked" prerequisite; and the
  README's Dream section carries a one-line callout.
- **dirty-tree guard on branch cleanup (B16)** — `dream-reconcile.py
  --cleanup-branches` merges discoveries into the working tree without
  committing, so the old ancestor check passed against a stale HEAD and could
  delete dream branches (the only durable copy) before the merge was
  persisted. Cleanup now also refuses when `git status --porcelain -- .shadow/`
  is non-empty, enforcing reconcile → commit → push → cleanup.

### Fixed
- **Pre-release audit bug sweep** — a broad set of correctness fixes surfaced
  by the 5-model audit: `dream-validate` normalizes bare-string/non-dict
  discoveries and tolerates BOM/CRLF in frontmatter; `dream-reconcile` emits
  canonical per-file shadow headers, unions refs into existing `_cross` slugs
  instead of dropping them, stops truncating the tip SHA, and mirrors
  manifest/patch even when a report is corrupt; `dream-coverage` filters to the
  shadowed source set; `shadow-viewer` preserves dotfile paths and scopes
  cross-ref parsing to the `**Refs**` block; `meditate-repair` recovers
  category from frontmatter; `shadow-init` prunes only `EXCLUDE_DIRS` + `.git`;
  `install.sh` / `dream-setup.sh` validate args and pass values via quoted
  heredocs to prevent shell injection. README drops nonexistent viewer flags;
  several SKILL docs corrected. Added 28 regression tests.
- **Discovery metadata loss on duplicate merge (B15)** — on an exact-text
  duplicate the reconciler returned early and discarded the new discovery's
  metadata, so a later dream recording the same claim with stronger evidence,
  higher-trust source, or extra labels lost the upgrade. Exact matches now
  merge metadata (union labels, upgrade source by trust, promote
  uncertain → verified, never touch `refuted`). Fuzzy near-duplicates still
  skip.
- **`_`-prefixed source dirs dropped from totals (B19)** — `update_state` and
  `rebuild_top_index` pruned every `_*` directory at every depth, which also
  excluded mirrored shadows under `_`-prefixed *source* dirs (e.g.
  `src/_internal/foo.py.md`). Internal dirs only live at the top level, so the
  `_*` prune now applies only at `.shadow/`'s root.
- **`_index.md` parent column convention (D9)** — meditate's `repair_parent`
  wrote a resolved `dream_id` while the reconciler and the branch-keyed lineage
  reader used branch names, orphaning nodes after a meditate pass. Standardized
  on branch names everywhere.
- **manifest-vs-shadow source-of-truth contradiction** — the dream SKILL
  contradicted itself and misdescribed reconcile (the reconciler merges
  discoveries from `manifest.json`, not by replaying the branch shadow).
  Reworded to match the code: the manifest is authoritative for propagation,
  per-file shadows are the human-readable copy and a required validate gate,
  and every discovery must be written into both.

### Removed
- **dream-lineage Graph tab** — the interactive radial force-graph tab in
  `dream-lineage.py`'s HTML output (~326 lines: graph data prep, JS
  `CAT_COLORS`, constellation CSS, the tab button/container, and `initGraph()`).
  The documented Chains / Fresh / Full Tree tabs are unchanged. (`dream-lineage.py`
  1076 → 750 lines.)
- **`_dreams/_coverage.json`** — the exploration coverage map written by
  `dream-reconcile.py`'s `rebuild_coverage`. Nothing read it: `dream-coverage.py`
  recomputes coverage live on every invocation. Removed the function and its
  reconcile call (reconcile steps renumbered 1–9). The shared discovery-counting
  helper it used is retained for `rebuild_top_index`.
- **Dead constants and legacy fossils** — unused `VALID_CATEGORIES` /
  `VALID_VERDICTS` in `dream-reconcile.py`; the obsolete `_None yet._`
  placeholder heal (only the canonical `_No cross-cutting discoveries yet._` is
  emitted); and the undocumented `**Parent**:` / `**Chain**:` report-body
  scrapers in `dream-lineage.py` (the `builds_on` frontmatter parse is the
  supported lineage source).

---

## 2026-05-19

Round-2 multi-reviewer audit (Opus 4.7 xhigh + high, Opus 4.6, GPT-5.5):
correctness, security, and documentation fixes across the dream pipeline,
the preToolUse hook, and the meditate index repair. A follow-on Tier 3
bug sweep landed seven additional correctness fixes in the same area.

### Fixed (Tier 3 bug sweep)
- **preToolUse hook dedup collision** — `DEDUP_DIR` was keyed on `$PPID`
  alone, so distinct Claude Code sessions sharing `PPID=1` under a
  process manager, transient shells reusing a PPID, or PID-wrap on
  long-running systems would share a dedup bucket and silently suppress
  each other's discovery injection. Now keys on `$PPID` plus
  `ps -p $PPID -o lstart=` (parent process start time), falling back to
  PPID-only if `ps` is unavailable inside sandboxed containers.
- **shadow-init last_commit empty-string sentinel** — `build_state_json`
  and the main init path defaulted `last_commit` to `""` when
  `git rev-parse` failed. The preToolUse hook reads
  `state.get('last_commit', 'none')`, but `.get()`'s default only fires
  on missing keys, not empty values — so `git rev-parse --verify ""`
  failed and every subsequent staleness comparison misfired with a
  false warning. Both call sites now default to `"none"`, which the
  hook already handles as the non-git sentinel.
- **shadow-viewer parse_discovery "Dream report:" leakage** — the
  continuation-line catch-all branch in `parse_discovery` swept
  `Dream report: \`_dreams/<slug>/\`` markers into the discovery body,
  so `--top` and `--summary` output rendered them concatenated to the
  behavioral statement. Added an explicit `Dream report:` branch that
  extracts the slug into `meta["dream_report"]` instead.
- **dream-reconcile back-pointer idempotency false positive** — the
  "is the back-pointer already present?" check used substring
  `f'_cross/{slug}.md' in line`, which false-matched any discovery
  body mentioning the same slug (e.g. `Also involves: \`_cross/foo.md\``).
  That silently swallowed legitimate back-pointer adds on subsequent
  dreams. Now matches the actual markdown link target
  `]({prefix}_cross/{slug}.md)` using the same depth-aware prefix as
  the write path.
- **dream-reconcile case-insensitive heading reads** — most cross-ref
  read sites already lowercased the comparison, but
  `find_cross_references_heading` used exact match. A meditate or user
  rewrite that lowercased `## cross-references` would slip past the
  finder, causing `_ensure_cross_references_section` to append a
  duplicate section and back-pointer dedup to miss entirely. All
  read sites are now consistently case-insensitive; writes still
  emit the canonical `## Cross-References`.
- **dream-reconcile top-level `_index.md` not refreshed** — the
  reconciler updated `state.json`, per-file shadows, and cross-cutting
  files, but `.shadow/_index.md` (the human-readable manifest with
  symbol lists and discovery counts) was never regenerated after
  reconciliation. It stayed frozen at init values until a manual
  meditate or re-init. Added `rebuild_top_index` as Step 8 (runs after
  `update_state` so totals reflect the new dream cycle), with
  extracted `_count_discoveries` and `_shadow_symbol_names` helpers
  shared between coverage and index rebuilds.
- **shadow-frog-dream SKILL anchor mismatch** — the per-validation
  error message pointed reviewers to "see Critical Invariants" for
  the artifact-format requirement, but the Critical Invariants section
  covers paths, branches, RUN_PREFIX, and reconciliation rules —
  artifact format is a sub-section. Updated the cross-reference to
  "see Critical Invariants → Artifact Format" so reviewers land at
  the correct sub-section.

### Fixed
- **dream-reconcile / dream-coverage count inflation** — `rebuild_coverage`
  and `update_state` counted `- ` bullets inside `## Cross-References`
  (which are back-pointer links, not discoveries) and counted
  `## Cross-References` / `## File-Level` as symbols. Every reconcile
  silently corrupted `state.json` totals and `_coverage.json`. Both
  loops (plus `dream-coverage.py`'s `check_coverage`) now use an
  `in_xref` state machine that matches `--check-invariants` semantics.
- **dream-reconcile back-pointer paths** — subdir shadows (e.g.
  `.shadow/src/foo.py.md`) wrote relative links as `_cross/slug.md`
  instead of `../_cross/slug.md`, breaking markdown rendering and
  `--check-invariants` on any non-flat repo. Now computes depth and
  prepends `../` per level.
- **dream-reconcile canonical layout** — bootstrap for never-before-seen
  shadow files used `## ` + filename as the heading (treating a filename
  as a symbol) and `_None yet._` for the cross-ref placeholder. Now
  uses `## File-Level` and `_No cross-cutting discoveries yet._` to
  match `shadow-init.py`. Placeholder-detection accepts both forms so
  meditate runs heal older shadows.
- **dream-validate first-dream mirror gate** — `git status --porcelain`
  rolled untracked subtrees up to a single `?? .shadow/` line, so
  first-dream cases where the entire `.shadow/src/` subtree is untracked
  false-failed the discovery-mirror gate. Added `--untracked-files=all`.
- **dream-validate mirror-gate error message** — claimed manifest entries
  were "LOST at merge time", but `dream-reconcile.py` reads
  `manifest.discoveries` directly. Gate kept (PR reviewers still need
  shadows in sync with the branch), but the message now accurately
  describes the workflow contract being enforced.
- **preToolUse shell→Python injection** — the discovery-inlining heredoc
  used an unquoted `<<PYEOF` delimiter, so the shell interpolated
  agent-controlled `$REL_PATH` into the Python source. Malformed paths
  could crash the hook; worst-case (`foo"); import os; os.system(...)`)
  execute arbitrary code in the hook process. Quoted the delimiter and
  switched to env-var passing.
- **preToolUse Claude Code field-name compatibility** — the hook only
  read Copilot CLI's `toolName`/`toolInput`. Claude Code uses snake_case
  (`tool_name`/`tool_input`), so per-file actionable discoveries were
  silently never injected on Claude Code. Added snake_case fallback.
- **meditate parent_branch index repair** — `shadow-frog-meditate/SKILL.md`
  steps 10-11 documented manifest-based and slug-heuristic parent
  repair, but `meditate-repair.py` never implemented either. Reconciled
  rows often had `parent: main` even when the dream actually compounded
  a prior experiment, breaking `dream-lineage.py` chains. Implemented
  both steps; ambiguous slug matches abort with `WARNING AMBIGUOUS`
  to stderr instead of guessing.
- **meditate no-op repaired counter** — `repaired` incremented on every
  loop iteration where `repair_row` didn't return None, even when no
  cells changed. Now tracks an explicit did-change flag.
- **state.json schema drift in shadow-frog-update/SKILL.md** — Phase 8
  template was missing `dream_cycles_completed`, so agents running
  `/shadow-frog-update` would silently zero it. Added the field with
  a `<preserved>` marker. Also: Phase 7 said "seven invariants" but
  listed five — clarified as "five core (full 7-invariant set in
  `/shadow-frog`)".
- **`dream-reconcile.py --all` references** — three places documented
  an `--all` flag that was never implemented. Rewrote
  parallel-batch instructions to use positional branch arguments.
- **`--check-invariants` surfacing** — added missing row to the
  shadow-frog-viewer/SKILL.md Available Views table and an example
  invocation. Previously only documented in shadow-frog/SKILL.md.
- **Duplicate `# 9.` numbering** in `dream-validate.py` — two checks
  were both labelled step 9 in source comments and docstring.
  Renumbered consistently (op validator → 9, mirror gate → 10,
  label triage → 11).

### Changed
- **Discovery format spec dedup** — claude.md and
  shadow-frog-update/SKILL.md trimmed their duplicated discovery-format
  sections (~31% reduction across both) and now point at
  shadow-frog/SKILL.md as the canonical source. shadow-frog-init was
  audited and left alone (no actual discovery-writing spec there).

### Fixed (code-quality audit follow-on)

Systematic 5-script code-quality audit (Opus 4.7 xhigh × 2, Opus 4.7
high, Opus 4.6 × 2) of `shadow-init.py`, `shadow-viewer.py`,
`dream-reconcile.py`, `dream-lineage.py`, and the seven smaller scripts
(`meditate-repair`, `dream-validate`, `dream-coverage`, `dream-setup.sh`,
both hooks, `install.sh`). Two real bugs surfaced; rest was inline
tidying. Risky refactors flagged for future review.

- **dream-reconcile prefix-substring data loss in cleanup_branches** —
  Safety check 2 used `if dream_id not in index_content:` raw substring
  against `.shadow/_dreams/_index.md`. When our dream_id is a prefix of
  any indexed ID (e.g. branch `…1400-foo` plus indexed
  `…1400-foo-extended`), the check falsely passed, allowing cleanup
  to DELETE the unreconciled branch — irreversible loss of whatever
  discoveries were only on it. Same false-pass shape in the
  descendant-detection check at line 1097. Both now use
  `_read_indexed_dream_ids` for parsed-ID set membership.
- **dream-reconcile prefix-substring false-pass in verify_reconciliation** —
  Same `if dream_id not in f.read():` substring against the index.
  Less severe (verify only reports failures; no data mutation), but
  still silently swallowed real index-mismatch bugs. Same fix.
- **dream-lineage md_to_html emitted invalid HTML** — `- item` lines
  became bare `<li>` tags with no `<ul>` wrapper, producing
  structurally invalid HTML in the dream-lineage panel-body. Switched
  to a `render_ul` callback that wraps consecutive list runs in a
  single `<ul>`; indented items keep the `class='nested'` hook so
  the existing `.panel-body li.nested` CSS still drives visual
  nesting (no rendering regression).

### Changed (code-quality audit follow-on)

- **shadow-viewer discovery-metadata regex consolidation** — the
  `_(status, source: type, labels: […])_` pattern was inline-duplicated
  in `parse_discovery`, `parse_cross_cutting`, and
  `view_check_invariants`. Now one module-level `_DISCOVERY_META_RE`;
  future drift between the three sites is now impossible.
- **Dead-code removal** (zero behavior change, all CLI snapshots
  byte-identical before/after): unused `defaultdict` import + dead
  `created_cross` variable in `dream-reconcile`; dead `CAT_ICONS`
  dict in `dream-lineage`; dead `HELP_TEXT = __doc__` alias in
  `dream-validate`; no-op `try/except SystemExit: raise` in
  `shadow-init`'s argparse path (SystemExit derives from
  BaseException, not Exception); dead `all_disc = []` initialization
  in `shadow-viewer`'s `view_summary`; unused `other_ts` tuple-unpack
  target in `meditate-repair`; dead `TIMESTAMP=$(date …)` in
  `shadow-frog-check-init.sh`.

### Added (test suite)

- **First test suite** — 408 pytest cases across 11 test files
  covering every script in the package (5 Python scripts + 4 shell
  hook template scripts + `install.sh` + `dream-setup.sh`). Full suite
  runs in ~22s; no per-test fixtures rely on network, user gitconfig,
  or `~/.copilot`. Counts by file:
  - `shadow-init.py` — 164 (10 language extractor families, brace
    nesting, decorator skip, B2 sentinel regression).
  - `shadow-viewer.py` — 55 (parse, summary, top, search, dream
    parser; B3 "Dream report:" leakage regression).
  - `dream-lineage.py` — 28 (load_index, generate_html, md_to_html
    inline + nested-list HTML regression).
  - `dream-reconcile.py` — 57 (B4 idempotency, B5 case-insensitive,
    B6 rebuild_top_index, two prefix-substring data-loss regressions
    in `verify_reconciliation` + `cleanup_branches`).
  - `dream-validate.py` — 19 (all 11 validation checks).
  - `dream-coverage.py` — 18 (coverage, fan-in, scope).
  - `meditate-repair.py` — 27 (dedup, state repair, I4 parent_branch
    regression).
  - `shadow-frog-pre-tool.sh` — 8 (B1 PPID dedup + K4 injection).
  - `shadow-frog-check-init.sh` — 5 (B2 sentinel).
  - `install.sh` — 6 (project install, --no-hooks, --no-context).
  - `dream-setup.sh` — 15 (worktree, branch naming, namespace,
    RUN_PREFIX, slug validation, --dry-run).
  - Plus 6 smoke tests in `tests/test_smoke.py` covering the
    shared importlib loader + git-config isolation fixtures.
- **Pytest scaffolding** — `pytest.ini` (testpaths + custom
  `slow`/`integration` markers) and `tests/conftest.py` (shared
  `_load_script` importlib helper for hyphenated script filenames,
  per-script module fixtures, mutable `coupon_demo` copy, isolated
  `tmp_git_repo` with `GIT_CONFIG_GLOBAL=/dev/null` so tests are
  independent of the developer's `~/.gitconfig`).

### Added (test suite — Phase 3 coverage push)

- **+281 tests across 4 files** lifting overall Python coverage from
  53% to **87%** (689 tests total, all passing in 56s). Each script's
  primitive layer was already covered by Phase-2; Phase-3 closed the
  render/output/orchestrator gap:
  - `shadow-viewer.py` 27% → **82%**: 91 cases for `view_summary`,
    `view_search`, `view_prefs`, `view_labels`, `view_recent`,
    `view_check_invariants` (synthetic-violation injection covering
    all 7 shadow invariants), and `main()` CLI dispatch.
  - `dream-lineage.py` 49% → **92%**: 46 cases for `generate_html`
    (happy path on coupon-demo + empty/missing/malformed edge cases +
    dream-chain ancestry rendering), `node_html`/`compact_node`/
    `tree_depth` helpers, and `--output` CLI.
  - `shadow-init.py` 61% → **81%**: 63 cases for `main()` CLI (in-
    process via argparse + subprocess for `__main__` smoke),
    `init_shadow` integration (mixed-language project, `.shadowignore`
    end-to-end, totals reconciliation), and the remaining extractor
    edge branches not exercised by the existing 164 cases.
  - `dream-reconcile.py` 50% → **93%**: 74 cases for the integration
    paths (`merge_discoveries`, `mirror_reports`, `update_index`,
    `update_state`, `rebuild_coverage`, `_resolve_tip_commit`,
    `cleanup_branches` real-delete path, full `main()` orchestrator
    end-to-end with multi-branch fixtures).
- **Mock-audit passed**: 0 `unittest.mock` / `Mock` / `MagicMock` /
  `@patch` / `mocker` / custom Fake/Stub classes across all 689
  tests. Real `subprocess` (63 invocations), real `git` (via
  isolated `tmp_git_repo`), real filesystem (via `tmp_path` and
  per-test `coupon_demo` copies). `monkeypatch.chdir` / `setenv` /
  `setattr(sys, "argv", ...)` are used only for genuine env/cwd/
  argparse plumbing — never to stub source code. This means a
  regression in any covered code path is guaranteed to fail tests
  on regression (e.g., the Phase-1 prefix-substring + nested-list-
  HTML bugs would have surfaced immediately under these tests).

### Fixed (Phase-3 coverage-audit follow-on)

The Phase-3 coverage push surfaced two real bugs that the new tests
now also cover as regressions:

- **dream-lineage `tree_depth` unbounded recursion on cyclic
  `_index.md`** — `tree_depth(node, children)` was single-line
  recursive with no cycle guard. A malformed `_dreams/_index.md`
  with a self-parent (`A → A`) or any cycle (`A → B → A`,
  `A → B → C → A`) crashed `generate_html` with `RecursionError`.
  Cycles aren't expected in well-formed input, but the parser does
  not reject them and a human editing the index manually can
  produce them. Now passes a `_seen` set through the recursion and
  treats a re-visited node as terminal — strictly safer than
  crashing on one malformed row.
- **dream-reconcile `update_index --dry-run` writing to disk** —
  `update_index` bootstrapped `.shadow/_dreams/_index.md` (with
  `os.makedirs` + initial header write) BEFORE the `if dry_run:
  return` check. The user-facing contract of `--dry-run` is to
  preview without touching disk, but the bootstrap silently
  materialized the `_dreams/` directory + an empty index file on
  every dry-run, even when the user just wanted to see what would
  happen. Fix: dry-run check moved above the bootstrap; dry-run
  now prints the preview lines and returns without any disk write.

---

## 2026-05-18

Branch-based dream persistence, systematic eval framework with results
dashboard, hook upgrade to inline shadow knowledge, plus the format/
validator hardening from a multi-agent review of the whole repo.


### Added
- **Branch-based dream persistence** — each experiment becomes a
  `dream/<namespace>/<id>` branch pushed to the fork. Dreams compound
  by branching from prior dream branches, inheriting code + shadow from
  the ancestor. The `<namespace>` segment isolates dreams across
  concurrent projects sharing a fork.
- **Dream pipeline scripts** — `dream-setup.sh`, `dream-validate.py`,
  `dream-reconcile.py`, `dream-coverage.py`. Self-documenting, with
  `--help` and clear error messages. Listed in SKILL.md `scripts:`
  frontmatter and resolved at runtime via the `SKILL_DIR` lookup.
- **`dream-coverage.py --scope`** — repeatable flag for focused
  exploration (e.g. `--scope src/ --scope tests/`). Coverage map only
  considers files under the given prefixes; planning prompt narrows
  accordingly.
- **`dream-lineage.py`** in viewer — visualizes dream ancestry chains
  and branch relationships across reconciled dreams.
- **`shadow-viewer.py --top FILE`** — derived view returning the top-N
  actionable discoveries for a single file, ranked by label/status/source.
  Powers the preToolUse hook (no file mutation, no invariant violations).
- **Hook upgrade**: `preToolUse` now inlines actionable shadow
  discoveries before mutation tools (`edit`, `create`, `str_replace`,
  `write`) instead of just reminding the agent to read the shadow.
  Includes per-session dedup, hard latency cap, output cap, and
  `_cross/` discoveries surfaced alongside per-file ones.
- **`meditate-repair.py`** — extracted from a 100-line inline Python
  block in the SKILL into a real script. Backs up `_dreams/_index.md`,
  detects corrupted reports (frontmatter `dream_id` != folder name),
  and rebuilds `category` / `verdict` / `title` cells from each dream's
  report + manifest. Idempotent.
- **`_prefs.md`** — project-wide preferences file (not tied to any
  file/symbol). Captured from user conventions, separate from per-file
  shadows.
- **`_dreams/_coverage.json`** — exploration coverage map rebuilt by
  the reconciler so future dream planners can see what's saturated.
- **7-column `_dreams/_index.md` schema** — `dream_id | category |
  verdict | title | branch | parent | tip_commit`. Replaces the old
  4-column form; init now emits the canonical header.
- **`dream_cycles_completed`** field in `_meta/state.json` for tracking
  dream activity over a project's lifetime.
- **Label triage at discovery write time** — dream prompts agents to
  apply `bug` / `security` / `performance` / `feature-gap` /
  `tech-debt` labels as discoveries are written, not retroactively.
  `dream-validate.py` emits non-blocking warnings when actionable text
  is missing a label.
- **Eval framework** (`eval/`) — systematic SWE-Smith evaluation
  (`eval/swesmith/`) with stacked-bug methodology, Docker-based
  anti-cheat, three-level scoring (L1 file / L2 function / L3
  LLM-as-judge), and per-repo dream lineage figures.
- **Results dashboard** (`eval/results_dashboard.html`) — interactive
  Chart.js dashboard structured around three findings, embedded
  navigation eval, sunburst lineage figures, and full scoring
  methodology.

### Changed
- **Dream SKILL.md** rewritten end-to-end around the branch-based
  workflow: pipeline phases call out script entry points, mandatory
  reconciliation timing (parallel batch mode vs sequential mode) is
  explicit, and the "Curating Dream Experiments for Upstream PRs"
  cheat sheet (maintainer test, devil's-advocate framing, "so what?"
  test, "no credit for effort" rule) is embedded at the end so users
  can apply curation heuristics in any agent session.
- **Backtick-in-heading** promoted from a passing mention to a hard
  rule in `shadow-frog/SKILL.md`. The viewer parser only matches
  `` ## `SYMBOL` ``; headings without backticks have their discoveries
  silently dropped from search and `--top` output.
- **`shadow-frog-update/SKILL.md`** description and trigger list
  rewritten to match actual behavior: hooks remind (sessionStart and
  preToolUse), they don't run update, and dream calls reconcile
  directly rather than going through `/shadow-frog-update`.
- **`shadow-frog-meditate/SKILL.md`** index-repair section now calls
  `meditate-repair.py` via the `SKILL_DIR` lookup instead of inlining
  100 lines of Python.
- **`examples/coupon-demo/.shadow/`** regenerated to match the current
  spec: ghost shadows for non-existent source files deleted, ghost
  dream folders deleted, surviving dreams given required `manifest.json`
  + `patch.diff` + `branch`/`parent_branch`/`remote` frontmatter,
  `_cross/` category enum corrected (`bug` → `edge-case`),
  `_index.md` and `state.json` recounted to match reality.
- **`examples/coupon-demo`** repurposed as a pure example of "what a
  real `.shadow/` looks like." Eval scaffolding (`task.json`,
  `instructions.md`, `EVAL_RESULTS.md`) removed; README rewritten
  around browsing the shadow with the viewer. Systematic eval moves to
  `eval/`.

### Removed
- **`eval.sh`** — coupon-demo-specific A/B eval runner, superseded by
  the systematic SWE-Smith framework under `eval/swesmith/`.
- **`examples/coupon-demo/task.json`**,
  **`examples/coupon-demo/instructions.md`**,
  **`examples/coupon-demo/EVAL_RESULTS.md`** — eval scaffolding for
  the now-deleted `eval.sh` runner.

### Fixed
- `_dreams/_index.md` header in `shadow-init.py` regenerated as the
  canonical 7-column schema (was emitting the obsolete 4-column form,
  which broke `dream-reconcile.py` and `dream-lineage.py` against
  freshly-initialized repos).
- `state.json` template in `shadow-frog-init` includes
  `dream_cycles_completed: 0` from initialization (was missing, so
  dream's first increment failed against a freshly-initialized repo).
- Reconciliation-timing language in dream SKILL.md was self-
  contradictory ("MUST reconcile before starting another dream" vs
  "After all agents complete, merge discoveries"). Rewritten as two
  explicit modes — parallel batch (one `dream-reconcile.py` call with
  all branch names after the batch) vs sequential (reconcile after each
  dream) — with the invariant that batches are never queued un-reconciled.

---

## 2026-04-17

Major dream overhaul: experiment-only mode, 5-model review fixes, dreams
archive, hook refinements, dead hook removal.

### Added
- **Dream is now experiment-only** — removed observe mode entirely. Every
  task must produce code, run it, and capture a patch. Reading code is
  preparation, not a deliverable.
- **`_dreams/` experiment archive** — persistent storage for dream reports
  (`report.md`) and patches (`patch.diff`), enabling compounding across
  dream sessions.
- **`_dreams/_index.md`** — tabular index of all experiments with dream_id,
  category, verdict, status, and title. Bootstrapped with header on first
  dream.
- **Phase 7: Experiment Review** — interactive walk-through of each
  experiment with Keep/Delete/Follow-up actions.
- **Experiment completion criteria** — tasks require non-empty `patch.diff`,
  at least one executed command with recorded output, and a saved `report.md`.
- **Experiment-Only Files rule** — shadows are never created for files that
  only exist in the worktree. Discoveries are anchored to the existing code
  the experiment relates to.
- **Continuing a Prior Dream** subsection — apply prior `patch.diff`,
  handle conflicts, set `builds_on` lineage.
- **Delegate mode artifact contract** — PR must contain `report.md`,
  `patch.diff`, updated `_dreams/_index.md`, and per-file shadows.
- **Applying Experiment Patches** section — `git apply` instructions with
  conflict guidance.
- **Standalone harness guidance** — for repos with no test infrastructure,
  create scripts that exercise the code directly.
- **Parallel safety** — task counter in slugs; only orchestrator writes
  `_dreams/_index.md`.
- **Hallucination guard** — mandatory `echo` + verify step for `DREAM_ID`
  timestamps; agents must never fabricate dates.
- **Dream hygiene in meditate** — index consistency checks, report
  completeness validation, stale patch warnings.

### Changed
- **Diff capture** — commit-then-diff (`git diff $BASE HEAD`) replaces the
  unreliable staging-based approach.
- **Verdict vs status taxonomy** — simplified to just `verdict` (agent:
  useful/dead_end). No status column — everything in the index is kept;
  deleted experiments are removed entirely.
- **`REPO_ROOT`** — uses `git rev-parse --show-toplevel` instead of `$(pwd)`
  so it works across tool calls.
- **Worktree reuse** — adds `git clean -fdx && git checkout .` before reuse.
- **Investigation category** — requires assertion-based tests with falsifiable
  claims, not just print statements.
- **Phase 7 in delegate mode** — skipped entirely; the PR is the review surface.
- **Security experiments** — constrained to local/test only, never external systems.
- **preToolUse message** — now includes mirroring convention example
  (`src/auth.py -> .shadow/src/auth.py.md`) and `/shadow-frog` skill pointer.
- **preToolUse reminder** — prompts agent to capture user-shared knowledge
  as `source: user` discoveries and preferences to `_prefs.md`.

### Removed
- **Observe mode** in dream — every task is now an experiment.
- **sessionEnd hook** — output was silently ignored by Copilot CLI runtime.
  Removed script, `hooks.json` entry, and all doc references. Two active
  hooks remain: `sessionStart` + `preToolUse`.
- `approach: experiment` metadata field (vestigial — only one legal value).
- `/tmp` fallback for failed worktrees (broke git context; now marks task
  blocked and replans).

### Fixed
- `_dreams/` directory not created before temp patch write (first-dream bug).
- Orphaned markdown in init and viewer SKILL.md.
- 5-model parallel review (GPT-5.3-Codex, GPT-5.4, Opus 4.6, Opus 4.7,
  Goldeneye) identified 15 issues — all addressed:
  - `shadow-init.py`: store full SHA (was `--short`), fixing false staleness.
  - Dream: save `REPO_ROOT` before `cd` worktree; capture `BASE_COMMIT`
    before changes.
  - Hook counts read `state.json` instead of broken regex on markdown.
  - `total_discoveries` defined as per-file only (excludes `_cross/`, `_dreams/`).
  - Script paths use explicit installed paths, not broken `$0` trick.
  - Cross-cutting threshold consistently 3+ files across all skills.
  - Viewer shell fallback excludes `_dreams/` from find commands.

---

## 2026-04-15

Delegate mode, project install, pure hooks.

### Added
- **Delegate mode for dream** — run dream via `/delegate` on cloud agent
  infrastructure. Creates branch, runs experiments, opens draft PR.
- **Project install** (`install.sh --project <path>`) — copies skills and
  hooks into `.github/skills/` and `.github/hooks/` for cloud agent access.
- **`agent-context.md`** — minimal always-on context injected into
  `copilot-instructions.md` for project-level agent awareness.

### Changed
- **Hooks are now pure** — removed all file-writing side effects
  (`_meta/stale`, `_meta/hooks.log`). Hooks read state, output
  `additionalContext`, done. No more dirty git status from hooks.
- **install.sh** — detects and removes symlinks from prior personal install
  before copying (prevents silent writes into symlink target).

### Fixed
- Removed `.shadow-frog-needs-init` flag file mechanism (replaced by
  `sessionStart` hook check).

---

## 2026-04-14

Hook system overhaul: `additionalContext` injection, `preToolUse` as
primary hook.

### Changed
- **Hooks use `additionalContext`** (Copilot CLI v1.0.11+) — context is
  injected directly into the agent conversation instead of being printed
  to ignored stdout.
- **`preToolUse` replaces `postToolUse`** as the primary hook — fires
  before every tool call, giving the agent shadow context at the moment
  it needs it most.
- Renamed `shadow-frog-post-tool.sh` → `shadow-frog-pre-tool.sh`.

### Fixed
- `grep -c '|' || echo '0'` double-output bug (was printing `0\n0`).
- `_index.md` count handles both table (`|`) and list (`- .md`) formats.
- Safe JSON serialization via env vars instead of shell interpolation.

---

## 2026-03-23

Agent context, broader trigger, install improvements.

### Added
- **`agent-context.md`** — single source of truth for project instructions,
  read by `install.sh` instead of hardcoded snippet.
- **Broader shadow-frog trigger** — skill loads for more query patterns.
- **Custom instructions guidance** in README for manual setup.

### Fixed
- Circular symlinks in hook template scripts (skenv artifact).

---

## 2026-03-22

Dream enforcement, init script.

### Changed
- **2-per-category minimum enforced** in dream (was 3, consistently ignored).
  MANDATORY language repeated 3 times. 12 tasks minimum (6 categories × 2).
- **User-focus override** — `dream focus on security` allocates all tasks
  to one category.

### Added
- **`shadow-init.py`** (1375 lines) — language-agnostic symbol extraction
  for 15 languages, git-based file discovery, `.shadowignore` filtering,
  full `.shadow/` scaffolding.
- **Meditate structured output** — JSON-per-line for auto-apply of
  merge/conflict resolutions.
- **Script discoverability** — `scripts` field in SKILL.md frontmatter,
  explicit installation paths.
- **Dream: direct-write** instead of JSON handoff for parallel agents.
- **Dream: reconciliation phase** for post-agent metadata consistency.

---

## 2026-03-20

Labels, AFK-safe patterns, viewer hardening.

### Added
- **Discovery labels** — optional `labels: [bug, performance, security,
  feature-gap, tech-debt]` for actionable discoveries.
- **AFK-safe patterns** for dream — worktrees outside repo, temp scripts in
  `/tmp`, deferred cleanup, no working-branch modifications.
- **Category-first dream planning** — plan organized by category, not by file.

### Changed
- Viewer hardened with better error handling and edge cases.
- Orchestrator feedback addressed: write rules, dedup, output schema,
  incremental meditate.

---

## 2026-03-18

Meditate skill, dream categories, README refresh.

### Added
- **`shadow-frog-meditate`** — shadow hygiene skill that scans for
  duplicates, near-duplicates, and conflicts. Merges automatically where
  possible, asks user for ambiguous cases.
- **6 investigation categories** for dream — investigation, bug hunting,
  feature design, refactoring, optimization, security audit.
- **Format compliance** section in meditate for strict discovery format.
- **Harmonized discovery format** across all 6 skills.

### Changed
- README: added meditate and viewer to Usage section, shadow repo diagram,
  froggy team logo.

---

## 2026-03-16

Viewer skill, audit fixes.

### Added
- **`shadow-frog-viewer`** — browse and query the shadow knowledge base
  with 4 commands: overview, search, view preferences, recent discoveries.
  Includes Python helper script with shell fallbacks.

### Fixed
- 14 audit issues across the skill suite.

---

## 2026-03-11

Shadow filtering and preferences.

### Added
- **`.shadowignore`** — gitignore-syntax file for filtering which files
  get shadow coverage (skip vendor, generated, etc.).
- **`_prefs.md`** — project-wide user preferences not tied to any specific
  file or symbol. Captured from conversations with `source: user`.

---

## 2026-03-10

Hooks, dream experiments, README rewrite, EMU migration.

### Added
- **Dream experiment mode** — alongside observe mode, dream can now set up
  git worktrees, implement changes, run tests, and produce patches.
- **Hook system** — `sessionStart` and `postToolUse` shell hooks with
  shadow status and staleness detection.

### Changed
- **README rewritten** — skenv and direct install presented as equal
  options, stale repo URL fixed.
- Hooks use side effects instead of ignored stdout.

### Fixed
- Hook config: `timeoutSec` field name, stale discovery grep pattern.
- Added `.github/hooks/` to `.gitignore` (skenv-generated).
- Discovery format: removed surprise scores, stale line range mention.
- Heading format: fixed rendering, use code blocks for examples.

---

## 2026-03-09

ShadowFrog pivots from Python codebase to distributable skill suite.

### Changed
- **Repurposed ShadowFrog** from a Python shadow-generation tool to a
  distributable suite of 6 AI coding agent skills.
- All SKILL.md files rewritten for LLM agent comprehension.

### Added
- **6-skill suite**: `shadow-frog` (main), `shadow-frog-init`,
  `shadow-frog-update`, `shadow-frog-dream`, `shadow-frog-meditate`
  (placeholder), `shadow-frog-viewer` (placeholder).
- **Symbol-level granularity** — shadows mirror at the symbol level, not
  just file level. Every class, function, method gets a `##` section.
- **Bidirectional reference system** — `file::symbol` notation with 7
  invariants ensuring code↔shadow integrity.
- **User-agent conversational knowledge capture** — `source: user` and
  `source: interaction` trust levels alongside `source: exploration`.
- **Auto-placement logic** for user-shared knowledge.
- **Verification and dedup procedures** for discoveries.
- **Cross-cutting discoveries** with descriptive slug filenames in `_cross/`.
- **`claude.md`** with comprehensive project guidelines.

### Removed
- Confidence scores from discovery format.
- Per-file discovery IDs (anchored by `file::symbol` heading instead).
- Line numbers from shadow headings and refs.
- Numeric IDs from cross-cutting filenames.

### Fixed
- 20 audit findings across the skill suite.
- 11 issues from systematic audit.

---

## 2026-02-18

Initial implementation (Python codebase phase).

### Added
- Initial ShadowFrog implementation as a Python shadow-generation tool.
- Common utilities ported from gray_treefrog (config, caching, logging).
- Thread-safe caching for QueryEngine, LLMDescriber, PromptRenderer.
- LLMDescriber tests.
- Docker image enforcement for CLI commands, pre-commit hooks.
- README with architecture diagrams.

### Fixed
- Config wiring: frozen model override, dead config fields, missing CLI args.
- Upward dependency, split pipeline, mirrored test directories.
