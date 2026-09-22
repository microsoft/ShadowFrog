---
name: shadow-frog-nap
description: >-
  Generate grounded software feature-task briefs with a small, bounded
  ideation budget instead of implementing every idea. Read focused source
  and optional shadow/dream evidence, refine a shortlist, and run only
  decision-changing probes. Use mode=coherent to connect each child to
  its parent's findings or design while encouraging diverse siblings.
  Use for lightweight ideation, nap runs, or preparing SWE implementation
  tasks; use shadow-frog-dream for full implementation-backed experiments.
scripts:
  - nap.py
---

# ShadowFrog Nap

Nap produces **feature-task briefs**, not finished implementations or verified
dreams. Reasoning stays with the agent; the Python helper validates records,
retrieves compact parent context, and exports tasks. It never runs probes,
creates worktrees, changes branches, pushes, or updates shadow discoveries.

## Inputs and Limits

Accept a user goal or area, optional prior dream/nap evidence, a mode, and a
work budget. A Git repository with a commit is required; a remote and an
initialized `.shadow/` are **not** required.

| Setting | Default | Meaning |
|---------|---------|---------|
| `mode` | `broad` | Explore distinct opportunities; `coherent` regularizes parent-child connections |
| `max_nodes` | 7 | Total recorded nodes, including imported seeds and rejected attempts |
| `max_depth` | 2 | Maximum parent edges from a root; roots have depth 0 |
| `max_probes` | 2 | Total executed probes recorded across all nodes, including failures |
| `max_tasks` | 2 | Maximum selected ready task briefs |

These are **ceilings, not quotas**. Zero selected tasks is a valid result.
Users can request larger budgets, including ten diverse children of one
parent. Agree on limits before starting and persist them in the record;
never erase rejected work or enlarge limits to disguise an overrun.

The helper enforces **recorded** limits, not actual API spending or unrecorded
commands. Respect the host's token/cost/time limits separately. Do not install
dependencies, debug infrastructure, start nested agents, or run full suites
automatically. Escalation to a full dream is a separate decision.

## Modes

Examples: `/shadow-frog-nap` or `/shadow-frog-nap mode=coherent`.

Resolve the requested mode once and carry it through planning, child context,
the run's `mode`, and every helper invocation's `--mode` argument. Do not
silently fall back to broad mode.
The CLI's expected mode defaults to `broad`, just like Dream validation;
a coherent record requires an explicit `--mode coherent`.

**Broad:** propose a few distinct, code-grounded opportunities. With defaults,
start with up to three candidates, retain at most two promising directions,
and refine for at most two rounds within the total node budget.

**Coherent:** each child identifies a concrete parent capability, finding,
limitation, or design decision and extends, integrates, challenges, replaces,
simplifies, or proposes an alternative to it. Use `/shadow-frog`'s shared
`parent_connection` format.

- Coherence is **local to an edge**, not one fixed goal for the whole tree.
- Each child has its own goal. Diverse siblings are encouraged, not constrained
  to the same subgoal, different files, or a prescribed diversity quota.
- Technical independence does not disqualify a justified alternative. Explain
  the parent connection; thematic similarity or a parent ID alone is not enough.
- Challenges are permitted, not mandatory. Do not manufacture bad designs to
  increase depth.
- A parent's proposed API does not exist merely because a nap described it.
  Parent links express idea provenance, not implemented code dependencies.

## Workflow

```text
pinned source + optional prior evidence
                  |
          grounded candidates
                  |
      select, challenge, refine
                  |
       probe decisive unknowns
                  |
       selected task contracts
```

### 1. Pin and Ground

Read the repository's instructions and, when present, relevant preferences,
per-file shadows, cross-cutting discoveries, and selected dream reports.
Use existing indexes to select context; do not read every past report or
initialize a full shadow merely to nap.

Obtain the full base commit with `git rev-parse HEAD`. Inspect source and
existing behavior at that commit. If the working tree differs, inspect the
pinned versions rather than treating uncommitted or newer code as baseline.
Resolve real `file::symbol` anchors; do not invent symbols or copy template
references. The helper checks file existence at the commit, **not symbol
existence or the truth of observations**.

For each candidate, identify a user need, an existing limitation, and an
integration point. Search for existing equivalent functionality before
developing the idea. Generic TODO lists and unrelated standalone add-ons
are not sufficient motivation.

### 2. Select and Refine

Reject weak or duplicate ideas early, before implementation. Maintain one
canonical run record with a single writer. Save observations and rejected
directions as work proceeds so another session can resume from that record.

Use a selected parent's context packet, not its entire conversation history.
Read cited source/report details when necessary to check the evidence.
Brief summaries of existing children help avoid duplicates and inspire
different directions; siblings' proposed features are **not** inherited code.

Each refinement must add a grounded constraint, useful composition,
counterexample, or reasoned alternative. Rephrasing a proposal or adding
unrelated requirements is not progress. Stop or choose another parent when
no worthwhile continuation exists. Once a node has children, preserve its
historical goal/evidence and record revisions as new nodes.

Imported dream or nap evidence can be represented by a `seed` node with
`inherited` evidence and its original reference. Its children use normal
local node IDs. Import only the relevant parent packet, not an entire old
tree. Recheck stale observations against this run's pinned commit.
Keep historical commands as inherited evidence references, not new probes
charged to this run's execution budget.

