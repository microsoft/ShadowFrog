"""Structural contracts and real Git/CLI integration, not ideation-quality scoring."""

from copy import deepcopy
import errno
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from tests.skills.shadow_frog.test_coherence import connection


SCRIPT = Path(__file__).resolve().parents[3] / "skills/shadow-frog-nap/nap.py"


def node(node_id="root", parent=None, goal="Support bounded-memory export"):
    return {
        "id": node_id,
        "parent_id": parent,
        "title": f"Proposal {node_id}",
        "goal": goal,
        "status": "candidate",
        "parent_connection": connection() if parent else None,
        "evidence": [{
            "anchor": "cart.py::calculate_total",
            "kind": "inspection",
            "observation": "Totals are calculated over the supplied items.",
        }],
        "probes": [],
        "task": {
            "current_behavior": "The public API does not support the proposed use case.",
            "desired_behavior": f"Provide {goal}.",
            "acceptance_criteria": [f"Demonstrate {goal} with an edge-case example."],
            "non_goals": ["Changing unrelated APIs"],
            "open_questions": [],
        },
    }


def make_record(commit):
    return {
        "version": 2,
        "revision": 0,
        "mode": "coherent",
        "base_commit": commit,
        "limits": {"max_nodes": 7, "max_depth": None, "max_probes": 2, "max_tasks": 2},
        "nodes": [node()],
        "reviews": [],
        "selected": [],
    }


def approve(nap, record, *node_ids):
    record["selected"] = []
    packet = nap.review_packet(record, list(node_ids))
    response = {
        "reviewer": "external fixture judge",
        "judgments": [
            {
                "node_id": target["node_id"], "input_hash": target["input_hash"],
                "decision": "accept", "rationale": "Fixture task accepted.",
                "blocking_issues": [],
                "evidence": ["cart.py::calculate_total"],
            }
            for target in packet["targets"]
        ],
    }
    updated, _ = nap.record_review(record, response)
    record.clear()
    record.update(updated)
    record["selected"] = list(node_ids)


@pytest.fixture
def record(coupon_demo):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    return make_record(commit)


def run_cli(record, tmp_path, repo, *args, env=None):
    path = tmp_path / "nap run.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable, str(SCRIPT), str(path), "--repo", str(repo),
            "--mode", record["mode"], *args,
        ],
        capture_output=True, text=True, encoding="utf-8", env=env,
    )


def test_valid_record_and_input_are_preserved(nap, record):
    before = deepcopy(record)
    nap.validate_record(record)
    assert record == before


def test_empty_run_is_valid_and_does_not_require_filler(nap, record):
    record["nodes"] = []
    record["selected"] = []
    nap.validate_record(record)


def test_default_depth_is_unset(nap):
    assert nap.RunLimits().max_depth is None


@pytest.mark.parametrize("explicit_null", [False, True])
def test_no_depth_cap_allows_a_full_node_budget_chain(nap, explicit_null):
    record = make_record("a" * 40)
    if not explicit_null:
        del record["limits"]["max_depth"]
    record["nodes"] = [
        node(f"n{index}", f"n{index - 1}" if index else None)
        for index in range(7)
    ]
    nap.validate_record(record)
    assert nap.parent_context(record, "n6")["limits"]["max_depth"] is None
    assert len(nap.idea_trajectory(record, "n6")["nodes"]) == 7


@pytest.mark.parametrize("limit", [0, 1, 2])
def test_explicit_depth_cap_is_still_enforced(nap, limit):
    record = make_record("a" * 40)
    record["limits"]["max_depth"] = limit
    record["nodes"] = [
        node(f"n{index}", f"n{index - 1}" if index else None)
        for index in range(4)
    ]
    with pytest.raises(ValueError, match="max_depth"):
        nap.validate_record(record)


def test_ten_siblings_have_distinct_goals_on_the_same_files(nap, record):
    record["limits"]["max_nodes"] = 11
    record["nodes"] += [
        node(f"child-{i}", "root", f"Distinct direction {i}") for i in range(10)
    ]
    nap.validate_record(record)


