---
name: shadow-frog-nap
description: >-
  Generate grounded software feature-task briefs with a small, bounded
  ideation budget instead of implementing every idea. Read focused source
  and optional shadow/dream evidence, grow or resume a persistent proposal
  tree, and obtain independent shortlist judgments. Run only decision-changing
  probes of existing behavior. Use mode=coherent to connect each child to
  its parent's findings or design while encouraging diverse siblings.
  Use for lightweight ideation, nap runs, or preparing SWE implementation
  tasks; use shadow-frog-dream for full implementation-backed experiments.
scripts:
  - nap.py
---

# ShadowFrog Nap

Nap is **implementation-free feature-task ideation**, not feature development
or verified dreaming. The host agent generates proposals and delegates an
independent judgment. The Python helper manages the persistent tree, binds
judgments to exact proposal inputs, and exports reviewed contracts. It never
calls a model, executes probes, implements features, creates worktrees/branches,
pushes, or writes shadow discoveries.

## Inputs and Limits

Accept a user goal or area, optional prior dream/nap evidence, a mode, and a
work budget. A Git repository with a commit is required; a remote and an
initialized `.shadow/` are **not** required.

| Setting | Default | Meaning |
|---------|---------|---------|
| `mode` | `broad` | Explore distinct opportunities; `coherent` regularizes parent-child connections |
| `max_nodes` | 7 | Total recorded nodes, including imported seeds and rejected attempts |
| `max_depth` | unset (`null`) | Optional user-requested maximum parent edges; roots have depth 0 |
| `max_probes` | 2 | Total executed probes recorded across all nodes, including failures |
| `max_tasks` | 2 | Maximum selected ready task briefs |
| `max_reviews` | 2 | Recorded judge batches: one shortlist review plus a bounded re-review |

These are **ceilings, not quotas**. Zero selected tasks is a valid result.
Users can request larger budgets, including ten diverse children of one
parent. Agree on limits before starting and persist them in the record;
never erase rejected work or enlarge limits to disguise an overrun.
Depth is not a default stopping condition. Omit `max_depth` or set it to
`null` to leave it uncapped; an explicit nonnegative integer still limits it.
The total node budget bounds every run, and parent/cycle checks always apply.

The helper enforces **recorded** limits, not actual API spending or unrecorded
commands/model calls. Respect the host's token/cost/time limits separately,
including invalid or failed judge responses. Do not install dependencies,
debug infrastructure, spawn per-candidate implementation workers, or run full
suites automatically. One independent shortlist judge and a bounded re-review
are allowed within the review budget; do not start a model panel for every node.
Escalation to a full dream is a separate decision.

## Modes

Examples: `/shadow-frog-nap` or `/shadow-frog-nap mode=coherent`.

Resolve the requested mode once and carry it through planning, child context,
the run's `mode`, and every helper invocation's `--mode` argument. Do not
silently fall back to broad mode.
The CLI's expected mode defaults to `broad`, just like Dream validation;
a coherent record requires an explicit `--mode coherent`.

**Broad:** propose a few distinct, code-grounded opportunities. With defaults,
start with up to three candidates, retain at most two promising directions,
and refine while useful grounded progress and the total work budget remain.
Do not impose a fixed number of refinement rounds.

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
       independent shortlist judge
                  |
       selected task contracts
