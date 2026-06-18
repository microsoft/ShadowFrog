# ShadowFrog Evaluation Suite

This folder is the view-time entry point for the ShadowFrog evaluation.
It contains exactly two things:

- **[`results_dashboard.html`](results_dashboard.html)** — a single
  self-contained HTML report rolling up every experiment (the xarray
  Lens 1 dream-lineage sunburst is inlined via `<iframe srcdoc>`).
- **This README** — a near-reproducibility-grade description of *what we
  did* across all five sub-experiments: corpus, environment, pipeline,
  prompts, scoring rubrics, and key design decisions. Numbers are quoted
  when they describe scope (corpus sizes, run counts, axis sweeps) but
  no results are reported here — for those, open the dashboard.

The README is intended to be **self-contained**: every step, prompt,
and scoring decision is described in enough detail that a reader could
re-derive the pipeline from public sources (the corpora are all public
GitHub repos; SWE-Bench Verified and SWE-Smith are public datasets).
The raw outputs (patches, dreams, judge verdicts, logs, manifests,
shadow KBs, per-task analyses, per-arm operator runbooks, Dockerfiles)
amount to ~42 GB and are not open-sourced.

```
1. swebench         — blind bug hunting on SWE-Bench Verified
2. swebench-fix     — bug fixing with vs. without shadow knowledge
3. swesmith         — stacked synthetic-bug hunting on SWE-Smith
4. feature-ideation — shadow vs. baseline feature ideation
5. navigation       — H8/H10/H11 navigation hypotheses + wrong-needle probe
```

Common conventions across all five:
- **Model**: `claude-opus-4.6` for the agent, the judges, and (where
  applicable) needle authoring / perturbation / question generation.
- **Agent runtime**: GitHub Copilot CLI invoked non-interactively
  (`copilot -p "$PROMPT"`) with `--output-format json
  --no-custom-instructions` and the appropriate `--allow-all*` flag(s)
  to permit unattended tool use. The exact flag combination is repeated
  per experiment below since it varied across the five.
- **Reproducibility seed**: `seed=42` for task sampling; `random_seed:
  1729` for the navigation eval.
- **Host/Docker split**: the agent runs on the host so it has full
  Copilot CLI + ShadowFrog skills infrastructure; `python` / `pytest` /
  `pip` are routed into a per-task Docker container via wrappers in
  `.docker-bin/`. File operations (read/edit/grep/git) run on the host
  against bind-mounted files so Docker sees them too.
- **Dream branches isolated**: every experiment uses bare local clones
  as `origin` so dream branches never push to upstream.

---

## 1. Blind Bug Hunting on SWE-Bench Verified (`swebench`)

**Question.** Can ShadowFrog's autonomous dream exploration discover
real-world bugs *without being told they exist*? No problem statement,
no failing test — just the codebase.

### Corpus

50 SWE-Bench Verified tasks (out of 500), stratified-sampled across 12
Python repos (seeded random sample with `seed=42`):

| Repo | Tasks |
|---|---:|
| astropy/astropy | 5 |
| django/django | 5 |
| matplotlib/matplotlib | 5 |
| mwaskom/seaborn | 2 |
| pallets/flask | 1 |
| psf/requests | 4 |
| pydata/xarray | 5 |
| pylint-dev/pylint | 4 |
| pytest-dev/pytest | 4 |
| scikit-learn/scikit-learn | 5 |
| sphinx-doc/sphinx | 5 |
| sympy/sympy | 5 |

Allocation algorithm: floor-divide tasks by repo count, then redistribute
the shortfall to larger repos round-robin until total = N. Within each
repo, uniform random sampling from the SWE-Bench Verified `test` split.

### Pipeline

1. **Stratified sampling** (`seed=42`) selects 50 instances from the
   SWE-Bench Verified `test` split. The sampler records, per task,
   `instance_id` / `repo` / `base_commit` / `version` / `difficulty`,
   alongside the gold patch, the test patch, and the `fail_to_pass`
   test list — the last three are kept private from the agent and used
   only at scoring time.
2. **Per-task setup**. For each unique repo we `git clone --bare`
   upstream and, for each task on that repo, `git worktree add` at the
   task's `base_commit`. `origin` in the worktree is repointed to the
   local bare clone so dream branches stay private. A small metadata
   file (containing only the namespace string, no bug info) is written
   so the dream skill can name branches by `instance_id` when multiple
   tasks share a repo. ShadowFrog is installed into the worktree with
   `install.sh --project <worktree> --no-hooks`, then the hooks JSON
   and accompanying scripts are copied into `.github/hooks/`. Finally
   the per-task SWE-Bench Docker image
   (`swebench/sweb.eval.x86_64.<owner>_1776_<repo>-<n>`) is pulled.
3. **Per-task docker env**. A sourced wrapper starts the per-task
   container, bind-mounts the worktree, writes wrapper executables for
   `python` / `python3` / `pytest` / `pip` / `conda` into a
   `.docker-bin/` directory, and prepends that directory to PATH. File
   operations stay on the host; Python execution runs inside Docker.
   The wrappers detect the agent's current working directory and
   translate worktree-relative paths into the container, so dreams that
   create sub-worktrees under `/tmp/shadowfrog-dreams/dream-<slug>/`
   for compounding experiments still see `python` / `pytest` working
   at arbitrary depth.