def test_nodes_need_not_be_in_topological_order(nap, record):
    record["nodes"].insert(0, node("child", "root"))
    nap.validate_record(record)


@pytest.mark.parametrize("value", [None, [], "run", 1])
def test_record_must_be_an_object(nap, value):
    with pytest.raises(ValueError, match="object"):
        nap.validate_record(value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", 1, "version"),
        ("version", 3, "version"),
        ("version", True, "version"),
        ("mode", "dream", "mode"),
        ("base_commit", "HEAD", "base_commit"),
        ("base_commit", "abc1234", "base_commit"),
        ("nodes", {}, "nodes"),
        ("selected", "root", "selected"),
        ("limits", [], "limits"),
    ],
)
def test_invalid_run_fields(nap, record, field, value, message):
    record[field] = value
    with pytest.raises(ValueError, match=message):
        nap.validate_record(record)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_nodes", 0), ("max_tasks", 0), ("max_depth", -1),
        ("max_probes", -1), ("max_nodes", True), ("max_depth", "2"),
        ("max_probes", 1.5), ("max_nodes", None), ("max_probes", None),
        ("max_tasks", None),
        ("max_reviews", None), ("max_reviews", -1),
    ],
)
def test_limits_fail_early(nap, record, field, value):
    record["limits"][field] = value
    with pytest.raises(ValueError, match=field):
        nap.validate_record(record)


def test_unknown_limit_is_not_ignored(nap, record):
    record["limits"]["max_node"] = 1
    with pytest.raises(ValueError, match="max_node"):
        nap.validate_record(record)


def test_node_budget_is_enforced(nap, record):
    record["limits"]["max_nodes"] = 1
    record["nodes"].append(node("child", "root"))
    with pytest.raises(ValueError, match="max_nodes"):
        nap.validate_record(record)


def test_depth_budget_is_enforced(nap, record):
    record["limits"]["max_depth"] = 0
    record["nodes"].append(node("child", "root"))
    with pytest.raises(ValueError, match="max_depth"):
        nap.validate_record(record)


def test_task_budget_is_enforced(nap, record):
    record["limits"]["max_tasks"] = 1
    record["nodes"].append(node("child", "root"))
    record["selected"] = ["root", "child"]
    with pytest.raises(ValueError, match="max_tasks"):
        nap.validate_record(record)


def test_probe_budget_counts_rejected_nodes(nap, record):
    record["limits"]["max_probes"] = 0
    record["nodes"][0]["probes"] = [{
        "question": "Does the existing API accept empty input?",
        "command": [sys.executable, "probe.py"],
        "exit_code": 1,
        "result": "The assertion failed; the limitation is real.",
    }]
    record["nodes"][0]["status"] = "rejected"
    record["nodes"][0]["reason"] = "Not enough user value."
    record["selected"] = []
    with pytest.raises(ValueError, match="max_probes"):
        nap.validate_record(record)


@pytest.mark.parametrize("bad_command", ["python probe.py", [], [""]])
def test_probes_store_argument_vectors(nap, record, bad_command):
    record["nodes"][0]["probes"] = [{
        "question": "Question", "command": bad_command, "exit_code": 0, "result": "Result",
    }]
    with pytest.raises(ValueError, match="command"):
        nap.validate_record(record)


def test_probe_evidence_must_reference_an_executed_probe(nap, record):
    record["nodes"][0]["evidence"][0].update(kind="probe", probe=0)
    with pytest.raises(ValueError, match="probe"):
        nap.validate_record(record)


@pytest.mark.parametrize("parent", ["missing", "root"])
def test_missing_parent_and_self_cycle(nap, record, parent):
    record["nodes"][0]["parent_id"] = parent
    record["nodes"][0]["parent_connection"] = connection()
    with pytest.raises(ValueError, match="parent|cycle"):
        nap.validate_record(record)


def test_multi_node_cycle_is_rejected(nap, record):
    record["nodes"] = [node("a", "b"), node("b", "a")]
    record["selected"] = []
    with pytest.raises(ValueError, match="cycle"):
        nap.validate_record(record)


def test_duplicate_ids_are_rejected(nap, record):
    record["nodes"].append(node())
    with pytest.raises(ValueError, match="duplicate"):
        nap.validate_record(record)


