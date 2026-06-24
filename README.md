# ShadowFrog

ShadowFrog gives coding agents a **shadow knowledge base** for any codebase:
a file-backed memory of tacit codebase knowledge learned from code reading,
experiments, and conversations with you.

Most agent memory preserves what happened in past chats. ShadowFrog is built
for **tacit knowledge** that is hard to recover from chat history or source
alone: which refactor breaks downstream callers, which invariant the tests
never exercise, which "obvious" cleanup removes a production workaround, or
which cross-file edge case is easy to miss. The code tells you *what runs*. The
shadow tells future agents *what has been learned about how it behaves*.

Read the launch blog post: [Shadow-Frog: Coding Agents that Dream and
Discover](https://microsoft.github.io/debug-gym/blog/2026/06/shadow-frog/).

<p align="center">
  <img src="shadow_repo.png" alt="Shadow repository structure" width="320" />
</p>

---

## Quick Start

```bash
# From your ShadowFrog checkout, install into the repo you want agents to remember.
cd /path/to/ShadowFrog
# Choose one:
./install.sh --project /path/to/your-repo                 # Copilot CLI (default)
# ./install.sh --agent claude --project /path/to/your-repo  # Claude Code
```

On **Windows**, use the PowerShell installer instead (no `bash`/`python3` needed):

```powershell
cd C:\path\to\ShadowFrog
.\install.ps1 -Project C:\path\to\your-repo                 # Copilot CLI (default)
# .\install.ps1 -Agent claude -Project C:\path\to\your-repo  # Claude Code
```

Commit the installed files in the target repo:

```bash
cd /path/to/your-repo
# Stage the files for the agent you installed:
git add .github/skills/ .github/hooks/ .github/copilot-instructions.md
# git add .claude/skills/ .claude/hooks/ .claude/settings.json CLAUDE.md
git commit -m "Add ShadowFrog skills, hooks, and context"
git push
```

Then open that repo in your AI agent session:

| Command | Use |
|---------|-----|
| `/shadow-frog-init` | Create the shadow |
| `/shadow-frog-update` | Refresh after code changes |
| `/shadow-frog-dream` | Explore and experiment while you're AFK |
| `/shadow-frog-meditate` | Deduplicate and resolve conflicts |
| `/shadow-frog-viewer` | Browse what's in the shadow |

> See [Installation](#installation) for full details.

---

## What is a Shadow?

A shadow is a `.shadow/` directory that mirrors your source tree with markdown
files. It is not generated API documentation and it is not a transcript store.
It stores **discoveries**: behavioral facts, edge cases, implicit contracts,
warnings, and cross-file interactions that are useful to future agents.

The retrieval design is **index-free** in the same sense that source-code
navigation is index-free: the codebase itself tells the agent where to look.
If an agent is editing `src/auth.py`, the corresponding knowledge lives at
`.shadow/src/auth.py.md`; if it is reasoning about `src/auth.py::login`, the
same shadow file contains the symbol-level section. Cross-file discoveries use
`file::symbol` back-pointers in `.shadow/_cross/`. No vector store, embedding
database, or separate retrieval service is required for this lookup path.

```
source code + user context + dream experiments
                 |
                 v
       shadow-frog skills (init, update, dream)
                 |
                 v
 .shadow/  (per-file discoveries, cross-cutting notes, prefs, dreams)
                 |
                 v
 future agent sessions  (hooks, viewer, ordinary cat/grep)
```

The `.shadow/` layout mirrors the repo:

```
your-repo/
  src/
    auth.py
    db/models.py
  .shadow/
    .shadowignore                      gitignore-syntax excludes
    _index.md                          file list with discovery counts
    _prefs.md                          project-wide user preferences
    _cross/                            cross-cutting discoveries (span multiple files)
      token-expiry-config-split.md
    _meta/
      state.json                       tracking state
    _dreams/                           experiment archive (reports + branch metadata)
      _index.md                        table of all experiments (branch, parent, tip)
      20250115-143012Z-retry-logic/    one folder per experiment (mirrored from branch)
        report.md                      structured report with YAML frontmatter
        patch.diff                     code-only diff (excludes .shadow/)
        manifest.json                  machine-readable discovery manifest
    src/
      auth.py.md                       discoveries about auth.py
      db/
        models.py.md                   discoveries about models.py
```

Discoveries come from three sources:

**Agent exploration**: the agent reads code and runs experiments:
```markdown
- authenticate_user() silently returns None on expired tokens
  instead of raising. 3 of 7 callers don't check the return value.
  _(verified, source: exploration, labels: [bug])_
```

**User knowledge**: things you tell the agent during conversation:
```markdown
- The retry logic here took 3 iterations to get right -- it handles
  a subtle race condition during rolling deployments. Do not simplify.
  _(verified, source: user)_
```

**Collaborative work**: insights from debugging, refactoring, etc.:
```markdown
- While debugging issue #42, discovered that process_batch() silently
  drops items exceeding 1MB -- logged at DEBUG level only.
  _(verified, source: interaction)_
```

Good discoveries are claims a future agent can act on, not summaries of what a
function is named. Prefer "silently returns `None` on expired tokens" over
"handles token expiration."

---

## Skills

| Skill | What it does | When to use |
|-------|-------------|-------------|
| **shadow-frog** | Reference docs for the shadow format and conventions | Use when working in a repo with `.shadow/` |
| **shadow-frog-init** | Creates `.shadow/` with structural templates for every file | Once per repo |
| **shadow-frog-update** | Refreshes shadows after code changes; captures conversational knowledge | After commits, or when you share context |
| **shadow-frog-dream** | Autonomous exploration AND experimentation while you're AFK | Before lunch, overnight, weekends |
| **shadow-frog-meditate** | Deduplicates, merges, and resolves conflicting discoveries | Periodically, to keep the shadow clean |
| **shadow-frog-viewer** | Browse and query the shadow: overview, search, top-discoveries-per-file, recent, preferences, labels, dream lineage, structural invariant check | When you want to see what's in the shadow, or audit its integrity |

**Design note:** skills are readable instructions backed by small helper
scripts for deterministic work: initializing shadows, managing dream
worktrees, validating artifacts, reconciling branches, repairing structure,
and rendering viewer outputs. The installer copies both into the target repo.

### The Dream Skill

Dream is ShadowFrog's active-discovery mode. Each dream is an **experiment**:
the agent implements real code, runs it, and persists the result as a **named
git branch** pushed to a remote, typically your fork. The experiment branch is
live, runnable code, but the primary output is the knowledge distilled into
`.shadow/`.

- **Branch-based persistence**: each experiment becomes a `dream/<namespace>/<id>` branch
- **Natural compounding**: future dreams branch from prior dream branches,
  inheriting code + shadow from the ancestor chain
- **Shadow follows lineage**: each branch has its ancestor's shadow, not
  sibling branches. The default branch accumulates all discoveries during
  reconciliation.

The experiment mode can surface tacit knowledge that was not already recorded
in the shadow. While you're away, the agent might try adding retry logic,
parallelizing a pipeline, or refactoring auth into middleware, then distill
what worked, what broke, and why into the shadow.

**Compounding dreams**: Every experiment saves a report and branch to the
remote. Future dreams read past reports and can branch from prior experiment
branches, continuing partially useful work, avoiding dead ends, and chaining
discoveries across sessions. Dream #3 can branch from dream #1's code and pick
up where it left off.

> Selecting which dream experiments to submit upstream is a manual
> curation step. The dream skill embeds a brief "Curating Dream
> Experiments for Upstream PRs" cheat sheet covering the maintainer
> test, devil's-advocate framing, and the 70% rejection heuristic.

---

## Installation

ShadowFrog supports both **GitHub Copilot CLI** (the default) and **Claude
Code**. The installer targets one agent's conventions at a time via
`--agent`:

| Agent | Skills | Hooks | Context |
|-------|--------|-------|---------|
| `copilot` (default) | `.github/skills/` | `.github/hooks/hooks.json` | `.github/copilot-instructions.md` |
| `claude` | `.claude/skills/` | `.claude/settings.json` | `CLAUDE.md` |

### Install into your repo

ShadowFrog installs **into a specific repository**. It is never installed
globally. This is deliberate: its shadow-edit hooks should only fire inside
projects you have opted in, and a per-repo install is what enables both local
agent use and fork-based [dream experiments](#4-dream).

Prerequisites:

- `git` and `python3`
- GitHub Copilot CLI or Claude Code
- A git repository as the target project
- For `/shadow-frog-dream`: a pushable fork/remote and a git-tracked `.shadow/`

```bash
cd /path/to/ShadowFrog
# Choose one:
./install.sh --project /path/to/your-repo                 # Copilot CLI (default)
# ./install.sh --agent claude --project /path/to/your-repo  # Claude Code
```

On **Windows**, run the PowerShell equivalent (same flags, PowerShell style):

```powershell
cd C:\path\to\ShadowFrog
.\install.ps1 -Project C:\path\to\your-repo                 # Copilot CLI (default)
# .\install.ps1 -Agent claude -Project C:\path\to\your-repo  # Claude Code
```

This installs skills, hooks, and agent-context all at once.
Use `--no-hooks` or `--no-context` (`-NoHooks` / `-NoContext` in PowerShell) to skip individual components.

**After installing**, commit and push so future agent sessions find the skills.
The installer prints the exact `git add` paths for your chosen agent. For
Copilot CLI:

```bash
cd your-repo
git add .github/skills/ .github/hooks/ .github/copilot-instructions.md
git commit -m "Add ShadowFrog skills, hooks, and context"
git push
```

For Claude Code, stage `.claude/skills/`, `.claude/hooks/`,
`.claude/settings.json`, and `CLAUDE.md` instead.

---

## Usage

### 1. Initialize

Open your project in Copilot CLI or Claude Code and run:

```
/shadow-frog-init
```

This scans the codebase, extracts symbols (functions, classes, constants), and
creates `.shadow/` with a template for every file.

After init, choose how `.shadow/` should live:

| Mode | Use when | Tradeoff |
|------|----------|----------|
| **Committed** | You want team-shared memory and `/shadow-frog-dream` | Best for compounding knowledge; `.shadow/` travels through git |
| **Gitignored** | You want local-only notes | Update, meditate, and viewer still work; dream is disabled |

If you plan to run `/shadow-frog-dream`, commit `.shadow/` after init.

### 2. Work Normally

As you code and talk to the agent, ShadowFrog gives the agent places to record
what would otherwise be lost:

- **You share context** ("don't touch the retry logic, it's subtle") → agent writes it as a `source: user` discovery
- **You debug together** → agent captures insights as `source: interaction`
- **You commit** → before the next mutating agent action, the `preToolUse` hook notices the shadow is behind HEAD (compares `state.json::last_commit` to current HEAD) and reminds the agent to run `/shadow-frog-update`

### 3. Update

After significant changes:

```
/shadow-frog-update
```

Detects what changed via git diff, updates symbol headings, checks if existing
discoveries still hold, and captures any unrecorded session knowledge.

### 4. Dream

Going AFK? Let the agent work while you're gone:

```
/shadow-frog-dream
```

The agent explores uncovered code areas, runs experiments in isolated
worktrees, pushes results as persistent dream branches, and writes what it
learns into the shadow. When you return, the shadow is richer and experiment
code is accessible on named branches.

> **Requires a git-tracked `.shadow/`.** Dream pushes shadow updates through
> git, so if you chose "local only" (gitignored `.shadow/`) during init, dream
> is disabled. `dream-setup.sh` will tell you. The other skills (update,
> meditate, viewer) work either way.

#### Fork-Based Workflow

For dreaming on external repos, fork the target repo first:

1. Fork the repo on GitHub
2. Clone your fork locally
3. Install ShadowFrog: `./install.sh --project /path/to/fork`
4. Init shadow: invoke `/shadow-frog-init`
5. Dream: invoke `/shadow-frog-dream`

Dream branches are pushed to the fork, keeping the original repo clean.

### 5. Meditate

Shadow getting noisy? Clean it up:

```
/shadow-frog-meditate
```

Scans for duplicate discoveries, merges near-duplicates, and resolves
conflicting claims. Escalates hard conflicts to you.

### 6. Browse

Want to see what's in the shadow?

```
/shadow-frog-viewer --summary                  # counts + per-file breakdown
/shadow-frog-viewer --search "auth"            # keyword search across all discoveries
/shadow-frog-viewer --recent 5                 # most recent discoveries
/shadow-frog-viewer --top src/auth.py          # top actionable discoveries for one file
/shadow-frog-viewer --labels bug,security      # filter actionable discoveries by label
/shadow-frog-viewer --prefs                    # all `_prefs.md` entries (project-wide)
/shadow-frog-viewer --check-invariants         # audit structural integrity
```

Dream experiments are listed in `.shadow/_dreams/_index.md` (dream_id,
category, verdict, title, branch, parent, tip_commit). To render an
interactive view of the dream-branch tree (chains, fresh, and full-tree
tabs), run the bundled script directly:

```
python3 .github/skills/shadow-frog-viewer/dream-lineage.py -o lineage.html
# (Claude Code: .claude/skills/shadow-frog-viewer/dream-lineage.py)
```

---

## How Discoveries Work

Each discovery is anchored to a file or symbol and has these properties:

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

### Searching the Shadow

```bash
cat .shadow/src/auth.py.md                                    # read a file's shadow
cat .shadow/_prefs.md                                         # project-wide preferences
grep -rl "src/auth.py::" .shadow/_cross/                       # cross-cutting discoveries
grep -rl "error.handling\|exception" .shadow/ --include="*.md" # search by topic
grep -r "source: user" .shadow/ --include="*.md"               # all user knowledge
```

---

## Repository Structure

For contributors, the main directories are:

| Path | Purpose |
|------|---------|
| `skills/` | The six ShadowFrog skills and their helper scripts |
| `hook-templates/` | Copilot CLI and Claude Code hook configs plus shared hook scripts |
| `examples/coupon-demo/` | Tiny worked example with a real `.shadow/` |
| `eval/` | Evaluation methodology and results dashboard |
| `tests/` | Pytest suite for installer behavior, hooks, and skill helpers |

---

## Tests

ShadowFrog ships with a comprehensive test suite: **1,063 tests, 76% line
coverage with the declared dev dependencies, and no mocked helper layers**.
Tests exercise the real Python scripts and shell hooks against temporary
shadow trees and git repositories.

Run the suite locally:

```bash
pip install -r requirements-dev.txt  # pytest, pytest-cov, pathspec
python3 -m pytest                    # all 1,063 tests
python3 -m pytest tests/skills/      # just the skill-script tests
python3 -m pytest -k viewer          # everything matching "viewer"
python3 -m pytest --cov=skills       # coverage report
```

Test layout mirrors the source layout: `tests/skills/shadow_frog_viewer/`
tests `skills/shadow-frog-viewer/`, etc.

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