4. **Dreams**. In each worktree the operator runs `/shadow-frog-init`
   then prompts for 5 iterations of compounding dreams focusing on bug
   hunting.
5. **Patch + report collection**. Each worktree (plus its dream
   sub-worktrees) is walked to gather dream artifacts in two formats:
   the canonical subdirectory layout
   (`_dreams/<id>/{report.md, manifest.json, patch.diff}`) and a
   flat single-file report-only variant (`_dreams/<id>.md`) used by
   later compounding sessions. Sub-worktree dreams are attributed to
   their parent task via a 4-level fallback (a `.dream_parent` marker
   file → the metadata file → exact `base_commit` match → closest
   ancestor by commit distance). Aggregate: **6,701 dreams across 50
   tasks, ~134 dreams per task on average**.
6. **Localization scoring**. L1 (file hit) and L2 (function hit) are
   automated (described in the rubric below); L3 emits per-task LLM-judge
   prompts and then merges the returned verdicts back into the summary.

### Scoring rubric

Three levels, "best match over all dream reports for a task":

| Level | Name | How |
|---|---|---|
| L1 | File hit | string match: did any dream mention/touch a file in the gold patch? matches both path suffixes and Python module notation (e.g. `sklearn.metrics._ranking` ↔ `sklearn/metrics/_ranking.py`) |
| L2 | Function hit | word-boundary regex match against function names extracted from the gold diff. Extracts from (a) `@@` hunk headers, (b) `+`/`-` def/class lines, (c) **context** lines inside hunks showing `def`/`class` (so we catch the actual method when the hunk header only shows the enclosing class) |
| L3-4 | IDENTIFIED | LLM judge: bug describable from the report alone — root cause or trigger matches the gold patch |
| L3-3 | PARTIAL | LLM judge: symptom of the bug, right code path with mis-attributed root cause, or proposes a fix that would partially address the issue |
| L3-2 | ADJACENT | LLM judge: real, verified bugs in the **same function** as the gold patch but distinct from it |
| L3-1 | AREA | LLM judge: right file/module with demonstrated subsystem understanding, no in-function bug |
| L3-0 | MISSED | LLM judge: unrelated to the bug's file/module/subsystem |

The L3 judge sees the problem statement, the gold patch, and **all**
non-trivial dream reports (reports shorter than 50 characters are
treated as empty placeholders and skipped; L1-hitting reports are
listed first). The "best-match" semantic means a single high-level
report among 100+ wins
the verdict. ADJACENT exists because "5 verified real bugs in the exact
gold function" is meaningful work even if the agent missed the specific
SWE-Bench bug.

### Pipeline summary

End-to-end the experiment is: stratified sampler → per-task setup
(bare clone + worktree + ShadowFrog install + Docker pull) → sourced
docker-env wrapper → 5 compounding dream iterations per task →
collect dream reports + patches → score L1/L2 automatically and L3 via
LLM judge.

---

## 2. Bug Fixing on SWE-Bench Verified (`swebench-fix`)

**Question.** Does pre-collected shadow knowledge help an agent *fix
known bugs* better than a baseline agent without it? Same corpus, same
Docker, but here the agent **is told** about the bug.

### Corpus

The same 50-task SWE-Bench Verified subset as `swebench`, locked in a
task manifest (12 repos: astropy 5, django 5, matplotlib 5, xarray 5,
scikit-learn 5, sphinx 5, sympy 5, requests 4, pylint 4, pytest 4,
seaborn 2, flask 1). **3 seeds per task per arm** for variance
(50 tasks × 3 seeds × 2 arms = 300 runs).

### Pre-built shadow knowledge bases

The SF arm consumes a per-repo shadow KB built once before the fix
experiment (~88 MB across all 12 repos). The KBs were built by an
independent dream campaign with no access to the SWE-Bench problem
statements, so the shadow's coverage of any particular bug is
incidental, not engineered. The pre-filter (next step) consumes these.

### Pipeline

1. **Setup**. For each of the 50 tasks, create a per-task worktree at
   `base_commit`, pull the SWE-Bench Docker image, bind-mount the
   worktree, write a `TASK.md` containing the official problem
   statement.
2. **Pre-filter (SF arm only)**. Given the task directory (which
   contains `TASK.md` and a copy of `.shadow/`), extract file paths and
   symbols from the problem statement using:
   - File-path regex (e.g. `path/to/file.py`)
   - Module dotted refs (`django.db.models.query`) → `django/db/models/query.py`
   - Backtick-quoted paths
   - **Distinctive tokens**: CamelCase, snake_case, ALL_CAPS only — common
     words are filtered against a 124-word stopword list
   - **Dream-index search**: matches dream experiment titles against
     problem keywords; the matched dreams' files become candidates
   - **BUG-tag proximity boost**: shadow files with `BUG:` markers
     within the matched windows score higher
   - **Cross-reference scoring**: require ≥2 keyword matches to promote
     a cross-reference (reduces false positives)

   The pre-filter writes a `SHADOW_HINTS.md` capped at ~4 KB into the
   task dir. Tasks with no extracted keywords or no matching shadow
   content produce an empty hint file, in which case the agent proceeds
   with zero shadow context.
3. **Run agents** (one runner per arm), 3 seeds each, model
   `claude-opus-4.6`, agent timeout 1800 s, Docker memory 4 g, batch
   size 25 in parallel. SF prompt enforces an **investigate-first**
   workflow (see "Prompts" below). BL prompt is a minimal 55-word
   "fix the bug" instruction.