```

## Operational Tree Workflow

Use one persistent record across sessions. The default branch is represented
by an immutable `base_commit`; `@base` is a virtual code-root selector, not an
invented proposal with fake symbol evidence. Root proposals have null
`parent_id`. Depth counts proposal-parent edges, and the virtual root consumes
no node budget.

Resolve the intended default branch or commit explicitly; do not assume the
currently checked-out feature branch is the intended baseline. Initialize once:

```text
python .github/skills/shadow-frog-nap/nap.py TREE.json --repo REPO --mode coherent --init --base DEFAULT_REF --max-nodes 20
```

Use the actual default branch name/ref or full commit instead of `DEFAULT_REF`.
The helper resolves it once, creates a version-2 record, and refuses to overwrite
an existing tree. `--max-depth` is optional; initial `--max-nodes`, `--max-probes`,
`--max-tasks`, and `--max-reviews` flags are accepted only with `--init`.

To expand a parent, use `--context @base` for root opportunities or
`--context n1` for a proposal. The packet supplies the pinned code baseline,
the parent's full proposal and review feedback, brief ancestors/existing
children, and current usage. Keep actual code and hypothetical design state
separate.

Generate a UTF-8 JSON **list of proposal objects** in a submission file. Each
object uses the node fields below except `id`, `parent_id`, and `review_id`;
the driver owns those fields. Omit status for a candidate, or explicitly record
a `seed`/`rejected` attempt with its required evidence/reason. Never submit
`ready`: only a current accepted judgment can grant it.

```text
python .github/skills/shadow-frog-nap/nap.py TREE.json --repo REPO --mode coherent --add CHILDREN.json --parent n1
```

The helper assigns unique IDs and appends the whole batch or nothing. Exact
same-parent retries reuse existing IDs without resetting judgment/status or
consuming another node. Existing parent/sibling design payloads are unchanged.
To revise or replace a proposal,
append a child with its full active contract and appropriate parent connection;
do not rewrite its parent. This also permits sibling alternatives and rejected
ideas to motivate a better child.

One coordinator writes the canonical tree. Workers return proposal/judgment
files, not competing edits to `TREE.json`. Mutations use an exclusive adjacent
lock and atomic replacement, increment `revision`, and validate the complete
record and pinned source before publishing. On a stale lock after interruption,
confirm its writer stopped before removing only that lock; never discard the
tree to bypass contention. Resume by reopening the same tree and selecting a
parent, not by reconstructing state from chat history.

Record data is flushed and file-synced before replacement. Unsupported file
sync is reported explicitly; other sync failures leave the old record intact.
Atomic publication prevents partial JSON visibility during process interruption,
but this helper does not perform a portable directory sync or promise survival
of the latest update across power loss on every filesystem. Keep required
durability/backups in the host storage layer. Never infer that a PID-only lock
is safe to remove merely because it is old.

### 1. Pin and Ground

Read the repository's instructions and, when present, relevant preferences,
per-file shadows, cross-cutting discoveries, and selected dream reports.
Use existing indexes to select context; do not read every past report or
initialize a full shadow merely to nap.

Use `--init --base` to obtain the full intended baseline commit. Inspect source and
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

Reject weak or duplicate ideas early, without implementing them. Maintain one
canonical run record through the managed append operations. Save observations
and rejected directions as work proceeds so another session can resume.

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
acceptance criteria, or the choice between alternatives. Probes observe
existing behavior; they must not implement or prototype the proposed feature.
Use the repository's supported runner and isolation appropriate to the command.
Do not execute experimental mutations in the user's working tree.

Record the exact command as an argument array, its exit code, the question,
and the observed result. Failed probes consume budget too. An unexecuted
probe belongs in `open_questions`, not the `probes` list. Missing dependencies
or an exhausted budget are explicit unresolved questions, never evidence of
success. The helper does not execute or independently attest these commands.

### 4. Independent Judgment and Readiness

Before selecting tasks, prepare one review packet for the shortlisted paths:

```text
python .github/skills/shadow-frog-nap/nap.py TREE.json --repo REPO --mode coherent --review-packet n3 n6
```

Capture its JSON output through the host's tooling. Delegate to a **strong
reasoning model in a fresh context**, preferably a different backbone from the
generator when the host supports it. Give the judge the packet and read-only
access to source at its exact `base_commit`, not the generator's conversation
or self-assigned confidence. If the host cannot provide an independent context,
leave proposals as candidates; do not fabricate a review or impersonate a judge.

The judge assesses grounding, plausible feasibility, user value/distinctness,
scope and acceptance criteria, each parent-child connection, and the final active
contract. Siblings need not share a goal. A useful alternative need not call
parent code, and no hypothetical parent API may be assumed implemented.

For a selected path, use the packet's concrete `path_questions` in that same
review, without requiring another model pass:
- What worthwhile outcome does the path now describe?
- Which steps add capability, resolve uncertainty, or change a meaningful tradeoff?
- Which merely rephrase earlier work, and what actionable refinement would help?
- Does the final contract stand alone at the pinned baseline?

This is a path-level planning lens, not a fixed objective for the entire tree.
Changing direction, revisiting the same files, and learning from a rejected
design are legitimate. Do not turn the history into a requirement to implement
every ancestor. Explain useful progression rather than scoring length. If a
continuation merely repeats an existing useful task, keep that task selected
or append a genuinely revised child; do not rewrite reviewed ancestor payloads
to follow a suggestion to "consolidate" the history.

Separate binding behavior/constraints from suggested implementation choices.
Replacing a design does not implicitly retire a requirement. Scope changes must
be explicit in the active contract and parent connection; user constraints must
not disappear under the label of an architectural alternative. Keep blocking
planning questions separate from nonblocking implementation risks.

Return **accept / revise / reject**, with code references, a rationale, and
blocking issues. Do not produce only a score. The output format is:

```json
{
  "reviewer": "<actual independent model/context identity recorded by the host>",
  "judgments": [
    {
      "node_id": "n3",
      "input_hash": "<copy exactly from the corresponding packet target>",
      "decision": "accept",
      "rationale": "<specific source-grounded assessment>",
      "blocking_issues": [],
      "evidence": ["<real file>::<real symbol>"]
    }
  ]
}
```

An accept decision requires no blockers and a complete task with no unresolved
questions. Revise/reject decisions require concrete blockers. The host records
the real reviewer identity and submits the response, without changing a verdict
to make the demonstration or quota succeed:

```text
python .github/skills/shadow-frog-nap/nap.py TREE.json --repo REPO --mode coherent --record-review JUDGMENT.json
python .github/skills/shadow-frog-nap/nap.py TREE.json --repo REPO --mode coherent --select n3 n6
```

Recording is atomic and exact retries are idempotent. The helper stores review
batches, changes only review/status metadata, and checks SHA-256 bindings over
the proposal, its ancestor design payloads, mode, and base commit. Adding a
sibling does not invalidate an existing approval. Changing reviewed semantics
does; append a revision and obtain a new judgment rather than forging hashes.
Status/review metadata is not implementation state and is not inherited as code.

Permit at most one targeted revision/re-review under default limits. If a judge
cannot settle a critical uncertainty without implementation, keep the proposal
unready and hand that question to Dream. Do not run open-ended debates.

A ready task must:

- Address a project-specific need, with evidence anchored to the pinned source.
- State observable desired behavior, acceptance criteria, and non-goals.
- Resolve blocking questions and fit a reasonable implementation scope.
- Stand alone at `base_commit`, without assuming an idea parent was implemented.
- In coherent mode, explain a meaningful parent connection and its own delta.

`ready` means **independently judge-reviewed and specified**, not execution-proven.
It must reference the latest judgment for that node; an older acceptance cannot
override a later rejection.
Receipts prevent accidental stale approvals but cannot authenticate that an LLM
actually ran or establish the truth of its conclusions. The host must perform
the real independent review; the Python helper is not a semantic oracle.
Views and exports scope this status to planning: implementation is not assessed
by Nap, proposed-feature runtime validation has not been performed by Nap, and
reviewer authenticity is not attested. Do not equate missing risk notes with an
absence of implementation risk.

If a blocking question requires implementing a candidate or prototype, leave
it unresolved and hand it to `/shadow-frog-dream` or a downstream implementation
task. Do not turn the nap into an implementation loop. A later nap can pin an
actual, independently validated implementation commit and import its evidence.
Do not pretend hypothetical dependencies exist. Freeze task definitions and
parent commits before using them for an offline benchmark.

## Record Format

Store `run.json` and exports under `.shadow/_meta/naps/<run-name>/` **only if
the shadow is already initialized**. Otherwise use an agent workspace or
another user-approved directory outside `.shadow/`. Do not create a partial
shadow, `_dreams/` artifacts, or per-file discovery entries for proposals.

The persisted record is normally created and mutated by the helper, not edited
by workers. This example illustrates its shape; placeholders must become actual
source evidence. Version 1 records do not have judgment provenance; import their
proposals as unreviewed candidates/seeds in a new tree rather than inventing
approvals.

```json
{
  "version": 2,
  "revision": 0,
  "mode": "coherent",
  "base_commit": "<full commit ID>",
  "limits": {
    "max_nodes": 7,
    "max_depth": null,
    "max_probes": 2,
    "max_tasks": 2,
    "max_reviews": 2
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
  "reviews": [],
  "selected": []
}
```

Node IDs use letters, digits, dots, underscores, and hyphens, start with a
letter/digit, and are at most 80 characters. `parent_id` is null for a root
or an existing node ID; records may be unordered but must be acyclic.

Statuses: `seed`, `candidate`, `ready`, `rejected`. A rejected node also needs
a nonempty `reason`. Seeds/rejections can motivate a child. A ready node has
a `review_id` pointing to a current accepted judgment in the append-only
`reviews` list. Preserve historical proposal payloads; supersession is local
to the child path, not a rewrite of the parent or its other children.

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
    "open_questions": [],
    "constraints": ["<binding constraint, when needed>"],
    "design_suggestions": ["<optional implementation approach, not a requirement>"],
    "implementation_risks": ["<nonblocking risk to evaluate during implementation>"]
  }
}
```

Non-ready nodes may omit `task`. A review packet requires a complete task shape,
which may still have open questions for the judge to identify as blockers.
`selected` contains unique ready IDs only. Use `--select` with no IDs to clear it.

`constraints`, `design_suggestions`, and `implementation_risks` are optional
lists of nonempty strings. Binding behavior belongs in `desired_behavior`,
`acceptance_criteria`, or `constraints`; advice belongs in `design_suggestions`.
Explicit `parent_connection.preserves` commitments remain required and appear
in both export audiences. The judge must check that the full active contract
actually respects them; the helper does not infer semantic preservation.
An implementation risk that makes the plan itself ungrounded or infeasible is
instead a blocking `open_questions` item and must prevent acceptance.

## Python Helper

Locate `nap.py` in `.github/skills/shadow-frog-nap/` or
`.claude/skills/shadow-frog-nap/`. Use a Python 3 interpreter available on the
host (`python`, `python3`, or `py -3`); no Bash or Unix utilities are required.
The examples below use the Copilot install path and a coherent run:

```text
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --context n1
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --export TASKS.md
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --export HANDOFF.md --audience implementation
python .github/skills/shadow-frog-nap/nap.py RUN.json --repo REPO --mode coherent --trajectory n3
```

Validation checks limits, lineage, evidence shape, commit/file existence, and
review bindings/readiness. `--context` emits the parent's full record/review
and brief ancestor/child summaries; `--context @base` starts from real code.
`--export` writes UTF-8 Markdown to a **new** file
and refuses to overwrite existing files. Other outputs are JSON on stdout;
errors go to stderr with exit 1 (argument errors use exit 2).

Exports have two explicit audiences. The default `planning` brief retains the
review rationale, detailed probes, and parent design context for comparing
alternatives. `--audience implementation` produces a more concise handoff that
leads with the user problem, required behavior/constraints, acceptance criteria,
non-goals, suggestions, and implementation risks. Both retain supporting source
references, planning-only readiness, and the **same active requirements**.
Changing the audience does not change the node or its review.

Suggested implementation is labelled non-binding. Parent history and superseded
designs remain planning context, not additional implementation requirements.
JSON summaries, parent context, trajectories, and export responses also expose
scope-qualified readiness; an implementation may exist elsewhere, so Nap says
`not_assessed`, not that no implementation has ever been started.

The final export uses selected nodes' active contracts, **not** a union of
ancestor requirements. Replacing A with B must not produce a task requiring
both A and B. `--trajectory` returns one root-to-node **idea** path, never a
concatenation of siblings or a claim that intermediate code was implemented.
Independent siblings require an explicit integration idea before combining
them into one long-horizon task.

Do not run dream reconciliation, increment dream counters, or label task
proposals `verified` in the shadow. Independently established behavioral facts
can be captured through the normal shadow workflow separately.
