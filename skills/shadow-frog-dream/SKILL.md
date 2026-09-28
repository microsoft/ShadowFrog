---
name: shadow-frog-dream
description: >-
  Run autonomous experimentation while the user is AFK. Uses 6 investigation
  categories (investigation, bug hunting, feature design, refactoring,
  optimization, security audit) to systematically discover non-obvious
  behaviors. Every task is an experiment — implement in worktrees, commit
  to persistent dream branches, and push to the configured remote. Dreams compound
  across sessions: future experiments branch from prior dream branches,
  building a tree of progressively deeper work. Invoke when the user is
  AFK or asks for a dream run. Use mode=coherent to ground each child in
  its parent while encouraging diverse siblings, challenges, and alternatives.
scripts:
  - dream-tools.py
  - dream-coverage.py
  - dream-validate.py
  - dream-reconcile.py
  - dream-setup.sh
  - dream-cleanup.sh
  - dream-gc.sh
---

# ShadowFrog Dream

Autonomous experimentation while the user is away. Every task is an
**experiment** — implement real code in a worktree, run it, persist as a
**named git branch** pushed to a remote the user can write to. Dream's unique value is
implementation experience that **compounds across sessions**.

## Modes: Broad or Coherent

Accept `mode=broad` (default) or `mode=coherent`, for example
`/shadow-frog-dream mode=coherent`. Resolve the requested mode before planning.
Carry it into every task/subagent prompt, manifest, report, and validation
invocation. Use `/shadow-frog-nap` instead when the desired deliverable is a
lightweight feature-task brief rather than an executed experiment.

**Broad mode** keeps the category, coverage, and breadth rules below.

**Coherent mode regularizes parent-child edges, not siblings or a whole tree.**
Each child has its own `goal` and identifies a parent capability, finding,
limitation, or design decision motivating its work. It can extend, integrate,
challenge, replace, simplify, or offer an alternative. The shared
`parent_connection` format is defined in `/shadow-frog`.

- Ten children of one parent may pursue ten different worthwhile directions.
  Sibling summaries help avoid duplicates; they do not impose a common goal,
  file-disjointness requirement, or fixed diversity quota.
- Technical independence is not an automatic rejection. A justified alternative
  can be coherent even without calling the parent's modules. Shared keywords,
  a parent ID, or merely touching the same file are not enough.
- A challenge needs a reason or evidence; do not manufacture flaws to lengthen
  a chain. Preserve unchanged user requirements and name superseded decisions.
- There is no fixed tree-wide feature objective. Direction can evolve through
  substantive local transitions. Stop a path when no useful continuation exists.
- Descendants wait for their actual parent implementation. Siblings may run in
  parallel in separate worktrees, including on the same files. Refresh the
  selected parent branch/tip after each accepted step rather than relying on
  the session-start branch snapshot for newly created parents.

**Precedence:** in coherent mode, skip category minimums, uncovered-file quotas,
saturation-driven parent rejection, unique-file/directory checks, mid-session
breadth replanning, disjoint-file assignments, and the "breadth over depth"
preference below. Follow the user's total work budget; absent one, use 12
experiments as a ceiling, not a quota. All execution, artifact, reconciliation,
and worktree safety requirements still apply.

Before launching each child, record its parent branch and resolved commit,
own goal, and intended connection. Give it the parent's report, manifest,
relevant source/evidence, and short sibling summaries. Do not claim sibling
features are inherited code. Seeds from the base branch have no parent
connection; importing a prior dream uses that dream's actual branch.

**Completion review:** explain what the parent made possible or revealed to be
inadequate, the child's concrete delta, and why the result is useful. For a
replacement, compare the old/new behavior against the relevant constraints.
Structural validation cannot judge semantic coherence.

Coherent branches and their canonical index ancestors are retained by the
pinned reconciler for continued exploration and reproducible task baselines.
Every mode uses that same guarded pruning path. Explicit later curation may
remove branches only after checking references and preserving required commits.
Worktrees can still be cleaned up normally after successful pushes.

## Critical Invariants

These rules are stated ONCE here and enforced by helper scripts. Violating
any of them is a completion criteria failure.

### Prerequisite: `.shadow/` must be git-tracked

Dream moves `.shadow/` content **through git** — artifacts are committed onto
the dream branch, pushed to the remote, then read back by the reconciler via
`git show origin/<branch> .shadow/...`. If `.shadow/` is gitignored (the
"local only" option in `shadow-frog-init`), `git add -A` silently skips those
files, nothing reaches the remote, and the reconciler finds no manifest —
**every discovery is lost without warning**. `dream-setup.sh` runs
`git check-ignore .shadow` up front and refuses to start if it's ignored.
Use `shadow-frog-update` instead for local-only shadows.

### Path Isolation

```
WORKTREE_BASE = /tmp/shadowfrog-dreams/<DREAM_NS>/
WORKTREE_DIR  = $WORKTREE_BASE/dream-<SLUG>
```

- Worktrees are ALWAYS in `/tmp/shadowfrog-dreams/<DREAM_NS>/`, NEVER in
  the project directory. This prevents conflicts between parallel agents
  and keeps the main repo clean.
- `DREAM_NS` (namespace) isolates branches per task/instance. Resolved
  from: `DREAM_NAMESPACE` env → `TASK_INFO.json` → `.env` → repo basename.
- Override only with `DREAM_WORKTREE_BASE` env var if `/tmp` is too small.
- `dream-setup.sh` computes and enforces all paths. Use it.

### Branch Naming

```
BRANCH_NAME = dream/<DREAM_NS>/<DREAM_ID>
DREAM_ID    = YYYYMMDD-HHMMSSZ-<SLUG>
```

### Artifact Format

```
.shadow/_dreams/<DREAM_ID>/report.md
.shadow/_dreams/<DREAM_ID>/manifest.json
.shadow/_dreams/<DREAM_ID>/patch.diff
```

NEVER flat files (`_dreams/<DREAM_ID>.md`). Flat files break the pipeline.

### RUN_PREFIX

Repository code and tests MUST use the `RUN_PREFIX` resolved during preflight.
When `RUN_PREFIX="uv run"`, use it for the repository's Python/pytest commands.
ShadowFrog's standard-library helpers instead use the interpreter and absolute
command arguments captured by `dream-tools.py`; their tooling version must not
come from the historical experiment checkout.

### Reconciliation is Mandatory

Every dream branch must reconcile to main before the session ends. The
most common failure mode is agents pushing dream branches but never
reconciling — losing all discoveries.