4. **Score**. Apply each candidate patch to a clean worktree in Docker,
   run repo-specific test commands (a curated lookup table maps each
   `(repo, version)` pair to its install / test / coverage commands —
   the table is derived from SWE-Bench's `MAP_REPO_VERSION_TO_SPECS`
   plus a few env-drift overrides), parse with SWE-Bench's log parsers,
   compute the `fail_to_pass` set, and declare RESOLVED iff all
   `fail_to_pass` tests pass. Aggregates are emitted per arm × seed.

### Prompts

SF prompt structure (abridged; the full text is the literal block
below plus the four numbered workflow steps that follow):

```
RULES:
- Do not fix pre-existing environment issues …
- Do not run pip install …
- Do not edit setup.py, setup.cfg, pyproject.toml, tox.ini unless the
  bug originates there …
- When using grep or find, exclude the .shadow/ directory …

WORKFLOW — follow this order:
Step 1: INVESTIGATE THE BUG (do this first, before anything else)
  Read the bug report. Reproduce it. grep / read / trace to identify
  buggy file(s) and function(s). Form your own hypothesis about
  root cause.
Step 2: CHECK PRIOR ANALYSIS (optional)
  If SHADOW_HINTS.md exists, read it after Step 1. Use any relevant
  insights as hints, but verify everything against the actual source.
  If no SHADOW_HINTS.md exists, skip this step entirely.
Step 3: IMPLEMENT THE FIX
  Write the fix based on your investigation. Aim for correctness
  over minimality.
Step 4: VERIFY COMPLETENESS
  - If the fix touches a shared helper, check sibling methods
  - Run git status — if you created new files, make sure they're tracked
  - Run relevant tests to verify
  - An empty patch means the bug is not fixed
```

BL prompt: minimal (~55-word fix-the-bug instruction) — same task
environment, no shadow, no workflow scaffolding.

Patch-comparison excludes: `.shadow`, `.docker-bin`, `TASK.md`,
`TASK_INFO.json`, `SHADOW_HINTS.md`, `.github`, `.copilot`, `.claude`,
`test-output`, `*.pyc`, `__pycache__`, `*.so`, `*.o`,
`copilot-instructions.md`.

### Key design decisions

- **Pre-filter, not browsing.** Free browsing of the shadow distracts the
  agent — too much off-target content drowns out the few relevant hints.
  The investigate-first + pre-filter design lets the agent see only
  shadow content already judged relevant to the bug at hand.
- **Source-over-shadow on conflict.** If shadow content disagrees with
  source, trust the source — discoveries can become stale.
- **No index browsing.** The agent never sees `.shadow/_index.md` or
  `_dreams/` directories — index browsing causes "attention theft": the
  agent spends turns navigating the index rather than acting on the
  hints already extracted.

### Pipeline summary

End-to-end the experiment is: per-task setup (worktree + Docker +
problem statement) → SF-only pre-filter (extract problem-statement
keywords → match against the pre-built shadow KB → write a
≤4 KB `SHADOW_HINTS.md` into the task dir) → run the SF and BL agents
3× each → score by re-applying each candidate patch in a clean Docker
worktree and checking that all `fail_to_pass` tests pass.

---

## 3. Stacked Synthetic Bug Hunting on SWE-Smith (`swesmith`)

**Question.** How does blind bug hunting scale when we don't need to
treat each bug as a separate task? Stack many synthetic bugs into one
container and measure coverage from longer dream sessions.

### Corpus

20 Python repos drawn from SWE-Smith (which catalogs 50,000+ synthetic
bugs across 128 repos), 100 non-conflicting synthetic bugs each → 2,000
bugs total. Bug selection is greedy non-conflicting with `seed=42`
(SWE-Smith is ~90% single-file, so conflicts are rare). Unique bugged
files per repo: 11 (pypika) to 121 (conan).

```
cantools, conan, pypika, oauthlib, jinja (pallets), pandas,
paramiko, pyasn1, patsy (pydata), pydicom, pygments, astroid,
python-docx, trio, safety, python-pptx, deepdiff, sqlfluff,
sunpy, sqlglot
```

### Pipeline

1. **Manifest generation**. We pick 100 non-conflicting bugs per repo
   from the SWE-Smith HuggingFace dataset (seed=42, greedy hunk-overlap
   detection with a 4-line margin). The manifests are reproducible:
   re-running the selection produces a byte-identical result for 19/20
   repos (pandas-dev differs due to unrecoverable original generation
   code but is equally valid). One manifest per repo is committed so
   scoring is always reproducible.
2. **Bug stacking**. For each repo we pull the SWE-Smith Docker image,
   start a container with `--network none --memory=8g --memory-swap=8g`,
   then inside the container:
   - apply each of 100 bug patches forward (clean → buggy);
   - remove **all** test files — pattern-based (test directories,
     `test_*.py`, `*_test.py`, `conftest.py`) plus manifest-based (any
     `fail_to_pass` paths);
   - `rm -rf .git && git init && git add -A && git commit -m 'initial state'`
     so there's no recoverable history;
   - record the apply outcomes for each bug.
3. **Lock-down**: every run uses `--network none` to block GitHub/PyPI
   fetches of upstream source or tests.
