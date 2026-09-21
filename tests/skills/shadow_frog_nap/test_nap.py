"""Structural contracts and real Git/CLI integration, not ideation-quality scoring."""

from copy import deepcopy
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
        "status": "ready",
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


@pytest.fixture
def record(coupon_demo):
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo, check=True,
        capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()
    return {
        "version": 1,
        "mode": "coherent",
        "base_commit": commit,
        "limits": {"max_nodes": 7, "max_depth": 2, "max_probes": 2, "max_tasks": 2},
        "nodes": [node()],
        "selected": ["root"],
    }


def run_cli(record, tmp_path, repo, *args, env=None):
    path = tmp_path / "nap run.json"
    path.write_text(json.dumps(record), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path), "--repo", str(repo), *args],
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


def test_ten_siblings_have_distinct_goals_on_the_same_files(nap, record):
    record["limits"]["max_nodes"] = 11
    record["nodes"] += [
        node(f"child-{i}", "root", f"Distinct direction {i}") for i in range(10)
    ]
    record["selected"] = ["child-1", "child-9"]
    nap.validate_record(record)


def test_nodes_need_not_be_in_topological_order(nap, record):
    record["nodes"].insert(0, node("child", "root"))
    record["selected"] = ["child"]
    nap.validate_record(record)


@pytest.mark.parametrize("value", [None, [], "run", 1])
def test_record_must_be_an_object(nap, value):
    with pytest.raises(ValueError, match="object"):
        nap.validate_record(value)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("version", 2, "version"),
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
        ("max_probes", 1.5),
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
    record["selected"].append("child")
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
    with pytest.raises(ValueError, match="ready"):
        nap.validate_record(record)


def test_ready_task_cannot_hide_open_questions(nap, record):
    record["nodes"][0]["task"]["open_questions"] = ["Is the API usable?"]
    with pytest.raises(ValueError, match="open_questions"):
        nap.validate_record(record)


@pytest.mark.parametrize("field", ["current_behavior", "desired_behavior", "acceptance_criteria"])
def test_ready_task_requires_an_actionable_contract(nap, record, field):
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
    record["selected"] = ["replacement"]
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


def test_cli_export_is_utf8_and_does_not_overwrite_record(record, tmp_path, coupon_demo):
    record["nodes"][0]["title"] = "Export caf\u00e9"
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


def test_exports_do_not_pollute_discovery_views(record, tmp_path, coupon_demo, shadow_viewer):
    before = shadow_viewer.get_all_shadow_files(coupon_demo / ".shadow")
    output = coupon_demo / ".shadow" / "_meta" / "naps" / "tasks.md"
    result = run_cli(record, tmp_path, coupon_demo, "--export", str(output))
    assert result.returncode == 0, result.stderr
    assert shadow_viewer.get_all_shadow_files(coupon_demo / ".shadow") == before
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
    template["nodes"][0]["status"] = "ready"
    template["selected"] = ["n1"]
    nap.validate_record(template)


@pytest.mark.parametrize("flag", ["--context", "--trajectory"])
def test_empty_view_id_is_not_silently_ignored(record, tmp_path, coupon_demo, flag):
    result = run_cli(record, tmp_path, coupon_demo, flag, "")
    assert result.returncode == 1
    assert "Unknown node" in result.stderr


def test_nap_needs_neither_a_shadow_nor_a_remote(record, tmp_path, tmp_git_repo):
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
        [sys.executable, str(SCRIPT), str(path), "--repo", str(coupon_demo)],
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