@pytest.mark.parametrize("node_id", ["../escape", "a/b", "", "a\nb"])
def test_ids_are_portable(nap, record, node_id):
    record["nodes"][0]["id"] = node_id
    with pytest.raises(ValueError, match="id"):
        nap.validate_record(record)


@pytest.mark.parametrize(
    "anchor", ["cart.py", "../cart.py::f", "/cart.py::f", "C:\\cart.py::f", "cart.py::"]
)
def test_anchors_are_repository_relative(nap, record, anchor):
    record["nodes"][0]["evidence"][0]["anchor"] = anchor
    with pytest.raises(ValueError, match="anchor"):
        nap.validate_record(record)


def test_inherited_claims_keep_their_provenance(nap, record):
    evidence = record["nodes"][0]["evidence"][0]
    evidence["kind"] = "inherited"
    with pytest.raises(ValueError, match="reference"):
        nap.validate_record(record)
    evidence["reference"] = "dream:previous-experiment"
    nap.validate_record(record)


@pytest.mark.parametrize("status", ["candidate", "seed", "rejected"])
def test_unready_nodes_cannot_be_selected(nap, record, status):
    record["nodes"][0]["status"] = status
    record["nodes"][0]["reason"] = "Feasibility unresolved."
    record["selected"] = ["root"]
    with pytest.raises(ValueError, match="ready"):
        nap.validate_record(record)


def test_ready_task_cannot_hide_open_questions(nap, record):
    record["nodes"][0]["status"] = "ready"
    record["nodes"][0]["task"]["open_questions"] = ["Is the API usable?"]
    with pytest.raises(ValueError, match="open_questions"):
        nap.validate_record(record)


@pytest.mark.parametrize("field", ["current_behavior", "desired_behavior", "acceptance_criteria"])
def test_ready_task_requires_an_actionable_contract(nap, record, field):
    record["nodes"][0]["status"] = "ready"
    del record["nodes"][0]["task"][field]
    with pytest.raises(ValueError, match=field):
        nap.validate_record(record)


def test_coherent_child_cannot_omit_connection(nap, record):
    child = node("child", "root")
    del child["parent_connection"]
    record["nodes"].append(child)
    with pytest.raises(ValueError, match="parent_connection"):
        nap.validate_record(record)
    record["mode"] = "broad"
    nap.validate_record(record)


def test_parent_context_shows_diverse_children_without_loading_sibling_bodies(nap, record):
    record["nodes"] += [node("left", "root", "Cancellation"), node("right", "root", "Resuming")]
    record["nodes"][1]["task"]["desired_behavior"] = "LARGE SIBLING IMPLEMENTATION PLAN"
    packet = nap.parent_context(record, "root")
    assert packet["parent"]["id"] == "root"
    assert {child["id"] for child in packet["children"]} == {"left", "right"}
    assert "LARGE SIBLING IMPLEMENTATION PLAN" not in json.dumps(packet)


def test_trajectory_is_one_path_and_is_not_an_implementation_chain(nap, record):
    record["nodes"] += [
        node("left", "root"), node("right", "root"), node("leaf", "left"),
    ]
    path = nap.idea_trajectory(record, "leaf")
    assert path["kind"] == "idea-trajectory"
    assert [item["id"] for item in path["nodes"]] == ["root", "left", "leaf"]
    assert path["base_commit"] == record["base_commit"]


def test_export_uses_final_contract_not_superseded_parent_requirements(nap, record):
    record["nodes"][0]["task"]["acceptance_criteria"] = ["OLD REQUIREMENT"]
    child = node("replacement", "root")
    child["parent_connection"] = connection("replace")
    child["task"]["acceptance_criteria"] = ["NEW REQUIREMENT"]
    record["nodes"].append(child)
    approve(nap, record, "replacement")
    rendered = nap.render_tasks(record)
    assert "NEW REQUIREMENT" in rendered
    assert "OLD REQUIREMENT" not in rendered
    assert "Bounded memory" in rendered
    assert record["base_commit"] in rendered
    assert "not implementation dependencies" in rendered