4. **Run dreams** (SF arm): 10 structured dream sessions × 12 sub-agents
   each = 120 agents per repo.
5. **Run baseline** (BL arm): 120 independent parallel agents per repo.
   Same Docker image, same bug stack, but no ShadowFrog skills installed
   and a minimal prompt (verbatim):

   ```
   You are given a codebase to explore. Your task is to find bugs, edge
   cases, and issues in the code. Examine the source files, trace logic
   flows, and identify problems.
   Output your findings as a JSON array. Each finding must have:
     "file" / "symbol" / "line" / "description" (1-2 sentences).
   Write ONLY valid JSON to: {output_path}
   ```

   The 120-vs-120 design lets us match SF's total compute exactly while
   varying only the SF-vs-BL axis.
6. **Budget sweep**. Re-score the same transcripts at 10 budgets
   `b12, b24, …, b120` controlling how many sessions / agents are
   included. For SF, `b<N>` = N sub-agents from `N/12` sessions; for
   BL, `b<N>` = N agents.
7. **Score**:
   - **Stacked scorer**: L1/L2/L3 (exact intersection of changed-line
     sets, per file) against the canonical manifest.
   - **Budget judge**: an LLM judge re-grades the top candidates at
     each budget on the IDENTIFIED rubric and emits a per-budget
     verdict file.
   - These are rolled up into a single per-repo results blob that
     drives the final figure.
8. **Render** the headline figure: a 3-panel plot of L1 / L2 / Judge
   IDENTIFIED% as a function of budget.

### Scoring rubric

| Level | Name | Definition |
|---|---|---|
| L1 | File hit | agent explored/modified a file containing a bug |
| L2 | Function hit | agent's patch touches the bugged function |
| L3 | Line overlap | exact intersection of changed-line sets per file (no tolerance) |
| Judge | IDENTIFIED% | per-budget Opus-4.6 judge verdict, % of 100 bugs |

L3 uses actual changed lines (not full hunk ranges) to avoid context-line
false positives.

### Anti-cheat hardening (locked-in container)

| Threat | Mitigation |
|---|---|
| Run pytest to see failures | All test files removed (pattern + manifest) |
| `git reset` to recover clean | `rm -rf .git && git init` (no history) |
| Fetch from GitHub/PyPI | `--network none` on container |
| Inspect git reflog/fsck | Single commit, no dangling objects |

### Pipeline summary

End-to-end the experiment is: deterministic bug-stack manifests
(100 non-conflicting bugs per repo, seed=42) → stack into a per-repo
container with tests removed, git history wiped, and
`--network none` → run 120 agents per repo per arm → budget-sweep
re-scoring at b12…b120 → aggregate into a single results blob and
plot the L1 / L2 / Judge-IDENTIFIED% headline panels.

---

## 4. Feature Ideation (`feature-ideation`)

**Question.** Does an agent with a `.shadow/` knowledge base propose
*better feature ideas* for a codebase than a baseline agent without one?
"Better" = ideas that overlap with real GitHub feature requests the
community/maintainers actually engaged with **after** the shadow's
build-time snapshot.

### Corpus (Phase 0)

10 SWE-Bench Verified repos pinned at January 2023 commits. 8 are
"dream-eligible" — they carry enough community-validated silver-standard
feature requests to support per-repo recall metrics. `django` (uses
Trac, not GitHub Issues) and `pallets/flask` (only ~33% of recent `main`
commits are PR-driven, violating the "green via PR" assumption) were
dropped from the candidate set entirely.

| Repo | Jan 2023 SHA | Branch | Silver pool LB | Shipped silver | Dream-eligible |
|---|---|---|---:|---:|:---:|
| astropy/astropy | `36ba03588a` | main | 521 | 129 | ✓ |
| matplotlib/matplotlib | `d247979a1e` | main | 719 | 150 | ✓ |
| mwaskom/seaborn | `0ebc82e858` | master | 60 | 3 | ✗ (too few shipped silver) |
| psf/requests | `15585909c3` | main | 61 | 3 | ✗ (too few shipped silver) |
| pydata/xarray | `67d0ee20f6` | main | 453 | 156 | ✓ |
| pylint-dev/pylint | `c70285dfdf` | main | 333 | 64 | ✓ |
| pytest-dev/pytest | `4a46ee8bc9` | main | 341 | 81 | ✓ |
| scikit-learn/scikit-learn | `7b13a8f120` | main | 734 | 154 | ✓ |
| sphinx-doc/sphinx | `ecfd08d325` | master | 327 | 46 | ✓ |
| sympy/sympy | `9f492aa538` | master | 549 | 130 | ✓ |
|  |  |  |  | **916 total** | **8** |

**Silver pool LB**: max of three engagement signals (linked-PR closes,
≥5 comments, ≥3 👍) over GitHub issues created in window
`2023-01-01 → 2025-12-31`. Window pinned for reproducibility and
post-shadow-build to prevent temporal leakage.

**Shipped silver**: LLM-confirmed feature request that was actually
built (closed by a merged PR or has a linked merged PR).

**Engagement filter** (the permissive OR): feature-request AND
(merged-PR-closed OR ≥5 comments OR ≥3 👍).

### Pipeline (6 phases)