**Two modes:**
- **Parallel mode (default):** Launch a batch of 3-4 sub-agents → wait
  for all to push → run the pinned `commands.reconcile` ONCE at the end
  of the batch. The reconciler auto-discovers every un-reconciled dream
  branch in the namespace — you do NOT pass branch names. One call merges
  every pushed branch.
- **Sequential mode (fallback when sub-agents unavailable):**
  Complete dream → push → reconcile → verify → next dream. Adds ~30s
  per dream but guarantees zero data loss if the session crashes
  mid-batch.

Never queue multiple un-reconciled batches; reconcile at the end of
each batch or each individual dream.

### Script Failure Recovery

The pinned helpers are self-documenting. Diagnose errors by reading their
current sources, fix the reported prerequisite or artifact, and retry the same
checked command. Never substitute worktree-local helpers, downgrade the mode,
skip validation, or imitate branch deletion manually.

If the tooling snapshot is missing or changed, stop. Restore or pin a fresh
complete bundle from the current installation in the controller checkout, not
from a historical dream. Do not edit a snapshot to make its hash checks pass.

```
Pushable remote
  main --- .shadow/ (accumulated ALL discoveries)
  |
  +-- dream/<ns>/<id-1>  (cycle 1, agent A, from main)
  +-- dream/<ns>/<id-2>  (cycle 1, agent B, from main)
  +-- dream/<ns>/<id-3>  (cycle 2, from dream/<ns>/<id-1>, compounding)
```

- Branches are live — `git checkout dream/<id>` runs the code
- Shadow follows lineage — ancestor chain, not sibling branches
- Main is the accumulator — reconciliation merges ALL discoveries

### Prerequisites

1. Repo cloned locally with a pushable remote where `dream/...` branches are allowed
2. ShadowFrog skills installed (`install.sh --project /path/to/repo`)
3. `.shadow/` initialized (`/shadow-frog-init`) and tracked by git

Use the user's existing repo directly when it has a remote that accepts
`dream/...` branches. If no writable remote is configured, do not start a
dream run; the user must configure one first.

## Helper Scripts

This skill bundles 7 helper scripts. **Pin current tooling before creating or
entering an experiment worktree, or switching the controller's code branch.**
Historical branches can contain older helpers that silently ignore new flags.

Locate `dream-tools.py` beside this currently loaded skill in the controller's
`.github/skills/shadow-frog-dream/` or `.claude/skills/shadow-frog-dream/`.
Choose a new directory in the host's run/session workspace, **outside both the
target repository and the skill-source repository**:

```text
python CURRENT_SKILL/dream-tools.py pin --repo-root REPO_ROOT --output EXTERNAL_RUN_TOOLS --mode coherent
```

Use `--mode broad` for a broad run. The command copies the current Dream and
core helpers/instructions, atomically writes `tooling.json` after copying, and
prints a JSON control packet. Save the packet in the external run workspace
and pass it unchanged to every child. This is host-local tooling, not a shadow
discovery or task artifact; never commit it into the target repository.

The packet contains:
- `commands.validate`, `commands.reconcile`, `commands.coverage`: absolute
  argument arrays, including the captured interpreter, snapshot runner, and
  manifest digest. Reconcile/coverage already include `REPO_ROOT`.
- `helper_paths`: absolute paths to the bundled lifecycle scripts/modules.
- `instructions`: current core/Dream skill copies children must use for the
  workflow, rather than the old copies in their code worktrees.
- `mode`, `repo_root`, and the absolute pinned `skill_dir` (`SKILL_DIR` below).

Execute command arrays as arguments, not shell code. Append only the operation's
arguments: `[DREAM_ID, WORKTREE_DIR]` for validation, `["--namespace", DREAM_NS]`
for reconciliation, and optionally `["--cleanup-branches"]` after committing and
pushing reconciliation. The dispatcher supplies the pinned validation mode and
rejects mode overrides. It checks the manifest digest and all captured file
hashes before executing the helper, preserving its output and exit status.

For example, the equivalent native command shape for validation is:

```text
python PINNED_SKILL/dream-tools.py run --digest PIN_DIGEST validate DREAM_ID WORKTREE_DIR
```

`PINNED_SKILL` and `PIN_DIGEST` come from the packet; do not fabricate them.
Keep the snapshot while children or resumed work reference it. The run workspace
owns its eventual cleanup; this helper never recursively deletes directories.
Re-pin on a different host rather than reusing host-specific interpreter paths.

| Script | Purpose | When to use |
|--------|---------|-------------|
| `dream-tools.py` | Pins current tooling and dispatches checked Python helpers | **Before worktrees** and for every validation/reconciliation/coverage call |
| `dream-setup.sh` | Creates worktree + branch with namespace isolation | **Phase 3** — start of every experiment |
| `dream-validate.py` | Validates artifacts before push (hard gate) | **Phase 5** — before `git push` |
| `dream-reconcile.py` | Merges dream branches into main's `.shadow/` | **Phase 6** — after all experiments done |
| `dream-coverage.py` | Computes exploration coverage map | **Phase 2** — task planning for diversity |
| `dream-cleanup.sh` | Safely removes ONE dream worktree (with safety gate) | **After push** — replaces the old inline cleanup snippet |
| `dream-gc.sh` | Sweeps orphan dream worktrees from `$DREAM_WORKTREE_BASE` | **Auto** — triggered by `dream-setup.sh` (per-namespace throttle, default 1× / hour) in orphan-only mode; also `--task-complete --namespace "$DREAM_NS" --min-age-min 0` for end-of-session sweep of registered-but-stale dirs |

The shared `shadow-frog/_coherence.py` is captured beside the Dream tools.
Never rediscover these Python helpers relative to `WORKTREE_DIR`. All scripts
support `--help`. Setup/cleanup/GC still have their current lifecycle interfaces;
the tool snapshot does not port remaining shell scripts or bypass their safety
checks. Use a native Python port when it is present in the installed bundle.

## Phase 1: Preflight and Assess

### Preflight Validation