def test_cli_validates_canonical_record(record, tmp_path, coupon_demo):
    result = run_cli(record, tmp_path, coupon_demo, "--mode", "coherent")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_cli_requested_mode_reaches_validation(record, tmp_path, coupon_demo):
    result = run_cli(record, tmp_path, coupon_demo, "--mode", "broad")
    assert result.returncode == 1
    assert "mode" in result.stderr


def test_cli_checks_evidence_files_at_the_pinned_commit(record, tmp_path, coupon_demo):
    record["nodes"][0]["evidence"][0]["anchor"] = "missing.py::f"
    result = run_cli(record, tmp_path, coupon_demo)
    assert result.returncode == 1
    assert "missing.py" in result.stderr
    assert "Traceback" not in result.stderr


def test_cli_rejects_an_unavailable_commit(record, tmp_path, coupon_demo):
    record["base_commit"] = "0" * 40
    result = run_cli(record, tmp_path, coupon_demo)
    assert result.returncode == 1
    assert "commit" in result.stderr.lower()


@pytest.mark.parametrize("flag", ["--context", "--trajectory"])
def test_cli_views_are_wired(record, tmp_path, coupon_demo, flag):
    result = run_cli(record, tmp_path, coupon_demo, flag, "root")
    assert result.returncode == 0, result.stderr
    output = json.loads(result.stdout)
    assert ("parent" if flag == "--context" else "nodes") in output


def test_cli_export_is_utf8_and_does_not_overwrite_record(nap, record, tmp_path, coupon_demo):
    record["nodes"][0]["title"] = "Export caf\u00e9"
    approve(nap, record, "root")
    output = tmp_path / "tasks with spaces.md"
    result = run_cli(record, tmp_path, coupon_demo, "--export", str(output))
    assert result.returncode == 0, result.stderr
    assert "caf\u00e9" in output.read_text(encoding="utf-8")
    again = run_cli(record, tmp_path, coupon_demo, "--export", str(output))
    assert again.returncode == 1
    assert "exists" in again.stderr
    result = run_cli(record, tmp_path, coupon_demo, "--export", str(tmp_path / "nap run.json"))
    assert result.returncode == 1
    assert json.loads((tmp_path / "nap run.json").read_text(encoding="utf-8")) == record


def test_exports_do_not_pollute_discovery_views(record, tmp_path, coupon_demo, shadow_knowledge):
    before = shadow_knowledge.get_all_shadow_files(coupon_demo / ".shadow")
    output = coupon_demo / ".shadow" / "_meta" / "naps" / "tasks.md"
    result = run_cli(record, tmp_path, coupon_demo, "--export", str(output))
    assert result.returncode == 0, result.stderr
    assert shadow_knowledge.get_all_shadow_files(coupon_demo / ".shadow") == before
    result = run_cli(
        record, tmp_path, coupon_demo,
        "--export", str(coupon_demo / ".shadow" / "nap-proposals.md"),
    )
    assert result.returncode == 1
    assert not (coupon_demo / ".shadow" / "nap-proposals.md").exists()


def test_cli_runs_without_bash_on_path(record, tmp_path, coupon_demo):
    env = os.environ.copy()
    if os.name == "nt":
        env["PATH"] = os.pathsep.join(
            item for item in env["PATH"].split(os.pathsep)
            if not (Path(item) / "bash.exe").is_file()
        )
    else:
        tools = tmp_path / "native tools"
        tools.mkdir()
        (tools / "git").symlink_to(shutil.which("git"))
        env["PATH"] = str(tools)
    assert shutil.which("bash", path=env["PATH"]) is None
    assert shutil.which("git", path=env["PATH"]) is not None
    result = run_cli(record, tmp_path, coupon_demo, env=env)
    assert result.returncode == 0, result.stderr