| # | Phase | What we did |
|---|---|---|
| 0 | Repo selection | 10 repos locked at Jan 2023 SHAs; 8 dream-eligible |
| 1 | Docker construction | per-repo hermetic images named `shadowfrog-feat/<short>:jan2023`. 10 images, ~14 GB total. |
| 1.5 | Full-test green-up | every image runs its full pytest suite (with documented `--deselect` flags for env-drift issues unrelated to the repo). Result was a green baseline of 90,816 passing tests across the 10 images so dreams don't waste time chasing pre-existing failures. |
| 2 | Issue collection | 13,151 issues across 10 repos pulled via GitHub API for the window; then LLM-classified into feature-request vs. other; filtered to silver (1,603) and shipped silver (916 in the raw pool; 910 made it into per-silver alignment scoring in Phase 5) |
| 3 | Shadow KB build | 957 dream experiments (120 per repo, 117 for xarray) on the 8 eligible repos, yielding 7,731 discoveries across 8,412 shadow files. Per-file coverage 1–20% (intentional — dreams optimize depth, not breadth). |
| 4 | Idea generation | 48 idea-generation runs × 50 ideas = 2,400 ideas (8 repos × 2 arms × 3 seeds). Plus a **third "Human" arm** for Phase 6: each repo's silver-shipped issues are rewritten into the same agent-style schema (title / description / subsystem / files_referenced), yielding **910 Human ideas** — used only in Phase 6 (Lens 2), not in Phase 5 (Lens 1) |
| 5 | Alignment scoring (Lens 1) | a batched silver-anchor judge scores every (idea × shipped-silver) pair on the 1–5 rubric below — agent arms only |
| 6 | Intrinsic eval (Lens 2) | a separate multi-judge per-idea quality eval, arm-blind, across the 3-arm pool (1,200 BL + 1,200 SF + 910 Human = **3,310 ideas**). Re-scored by a **3-judge ensemble** (Opus 4.6 + Opus 4.7 + GPT-5.5) → **9,930 verdicts** |

### Phase 1.5 — Full-test green-up

Every image's full pytest suite was run with JUnit XML parsed for
structured counts. **0 failures, 0 errors** across all 10 images after
applying documented `--deselect` flags for env-drift failures unrelated
to repo behavior. This gives us confidence that dream sessions running
inside these containers won't waste time chasing pre-existing failures
— the agent always sees a "green tree" baseline. Total: **90,816
passing tests** across the 10 images.

### Phase 3 — Dreams

Run on a remote machine (the same one for Phase 4 later). A setup
script extracts the Jan-2023 source from each per-repo docker image
into a per-repo "host dir", installs ShadowFrog skills + hooks, sets
up a bare-clone `origin` so dream branches stay local, and writes the
docker image name into a sentinel file so the start-docker wrapper
knows which image to bind-mount. For each repo, in its host dir, the
knows which image to bind-mount. For each repo, in its host dir, the
operator sources a per-repo wrapper script (starts the
container, writes `.docker-bin/{python,pip,pytest}` wrappers, prepends
them to PATH) and launches the agent non-interactively with
`copilot --allow-all-tools`.

The dream-specific eval context is auto-loaded as `AGENTS.md` and
contains the prompt template:

```
Run 5 /shadow-frog-dream sessions one after another, focus on feature
design (forward-looking ideation: extension points, gaps, friction,
"what would users want next"). In each dream session run at least 12
experiments. Run parallel subagents with batch size of 3, always use
opus 4.6 or stronger models. Between every two dream sessions, run a
/shadow-frog-meditate session if needed to fix lineage. After every 3
dreams, reconcile back to main from this host dir with
`./.github/skills/shadow-frog-dream/dream-reconcile.py --push`.
```

Tune the 5/12 numbers per repo. Network is disabled
(`--network=none`); `python` / `pip` / `pytest` are routed into the
container via `.docker-bin/` wrappers.

### Phase 4 — Idea generation

A one-time, idempotent prep step creates **two sibling host dirs per
repo** — one per arm — so each `(repo, arm, seed)` run gets a clean
container with the correct content:

- `<short>-shadow/` — rsync of the dream host dir from Phase 3, plus
  ShadowFrog skills + hooks installed (so the agent knows how to query
  `.shadow/`).
- `<short>-baseline/` — fresh extract from the original docker image,
  with **no** `.shadow/`, **no** ShadowFrog skills, **no** hooks.

**Hard isolation by image** (locked design decision). The baseline image
has *no* `/repo/.shadow/` at all — not merely an instruction not to read
it. The cleanest way is to extract `/repo` from the image, which never
had `.shadow/`. Eval infrastructure (`AGENTS.md`, `TASK_INFO.json`) is
stripped from both arms so the agent can't tell which arm it's in.

Then for each `(repo, arm, seed)` (8 × 2 × 3 = 48):

1. Spin up a per-run docker container `feat-ideation-<short>-<arm>-s<seed>`,
   bind-mount the host dir at `/repo`.
2. Write `.docker-bin/{python,pip,pytest,bash}` wrappers.
3. Call:

   ```
   copilot -p "$PROMPT" --model claude-opus-4.6 \
       --output-format json --no-custom-instructions --allow-all-tools
   ```

   The prompt is the arm-specific ideation prompt (shadow vs. baseline,
   differing only in whether the agent is told about `.shadow/` and the
   `shadow_anchor` schema field) with `{repo}` and `{repo_short}`
   substituted. Both arms ask for **exactly 50 distinct feature ideas**
   in a strict JSON schema (id, title, description, subsystem,
   files_referenced). The shadow-arm schema additionally includes
   `shadow_anchor`, which the baseline schema omits.