```bash
REPO_ROOT=$(git rev-parse --show-toplevel)
cd "$REPO_ROOT"

# 0. Auto-detect DREAM_NAMESPACE
if [ -z "${DREAM_NAMESPACE:-}" ]; then
    if [ -f TASK_INFO.json ]; then
        DREAM_NAMESPACE=$(python3 -c "import json; print(json.load(open('TASK_INFO.json')).get('dream_namespace',''))" 2>/dev/null)
        export DREAM_NAMESPACE
    elif [ -f .env ]; then
        DREAM_NAMESPACE=$(grep '^DREAM_NAMESPACE=' .env | head -1 | cut -d'=' -f2-)
        export DREAM_NAMESPACE
    fi
fi
[ -n "${DREAM_NAMESPACE:-}" ] && echo "Dream namespace: $DREAM_NAMESPACE"

# 1. Detect default branch
DEFAULT_BRANCH=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||')
if [ -z "$DEFAULT_BRANCH" ]; then
    if git show-ref --verify refs/remotes/origin/main >/dev/null 2>&1; then
        DEFAULT_BRANCH="main"
    elif git show-ref --verify refs/remotes/origin/master >/dev/null 2>&1; then
        DEFAULT_BRANCH="master"
    else
        echo "ERROR: Cannot detect default branch. Fix: git remote set-head origin <branch>"
    fi
fi
echo "Default branch: $DEFAULT_BRANCH"

# 2-5. Validate environment
echo "Current branch: $(git branch --show-current)"
echo "Remote: $(git remote get-url origin)"
git ls-remote origin HEAD >/dev/null 2>&1 || echo "ERROR: Cannot reach remote."
[ -d .shadow ] || echo "ERROR: .shadow/ not found. Run /shadow-frog-init first."
mkdir -p .shadow/_dreams
git diff --quiet && git diff --cached --quiet || echo "ERROR: Uncommitted changes."

# 6. Fetch all remote branches (ONE fetch for all agents)
git fetch origin --prune

# 7. List dream branches (namespace-filtered)
DREAM_NS="${DREAM_NAMESPACE:-}"
BRANCH_PATTERN="${DREAM_NS:+origin/dream/${DREAM_NS}/}"
BRANCH_PATTERN="${BRANCH_PATTERN:-origin/dream/}"
echo "Available dream branches:"
git branch -r | grep "$BRANCH_PATTERN" | sed 's|origin/||' || echo "  (none)"

# 8. Detect RUN_PREFIX from lockfiles
if [ -f uv.lock ]; then
    uv sync --all-groups 2>&1 | tail -3
    RUN_PREFIX="uv run"
elif [ -f package-lock.json ]; then
    npm install --quiet 2>&1 | tail -3
    RUN_PREFIX="npx"
elif [ -f yarn.lock ]; then
    yarn install --silent 2>&1 | tail -3
    RUN_PREFIX="npx"
else
    RUN_PREFIX=""
fi
echo "RUN_PREFIX='$RUN_PREFIX'"

# 9. List compoundable experiments
echo ""
echo "=== COMPOUNDABLE EXPERIMENTS ==="
if [ -f .shadow/_dreams/_index.md ]; then
    awk -F'|' 'NR>2 && /useful/ {
        gsub(/ /,"",$2); gsub(/ /,"",$4); gsub(/ /,"",$6);
        gsub(/^ +| +$/,"",$5);
        if ($2 != "" && $6 != "") print $6 " | " $3 " | " $5
    }' .shadow/_dreams/_index.md
    COMPOUNDABLE=$(awk -F'|' 'NR>2 && /useful/ {gsub(/ /,"",$2); if ($2 != "") c++} END {print c+0}' .shadow/_dreams/_index.md)
    echo "Total compoundable: $COMPOUNDABLE"
else
    echo "(none — first dream session)"
fi
```

**If any check prints ERROR, STOP.** Do not use `exit 1` — check output
and stop at the agent level.

