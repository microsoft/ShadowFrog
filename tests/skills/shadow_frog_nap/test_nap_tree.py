"""Managed tree operations use real files/Git and external-review fixtures."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[3] / "skills/shadow-frog-nap/nap.py"


def cli(tree, repo, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(tree), "--repo", str(repo),
         "--mode", "coherent", *map(str, args)],
        capture_output=True, text=True, encoding="utf-8",
    )


def successful(tree, repo, *args):
    result = cli(tree, repo, *args)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def tree_code(tmp_path_factory):
    repo = tmp_path_factory.mktemp("nap-tree-code")
    (repo / "source.py").write_text(
        "def render(values):\n    return list(values)\n", encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    for args in (
        ["init", "-q", "-b", "main"],
        ["config", "user.name", "Nap Tree Tests"],
        ["config", "user.email", "tests@shadowfrog.invalid"],
        ["config", "commit.gpgsign", "false"],
        ["add", "source.py"],
        ["commit", "-q", "-m", "code baseline"],
    ):
        subprocess.run(["git", *args], cwd=repo, env=env, check=True)
    return repo


@pytest.fixture
def tree_repo(tree_code, tmp_path):
    repo = tree_code
    tree = tmp_path / "campaign" / "tree.json"
    successful(tree, repo, "--init", "--base", "main", "--max-nodes", "12")
    return tree, repo


def proposal(goal="Structured output", relation=None):
    return {
        "title": goal,
        "goal": goal,
        "evidence": [{
            "anchor": "source.py::render", "kind": "inspection",
            "observation": "The current function materializes values into a list.",
        }],
        "probes": [],
        "parent_connection": None if relation is None else {
            "relation": relation,
            "basis": "The parent proposes structured output.",
            "delta": goal,
            "preserves": ["Observable output semantics"],
            "supersedes": ["Parent buffering strategy"] if relation == "replace" else [],
        },
        "task": {
            "current_behavior": "The baseline eagerly materializes its input.",
            "desired_behavior": goal,
            "acceptance_criteria": [f"Demonstrate {goal} at the pinned baseline."],
            "non_goals": ["Unrelated API changes"],
            "open_questions": [],
        },
    }


def add(tree, repo, parent, proposals, path):
    path.write_text(json.dumps(proposals), encoding="utf-8")
    return successful(tree, repo, "--add", path, "--parent", parent)


def judgment(packet, reviewer="independent fixture reviewer", decisions=None):
    return {
        "reviewer": reviewer,
        "judgments": [
            {
                "node_id": target["node_id"],
                "input_hash": target["input_hash"],
                "decision": (decisions or {}).get(target["node_id"], "accept"),
                "rationale": "The proposal is grounded and its active contract is explicit.",
                "blocking_issues": (
                    [] if (decisions or {}).get(target["node_id"], "accept") == "accept"
                    else ["The proposal needs a concrete correction."]
                ),
                "evidence": ["source.py::render"],
            }
            for target in packet["targets"]
        ],
    }


def record_judgment(tree, repo, payload, path):
    path.write_text(json.dumps(payload), encoding="utf-8")
    return successful(tree, repo, "--record-review", path)


def test_init_pins_code_without_shadow_remote_or_feature_branches(tree_repo):
    tree, repo = tree_repo
    run = json.loads(tree.read_text(encoding="utf-8"))
    assert run["version"] == 2
    assert run["revision"] == 0
    assert run["nodes"] == run["reviews"] == run["selected"] == []
    assert run["limits"]["max_depth"] is None
    assert not (repo / ".shadow").exists()
    packet = successful(tree, repo, "--context", "@base")
    assert packet["parent"] is None
    assert packet["children"] == []
    assert packet["base_commit"] == run["base_commit"]
    before = tree.read_bytes()
    result = cli(tree, repo, "--init", "--base", "main")
    assert result.returncode == 1
    assert tree.read_bytes() == before


def test_append_diverse_children_and_replacement_without_rewriting_parents(tree_repo, tmp_path):
    tree, repo = tree_repo
    roots = add(tree, repo, "@base", [proposal(), proposal("A different root direction")],
                tmp_path / "roots.json")
    assert roots["added"] == ["n1", "n2"]
    siblings = add(
        tree, repo, "n1",
        [proposal("Resumable output", "extend"), proposal("Cancellation", "alternative")],
        tmp_path / "children.json",
    )
    assert siblings["added"] == ["n3", "n4"]
    before = json.loads(tree.read_text(encoding="utf-8"))
    added = add(tree, repo, "n3", [proposal("Replace buffering", "replace")],
                tmp_path / "replacement.json")
    assert added["added"] == ["n5"]
    after = json.loads(tree.read_text(encoding="utf-8"))
    assert after["nodes"][:4] == before["nodes"]
    assert after["base_commit"] == before["base_commit"]
    assert after["revision"] == 3
    path = successful(tree, repo, "--trajectory", "n5")
    assert [node["id"] for node in path["nodes"]] == ["n1", "n3", "n5"]
    context = successful(tree, repo, "--context", "n1")
    assert {node["id"] for node in context["children"]} == {"n3", "n4"}
    assert context["usage"]["nodes"] == 5


@pytest.mark.parametrize("reserved", ["id", "parent_id", "review_id"])
def test_append_cannot_override_identity_or_review_fields(tree_repo, tmp_path, reserved):
    tree, repo = tree_repo
    value = proposal()
    value[reserved] = "invented"
    submission = tmp_path / "bad.json"
    submission.write_text(json.dumps([value]), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--add", submission, "--parent", "@base")
    assert result.returncode == 1
    assert tree.read_bytes() == before
    assert not tree.with_name(tree.name + ".lock").exists()


def test_invalid_batch_cannot_partially_append(tree_repo, tmp_path):
    tree, repo = tree_repo
    bad = proposal("Invalid source")
    bad["evidence"][0]["anchor"] = "absent.py::f"
    submission = tmp_path / "bad.json"
    submission.write_text(json.dumps([proposal(), bad]), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--add", submission, "--parent", "@base")
    assert result.returncode == 1
    assert "absent.py" in result.stderr
    assert tree.read_bytes() == before
    good = add(tree, repo, "@base", [proposal()], tmp_path / "good.json")
    assert good["added"] == ["n1"]


def test_task_fields_cannot_disappear_silently_during_export(tree_repo, tmp_path):
    tree, repo = tree_repo
    value = proposal()
    value["task"]["hidden_requirements"] = ["A requirement the exporter would omit"]
    submission = tmp_path / "hidden.json"
    submission.write_text(json.dumps([value]), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--add", submission, "--parent", "@base")
    assert result.returncode == 1
    assert "unknown fields" in result.stderr
    assert tree.read_bytes() == before


def test_append_retries_reuse_ids_even_after_review(tree_repo, tmp_path):
    tree, repo = tree_repo
    values = [proposal()]
    first = add(tree, repo, "@base", values, tmp_path / "nodes.json")
    assert first["node_ids"] == ["n1"]
    packet = successful(tree, repo, "--review-packet", "n1")
    record_judgment(tree, repo, judgment(packet), tmp_path / "review.json")
    before = tree.read_bytes()
    repeated = add(tree, repo, "@base", values, tmp_path / "nodes.json")
    assert repeated["added"] == []
    assert repeated["reused"] == repeated["node_ids"] == ["n1"]
    assert tree.read_bytes() == before


@pytest.mark.parametrize("status", ["invalid", "rejected"])
def test_duplicate_detection_does_not_hide_invalid_submission(tree_repo, tmp_path, status):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "first.json")
    value = proposal()
    value["status"] = status
    path = tmp_path / "invalid-repeat.json"
    path.write_text(json.dumps([value]), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--add", path, "--parent", "@base")
    assert result.returncode == 1
    assert tree.read_bytes() == before


def test_existing_writer_lock_is_not_removed_or_ignored(tree_repo, tmp_path):
    tree, repo = tree_repo
    lock = tree.with_name(tree.name + ".lock")
    lock.write_text("other writer", encoding="utf-8")
    submission = tmp_path / "node.json"
    submission.write_text(json.dumps([proposal()]), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--add", submission, "--parent", "@base")
    assert result.returncode == 1
    assert "lock" in result.stderr.lower()
    assert lock.read_text(encoding="utf-8") == "other writer"
    assert tree.read_bytes() == before


def test_unjudged_proposal_cannot_be_selected(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    before = tree.read_bytes()
    result = cli(tree, repo, "--select", "n1")
    assert result.returncode == 1
    assert "ready" in result.stderr
    assert tree.read_bytes() == before


def test_judgment_gates_selection_and_binds_to_exact_content(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    packet = successful(tree, repo, "--review-packet", "n1")
    assert packet["kind"] == "nap-review-packet"
    receipt = record_judgment(tree, repo, judgment(packet), tmp_path / "review.json")
    assert receipt["review_id"] == "r1"
    successful(tree, repo, "--select", "n1")
    output = tmp_path / "tasks.md"
    successful(tree, repo, "--export", output)
    assert "independent fixture reviewer" in output.read_text(encoding="utf-8")

    run = json.loads(tree.read_text(encoding="utf-8"))
    run["nodes"][0]["task"]["desired_behavior"] = "A different unreviewed proposal"
    tree.write_text(json.dumps(run), encoding="utf-8")
    result = cli(tree, repo, "--export", tmp_path / "stale.md")
    assert result.returncode == 1
    assert "stale" in result.stderr.lower()
    assert not (tmp_path / "stale.md").exists()


def test_manually_marking_ready_cannot_bypass_judgment(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    run = json.loads(tree.read_text(encoding="utf-8"))
    run["nodes"][0]["status"] = "ready"
    run["selected"] = ["n1"]
    tree.write_text(json.dumps(run), encoding="utf-8")
    result = cli(tree, repo, "--export", tmp_path / "unreviewed.md")
    assert result.returncode == 1
    assert "accepted judgment" in result.stderr
    assert not (tmp_path / "unreviewed.md").exists()


def test_ancestor_changes_invalidate_child_approval(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "parent.json")
    add(tree, repo, "n1", [proposal("Child", "extend")], tmp_path / "child.json")
    packet = successful(tree, repo, "--review-packet", "n2")
    record_judgment(tree, repo, judgment(packet), tmp_path / "review.json")
    successful(tree, repo, "--select", "n2")
    run = json.loads(tree.read_text(encoding="utf-8"))
    run["nodes"][0]["task"]["desired_behavior"] = "Different parent design"
    tree.write_text(json.dumps(run), encoding="utf-8")
    result = cli(tree, repo, "--export", tmp_path / "changed-parent.md")
    assert result.returncode == 1
    assert "stale" in result.stderr.lower()


def test_old_acceptance_cannot_override_a_later_rejection(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    packet = successful(tree, repo, "--review-packet", "n1")
    record_judgment(tree, repo, judgment(packet), tmp_path / "accept.json")
    successful(tree, repo, "--select", "n1")
    record_judgment(
        tree, repo, judgment(packet, reviewer="second independent reviewer", decisions={"n1": "reject"}),
        tmp_path / "reject.json",
    )
    run = json.loads(tree.read_text(encoding="utf-8"))
    assert run["selected"] == []
    run["nodes"][0].update(status="ready", review_id="r1")
    run["selected"] = ["n1"]
    tree.write_text(json.dumps(run), encoding="utf-8")
    result = cli(tree, repo, "--export", tmp_path / "revoked.md")
    assert result.returncode == 1
    assert "latest judgment" in result.stderr


def test_review_retries_are_idempotent_and_siblings_do_not_stale_approval(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    packet = successful(tree, repo, "--review-packet", "n1")
    payload = judgment(packet)
    first = record_judgment(tree, repo, payload, tmp_path / "review.json")
    before = tree.read_bytes()
    repeated = record_judgment(tree, repo, payload, tmp_path / "review.json")
    assert first["review_id"] == repeated["review_id"] == "r1"
    assert tree.read_bytes() == before
    add(tree, repo, "@base", [proposal("Different idea")], tmp_path / "sibling.json")
    successful(tree, repo, "--select", "n1")
    successful(tree, repo, "--export", tmp_path / "still-reviewed.md")


def test_revise_and_reject_do_not_rewrite_proposals(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal("Needs revision"), proposal("Reject this")],
        tmp_path / "nodes.json")
    before = json.loads(tree.read_text(encoding="utf-8"))
    packet = successful(tree, repo, "--review-packet", "n1", "n2")
    record_judgment(
        tree, repo, judgment(packet, decisions={"n1": "revise", "n2": "reject"}),
        tmp_path / "review.json",
    )
    after = json.loads(tree.read_text(encoding="utf-8"))
    assert [node["status"] for node in after["nodes"]] == ["candidate", "rejected"]
    for old, new in zip(before["nodes"], after["nodes"]):
        assert old["task"] == new["task"]
        assert old["goal"] == new["goal"]
    result = cli(tree, repo, "--select", "n2")
    assert result.returncode == 1


def test_stale_review_and_accept_with_blockers_are_atomic_failures(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    packet = successful(tree, repo, "--review-packet", "n1")
    for change in ("hash", "blocker"):
        payload = judgment(packet)
        if change == "hash":
            payload["judgments"][0]["input_hash"] = "0" * 64
        else:
            payload["judgments"][0]["blocking_issues"] = ["Not actually ready"]
        path = tmp_path / f"{change}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        before = tree.read_bytes()
        result = cli(tree, repo, "--record-review", path)
        assert result.returncode == 1
        assert tree.read_bytes() == before


def test_review_budget_is_enforced_but_retry_does_not_consume_it(tree_repo, tmp_path):
    tree, repo = tree_repo
    add(tree, repo, "@base", [proposal()], tmp_path / "node.json")
    packet = successful(tree, repo, "--review-packet", "n1")
    for index in range(2):
        record_judgment(
            tree, repo, judgment(packet, reviewer=f"independent reviewer {index}"),
            tmp_path / f"review-{index}.json",
        )
    exhausted = cli(tree, repo, "--review-packet", "n1")
    assert exhausted.returncode == 1
    assert "max_reviews" in exhausted.stderr
    payload = judgment(packet, reviewer="third reviewer")
    path = tmp_path / "third.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = tree.read_bytes()
    result = cli(tree, repo, "--record-review", path)
    assert result.returncode == 1
    assert "max_reviews" in result.stderr
    assert tree.read_bytes() == before


def test_replacement_export_is_full_active_contract_not_parent_requirements(tree_repo, tmp_path):
    tree, repo = tree_repo
    parent = proposal("Original approach")
    parent["task"]["acceptance_criteria"] = ["OLD IDENTIFICATION REQUIREMENT"]
    add(tree, repo, "@base", [parent], tmp_path / "parent.json")
    child = proposal("Replacement approach", "replace")
    child["task"]["acceptance_criteria"] = ["NEW IDENTIFICATION REQUIREMENT"]
    add(tree, repo, "n1", [child], tmp_path / "child.json")
    packet = successful(tree, repo, "--review-packet", "n2")
    assert [entry["id"] for entry in packet["targets"][0]["input"]["path"]] == ["n1", "n2"]
    record_judgment(tree, repo, judgment(packet), tmp_path / "review.json")
    successful(tree, repo, "--select", "n2")
    output = tmp_path / "replacement.md"
    successful(tree, repo, "--export", output)
    text = output.read_text(encoding="utf-8")
    assert "NEW IDENTIFICATION REQUIREMENT" in text
    assert "OLD IDENTIFICATION REQUIREMENT" not in text
    historical = successful(tree, repo, "--trajectory", "n2")
    assert historical["nodes"][0]["task"]["acceptance_criteria"] == ["OLD IDENTIFICATION REQUIREMENT"]


def test_initialization_flags_are_not_ignored_on_other_actions(tree_repo):
    tree, repo = tree_repo
    result = cli(tree, repo, "--context", "@base", "--max-nodes", "100")
    assert result.returncode == 2
    assert "init" in result.stderr