4. Parse the final assistant message: extract the fenced JSON block,
   validate (50 items, distinct ids, fields present).
5. Emit one result file per `(repo, arm, seed)`.

Output schema example:

```json
{
  "repo": "pydata/xarray",
  "arm": "shadow",
  "seed": 0,
  "model": "claude-opus-4.6",
  "n_ideas_requested": 50,
  "n_ideas_returned": 50,
  "wall_seconds": 2389.0,
  "ideas": [
    {"id": 1, "title": "…", "description": "…", "subsystem": "…",
     "files_referenced": [...], "shadow_anchor": "…|null"},
    ...
  ]
}
```

### Phase 5 — Alignment scoring (the headline judge, Lens 1)

For each `(repo, shipped-silver-issue)` we score every candidate idea
across all (arm, seed) runs against that one silver issue using a
**silver-anchor alignment judge**. For every candidate the judge sees
only `{title, description, subsystem}`; `shadow_anchor` and
`files_referenced` are **stripped** before scoring (strict arm-blind).
Only the two agent arms (BL + SF) are scored in Lens 1 — the Human arm
is reserved for Phase 6.

```
Score per candidate: integer 1–5
  5 = exact match (duplicate request)
  4 = strong overlap (same need, slightly different scope)
  3 = adjacent / related (same module, related need)
  2 = weak / tangential (same broad subsystem only)
  1 = unrelated
  — = no Phase 4 idea matched this dream (sentinel for "no judge score")
```

Of the 916 raw shipped silvers, **910** entered alignment scoring.
The 6-silver gap is the seaborn (3) and requests (3) silvers, both
of which were excluded from the dream-eligible set ahead of time
(neither repo received its own dream campaign in the final design).
Batched: every (silver, idea) pair is scored, **5,460 batched judge
calls** total (910 silvers × 6 arm-seed combos), ~16,400 premium
requests, ~6h45m wall on a laptop with `--jobs 8`, yielding
**~272,500 individual alignment scores**.
**The dashboard's Lens 1 sunburst is built from these scores**, colored
by judge rubric level; the "—" sentinel applies to dreams that never
surfaced as an idea in Phase 4 (so the judge never had a chance to
score them).

### Phase 6 — Intrinsic eval (Lens 2)

A separate per-idea quality scoring pass on the full 3-arm pool of
**3,310 ideas (1,200 BL + 1,200 SF + 910 Human)** using an
**intrinsic-quality judge**. The judge is arm-blind: it sees only
`{title, description, subsystem, files_referenced}` of each idea (the
arm and repo identity are stripped before scoring). Lens 2 evaluates
ideas on their own merit rather than against any specific silver
target.

**Rubric — five scored dimensions plus one categorical tag**:

| Dimension | Prompt label | What it scores | Type |
|---|---|---|---|
| **Groundedness** | GROUNDEDNESS | Does the proposal demonstrate project-specific knowledge (real APIs, modules, conventions) vs. plausible-sounding generalities? | 1–5 |
| **Insight** | NON-OBVIOUSNESS | How unlikely is this idea to emerge from a 5-minute brainstorm by a regular contributor? | 1–5 |
| **User Impact** | USER VALUE | How many real users would benefit and how meaningfully? | 1–5 |
| **Spec Clarity** | ACTIONABILITY | Could a maintainer turn this into a PR scope without back-and-forth? | 1–5 |
| **Change Size** | SCOPE | Blast radius of the implied change — how many subsystems / modules / files would it touch? | 1–5 (descriptive, not evaluative) |
| **Change Locality** | Locus of Change | Categorical tag, one of: `api_surface`, `new_feature`, `internal_machinery`, `cross_cutting`, `ecosystem_bridge`, `dx_and_tooling`, `operational_quality` | enum |

The display names (Insight, User Impact, Spec Clarity, Change Size)
are used throughout the dashboard and reports; the bracketed
prompt-label column is the raw token shown to the judge.

The rubric is designed so the four evaluative dimensions
(Groundedness, Insight, User Impact, Spec Clarity) are mutually
orthogonal: a small local idea can be deeply grounded; a sweeping idea
can be ungrounded; insight is independent of size; spec clarity is
independent of impact. The judge prompt's per-dimension anchors
explicitly instruct independent scoring per dimension.

**3-judge ensemble for robustness**: every idea is scored by **Opus
4.6 + Opus 4.7 + GPT-5.5** → **3,310 ideas × 3 judges = 9,930
verdicts**. The Human arm comes in two style variants:
- The original GitHub-issue title + body, lightly normalized into the
  JSON schema (the "Human" arm proper).
- The same issue **rewritten** by an LLM into the agent register
  (title ≤120 chars, 1–3 sentence description, subsystem, 1–5 file
  refs) — the "Style Control" variant. This lets the dashboard
  separate the "register effect" (humans write differently from the
  agent prompt format) from the "content effect" (whether the
  underlying idea is better).

### Pipeline summary