(`RUN_PREFIX` MUST be threaded into every subagent prompt for repository
code/tests. Helper commands use the pinned packet's interpreter instead.)

### Snapshot Branch State

After the single `git fetch`, capture dream branches and pass to all
sub-agents — they do NOT fetch independently.

```bash
DREAM_NS="${DREAM_NAMESPACE:-}"
BRANCH_FILTER="${DREAM_NS:+origin/dream/${DREAM_NS}/}"
BRANCH_FILTER="${BRANCH_FILTER:-origin/dream/}"
git branch -r --format='%(refname:short) %(objectname:short)' \
  | grep -F "$BRANCH_FILTER" \
  | sed 's|origin/||' > .shadow/_dreams/.branch-map.txt
cat .shadow/_dreams/.branch-map.txt

# Initialize session tracking (orchestrator-only; agents do NOT write here)
: > .shadow/_dreams/.session-branches.txt
```

### Assess Codebase

Read `_meta/state.json`, `_index.md`, existing discoveries, and **past
dream reports** in `_dreams/`.

### Build Exploration Coverage Map

Run the packet's `commands.coverage`. It already contains the pinned
interpreter/tool version and the original repository path.

**Coverage definition:** A file is "covered" only when its shadow has
≥1 behavioral discovery (line starting with `- `). Placeholder-only = NOT covered.

**Scoped exploration (`--scope`)** — pass `--scope <path-prefix>` (repeatable)
to restrict the coverage map to a specific subtree. Use this when the
broader repo is well-explored but a particular area (e.g., a known
frontier of bugs, a newly-added module, a subsystem the user just
flagged) deserves a focused dream session. All counts (totals, %,
saturated, fan-in, per-dir) are computed over the scoped subset only.

For one subtree, append `["--scope", "src/auth/"]` to `commands.coverage`.
For several, append repeated `--scope` argument pairs.

In broad mode, when using `--scope`, the per-category task quotas (Phase 2) still apply
but are interpreted against the scoped subset. Don't use scoped
exploration as the default — pick it only when there's a concrete reason
to concentrate effort.

### Review Past Dreams (Required)

In coherent mode, use the index to select parents, then read the selected
parent reports/manifests and relevant ancestor evidence. Do not reread every
report or reject a useful parent merely because its files are well covered.

When compoundable experiments exist (preflight step 9):
- Broad mode: read the candidate reports. Coherent mode: read the selected
  parent's report/manifest and the relevant ancestor evidence.
- Choose which to continue (extending, fixing, integrating)
- Note `dead_end` experiments to avoid repeating
- Trace lineage via the `parent` column in `_dreams/_index.md`

**Compounding quality gate** — before choosing to compound from a parent:

1. Read the parent's `report.md` AND `manifest.json`
2. Verify the parent has a non-empty `patch.diff` (prose-only parents
   are low-value — prefer parents with working code)
3. Broad mode: identify a specific file/function to modify or extend.
   Coherent mode: identify the parent capability, finding, or decision that
   motivates the child; a technically independent alternative is allowed.
4. Broad mode: check saturation (8+ discoveries) and prefer a fresh target
   unless there is a concrete new angle. Coherent mode has no saturation gate;
   assess whether the proposed continuation adds useful evidence or capability.
5. Log your compounding intent: "I will extend parent's retry logic in
   `src/http.py` to handle connection timeouts" — vague "continue
   exploring" is NOT compounding

**First dream session:** if preflight step 9 shows `(none)`, all tasks
branch from main.

## Phase 2: Plan

In broad mode, generate a concrete plan. **Target 12 tasks (2 per category).** On small
codebases (<30 source files), minimum 6 tasks across 4+ categories.

### The 6 Investigation Categories

| Category | Priority signals | Experiment |
|----------|------------------|------------|
| **Investigation** | Files with 0-2 discoveries, untraced import chains, `uncertain` entries | Test behavior hypotheses with assertions |
| **Bug hunting** | Error handling, concurrency, unvalidated inputs | Fuzz inputs, trigger error paths, reproduce races |
| **Feature design** | TODOs, FIXMEs, user-facing gaps, integration opportunities | Implement the feature, run it, evaluate integration |
| **Refactoring** | God classes, duplication, high coupling | Refactor, run existing tests, measure complexity |
| **Optimization** | Hot paths, nested loops, repeated I/O, missing caches | Profile, optimize, measure before/after |
| **Security audit** | Auth, data handling, deserialization, user inputs | Test adversarial inputs and injection vectors locally |

**Exception — user-directed focus**: If the user specifies a focus area
(e.g., "dream focus on security"), allocate ALL tasks to that category.

### Task Plan Format

Each task specifies **base branch**, **primary target file(s)**, and **why**:

```
Tasks (by category):
  Investigation:
    1. [title] — write tracing tests for [target]
       Base: main
       Target: src/auth/validator.py (UNCOVERED, 12 refs)
       Why: High fan-in utility with no shadow coverage
  Bug hunting:
    1. [title] — fuzz [target]
       Base: dream/<ns>/<prior-id> (compounds prior)
       Target: src/parsers/csv.py (extending parent's failing tests)
       Why: Parent found 2 crashes, need to verify fixes
  ...
```

### Diversity Rules

In broad mode, prefer breadth over depth: more files with 2-3 discoveries over
one file with 20. Prevent fixation with these rules:

1. **Max 2 tasks per source file** (unless prior dream left concrete follow-up)
2. **≥30% of tasks on uncovered files** (from coverage map)
3. **≥2 tasks on "deep" files** (utilities, internals, converters)
4. **Vary directories** — no 3+ consecutive tasks in same dir

**Self-check before finalizing:** unique target files ≥ 60% of task count,
uncovered file tasks ≥ 30%, no file in > 2 tasks. Swap if failing.

**Escape hatches** (document justification): prior dream's failing test,
concrete untested hypothesis, file is 500+ lines with unexplored sections,
codebase has <20 source files.

### Task Design

Each task needs: **category**, **hypothesis**, **what to implement**,
**base branch**, **primary target** (with coverage status), **why this
target**, **scope** (hours, not days), and **success criteria**.

Also include the selected **mode**. Coherent tasks additionally carry their
own **goal** and **parent_connection**, not a shared sibling/tree objective.
Category still describes the experiment, but coherent mode has no category quota.

For example, "Fuzz the CSV parser with malformed inputs to reproduce crashes
or silent corruption" is a concrete experiment; "Review error handling" is not.

### Feature Design: Motivation Required

Feature experiments must address a real gap identified in existing code.
Answer: "Why would maintainers want this?" with a specific code reference.
The feature must connect to the existing codebase (imports, modifies,
replaces duplication). Standalone modules with only stdlib don't qualify.

### Vary Your Approach

Each experiment should have unique structure driven by its hypothesis. If
you find yourself copying the same module layout (one source file + one
test file, identical importlib hack) across experiments, you're optimizing
for throughput over insight. Vary your approach: some experiments modify
existing files, some add tests for existing code, some create minimal
scripts, some refactor existing modules.

### File Selection Guidance

Agents gravitate toward entry points. Evaluation shows this causes missed bugs.

**High-value targets typically missed:**
- High fan-in files (imported by many, rarely explored directly)
- Internal/private modules (`_internal/`, `_utils/`, `_compat/`)
- Conversion/serialization code (parse, encode, format, marshal)
- Error handling paths (exception hierarchies, fallback logic)

**Avoid:** Starting from `__init__.py`, skipping "boring" files, same
directory 3+ times, ignoring files with few public symbols.

## Phase 3: Execute Tasks

Work through the plan. **Launch 3-4 experiments in parallel** via
sub-agents. Each handles the full lifecycle: create worktree → implement
→ test → write shadow + manifest + report → commit → push → clean up.
If sub-agents are unavailable, fall back to sequential execution.

In coherent mode, a parallel batch can contain diverse siblings of an already
available parent. Never launch a descendant before its parent exists. Include
the mode, goal, connection, and expected parent commit in each child prompt.

**Each experiment runs in a separate git worktree.** The worktree IS the
dream branch (created with `-b`). After pushing, the worktree is removed
but the branch persists on the remote.

### Reading Before Implementing

Before implementing, read the target source and its shadow, plus shadows of
referenced/referencing files. Identify current behavior, edge cases, and
implicit contracts. Reading is preparation; the deliverable is code written
and run, with results recorded.

### Experiment Setup

Use `dream-setup.sh` to create worktrees (handles all path computation,
namespace resolution, worktree creation, and validation):

Use the absolute setup helper in the pinned packet. For the current shell
entry point, request JSON rather than shell exports:

```text
bash PINNED_SKILL/dream-setup.sh --slug t01-csv-fuzzer --repo-root REPO_ROOT --print-json
bash PINNED_SKILL/dream-setup.sh --slug t03-extend --repo-root REPO_ROOT --base-branch dream/<ns>/<prior-id> --print-json
```

Use the Python setup port when available. Keep the returned `repo_root`,
`default_branch`, `dream_ns`, `dream_id`, `branch_name`, `parent_branch`,
`worktree_dir`, `worktree_base`, `base_commit`, `run_prefix`, and `slug` in the
agent's task state. Uppercase names in the remaining recipes refer to these
values, not to shell variables that persist across tool calls.

**If `dream-setup.sh` fails or is not found:** Apply the Script Failure
Recovery rule (diagnose the pinned script and retry safely). Common causes:
missing git remote, branch already exists, `/tmp` permissions.

Do not infer the current tool location from the experimental worktree. The
packet and its outside-repository snapshot remain authoritative, even if the
controller checkout later changes branches.

**If worktree creation fails:** mark task `blocked`, replace with another.

### What Meaningful Compounding Looks Like

In broad mode, compounding means **actively engaging with the parent's code**, not just
sitting on its branch. Valid compounding approaches:
- **Extend**: import or call the parent's modules and build on them
- **Modify**: edit the parent's code to fix limitations noted in its report
- **Refactor**: restructure the parent's implementation for better design
- **Integrate**: wire the parent's standalone module into the real codebase
- **Test deeper**: add edge-case tests for the parent's implementation

Don't assume the parent dream's code is complete or frozen — iterative
improvement is the whole point. If you can't find anything meaningful to
build on, start fresh from main instead.

In broad mode, compounding that only adds a new standalone module beside the parent's
code (with no imports, edits, or integration) is NOT compounding — it's
a fresh experiment on the wrong branch.

In coherent mode, apply the parent-connection review instead: a standalone
alternative is allowed if it substantively responds to the parent's findings
or design, rather than being unrelated work parked on the same branch.

### Run

Implement the experiment, run tests/builds, and debug as needed. Record what
worked, failed, or surprised you as you go.

### Write Shadow Discoveries

**On the dream branch** (in the worktree), NOT on main.

**Every experiment MUST write ≥1 discovery to a per-file `.shadow/*.md`.**
Authoring order: write the human-readable shadow first, then mirror every
discovery into `manifest.json`. For reconciliation the **manifest is the
source of truth** — the reconciler merges manifest entries into main, so a
discovery that is missing from the manifest never reaches main. The per-file
shadow is the human-readable copy (and a required validate gate), not the
propagation path.

Follow the dedup and writing rules in `/shadow-frog`. Dream discoveries
are typically `source: exploration`. Mark `verified` when confirmed by
running code; `uncertain` if not fully testable.

New discoveries start with visible `citation_score: 0`. Keep that field in
the corresponding manifest entry as well. Existing knowledge that informed
the task can be cited once through the core increment helper or reported to
the coordinator for serialized updates. Do not count merely enumerated entries.
Scores are approximate within the relevant checkout; reconciliation preserves
the larger score when the same claim is supplied again, rather than summing
inherited counts. Counter-only branch edits are not imported unless represented
in a matching manifest discovery; they do not justify unsupported `op` values.

#### How to Append

Find the `##`/`###` heading for the symbol, then:
- Placeholder `_No discoveries yet._` → replace with discovery
- Existing discoveries → append after last bullet
- No heading → create before `## Cross-References`

#### Label Triage (REQUIRED)

After writing each discovery, evaluate whether it deserves any of the
five actionable labels from `/shadow-frog` (`bug`, `security`,
`performance`, `feature-gap`, `tech-debt`), using that skill's definitions.

Rules:
- Apply labels to BOTH the in-file discovery markdown AND the
  `manifest.json` discovery entry (`"labels": ["bug"]`). The reconciler
  uses the manifest as source of truth; the in-file copy is for humans
  reading the shadow directly.
- Multiple labels are fine when accurate: `labels: [bug, security]`.
- Omit labels for pure behavioral observations ("retries N times before
  giving up", "default timeout is 30s") — these are knowledge, not
  action items.
- Do not apply labels speculatively. The label says "an engineer should
  act on this." If you wouldn't act on it, don't label it.

Example:

```
- /api/upload accepts paths from request body without normalization,
  allowing `../` traversal into /etc/.
  _(verified, source: exploration, labels: [bug, security], citation_score: 0)_
  Dream report: `_dreams/20260518-161200Z-upload-traversal/`
```

`dream-validate.py` emits non-blocking warnings when discovery text
contains label-signal keywords but no label is set. Treat those
warnings as a prompt to re-check the triage, not as a directive.

#### Anchor Rules

- About existing code → anchor to that symbol
- Spans 3+ files → `_cross/<slug>.md`
- Project-wide convention → `_prefs.md`
- **Only create shadows for base-codebase files** — experiment-only files
  don't get shadows (the branch IS the artifact). Anchor findings to the
  existing code they relate to.

#### Cross-Cutting Discoveries

When you see the same behavior in 3+ files, create a `_cross/<slug>.md`
rather than repeating the discovery in each per-file shadow. Add
back-pointers in each file's `## Cross-References` section.

#### Discovery Quality

Discoveries must be **self-contained process knowledge** — understandable
from the base codebase without checking out the dream branch. Capture what
was learned, how to apply it, and what to avoid, not a description of the
experiment-only artifact.

- **Good:** "agent.py's retry loop catches all exceptions including OOM,
  masking fatal errors that should crash immediately."
- **Bad:** "The implemented PluginFramework has PluginRegistry, PluginManager,
  and 7 lifecycle hooks." This describes a branch-only artifact.

Per-file discoveries should reference the dream report:

```
- Retrying with exponential backoff recovers from 99% of transient errors,
  but must exclude 4xx or it retries bad requests for 30s.
  _(verified, source: exploration, citation_score: 0)_
  Dream report: `_dreams/20250612-143012Z-retry-logic/`
```

### Write Discovery Manifest

After shadow writes, create `.shadow/_dreams/$DREAM_ID/manifest.json`:

```json
{
  "dream_id": "<DREAM_ID>",
  "branch": "<BRANCH_NAME>",
  "parent_branch": "main",
  "mode": "broad",
  "category": "bug hunting",
  "verdict": "useful",
  "title": "CSV Parser Edge Cases",
  "discoveries": [
    {
      "op": "add",
      "anchor": "src/parsers/csv.py::parse_row",
      "text": "Unescaped quotes in fields cause silent truncation.",
      "status": "verified",
      "source": "exploration",
      "labels": ["bug"],
      "citation_score": 0,
      "also_involves": ["src/parsers/utils.py::unescape"],
      "dream_report": "_dreams/<DREAM_ID>/"
    }
  ],
  "cross_cutting": []
}
```

**Anchor format:** `file::symbol` with bare names (no backticks). The
reconciler handles normalization.

For coherent experiments, set `"mode": "coherent"`, add a nonempty `"goal"`
for this experiment, and include `"parent_connection"` in the shared
`/shadow-frog` format when `parent_branch` is a prior `dream/...` branch.
Seeds use null or omit the connection. `parent_branch` is the authoritative
parent reference; siblings need not share goals or modify different files.
These fields survive reconciliation in the archived manifest.

Manifest `op` values: only `add` is supported by the reconciler today.
`update` and `refute` are reserved keywords — `dream-validate.py` will
reject any discovery whose `op` is not `add`. To revise or contradict an
existing discovery, run a meditate session against main's `.shadow/`
instead of trying to do it from a dream branch.

`citation_score` on per-file or cross-cutting manifest entries is a nonnegative
integer, defaulting to 0. It is a reuse hint, never a verification or trust signal.

**Hard gate — discoveries must be mirrored into per-file shadows.** The
reconciler merges `manifest.json` entries into main directly (so discoveries
are not lost at merge time), but the branch's per-file shadows must ALSO be
updated so human PR reviewers can read the discoveries in context. If
`manifest.json` declares discoveries but no `.shadow/*.md` files outside
`_dreams/` are modified in the branch diff vs `base_commit`,
`dream-validate.py` rejects the dream. Always write each discovery into BOTH
the corresponding per-file shadow (or `.shadow/_cross/`) AND the manifest
before staging.

### Save Dream Report

Save as `.shadow/_dreams/$DREAM_ID/report.md`:

```markdown
---
dream_id: "<DREAM_ID>"
category: bug hunting
verdict: useful
base_commit: "<BASE_COMMIT>"
branch: "<BRANCH_NAME>"
parent_branch: "main"
mode: broad
remote: "origin"
related_symbols:
  - "src/parsers/csv.py::parse_row"
builds_on: []
---

# CSV Parser Edge Cases

## Motivation
<cite specific existing files/symbols where gap was identified>

## Compounding Delta
<ONLY for a prior-dream parent — what parent code or design was engaged and changed>

## Parent Connection
<coherent mode: own goal, relation, parent basis, delta, preserved constraints,
and superseded decisions; explain alternatives even when technically independent>

## Hypothesis
<what we expected to learn>

## Implementation
<key decisions, approach>

## Commands Run
<exact commands with exit codes>

## Evaluation
<results, what worked/didn't>

## Takeaways
<lessons, gotchas>

## Verdict Details
<why useful/dead_end>
```

| Field | Required | Values |
|-------|----------|--------|
| `dream_id` | yes | `YYYYMMDD-HHMMSSZ-slug` |
| `category` | yes | one of the 6 categories |
| `verdict` | yes | `useful` or `dead_end` |
| `base_commit` | yes | SHA branched from |
| `branch` | yes | full branch name |
| `parent_branch` | yes | `main` or prior branch path |
| `mode` | coherent runs | `broad` (default) or `coherent`; must match manifest and validator |
| `related_symbols` | yes | `file::symbol` refs |

**`tip_commit` is NOT in the report.** Including the final commit SHA
creates a chicken-and-egg problem (SHA changes when report is committed).
The reconciler derives it via `git rev-parse origin/$BRANCH` and records
it in `_dreams/_index.md`.

**Verdict** is the agent's assessment (set once, immutable):
- `useful` — produced actionable findings, working code, or valuable lessons
- `dead_end` — approach doesn't work; documented why so future dreams skip

### Validate, Commit, Push

Validation must use `commands.validate` from the pinned packet. Its mode was
fixed before the worktree was created; no mode override or local-helper fallback
is allowed. Carry this exact command prefix into each subagent prompt.

```bash
cd "$WORKTREE_DIR"

# 1. Generate diff (exclude .shadow/ and common build artifacts)
git add -A -- ':!.dream_parent' ':!__pycache__/' ':!.pytest_cache/'
git commit -m "dream: $SLUG"
mkdir -p .shadow/_dreams/"$DREAM_ID"
git diff "$BASE_COMMIT" HEAD -- \
    ':!.shadow/' ':!__pycache__/' ':!*.pyc' ':!.pytest_cache/' \
    ':!node_modules/' ':!*.lock' ':!dist/' ':!build/' \
    > .shadow/_dreams/"$DREAM_ID"/patch.diff
[ ! -s .shadow/_dreams/"$DREAM_ID"/patch.diff ] && echo "WARNING: Empty diff"

```

**2. Validate (hard gate):** execute `commands.validate + [DREAM_ID,
WORKTREE_DIR]`. The copied runner verifies the tooling and passes the pinned
`--mode` to the copied validator. Stop on any nonzero exit; fix the artifact or
tooling issue and retry. File-existence checks are not a substitute for validation.

**3. Only after validation succeeds, commit and push:**

```bash
git add -A -- ':!.dream_parent'
git commit -m "dream: $SLUG — final with report and manifest"
if git push origin "$BRANCH_NAME"; then
    echo "Pushed: $BRANCH_NAME"
else
    echo "ERROR: Push failed. Keep worktree for recovery."
    exit 1
fi
```

**Do NOT write to `.session-branches.txt`** — that is managed by the
orchestrator after all agents complete. Agents only push their branch;
the orchestrator discovers pushed branches from the remote.

### Worktree Cleanup

```bash
bash "$SKILL_DIR/dream-cleanup.sh" "$WORKTREE_DIR" --repo-root "$REPO_ROOT"
```

`dream-cleanup.sh` does the equivalent of `git worktree remove --force`
followed by `git worktree prune`, with a safety-gated `rm -rf` fallback if
worktree removal silently fails. The fallback only accepts paths matching
`${DREAM_WORKTREE_BASE:-/tmp/shadowfrog-dreams}/<ns>/dream-<slug>`
exactly; any other path is refused.

Remove as you go. If push failed, keep the worktree.

### Mid-Session Diversity Check

This breadth check applies to broad mode. In coherent mode, review edge quality
and sibling duplication instead; do not swap out a relevant continuation just
to increase unique-file counts.

After completing roughly half of your planned tasks, pause and review:

1. **Count unique primary target files** explored so far. If fewer than
   50% of completed tasks targeted distinct files, remaining tasks MUST
   target new files.
2. **Check for re-exploration** — are any completed tasks exploring files
   already well-covered before this session? Swap remaining tasks for
   uncovered ones.
3. **Review coverage map delta** — if fewer than 2 previously uncovered
   files explored, prioritize uncovered files for remaining tasks.
4. **Adjust the plan** — swap, add, or reorder remaining tasks. The plan
   is a starting point, not a contract.

This prevents the fixation failure mode where the first half discovers a
rich area and the second half keeps digging there instead of spreading.

## Phase 4: AFK-Safe Patterns

1. Worktrees are outside the repo — writes don't trigger approval
2. Temp scripts go in `/tmp/shadow-dream-<slug>.*`
3. Never modify main directly — only during reconciliation
4. Clean up worktrees after push
5. Shadow writes on dream branches are safe

## Phase 5: Parallel Agent Rules

1. Broad mode targets different files. Coherent siblings may target the same files in their separate worktrees.
2. Each agent gets its own branch (inherently isolated)
3. Each agent writes its own manifest in its `$DREAM_ID/` directory
4. Do NOT write to main or shared files (`_index.md`, `state.json`)
5. Do NOT update shared indexes or `state.json` — reconciled post-dream by orchestrator
6. Broad mode: fetch once and use the initial snapshot. Coherent mode: after a
   parent is pushed, the orchestrator refreshes that parent's ref/commit and
   branch map before launching its children. Siblings share the refreshed
   snapshot; subagents never fetch independently.
7. Manifest anchors use bare symbol names (reconciler normalizes)
8. Thread `RUN_PREFIX` into every subagent prompt
9. Include `WORKTREE_BASE` and `DREAM_NS` in every subagent prompt
10. Dream artifacts MUST use subdirectory format — flat files are a
    completion criteria violation (see Critical Invariants → Artifact Format)
11. Thread the selected mode and pinned tool packet into every prompt. For
    coherent children, include their own goal, parent packet, connection, and
    expected parent commit. Use the supplied validation command; never resolve
    a helper from the child's installed skill directories.

## Phase 6: Reconcile to Main

**⚠️ CRITICAL: Reconciliation is MANDATORY at the end of every dream batch.**
See Critical Invariants → Reconciliation is Mandatory (above) for the
parallel-vs-sequential mode definitions and the auto-discover rule. Do
NOT defer reconciliation across batches.

The `_index.md` entry is your sequential-mode checkpoint — any dream
listed there is safe if the session crashes.

Use the reconciliation script:

```bash
cd "$REPO_ROOT"
git checkout "$DEFAULT_BRANCH"
git fetch origin --prune

```

Then execute `commands.reconcile + ["--namespace", DREAM_NS]`. It already names
the original repository and the pinned helper, so checking out the default
branch cannot downgrade the tooling.

On an error, diagnose the pinned source and repair the reported artifacts or
preconditions before retrying. Do not fall back to a worktree-local reconciler
or manually imitate its branch-deletion steps.

### What the Reconciler Does

Manifest destinations are preflighted before any writes, including in dry runs.
Absolute/traversal paths and symlinks escaping `.shadow/` fail with a nonzero
exit. Repair the indicated manifest field or filesystem alias before retrying;
do not bypass containment checks.

1. **Discovers** new branches (namespace-filtered, not in `_index.md`)
2. **Reads/validates** manifests from remote branches
3. **Merges** discoveries into main's per-file shadows (semantic dedup; on an exact-text duplicate it upgrades the existing entry's metadata — unions labels, raises source trust, promotes `uncertain`→`verified` — but never alters a `refuted` status)
4. **Mirrors** reports, manifests, patches to main's `_dreams/`
5. **Updates** `_dreams/_index.md` with new entries
6. **Updates** `_meta/state.json`
7. **Rebuilds** top-level `.shadow/_index.md` (per-file discovery counts)
8. **Verifies** all artifacts present (hard gate)
9. **(Optional)** Deletes reconciled broad branches — only with `--cleanup-branches`, and only after the reconciliation has been committed and pushed (refuses on a dirty `.shadow/` or when HEAD is not yet on `origin/<default-branch>`). Coherent branches and their ancestors are retained; unreadable indexed manifests prevent cleanup rather than risking task baselines.

### After Reconciliation: Commit, Push, and Cleanup

```bash
cd "$REPO_ROOT"
git add .shadow/
git commit -m "dream: reconcile $(date -u +%Y%m%d-%H%M%SZ) — N experiments"
git pull --rebase origin "$DEFAULT_BRANCH" || {
    echo "ERROR: Rebase failed. Abort and retry manually."
    git rebase --abort 2>/dev/null
    exit 1
}
if git push origin "$DEFAULT_BRANCH"; then
    echo "✓ Pushed reconciliation"
else
    echo "ERROR: Push failed. Retry: git pull --rebase && git push"
    echo "⚠️ Do NOT clean up branches until push succeeds."
    exit 1
fi
```

### Post-Reconciliation Branch Cleanup

There is **one automatic pruning path in either mode**. After reconciliation
is committed and pushed, execute:

```text
commands.reconcile + ["--namespace", DREAM_NS, "--cleanup-branches"]
```

This is argument-array notation, not shell code. The command verifies persisted
artifacts, remote state, descendants, and coherent retention. A broad run can
reconcile pending coherent branches from an earlier session; the current run's
mode is never permission to delete those branches.

Never use an inline deletion loop or manually duplicate these checks. A retained
branch is not failed cleanup: coherent branches and their canonical index
ancestors remain available until explicit curation. `SHADOWFROG_KEEP_BRANCHES`
still disables automatic pruning. Worktree cleanup is independent.

Unreadable lineage metadata exits nonzero before any branch deletion. Restore
the indicated manifest or repair the stale `_dreams/_index.md` row after checking
its descendants, then retry. Do not delete valid archives to bypass the refusal.

**Worktree cleanup** happens separately (Phase 7 — see "Worktree Pruning"
below). Worktrees can be removed immediately after branch push regardless
of reconciliation status. Reconciled branches also have their worktree
GC'd automatically by `dream-reconcile.py --cleanup-branches`.

### Recovery

If reconciliation is interrupted: branches are already pushed (no data
loss). Re-run reconciliation — it's idempotent. The reconciler uses
`_dreams/_index.md` as its journal: any branch already listed there is
skipped, any branch not listed is reprocessed. The reconciler's own
step 8 verifies all artifacts on main; if verification fails the script
exits non-zero — fix the cause and re-run.

## Phase 7: Summary, Review, and Pruning

### End-of-Session Cleanup

Before the summary, sweep leftover worktrees from the mid-batch leak
(dreams that pushed but weren't `dream-cleanup.sh`'d before the loop
exited). Only run this once the agent has asserted no more dreams are
starting **in this namespace**:

```bash
bash "$SKILL_DIR/dream-gc.sh" \
    --task-complete --namespace "$DREAM_NS" \
    --repo-root "$REPO_ROOT" --min-age-min 0
```

See "Worktree Pruning" below for `--namespace` rationale, `--min-age-min`
semantics, and the other three cleanup paths.

### Summary

```
Dream session complete.
  Results (by category):
    Investigation: N tasks, M discoveries
    Bug hunting: ...
  Branches pushed: K
  Branch tree:
    main
    +-- dream/<id-1> (useful)
    +-- dream/<id-2> (dead_end)
  Top findings:
    - <discovery> -- <category>
```

### Experiment Review

Walk through each experiment with the user. Actions:
- **Keep** (default) — branch and report stay
- **Delete** — remove from `_dreams/`, delete remote branch
- **Checkout** — inspect the code live

Wait for user confirmation before deleting any remote branch.

### Branch Pruning

Only the guarded Phase 6 cleanup command performs automatic branch pruning.
If push failed or lineage verification was refused, fix that condition before
retrying it. If a branch was retained for coherence or `SHADOWFROG_KEEP_BRANCHES`,
leave it retained; do not route it to a separate deletion recipe.

Explicit curation is a distinct, user-approved decision. First identify all
dependent dreams and exported task baselines, preserve any required commits
under durable refs, and update consumers before retiring a branch or archive.
Removing an archive requires repairing its index entry and descendant references
as well. There is no automatic expiry for coherent task baselines.

### Worktree Pruning

Dream worktrees live OUTSIDE the repo at
`${DREAM_WORKTREE_BASE:-/tmp/shadowfrog-dreams}/<ns>/dream-<slug>/`. There
are four places they get cleaned up:

1. **`dream-cleanup.sh`** — called by the agent after each `git push` (see
   "Worktree Cleanup" earlier in this skill). Removes ONE worktree.
2. **`dream-reconcile.py --cleanup-branches`** — after deleting a merged
   branch, also `rm -rf`s its worktree directory. No extra command needed.
3. **`dream-gc.sh` (auto-triggered)** — `dream-setup.sh` invokes this
   sweeper at the start of each new dream, throttled by a per-namespace
   `.last-gc` tombstone to run at most once per `DREAM_GC_INTERVAL_MIN`
   minutes (default 60). Catches orphaned worktrees.

   Env knobs (all optional, sensible defaults):
     - `DREAM_GC_AUTO=0` — disable the auto-trigger entirely
     - `DREAM_GC_INTERVAL_MIN` — how often the trigger fires (default 60)
     - `DREAM_GC_AGE_MIN` — min worktree age to sweep (default 60)

4. **`dream-gc.sh --task-complete --namespace "$DREAM_NS"`** —
   end-of-session sweep, run by the agent when it stops dreaming (dream
   count reached, or genuinely blocked). Unlike the auto-trigger, this
   mode ALSO removes `stale-registered` worktrees (valid `.git` pointer
   but no `dream-cleanup.sh` ever ran on them — the mid-batch
   `task_complete` leak). The agent's assertion "I'm done dreaming
   **in this namespace**" is what makes this safe.

   **Required:** `--namespace` (or `DREAM_NAMESPACE` env). The script
   refuses with exit 2 if neither is given — that prevents a multi-repo
   fleet sharing one `$DREAM_WORKTREE_BASE` from one agent's
   `task_complete` destroying another agent's live worktrees.

   `--min-age-min` is an **mtime gate, not a liveness check**. Pass `0`
   at end-of-session to catch the freshly-pushed final batch; pass a
   higher value (e.g. `10`) if you can't fully assert that no other
   dream in the same namespace is in flight. Locked worktrees
   (`git worktree lock`) are always respected — the sweeper WARNs and
   leaves them in place.

   ```bash
   # At the end of the dream loop, before the final summary:
   bash "$SKILL_DIR/dream-gc.sh" \
       --task-complete \
       --namespace "$DREAM_NS" \
       --repo-root "$REPO_ROOT" \
       --min-age-min 0
   ```

All four paths share a single safety gate (`_worktree_safety.py`) that
refuses ANY path which is not strictly under `$DREAM_WORKTREE_BASE` and
doesn't match the exact `<base>/<ns>/dream-<slug>` shape. The gate is
unconditional — even an attacker-controlled `$DREAM_WORKTREE_BASE` cannot
cause `rm -rf /`.

### Applying Dream Code

```bash
# Option 1: Merge the dream branch
git checkout "$DEFAULT_BRANCH"
git merge dream/<id> --no-ff -m "Adopt dream: <title>"

# Option 2: Cherry-pick specific commits
git cherry-pick <tip_commit>

# Option 3: Apply the patch (if branch was pruned but commit exists)
git show <tip_commit> | git apply
```

## Experiment Completion Criteria

A task is complete ONLY when ALL of these hold:

1. Code was written or modified (non-empty `patch.diff`)
2. At least one command was executed with exit code recorded in `Commands Run`
3. At least one finding tied to running code (not just reading)
4. At least one per-file `.shadow/*.md` edit made
5. Discoveries are behavioral insights, not feature descriptions
6. `report.md` saved with all required fields
7. Dream branch pushed to remote
8. Reconciliation completed and verified (`report.md`, `manifest.json`,
   `patch.diff` exist on main, `_index.md` has entry)

Additional gates:
- **Feature design:** `## Motivation` cites specific existing code
- **Broad compounding:** `## Compounding Delta` explains what parent code was modified
- **Coherent mode:** mode is threaded through validation and artifacts; each
  child has a substantive parent connection and its own goal. Siblings remain
  free to differ. A validated schema is not a substitute for this review.

A task that fails these criteria is **not completed**. If setup fails or
the experiment produces nothing, mark it `blocked` in the summary and
replace it with another experiment. Blocked tasks do not count toward the
category minimum.

> **After reconciliation:** run `/shadow-frog-meditate` to consolidate
> discoveries and repair the index.

### Exporting Coherent Work as SWE Tasks

Select a root-to-leaf trajectory, not a concatenation of independent siblings.
Combining siblings requires an explicit integration experiment. Each task's
code baseline must be an actual retained commit, separate from conceptual
provenance. For sequential tasks, state which earlier requirements change.
For one combined task, use the final active behavioral contract: replacing
design A with B does not require implementing both. Do not rewrite historical
reports or use unsupported manifest `refute` operations to supersede a design.

## Curating Dream Experiments for Upstream PRs

Once dream branches accumulate, you (or the user) may want to surface a
few worth submitting to the upstream project. The default AI failure mode
is sycophancy — approving too many experiments because they look like
work. Resist that. Apply these heuristics:

1. **The maintainer test.** For each experiment, ask: *If I submitted this
   as a PR to an open-source repo I don't maintain, would the maintainer
   merge it — or politely close it?* This is the only question that matters.
2. **Devil's advocate framing.** Your job is to find reasons NOT to PR
   each experiment. Recommend only when you cannot find a compelling
   reason to reject.
3. **70% rejection quota.** If you approve more than 30% of experiments
   reviewed, your standards are too low. Re-evaluate.
4. **The "so what?" test.** Would a human engineer read the report and
   say "so what?" If yes, reject.
5. **The 30-minute test.** Could a competent developer have produced
   this in 30 minutes with a linter, TODO grep, or quick docs read? If
   yes, it's maintenance work, not a contribution. Reject.
6. **The novelty test.** Does the experiment reveal a non-obvious
   behavior, hidden assumption, or unexpected interaction? If not, reject.
7. **No credit for effort.** A 10-experiment chain that produces a minor
   tweak is still a minor tweak. Judge the result, not the journey.

When you do submit a PR, write the body for someone who has never seen
the dream branch. Include: one-paragraph "what it does" derived from the dream
report, the experiment's evidence (test output, before/after metric),
and an honest "what we did not verify" note.
