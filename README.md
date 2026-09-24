# ShadowFrog

ShadowFrog gives coding agents a **shadow knowledge base** for any codebase:
a file-backed memory of tacit codebase knowledge learned from code reading,
experiments, and conversations with you.

A **shadow** mirrors your source tree under `.shadow/`, storing discoveries in
symbol-organized Markdown files. Lookup is **index-free**: agents follow source
paths and `file::symbol` references rather than a vector index or embedding
service.

It records knowledge that is hard to recover from source alone: which refactor
breaks downstream callers, which invariant the tests never exercise, or which
"obvious" cleanup removes a production workaround. The code tells you *what
runs*. The shadow tells future agents *what has been learned about how it
behaves*.

Read the launch blog post: [Shadow-Frog: Coding Agents that Dream and
Discover](https://microsoft.github.io/debug-gym/blog/2026/06/shadow-frog/).

<p align="center">
  <img src="shadow_repo.png" alt="Shadow repository structure" width="320" />
</p>

---

## Quick Start

Install **per repository**, not globally. You need Git, Python 3, either GitHub
Copilot CLI or Claude Code, and a Git repository as the target project.
Hooks are limited to projects you explicitly opt into.

### 1. Install

From a checkout of ShadowFrog, choose one agent:

```bash
cd /path/to/ShadowFrog
./install.sh --project /path/to/your-repo                 # Copilot CLI (default)
# ./install.sh --agent claude --project /path/to/your-repo  # Claude Code
```

On **Windows**, use the PowerShell installer, which does not require Bash:

```powershell
cd C:\path\to\ShadowFrog
.\install.ps1 -Project C:\path\to\your-repo                 # Copilot CLI (default)
# .\install.ps1 -Agent claude -Project C:\path\to\your-repo  # Claude Code
```

### 2. Share the setup

For a shared setup, commit and push the installed files in the target repo.
The installer prints the exact staging paths for your selected components:

```bash
cd /path/to/your-repo
# Stage the files for the agent you installed:
git add .github/skills/ .github/hooks/ .github/copilot-instructions.md
# git add .claude/skills/ .claude/hooks/ .claude/settings.json CLAUDE.md
git commit -m "Add ShadowFrog skills, hooks, and context"
git push
```

Local-only use does not require a writable remote; Dream does.

### 3. Initialize

To start collecting shadow knowledge, open the target repo in your agent
session and run the following. Nap-only ideation can skip this step.

```
/shadow-frog-init
```

This creates symbol-organized templates in `.shadow/`. Choose how to store it:

| Mode | Effect |
|------|--------|
| **Committed** | Team-shared knowledge; required for Dream |
| **Gitignored** | Local-only knowledge; update, meditate, viewer, and Nap remain available, but Dream is disabled |

If you chose committed storage, commit and push `.shadow/` after initialization.
For installed paths and optional components, see [Installation options](#installation-options).

---

## Everyday Workflow

Use the skill that matches your goal. Each link contains its full workflow,
helper commands, and format definitions.

| Command | When to use |
|---------|-------------|
| [`/shadow-frog`](skills/shadow-frog/SKILL.md) | Consult relevant knowledge before editing or investigating code |
| [`/shadow-frog-init`](skills/shadow-frog-init/SKILL.md) | Create the shadow once per repo |
| [`/shadow-frog-update`](skills/shadow-frog-update/SKILL.md) | Refresh after code changes and capture session insights |
| [`/shadow-frog-dream`](skills/shadow-frog-dream/SKILL.md) | Run autonomous experiments while you're away |
| [`/shadow-frog-nap`](skills/shadow-frog-nap/SKILL.md) | Generate reviewed feature-task briefs without implementing them |
| [`/shadow-frog-meditate`](skills/shadow-frog-meditate/SKILL.md) | Merge duplicates and resolve conflicting discoveries |
| [`/shadow-frog-viewer`](skills/shadow-frog-viewer/SKILL.md) | Browse, search, inspect lineage, and audit structural integrity |

As you work, the agent captures your code context as `source: user` and
collaborative findings as `source: interaction`. After commits, the pre-tool
hook can detect a shadow behind HEAD and remind the agent to run
`/shadow-frog-update`; the hook does not run the update itself. Meditate
consolidates accumulated knowledge and escalates unresolved conflicts to you.

For example, use Viewer to find relevant knowledge or audit its structure:

```
/shadow-frog-viewer --search "auth"
/shadow-frog-viewer --symbol src/auth.py::login
/shadow-frog-viewer --top src/auth.py
/shadow-frog-viewer --check-invariants
```

The [Viewer reference](skills/shadow-frog-viewer/SKILL.md) also covers summaries,
recent discoveries, label filters, preferences, and interactive dream-lineage HTML.

Knowledge retrieval is bounded and pageable, with IDs for expanding individual
claims. A single local `citation_score` counts helper exposures, not proven
usefulness; relevance and trust outrank popularity. Scores are updated safely
across local Git worktrees without editing shadow Markdown or requiring a vector
index. New claims start at zero. See the Viewer reference for retries, local
storage, and the limits of this signal. Long-entry continuation counts as one
logical read and rejects changed content; local retry and pagination metadata
have retention limits. Compact hook hints are not a substitute for a file review.

---

## Choose Dream or Nap

| | Dream | Nap |
|---|---|---|
| Goal | Learn through implemented experiments | Develop source-grounded feature/task proposals |
| Output | Runnable experiment branches and discoveries | Reviewed task briefs and a persistent proposal tree |
| Continuation | Inherit code and shadow from an ancestor branch | Revise hypothetical designs over a pinned code baseline |
| Implementation | Write and run real code in isolated worktrees | No feature code or prototypes; optional probes inspect existing behavior |
| Prerequisites | Initialized, git-tracked shadow and writable remote | A Git repository with a commit; no remote or initialized shadow required |

### Dream: learn by doing

Experiments persist as `dream/<namespace>/<id>` branches. Future dreams can
continue a previous experiment, inheriting its code and shadow rather than
sibling branches. Reconciliation accumulates discoveries and experiment
reports on the default branch.

**Before running Dream, commit and push `.shadow/` and configure a remote
that permits pushing `dream/...` branches and reconciled shadow updates.**
Gitignored shadows cannot use Dream. Experiment code is **not merged
automatically**; adopting it into the project is a manual curation step.

See the [Dream workflow](skills/shadow-frog-dream/SKILL.md) for execution,
tooling snapshots, reconciliation, and safe cleanup.

### Nap: plan without implementing

Nap grows a resumable proposal tree and submits shortlisted paths to a strong
independent judge. Exported tasks require a current accepted judgment and
describe the complete change from a real code baseline, not assumed parent APIs.
**Planning approval is not runtime validation.** The host runs the judge;
the Python helper manages records and cannot authenticate reviewer identities.

Default limits are **7 recorded nodes, 2 probes, 2 judge batches, and 2 selected
tasks**, with no default depth cap. These configurable ceilings are not quotas:
recorded rejections and failed probes count. They do not cap actual API spending.
Keep proposals outside `.shadow/`, or in an initialized `.shadow/_meta/naps/`;
they are not verified discoveries.

Exports can be detailed **planning briefs** or concise **implementation
handoffs**, with the same active requirements. Binding constraints are separate
from design suggestions; nonblocking implementation risks are separate from
questions that prevent planning approval.

See the [Nap workflow and helper reference](skills/shadow-frog-nap/SKILL.md)
for tree operations, review receipts, and exports.

### Coherent parent-child exploration

Dream and Nap default to `mode=broad`. Use `mode=coherent` to require a
meaningful connection along each parent-child edge:

```
/shadow-frog-dream mode=coherent
/shadow-frog-nap mode=coherent
```

Children can extend, integrate, challenge, replace, simplify, or offer an
alternative to their parent. Each has its own goal; siblings can pursue
different directions, including on the same files. There is no fixed
tree-wide goal or diversity quota.

Dream descendants wait for their implemented parent; coherent branches and
their ancestors are retained as reproducible baselines until explicit curation.
Nap compounds ideas, not implemented APIs. A combined task follows a
root-to-leaf path and preserves the final active requirements, rather than
stacking unrelated siblings or requiring both discarded and replacement designs.
Combining sibling work requires an explicit integration experiment or proposal.
Structural validation alone cannot establish semantic coherence or feasibility.

---

## How Discoveries Work

For example, knowledge about `src/auth.py` lives at `.shadow/src/auth.py.md`;
locations such as `src/auth.py::login` identify the relevant symbol.
Cross-file discoveries live once in `_cross/`, with links from the involved
per-file shadows.

```
your-repo/
  src/auth.py
  .shadow/
    src/auth.py.md     file- and symbol-level discoveries
    _cross/           cross-cutting discoveries
    _prefs.md         project-wide preferences
    _dreams/          experiment reports, manifests, and patches
    _index.md         file inventory and counts
    _meta/state.json  update state
    .shadowignore     gitignore-style exclusions
```

Store **behavioral discoveries**, not API descriptions or chat transcripts.
For example:

**Agent exploration**:
```markdown
- authenticate_user() silently returns None on expired tokens
  instead of raising. 3 of 7 callers don't check the return value.
  _(verified, source: exploration, labels: [bug])_
```

**User knowledge**:
```markdown
- The retry logic here took 3 iterations to get right -- it handles
  a subtle race condition during rolling deployments. Do not simplify.
  _(verified, source: user)_
```

**Collaborative work**:
```markdown
- While debugging issue #42, discovered that process_batch() silently
  drops items exceeding 1MB -- logged at DEBUG level only.
  _(verified, source: interaction)_
```

| Property | Values | Meaning |
|----------|--------|---------|
| **Status** | `verified` / `uncertain` / `refuted` | Has the claim been confirmed? |
| **Source** | `exploration` / `user` / `interaction` | Where did this knowledge come from? |
| **Labels** | `bug`, `performance`, `security`, `feature-gap`, `tech-debt` | Optional; marks actionable discoveries |

### Trust Hierarchy

| Rank | Source | Trust |
|------|--------|-------|
| 1 | `source: user` | Highest; human stated it. Always verified. |
| 2 | `source: interaction` | Emerged from collaborative work. Always verified. |
| 3 | `verified, source: exploration` | Agent confirmed via code analysis or tests. |
| 4 | `uncertain` | Plausible but unconfirmed. |
| 5 | `refuted` | Known wrong; skip. |

See the [worked coupon example](examples/coupon-demo/README.md) for a real
shadow, and the [core skill](skills/shadow-frog/SKILL.md) for the canonical
formats and reference rules.

---

## Installation Options

The installer copies readable skill instructions, their helper scripts, hooks,
and agent context. It targets one agent's conventions at a time:

| Agent | Skills | Hooks | Context |
|-------|--------|-------|---------|
| `copilot` (default) | `.github/skills/` | `.github/hooks/hooks.json` | `.github/copilot-instructions.md` |
| `claude` | `.claude/skills/` | `.claude/settings.json` | `CLAUDE.md` |

Use `--no-hooks` or `--no-context` (`-NoHooks` / `-NoContext` in PowerShell) to
skip individual components. On Windows, `py -3` can be used when invoking
Python helpers. Full helper usage is in the linked skill references above.

---

## Contributing

| Path | Purpose |
|------|---------|
| `skills/` | The seven skills and their helper scripts |
| `hook-templates/` | Agent hook configs and shared scripts |
| `examples/coupon-demo/` | Worked example with a real `.shadow/` |
| `eval/` | [Evaluation methodology](eval/README.md) and [results dashboard](eval/results_dashboard.html) |
| `tests/` | Installer, hook, and helper regression coverage |

Install development dependencies and run the suite:

```bash
pip install -r requirements-dev.txt
python3 -m pytest
```

Tests use temporary shadow trees and Git repositories. Their layout mirrors
the source: `tests/skills/shadow_frog_viewer/` covers
`skills/shadow-frog-viewer/`, for example.

---

## Responsible AI

ShadowFrog is a research project. Before using it, please review our
[Responsible AI transparency note](RESPONSIBLE_AI.md), which covers intended
uses, out-of-scope uses, evaluation, limitations, and best practices.

---

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.

---

<p align="center">
  <img src="froggy_logo.png" alt="Froggy team logo" width="100" />
  <br />
  Built by the <a href="https://aka.ms/froggy-team">Froggy team</a>
</p>