End-to-end the experiment is six phases: (0) pick 10 repos, lock at
Jan-2023 SHAs, identify 8 dream-eligible; (1) build per-repo Docker
images and (1.5) run their full pytest suites until green; (2) pull
13,151 issues from GitHub for the window and LLM-classify into
silver / shipped silver; (3) run 957 dream experiments on the 8
eligible repos to build a shadow KB per repo; (4) generate 50 ideas
per (repo, arm, seed) for both SF and BL arms (48 runs, 2,400 ideas),
plus a 910-idea Human arm (raw + style-controlled rewrites) for Lens
2; (5) score every (idea × shipped-silver) pair with the alignment
judge (Lens 1, ~272K scores); (6) score every idea on the 5-dimension
intrinsic-quality rubric with a 3-judge ensemble (Lens 2, 9,930
verdicts).

---

## 5. Navigation (`navigation`)

**Question.** Does shadow knowledge help an agent *navigate* a large
codebase (find the right symbol fast under a tool-call budget)? This
underpins all four bug/ideation experiments — if shadow doesn't help
navigation, it can't help anything downstream.

### Corpora (pinned)

```
fastapi   pinned_tag: 0.136.1   pinned_sha: e54e5a89  source_root: fastapi/   (~360  callable symbols)
django    pinned_tag: 5.1.4     pinned_sha: 2d4add11  source_root: django/   (10,546 callable symbols)
```

### Conditions

Three conditions, each lives in its own worktree with its own shadow tree:

| External name | Internal name | Layout |
|---|---|---|
| `shadow-frog` | `mirror-nested` | per-file shadow mirroring the source tree (canonical ShadowFrog) |
| `flat-shadow` | `symbol-keyed-flat-giant` | one big `.shadow/SHADOW.md` with `## file::symbol` (ablation: same content, no spatial structure) |
| `no-shadow` | `no-KB` | empty: agent runs on the raw source tree with no shadow installed |

Internal names are baked into 60K+ data records and worktree paths;
display names appear in the dashboard only. The `flat-shadow` condition
ships a single concatenated `.shadow/SHADOW.md` (built at the
build-shadows stage) instead of the per-file mirror, paired with
prompt-level guidance that points the agent at the flat file rather
than per-file shadow paths.

### Axes (full sweep)

| Axis | Values |
|---|---|
| Corpus | `django`, `fastapi` |
| Condition | `shadow-frog`, `flat-shadow`, `no-shadow` |
| Scale (needles per cell) | small=50, medium=400, large=2,500, xlarge=10,000 (django only), xxlarge=50,000 configured / ~35,000 delivered (django only) |
| Wrong-needle rate | 0.00, 0.15, 0.30, 0.50 |
| Tool-call budget | 1, 2, 4, 8, 16, 32, `inf` |
| Task type | `path_known` (60 base + cross-scale extension: 23 for django → 83 total; 22 for fastapi → 82 total), `path_unknown` (same shape — 83 django / 82 fastapi) |

xlarge/xxlarge are django-only because fastapi's source has only ~360
callable symbols — it cannot produce 10K+ needles. xxlarge targets
50,000 needles in the config but actually delivers ~35,000 (capped by
the seedable-symbol pool × wrong-rate fan-out). Total **69,342 agent
runs judged** across the full matrix.

### Hypotheses (pre-registered, locked before any runs)

| ID | Statement | Threshold |
|---|---|---|
| **H8 — Budget collapse** | At `large × path_unknown × wrong=0`, dropping budget from `inf` to `1` reduces recall by ≥30 pp on both corpora | `recall(inf) − recall(1) ≥ 30 pp` per corpus |
| **H10 — Corpus generality** | At `large × path_unknown × wrong=0 × budget=inf`, `shadow-frog` recall exceeds `no-shadow` recall by ≥30 pp on both corpora | `recall(shadow-frog) − recall(no-shadow) ≥ 30 pp` per corpus¹ |
| **H11 — Multi-MB shadow scaling** | At `xxlarge × path_unknown × wrong=0 × budget=inf` (shadow ~6 MB), `shadow-frog` recall exceeds `flat-shadow` recall by ≥10 pp on django | `recall(shadow-frog) − recall(flat-shadow) ≥ 10 pp`¹ |

¹ Thresholds shown are the pre-registered values. The
operationalized scorer applies a common
20 pp cutoff for both H10 and H11 when stamping PASS/FAIL into
the hypothesis-verdicts output; reproducers running the scorer should
expect to see the 20 pp formulation in the output.

xlarge serves as a secondary observation (no hard threshold). fastapi is
excluded from H11 because its corpus is too small. Pass/fail is
determined automatically by the aggregator.

**Wrong-needle probe** (methodology, not a hypothesis). A small fraction
of needles is replaced by plausible-but-wrong twins (`wrong_rate` ∈ {0.15,
0.30, 0.50}). The agent doesn't know which; the judge does. If the
agent reports a wrong-but-shadow-planted fact, that's positive evidence
the agent is **actually reading the shadow** rather than recalling
training data of these open-source codebases. Without this probe, high
recall could in principle reflect memorized knowledge of
django/fastapi internals.

### Pipeline (16 stages, idempotent, strict dataflow order)

Stages 01–08 build the **needles** (the questions and the
shadow-content the agent should be able to find them through). Stages
09–10 build the **shadows and worktrees** (one cell per
condition × scale × wrong-rate combination). Stages 11–13 run, judge,
and aggregate. Stages 14–16 produce the dashboard.