### 3. Probe Only Decision-Changing Questions

Run a narrow probe only if its result can change selection, feasibility,
acceptance criteria, or the choice between alternatives. Prefer inspecting
an existing path or running one focused command over implementing the feature.
Use the repository's supported runner and isolation appropriate to the command.
Do not execute experimental mutations in the user's working tree.

Record the exact command as an argument array, its exit code, the question,
and the observed result. Failed probes consume budget too. An unexecuted
probe belongs in `open_questions`, not the `probes` list. Missing dependencies
or an exhausted budget are explicit unresolved questions, never evidence of
success. The helper does not execute or independently attest these commands.

### 4. Review Readiness

A ready task must:

- Address a project-specific need, with evidence anchored to the pinned source.
- State observable desired behavior, acceptance criteria, and non-goals.
- Resolve blocking questions and fit a reasonable implementation scope.
- Stand alone at `base_commit`, without assuming an idea parent was implemented.
- In coherent mode, explain a meaningful parent connection and its own delta.

`ready` means **reviewed and specified**, not execution-proven. The agent must
judge usefulness, novelty, feasibility, and semantic coherence; a structurally
valid JSON record does not establish these properties.

If real implementation-backed compounding is needed, implement only selected
parents (or reuse independently validated downstream work), pin that actual
commit in a new run, and import its evidence. Do not pretend hypothetical
dependencies exist. Freeze task definitions and parent commits before using
them for an offline benchmark.

## Record Format

Store `run.json` and exports under `.shadow/_meta/naps/<run-name>/` **only if
the shadow is already initialized**. Otherwise use an agent workspace or
another user-approved directory outside `.shadow/`. Do not create a partial
shadow, `_dreams/` artifacts, or per-file discovery entries for proposals.

This template illustrates the shape; replace commit and source placeholders
with actual evidence before validation:

```json
{
  "version": 1,
  "mode": "coherent",
  "base_commit": "<full commit ID>",
  "limits": {
    "max_nodes": 7,
    "max_depth": 2,
    "max_probes": 2,
    "max_tasks": 2
  },
  "nodes": [
    {
      "id": "n1",
      "parent_id": null,
      "title": "Cancellable streaming export",
      "goal": "Let callers stop an export without buffering all records",
      "status": "candidate",
      "parent_connection": null,
      "evidence": [
        {
          "anchor": "src/exporter.ext::stream",
          "kind": "inspection",
          "observation": "<observed current behavior>"
        }
      ],
      "probes": []
    }
  ],
  "selected": []
}
```

Node IDs use letters, digits, dots, underscores, and hyphens, start with a
letter/digit, and are at most 80 characters. `parent_id` is null for a root
or an existing node ID; records may be unordered but must be acyclic.

Statuses: `seed`, `candidate`, `ready`, `rejected`. A rejected node also needs
a nonempty `reason`. Seeds and rejected nodes can motivate a child, but cannot
be selected as ready tasks. Preserve their status in the context packet.

Every node has at least one evidence entry:
- `inspection`: an observation checked against source.
- `probe`: also supply `probe`, the zero-based index of the executed probe
  in this node's `probes` array.
- `inherited`: also supply `reference` identifying the prior report/node.
  Inherited evidence does not become verified merely by being reused.

A probe has `question`, `command` (a nonempty argument array), `exit_code`
(integer), and `result`. No shell pipelines or shell-export handoffs are
needed by the helper.

A ready node also contains this complete, **currently active** task contract:

```json
{
  "task": {
    "current_behavior": "<what the pinned source currently does>",
    "desired_behavior": "<observable behavior to implement>",
    "acceptance_criteria": ["<specific successful and edge-case outcomes>"],
    "non_goals": ["<explicit boundary>"],
    "open_questions": []
  }
}
```

Non-ready nodes may omit `task`. If present, it uses the same shape but can
retain open questions. `selected` contains unique IDs of ready nodes only.

## Python Helper

Locate `nap.py` in `.github/skills/shadow-frog-nap/` or
`.claude/skills/shadow-frog-nap/`. Use a Python 3 interpreter available on the
host (`python`, `python3`, or `py -3`); no Bash or Unix utilities are required.
The examples below use the Copilot install path and a coherent run:

```text
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --context n1
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --export TASKS.md
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --trajectory n3
```

Validation checks limits, lineage, evidence shape, commit/file existence, and
selected readiness. `--context` emits the parent's full record plus brief
ancestor/child summaries. `--export` writes UTF-8 Markdown to a **new** file
and refuses to overwrite existing files. Other outputs are JSON on stdout;
errors go to stderr with exit 1 (argument errors use exit 2).

The final export uses selected nodes' active contracts, **not** a union of
ancestor requirements. Replacing A with B must not produce a task requiring
both A and B. `--trajectory` returns one root-to-node **idea** path, never a
concatenation of siblings or a claim that intermediate code was implemented.
Independent siblings require an explicit integration idea before combining
them into one long-horizon task.

Do not run dream reconciliation, increment dream counters, or label task
proposals `verified` in the shadow. Independently established behavioral facts
can be captured through the normal shadow workflow separately.