def test_invalid_json_is_an_explicit_error(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("agent_dir", [".github", ".claude"])
def test_installed_layout_finds_shared_contract(record, tmp_path, coupon_demo, repo_root, agent_dir):
    skills = tmp_path / "installed project" / agent_dir / "skills"
    for name in ("shadow-frog", "shadow-frog-nap"):
        shutil.copytree(
            repo_root / "skills" / name, skills / name,
            ignore=shutil.ignore_patterns("__pycache__"),
        )
    data = tmp_path / "run.json"
    data.write_text(json.dumps(record), encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable, str(skills / "shadow-frog-nap" / "nap.py"),
            str(data), "--repo", str(coupon_demo), "--mode", "coherent",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"] is True


def test_documented_record_and_task_shapes_match_helper(nap, record, repo_root):
    text = (repo_root / "skills/shadow-frog-nap/SKILL.md").read_text(encoding="utf-8")
    examples = [json.loads(block) for block in re.findall(r"```json\n(.*?)\n```", text, re.S)]
    template = next(example for example in examples if "nodes" in example)
    task = next(example for example in examples if "task" in example)
    template["base_commit"] = record["base_commit"]
    template["nodes"][0]["evidence"] = record["nodes"][0]["evidence"]
    nap.validate_record(template)
    template["nodes"][0].update(task)
    approve(nap, template, "n1")
    nap.validate_record(template)


@pytest.mark.parametrize("flag", ["--context", "--trajectory"])
def test_empty_view_id_is_not_silently_ignored(record, tmp_path, coupon_demo, flag):
    result = run_cli(record, tmp_path, coupon_demo, flag, "")
    assert result.returncode == 1
    assert "Unknown node" in result.stderr


def test_nap_needs_neither_a_shadow_nor_a_remote(nap, record, tmp_path, tmp_git_repo):
    (tmp_git_repo / "cart.py").write_text(
        "def calculate_total(items):\n    return sum(items)\n", encoding="utf-8",
    )
    env = os.environ.copy()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    for args in (["add", "cart.py"], ["commit", "-q", "-m", "source baseline"]):
        subprocess.run(["git", *args], cwd=tmp_git_repo, env=env, check=True)
    record["base_commit"] = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_git_repo, env=env,
        check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    approve(nap, record, "root")
    output = tmp_path / "standalone tasks.md"
    result = run_cli(record, tmp_path, tmp_git_repo, "--export", str(output))
    assert result.returncode == 0, result.stderr
    assert output.is_file()
    assert not (tmp_git_repo / ".shadow").exists()

    unsafe_output = tmp_git_repo / ".shadow" / "_meta" / "naps" / "tasks.md"
    result = run_cli(record, tmp_path, tmp_git_repo, "--export", str(unsafe_output))
    assert result.returncode == 1
    assert "partial shadow" in result.stderr
    assert not (tmp_git_repo / ".shadow").exists()


def test_recorded_probe_commands_are_never_executed(record, tmp_path, coupon_demo):
    sentinel = tmp_path / "must-not-exist"
    record["nodes"][0]["probes"] = [{
        "question": "An already recorded probe",
        "command": [
            sys.executable, "-c",
            f"from pathlib import Path; Path({str(sentinel)!r}).touch()",
        ],
        "exit_code": 0,
        "result": "Recorded output, not a request to rerun.",
    }]
    record["nodes"][0]["evidence"][0].update(kind="probe", probe=0)
    result = run_cli(record, tmp_path, coupon_demo)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["probes"] == 1
    assert not sentinel.exists()


def test_utf8_bom_records_are_read_explicitly(record, tmp_path, coupon_demo):
    path = tmp_path / "powershell record.json"
    path.write_text(json.dumps(record), encoding="utf-8-sig")
    result = subprocess.run(
        [
            sys.executable, str(SCRIPT), str(path), "--repo", str(coupon_demo),
            "--mode", "coherent",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr


def test_context_output_is_utf8_under_a_narrow_locale(record, tmp_path, coupon_demo):
    record["nodes"][0]["title"] = "Export caf\u00e9"
    env = os.environ.copy()
    env.update(
        PYTHONIOENCODING="cp1252", PYTHONUTF8="0", PYTHONCOERCECLOCALE="0",
        LANG="C", LC_ALL="C",
    )
    result = run_cli(record, tmp_path, coupon_demo, "--context", "root", env=env)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["parent"]["title"] == "Export caf\u00e9"


@pytest.mark.parametrize("mode,expected", [("broad", 0), ("coherent", 1)])
def test_cli_default_mode_is_broad_and_cannot_silently_enable_coherent(
    record, tmp_path, coupon_demo, mode, expected,
):
    record["mode"] = mode
    path = tmp_path / "mode.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--repo", str(coupon_demo)],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == expected, result.stderr
    if expected:
        assert "Requested mode broad" in result.stderr


def test_export_audiences_share_one_active_contract(nap):
    run = make_record("a" * 40)
    run["nodes"][0]["task"]["acceptance_criteria"] = ["DISCARDED WORKER REQUIREMENT"]
    child = node("replacement", "root", "Cancellable bounded-memory export")
    child["parent_connection"] = connection("replace")
    child["parent_connection"]["basis"] = "Detailed planning rationale about the old worker."
    child["task"].update(
        constraints=["Memory remains bounded", "Cancellation remains available"],
        design_suggestions=["Consider a pull-based iterator"],
        implementation_risks=["Cancellation latency needs measurement during implementation"],
    )
    run["nodes"].append(child)
    approve(nap, run, "replacement")
    before = deepcopy(run)
    planning = nap.render_tasks(run, audience="planning")
    implementation = nap.render_tasks(run, audience="implementation")
    for rendered in (planning, implementation):
        for required in (
            child["task"]["desired_behavior"], *child["task"]["acceptance_criteria"],
            *child["task"]["constraints"], *child["task"]["non_goals"],
        ):
            assert required in rendered
        assert "Suggested implementation (non-binding)" in rendered
        assert "Consider a pull-based iterator" in rendered
        assert "Cancellation latency needs measurement" in rendered
        assert "Planning review: accepted" in rendered
        assert "Implementation: not assessed by Nap" in rendered
        assert "Runtime validation: not performed by Nap" in rendered
        assert "DISCARDED WORKER REQUIREMENT" not in rendered
        assert "`cart.py::calculate_total`" in rendered
    assert "# Nap planning briefs" in planning
    assert "# Nap implementation handoffs" in implementation
    assert "Detailed planning rationale about the old worker." in planning
    assert "Detailed planning rationale about the old worker." not in implementation
    assert run == before


def test_both_audiences_retain_explicit_parent_commitments(nap):
    run = make_record("a" * 40)
    child = node("child", "root")
    child["parent_connection"] = connection("replace")
    child["parent_connection"]["preserves"] = ["Bounded memory", "Cancellation"]
    run["nodes"].append(child)
    approve(nap, run, "child")
    for audience in ("planning", "implementation"):
        text = nap.render_tasks(run, audience=audience)
        constraints = text.split("### Required constraints\n", 1)[1].split("\n### ", 1)[0]
        assert "Bounded memory" in constraints
        assert "Cancellation" in constraints
        assert "Offset checkpoints" not in constraints


@pytest.mark.parametrize("field", ["constraints", "design_suggestions", "implementation_risks"])
@pytest.mark.parametrize("bad_value", [None, "not a list", [""]])
def test_optional_task_details_are_validated(nap, field, bad_value):
    run = make_record("a" * 40)
    run["nodes"][0]["task"][field] = bad_value
    with pytest.raises(ValueError, match=field):
        nap.validate_record(run)


@pytest.mark.parametrize("field", ["constraints", "design_suggestions", "implementation_risks"])
def test_task_detail_changes_require_a_fresh_judgment(nap, field):
    run = make_record("a" * 40)
    run["nodes"][0]["task"][field] = ["Original detail"]
    approve(nap, run, "root")
    run["nodes"][0]["task"][field] = ["Unreviewed replacement"]
    with pytest.raises(ValueError, match="Stale judgment"):
        nap.render_tasks(run, audience="implementation")


def test_readiness_metadata_never_claims_implementation_or_runtime_validation(nap):
    run = make_record("a" * 40)
    candidate = nap.parent_context(run, "root")["parent_readiness"]
    assert candidate["planning_review"] == "unreviewed"
    assert candidate["implementation_risks"] is None
    run["nodes"][0]["task"]["implementation_risks"] = ["Concurrency needs implementation evidence"]
    approve(nap, run, "root")
    expected = nap.parent_context(run, "root")["parent_readiness"]
    assert expected["status_scope"] == "planning"
    assert expected["planning_review"] == "accepted"
    assert expected["implementation"] == "not_assessed"
    assert expected["runtime_validation"] == "not_performed_by_nap"
    assert expected["review_authenticity"] == "not_attested"
    assert expected["blocking_planning_questions"] == []
    assert expected["implementation_risks"] == ["Concurrency needs implementation evidence"]
    assert nap.idea_trajectory(run, "root")["readiness"]["root"] == expected
    assert nap.parent_context(run, "@base")["children"][0]["status_scope"] == "planning"


def test_selected_path_review_has_concrete_progression_questions(nap):
    run = make_record("a" * 40)
    run["nodes"].append(node("child", "root", "Different architecture"))
    packet = nap.review_packet(run, ["child"])
    questions = " ".join(packet["path_questions"])
    assert "worthwhile outcome" in questions
    assert "uncertainty" in questions
    assert "rephrase" in questions
    assert "same files" in questions
    assert "pinned baseline" in questions
    assert [item["id"] for item in packet["targets"][0]["input"]["path"]] == ["root", "child"]


@pytest.mark.parametrize("audience", ["planning", "implementation"])
def test_cli_export_audience_and_readiness_are_wired(nap, record, tmp_path, coupon_demo, audience):
    record["nodes"][0]["task"]["implementation_risks"] = ["Performance needs measurement"]
    approve(nap, record, "root")
    output = tmp_path / f"{audience}.md"
    result = run_cli(
        record, tmp_path, coupon_demo, "--export", str(output), "--audience", audience,
    )
    assert result.returncode == 0, result.stderr
    response = json.loads(result.stdout)
    assert response["audience"] == audience
    assert response["readiness"]["root"]["planning_review"] == "accepted"
    assert response["readiness"]["root"]["implementation"] == "not_assessed"
    assert ("planning briefs" if audience == "planning" else "implementation handoffs") in output.read_text(encoding="utf-8")


def test_export_audience_is_not_silently_ignored_on_other_commands(record, tmp_path, coupon_demo):
    result = run_cli(record, tmp_path, coupon_demo, "--context", "root", "--audience", "implementation")
    assert result.returncode == 2
    assert "--export" in result.stderr


def test_record_data_is_flushed_and_synced_before_publication(nap, tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    path.write_text('{"old": true}\n', encoding="utf-8")
    original_fsync = os.fsync
    observed = []

    def observe_sync(fd):
        observed.append(os.fstat(fd).st_size)
        assert path.read_text(encoding="utf-8") == '{"old": true}\n'
        original_fsync(fd)

    monkeypatch.setattr(nap.os, "fsync", observe_sync)
    nap._save_record(path, {"new": "caf\u00e9"})
    assert observed and observed[0] > 0
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": "caf\u00e9"}


def test_failed_data_sync_preserves_the_previous_record(nap, tmp_path, monkeypatch):
    path = tmp_path / "record.json"
    original = b'{"old": true}\n'
    path.write_bytes(original)

    def fail_sync(fd):
        raise OSError("simulated filesystem sync failure")

    monkeypatch.setattr(nap.os, "fsync", fail_sync)
    with pytest.raises(OSError, match="sync failure"):
        nap._save_record(path, {"new": True})
    assert path.read_bytes() == original
    assert list(tmp_path.iterdir()) == [path]


def test_unsupported_sync_is_explicit_not_a_silent_durability_claim(
    nap, tmp_path, monkeypatch, capsys,
):
    path = tmp_path / "record.json"
    path.write_text('{"old": true}\n', encoding="utf-8")

    def unsupported(fd):
        raise OSError(errno.ENOTSUP, "sync unsupported")

    monkeypatch.setattr(nap.os, "fsync", unsupported)
    nap._save_record(path, {"new": True})
    assert json.loads(path.read_text(encoding="utf-8")) == {"new": True}
    warning = capsys.readouterr().err
    assert "WARNING" in warning and "not supported" in warning
    assert "reboot durability" in warning