| # | Stage | Reads | Writes |
|---|---|---|---|
| 01 | Clone corpus | corpora config | corpus clone, records the pinned SHA |
| 02 | Enumerate symbols | corpus source tree | per-corpus symbol index (jsonl) |
| 03 | Subsample symbols | symbols | seeded subsample (stratified for large corpora) |
| 04 | Author needles | symbols + author prompt | raw needles jsonl |
| 05 | Finalize needles | raw needles + scales | final needles jsonl (tagged per scale, strict-superset invariant) |
| 06 | Perturb needles | final needles + perturbation prompt | wrong-twin needles jsonl (plausible-but-false) |
| 07 | Generate base tasks | needles + path-known / path-unknown templates + needle-to-question prompt | `path_known` (pk-001..060) and `path_unknown` (pu-001..060) task jsonl |
| 08 | Cross-scale tasks | same inputs as 07 | extended task ids (pk-101+, pu-101+) |
| 09 | Build shadows | final + wrong needles + config | per-cell `.shadow/...` tree + manifest, hashed by `build_seed` |
| 10 | Setup worktrees | corpus + shadows + `install.sh` | one worktree per (corpus × condition × scale × wrong_rate) cell |
| 11 | Run agent | tasks + worktrees | per-run result jsonl + a SQLite run manifest |
| 12 | Judge recall | results + recall-judge prompt + shadow manifests | per-cell judgment json |
| 13 | Aggregate | judgments + run manifest | summary CSVs, headline metrics, hypothesis verdicts |
| 14 | Plot interaction | summaries | diagnostic PNGs |
| 15 | Render dashboard | metrics + verdicts | dashboard fragment HTML |
| 16 | Splice dashboard | dashboard fragment | the final `eval/results_dashboard.html` (the fragment is spliced into the §1 section between marker comments) |

Stages 09 → 16 are orchestrated as a single pipeline runner with a
`K`-way parallelism knob (default 16).

### Cell schema

```
<corpus>/<condition>/<scale>/wrong_<rate>/budget_<budget>/<task_type>/<task_id>
```

Budget is **not** a worktree key — worktrees are keyed on the first
four dims only; budget is enforced at agent invocation via prompt
augmentation plus harness-side hard interrupt.

### Orchestrator config (verbatim)

```yaml
agent:
  model: claude-opus-4.6
  max_turns: 50
  reasoning_effort: medium

judge:
  model: claude-opus-4.6
  ensemble: false                  # single-judge; per-claim verdicts add internal richness
  emit_per_claim: true             # judge must classify each agent claim individually

needle_author:
  model: claude-opus-4.6
  over_generation_target: 3000     # author over-generates, finalizer trims

needle_perturber:
  model: claude-opus-4.6
  plausibility_max_token_delta: 0.50
  plausibility_require_in_file_identifiers: true

needle_to_question:
  model: claude-opus-4.6

qa:
  sample_fraction: 0.05
  random_seed: 1729
  perturbation_qa_count_per_corpus: 50

orchestrator:
  parallelism: 8
  max_attempts_per_task: 3
  per_task_backoff_seconds: [10, 60, 300]
  rate_limit_window_seconds: 60
  rate_limit_trip_threshold: 3
  pool_pause_initial_seconds: 300
  pool_pause_max_seconds: 3600
  heartbeat_interval_seconds: 60
  per_task_timeout_seconds: 1500
  copilot_extra_args:
    - "--allow-all"
    - "--output-format"
    - "json"
    - "--no-remote"
    - "--no-custom-instructions"
  rate_limit_stderr_patterns:
    - "429"
    - "rate limit"
    - "rate_limit"
    - "quota_exceeded"
    - "RESOURCE_EXHAUSTED"
    - "too many requests"
```

Pre-registered constants: `random_seed: 1729` plus a `build_seed`
("shadowfrog-v2") baked into the shadow-tree hash at the perturb-needles
and build-shadows stages — changing it invalidates the entire 60K-cell
dataset. `build_seed` is hardcoded in those stages rather than surfaced
in the orchestrator config, by design
(it should not be edited casually).

### Pipeline summary

The pipeline runs from a single config file (the YAML above) plus a
set of prompts (authoring, needle-to-question, perturbation, recall
judge, QA, and signature rewrite). Each corpus passes through:
clone → enumerate symbols → seeded subsample → author needles →
finalize (per-scale tagging, strict-superset invariant) → perturb
into plausible-but-false wrong-twin needles → generate `path_known`
and `path_unknown` task variants → build per-cell shadow KBs and
worktrees → run the agent under each cell → judge recall claim-by-claim →
aggregate per-cell summaries → render the dashboard fragment and splice
it into `eval/results_dashboard.html`. The full 60K-cell sweep is
reproducible from the YAML, the corpus SHAs, and the pre-registered
seeds.

---

## What this folder contains

| File | What it is |
|---|---|
| `results_dashboard.html` | The single self-contained results dashboard rolling up every experiment (Lens 1 sunburst inlined via `<iframe srcdoc>`). |
| `README.md` (this document) | Methodology-only summary. For every experiment: corpus, environment, pipeline description, prompts (with verbatim wording for the key ones), scoring rubric, design decisions. |

The raw outputs of the evaluation — per-task analysis, patches,
dreams, judge verdicts, logs, manifests, shadow KBs, executable
scripts, Dockerfiles, per-arm operator runbooks, and the curated
reports — are kept outside this repo in a private archive.
