#!/usr/bin/env python3
"""Manage, review, inspect, and export implementation-free feature-idea trees.

Uses only Python's standard library and Git. Model generation and independent
judgment belong to the host agent; this helper checks their records, not their
authenticity or semantic truth. It never calls models, runs recorded probes,
implements features, changes branches, pushes, or writes shadow discoveries.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, dataclass, fields
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "shadow-frog"))
try:
    from _coherence import MODES, validate_connection
except ImportError as exc:
    raise SystemExit("ERROR: Missing shared shadow-frog/_coherence.py; reinstall the full skill set") from exc
finally:
    sys.path.pop(0)


@dataclass(frozen=True)
class RunLimits:
    """Bounds on recorded work, not a hard limit on an agent's API spending."""

    max_nodes: int = 7
    max_depth: int | None = None
    max_probes: int = 2
    max_tasks: int = 2
    max_reviews: int = 2

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name == "max_depth" and value is None:
                continue
            minimum = 0 if item.name in ("max_depth", "max_probes", "max_reviews") else 1
            if type(value) is not int or value < minimum:
                allowed = "null or an integer >= 0" if item.name == "max_depth" else f"an integer >= {minimum}"
                raise ValueError(f"limits.{item.name} must be {allowed}")

    @classmethod
    def from_record(cls, value: object) -> RunLimits:
        data = _object(value, "limits")
        unknown = data.keys() - {item.name for item in fields(cls)}
        if unknown:
            raise ValueError(f"Unknown limits: {', '.join(sorted(unknown))}")
        return cls(**data)


def _object(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be an object")
    return value


def _text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value


def _strings(value: object, name: str, *, nonempty: bool = False) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(f"{name} must be {'a nonempty' if nonempty else 'a'} list of strings")
    return [_text(item, name) for item in value]


def _identifier(value: object, name: str) -> str:
    text = _text(value, name)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}", text):
        raise ValueError(f"{name} must be a portable identifier (letters, digits, . _ -)")
    return text


def _anchor_file(value: object) -> str:
    anchor = _text(value, "evidence.anchor")
    path, separator, symbol = anchor.partition("::")
    if (
        not separator or not path or not symbol.strip()
        or PurePosixPath(path).is_absolute()
        or any(part in ("", ".", "..") for part in path.split("/"))
        or any(char in anchor for char in ("\0", "\r", "\n", "\\", "`"))
        or ":" in path
    ):
        raise ValueError("evidence.anchor must be a repository-relative file::symbol")
    return path


def _validate_task(value: object, name: str, ready: bool):
    task = _object(value, name)
    allowed = {
        "current_behavior", "desired_behavior", "acceptance_criteria",
        "non_goals", "open_questions",
    }
    if task.keys() - allowed:
        raise ValueError(f"{name} has unknown fields: {', '.join(sorted(task.keys() - allowed))}")
    for field in ("current_behavior", "desired_behavior"):
        _text(task.get(field), f"{name}.{field}")
    _strings(task.get("acceptance_criteria"), f"{name}.acceptance_criteria", nonempty=True)
    _strings(task.get("non_goals"), f"{name}.non_goals")
    questions = _strings(task.get("open_questions"), f"{name}.open_questions")
    if ready and questions:
        raise ValueError(f"{name}.open_questions must be resolved before marking ready")


def _validate_node(value: object, mode: str) -> dict:
    node = _object(value, "node")
    allowed = {
        "id", "parent_id", "title", "goal", "status", "parent_connection",
        "evidence", "probes", "task", "reason", "review_id",
    }
    if node.keys() - allowed:
        raise ValueError(f"Unknown node fields: {', '.join(sorted(node.keys() - allowed))}")
    node_id = _identifier(node.get("id"), "node.id")
    name = f"node {node_id}"
    _text(node.get("title"), f"{name}.title")
    _text(node.get("goal"), f"{name}.goal")
    parent = node.get("parent_id")
    if parent is not None:
        _identifier(parent, f"{name}.parent_id")
    status = node.get("status")
    if status not in ("seed", "candidate", "ready", "rejected"):
        raise ValueError(f"{name}.status must be seed, candidate, ready, or rejected")
    if status == "rejected":
        _text(node.get("reason"), f"{name}.reason")
    errors = validate_connection(mode, node["goal"], parent, node.get("parent_connection"))
    if errors:
        raise ValueError(f"{name}: {'; '.join(errors)}")

    probes = node.get("probes", [])
    if not isinstance(probes, list):
        raise ValueError(f"{name}.probes must be a list")
    for index, value in enumerate(probes):
        label = f"{name}.probes[{index}]"
        probe = _object(value, label)
        _text(probe.get("question"), f"{label}.question")
        _strings(probe.get("command"), f"{label}.command", nonempty=True)
        if type(probe.get("exit_code")) is not int:
            raise ValueError(f"{label}.exit_code must be an integer from an executed command")
        _text(probe.get("result"), f"{label}.result")

    evidence = node.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"{name}.evidence must contain at least one grounded observation")
    for index, value in enumerate(evidence):
        label = f"{name}.evidence[{index}]"
        entry = _object(value, label)
        _anchor_file(entry.get("anchor"))
        _text(entry.get("observation"), f"{label}.observation")
        kind = entry.get("kind")
        if kind not in ("inspection", "probe", "inherited"):
            raise ValueError(f"{label}.kind must be inspection, probe, or inherited")
        if kind == "inherited":
            _text(entry.get("reference"), f"{label}.reference")
        if kind == "probe":
            probe_index = entry.get("probe")
            if type(probe_index) is not int or not 0 <= probe_index < len(probes):
                raise ValueError(f"{label}.probe must index an executed probe in this node")

    if status == "ready" or node.get("task") is not None:
        _validate_task(node.get("task"), f"{name}.task", status == "ready")
    return node


def _path_to(nodes: dict, node_id: str) -> list[dict]:
    path = []
    seen = set()
    current = node_id
    while current is not None:
        if current in seen:
            raise ValueError(f"parent cycle at node {current}")
        if current not in nodes:
            raise ValueError(f"Unknown node or parent: {current}")
        seen.add(current)
        node = nodes[current]
        path.append(node)
        current = node.get("parent_id")
    return list(reversed(path))


def _review_input(run: dict, node_id: str) -> dict:
    nodes = {node["id"]: node for node in run["nodes"]}
    return {
        "base_commit": run["base_commit"],
        "mode": run["mode"],
        "path": [
            {
                key: deepcopy(value) for key, value in node.items()
                if key not in ("status", "reason", "review_id")
            }
            for node in _path_to(nodes, node_id)
        ],
    }


def _input_hash(value: dict) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _review_for(run: dict, node: dict) -> dict | None:
    review_id = node.get("review_id")
    if review_id is None:
        return None
    for review in run["reviews"]:
        if review["id"] == review_id:
            for judgment in review["judgments"]:
                if judgment["node_id"] == node["id"]:
                    return {"review_id": review_id, "reviewer": review["reviewer"], **judgment}
    raise ValueError(f"Node {node['id']} references an unavailable review")


def _validate_reviews(run: dict, nodes: dict, limits: RunLimits) -> None:
    reviews = run.get("reviews")
    if not isinstance(reviews, list):
        raise ValueError("reviews must be a list")
    if len(reviews) > limits.max_reviews:
        raise ValueError(f"max_reviews exceeded: {len(reviews)} > {limits.max_reviews}")
    seen = set()
    latest = {}
    for value in reviews:
        review = _object(value, "review")
        review_id = _identifier(review.get("id"), "review.id")
        if review_id in seen:
            raise ValueError(f"duplicate review id: {review_id}")
        seen.add(review_id)
        _text(review.get("reviewer"), f"review {review_id}.reviewer")
        judgments = review.get("judgments")
        if not isinstance(judgments, list) or not judgments:
            raise ValueError(f"review {review_id}.judgments must be a nonempty list")
        judged = set()
        for item in judgments:
            judgment = _object(item, "judgment")
            node_id = _identifier(judgment.get("node_id"), "judgment.node_id")
            if node_id not in nodes or node_id in judged:
                raise ValueError(f"Unknown or duplicate judgment node: {node_id}")
            judged.add(node_id)
            latest[node_id] = review_id
            if judgment.get("input_hash") != _input_hash(_review_input(run, node_id)):
                raise ValueError(f"Stale judgment for {node_id}: proposal, ancestry, mode or base changed")
            decision = judgment.get("decision")
            if decision not in ("accept", "revise", "reject"):
                raise ValueError("judgment.decision must be accept, revise, or reject")
            _text(judgment.get("rationale"), "judgment.rationale")
            blockers = _strings(judgment.get("blocking_issues"), "judgment.blocking_issues")
            evidence = _strings(judgment.get("evidence"), "judgment.evidence", nonempty=True)
            for anchor in evidence:
                _anchor_file(anchor)
            if decision == "accept":
                if blockers:
                    raise ValueError("An accept judgment cannot contain blocking_issues")
                _validate_task(nodes[node_id].get("task"), f"node {node_id}.task", True)
            elif not blockers:
                raise ValueError("A revise/reject judgment must explain blocking_issues")
    statuses = {"accept": "ready", "revise": "candidate", "reject": "rejected"}
    for node in nodes.values():
        if node.get("review_id") is not None:
            _identifier(node["review_id"], "node.review_id")
            if node["review_id"] != latest.get(node["id"]):
                raise ValueError(f"Node {node['id']} must reference its latest judgment")
            judgment = _review_for(run, node)
            if node["status"] != statuses[judgment["decision"]]:
                raise ValueError(f"Node {node['id']} status does not match its review")
        elif node["id"] in latest:
            raise ValueError(f"Node {node['id']} must reference its latest judgment")
        elif node["status"] == "ready":
            raise ValueError(f"Ready node {node['id']} requires a current accepted judgment")


def validate_record(value: object) -> None:
    """Validate shapes, lineage, recorded budgets, and selected task readiness."""
    run = _object(value, "run record")
    if type(run.get("version")) is not int or run["version"] != 2:
        raise ValueError("version must be 2; import older proposals as unreviewed candidates")
    if type(run.get("revision")) is not int or run["revision"] < 0:
        raise ValueError("revision must be a nonnegative integer")
    mode = run.get("mode")
    if not isinstance(mode, str) or mode not in MODES:
        raise ValueError("mode must be broad or coherent")
    commit = run.get("base_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", commit):
        raise ValueError("base_commit must be a full, lowercase Git commit ID")
    limits = RunLimits.from_record(run.get("limits", {}))
    entries = run.get("nodes")
    if not isinstance(entries, list):
        raise ValueError("nodes must be a list")
    if len(entries) > limits.max_nodes:
        raise ValueError(f"max_nodes exceeded: {len(entries)} > {limits.max_nodes}")
    nodes = {}
    probes = 0
    for value in entries:
        node = _validate_node(value, mode)
        if node["id"] in nodes:
            raise ValueError(f"duplicate node id: {node['id']}")
        nodes[node["id"]] = node
        probes += len(node.get("probes", []))
    for node_id in nodes:
        depth = len(_path_to(nodes, node_id)) - 1
        if limits.max_depth is not None and depth > limits.max_depth:
            raise ValueError(f"max_depth exceeded by node {node_id}")
    if probes > limits.max_probes:
        raise ValueError(f"max_probes exceeded: {probes} > {limits.max_probes}")
    _validate_reviews(run, nodes, limits)
    selected = _strings(run.get("selected"), "selected")
    if len(set(selected)) != len(selected):
        raise ValueError("selected contains duplicate node ids")
    if len(selected) > limits.max_tasks:
        raise ValueError(f"max_tasks exceeded: {len(selected)} > {limits.max_tasks}")
    for node_id in selected:
        if node_id not in nodes or nodes[node_id]["status"] != "ready":
            raise ValueError(f"selected node {node_id} must exist and be ready")


def _summary(node: dict) -> dict:
    result = {field: node[field] for field in ("id", "title", "goal", "status")}
    if node.get("review_id") is not None:
        result["review_id"] = node["review_id"]
    return result


def usage(run: dict) -> dict:
    return {
        "nodes": len(run["nodes"]),
        "probes": sum(len(node.get("probes", [])) for node in run["nodes"]),
        "tasks": len(run["selected"]),
        "reviews": len(run["reviews"]),
    }


def parent_context(run: dict, node_id: str) -> dict:
    """Return one parent's evidence plus small lineage/diversity summaries."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    path = [] if node_id == "@base" else _path_to(nodes, node_id)
    parent = path[-1] if path else None
    return {
        "kind": "proposal-context",
        "base_commit": run["base_commit"],
        "mode": run["mode"],
        "revision": run["revision"],
        "limits": asdict(RunLimits.from_record(run.get("limits", {}))),
        "usage": usage(run),
        "parent": parent,
        "parent_review": _review_for(run, parent) if parent else None,
        "ancestors": [_summary(node) for node in path[:-1]],
        "children": [
            _summary(node) for node in nodes.values()
            if node.get("parent_id") == (None if node_id == "@base" else node_id)
        ],
    }


def idea_trajectory(run: dict, node_id: str) -> dict:
    """Select a root-to-node idea path, never a concatenation of siblings."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    path = _path_to(nodes, node_id)
    path_ids = {node["id"] for node in path}
    return {
        "kind": "idea-trajectory",
        "base_commit": run["base_commit"],
        "mode": run["mode"],
        "nodes": path,
        "reviews": [
            {
                **review,
                "judgments": [
                    item for item in review["judgments"] if item["node_id"] in path_ids
                ],
            }
            for review in run["reviews"]
            if any(item["node_id"] in path_ids for item in review["judgments"])
        ],
    }


def review_packet(run: dict, node_ids: list[str]) -> dict:
    """Prepare exact, code-grounded proposal inputs for an external fresh judge."""
    validate_record(run)
    if len(run["reviews"]) >= RunLimits.from_record(run.get("limits", {})).max_reviews:
        raise ValueError("max_reviews exhausted; do not start another judgment without a new budget")
    if not node_ids or len(set(node_ids)) != len(node_ids):
        raise ValueError("Review targets must be a nonempty list of unique node IDs")
    nodes = {node["id"]: node for node in run["nodes"]}
    targets = []
    for node_id in node_ids:
        value = _review_input(run, node_id)
        _validate_task(nodes[node_id].get("task"), f"node {node_id}.task", False)
        targets.append({
            "node_id": node_id, "input_hash": _input_hash(value), "input": value,
            "other_children": [
                {field: node[field] for field in ("id", "title", "goal")}
                for node in nodes.values()
                if node.get("parent_id") == nodes[node_id].get("parent_id") and node["id"] != node_id
            ],
        })
    return {
        "kind": "nap-review-packet",
        "base_commit": run["base_commit"], "mode": run["mode"],
        "targets": targets,
        "rubric": [
            "Verify grounding against the pinned source, not just supplied observations.",
            "Assess plausible feasibility without assuming any proposal is implemented.",
            "Check each parent-child connection; do not require sibling similarity or a fixed tree goal.",
            "Check user value, distinct contribution, scope and measurable acceptance criteria.",
            "Check final active requirements and branch-local supersession for consistency.",
        ],
        "boundary": "Judge planning confidence only. Do not implement features or call proposals execution-verified.",
    }


def _next_id(prefix: str, existing: set[str]) -> str:
    index = 1
    while f"{prefix}{index}" in existing:
        index += 1
    return f"{prefix}{index}"


def _proposal_content(node: dict) -> dict:
    content = {
        key: value for key, value in node.items()
        if key not in ("id", "parent_id", "status", "review_id", "reason")
    }
    return {"probes": [], "parent_connection": None, **content}


def append_proposals(run: dict, parent: str, values: object) -> tuple[dict, dict]:
    validate_record(run)
    if not isinstance(values, list) or not values:
        raise ValueError("Proposal input must be a nonempty JSON list")
    nodes = {node["id"]: node for node in run["nodes"]}
    parent_id = None if parent == "@base" else parent
    if parent_id is not None and parent_id not in nodes:
        raise ValueError(f"Unknown parent: {parent}")
    updated = deepcopy(run)
    existing = set(nodes)
    added = []
    reused = []
    node_ids = []
    for value in values:
        proposal = _object(value, "proposal")
        if proposal.keys() & {"id", "parent_id", "review_id"}:
            raise ValueError("IDs, parent_id and review_id are assigned by the tree driver")
        if proposal.get("status", "candidate") == "ready":
            raise ValueError("New proposals cannot be ready before independent judgment")
        node_id = _next_id("n", existing)
        node = {
            **deepcopy(proposal), "id": node_id, "parent_id": parent_id,
            "status": proposal.get("status", "candidate"),
        }
        _validate_node(node, run["mode"])
        duplicate = next(
            (
                node for node in updated["nodes"]
                if node.get("parent_id") == parent_id
                and _proposal_content(node) == _proposal_content(proposal)
            ),
            None,
        )
        if duplicate is not None:
            reused.append(duplicate["id"])
            node_ids.append(duplicate["id"])
            continue
        updated["nodes"].append(node)
        existing.add(node_id)
        added.append(node_id)
        node_ids.append(node_id)
    validate_record(updated)
    return updated, {
        "added": added, "reused": reused, "node_ids": node_ids, "parent_id": parent_id,
    }


def record_review(run: dict, value: object) -> tuple[dict, dict]:
    validate_record(run)
    payload = _object(value, "review response")
    if set(payload) != {"reviewer", "judgments"}:
        raise ValueError("Review response must contain reviewer and judgments only")
    for review in run["reviews"]:
        if {key: item for key, item in review.items() if key != "id"} == payload:
            return run, {"review_id": review["id"], "recorded": False}
    updated = deepcopy(run)
    review_id = _next_id("r", {review["id"] for review in run["reviews"]})
    review = {"id": review_id, **deepcopy(payload)}
    updated["reviews"].append(review)
    nodes = {node["id"]: node for node in updated["nodes"]}
    judgments = payload.get("judgments")
    if not isinstance(judgments, list) or not judgments:
        raise ValueError("judgments must be a nonempty list")
    statuses = {"accept": "ready", "revise": "candidate", "reject": "rejected"}
    for item in judgments:
        judgment = _object(item, "judgment")
        node_id = _identifier(judgment.get("node_id"), "judgment.node_id")
        if node_id not in nodes:
            raise ValueError(f"Unknown judgment node: {node_id}")
        decision = judgment.get("decision")
        if not isinstance(decision, str) or decision not in statuses:
            raise ValueError("judgment.decision must be accept, revise, or reject")
        node = nodes[node_id]
        node["status"] = statuses[decision]
        node["review_id"] = review_id
        if decision == "reject":
            node["reason"] = judgment.get("rationale")
        else:
            node.pop("reason", None)
    updated["selected"] = [
        node_id for node_id in updated["selected"] if nodes[node_id]["status"] == "ready"
    ]
    validate_record(updated)
    return updated, {"review_id": review_id, "recorded": True}


def render_tasks(run: dict) -> str:
    """Render only selected nodes' final contracts, not inherited requirements."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    lines = [
        "# Nap task briefs", "",
        f"Base commit: `{run['base_commit']}`", "",
        f"Tree revision: {run['revision']}", "",
        "These are judge-reviewed proposals, not verified implementations.",
        "Parent links are idea provenance, not implementation dependencies.", "",
    ]
    if not run["selected"]:
        lines += ["No tasks selected. Do not invent filler tasks.", ""]
    for node_id in run["selected"]:
        node = nodes[node_id]
        task = node["task"]
        review = _review_for(run, node)
        lines += [
            f"## {node['title']}", "",
            f"ID: `{node_id}`",
            f"Parent idea: `{node.get('parent_id') or 'none'}`", "",
            f"Recorded reviewer: {review['reviewer']}", "",
            "### Goal", "", node["goal"], "",
            "### Current behavior", "", task["current_behavior"], "",
            "### Desired behavior", "", task["desired_behavior"], "",
            "### Acceptance criteria", "",
        ]
        lines += [f"- {item}" for item in task["acceptance_criteria"]]
        lines += ["", "### Non-goals", ""]
        lines += [f"- {item}" for item in task["non_goals"]] or ["None stated."]
        lines += ["", "### Evidence", ""]
        for entry in node["evidence"]:
            lines.append(f"- `{entry['anchor']}` ({entry['kind']}): {entry['observation']}")
            if entry["kind"] == "inherited":
                lines.append(f"  Source: {entry['reference']}")
            if entry["kind"] == "probe":
                probe = node["probes"][entry["probe"]]
                lines.append(f"  Command argv: `{json.dumps(probe['command'])}`")
                lines.append(f"  Exit code: {probe['exit_code']}. Result: {probe['result']}")
        edge = node.get("parent_connection")
        if edge:
            lines += [
                "", "### Parent connection", "",
                f"Transition: {edge['relation']}",
                f"Basis: {edge['basis']}", f"Delta: {edge['delta']}", "",
                "Preserved constraints:",
            ]
            lines += [f"- {item}" for item in edge["preserves"]] or ["None stated."]
            lines += ["", "Superseded decisions (not active requirements):"]
            lines += [f"- {item}" for item in edge["supersedes"]] or ["None."]
        lines.append("")
    return "\n".join(lines)


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(name, None)
    result = subprocess.run(
        ["git", "--no-pager", "--literal-pathspecs", "-C", str(repo), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=10, env=env,
    )
    if result.returncode:
        raise ValueError(f"Git {' '.join(args[:2])} failed: {result.stderr.strip()}")
    return result.stdout


def validate_snapshot(run: dict, repo: Path) -> None:
    """Check the actual commit and evidence files, not language-specific symbols."""
    commit = run["base_commit"]
    try:
        resolved = _git(repo, "rev-parse", "--verify", f"{commit}^{{commit}}").strip()
    except ValueError as exc:
        raise ValueError(f"Cannot resolve base_commit {commit}: {exc}") from exc
    if resolved != commit:
        raise ValueError("base_commit must identify a commit, not a tag object")
    expected = {
        _anchor_file(entry["anchor"])
        for node in run["nodes"] for entry in node["evidence"]
    }
    expected.update(
        _anchor_file(anchor)
        for review in run["reviews"] for judgment in review["judgments"]
        for anchor in judgment["evidence"]
    )
    if expected:
        paths = _git(
            repo, "ls-tree", "--full-tree", "-r", "--name-only", "-z",
            commit, "--", *sorted(expected),
        )
        missing = expected - set(paths.split("\0"))
        if missing:
            raise ValueError(
                f"Evidence files absent at base_commit: {', '.join(sorted(missing))}"
            )


def _check_archive_path(path: Path, repo: Path) -> None:
    shadow = (repo / ".shadow").resolve()
    if path.is_relative_to(shadow):
        if (
            not path.is_relative_to(shadow / "_meta" / "naps")
            or not (shadow / "_meta" / "state.json").is_file()
        ):
            raise ValueError(
                "Keep nap artifacts outside .shadow, or in an initialized "
                ".shadow/_meta/naps/; do not create a partial shadow or discovery files"
            )


@contextmanager
def _record_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name + ".lock")
    try:
        stream = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError(
            f"Record lock exists: {lock}. Another writer may be active. "
            "After an interruption, confirm it stopped before removing only this lock."
        ) from exc
    try:
        with stream:
            json.dump({"pid": os.getpid()}, stream)
        yield
    finally:
        lock.unlink()


def _save_record(path: Path, run: dict) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=path.parent, delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(run, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _load_record(path: Path, mode: str) -> dict:
    run = json.loads(path.read_text(encoding="utf-8-sig"))
    validate_record(run)
    if run["mode"] != mode:
        raise ValueError(f"Requested mode {mode} does not match record mode {run['mode']}")
    return run


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="Canonical nap JSON record")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Source repository")
    parser.add_argument("--mode", choices=MODES, default="broad",
                        help="Require the requested mode (default: broad)")
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--context", metavar="NODE", help="Compact context for proposing children")
    views.add_argument("--trajectory", metavar="NODE", help="One root-to-node idea path as JSON")
    views.add_argument("--export", type=Path, metavar="PATH", help="Write selected task briefs to a new file")
    views.add_argument("--init", action="store_true", help="Create an empty tree at an explicit code baseline")
    views.add_argument("--add", type=Path, metavar="PATH", help="Append a JSON list of proposals")
    views.add_argument("--review-packet", nargs="+", metavar="NODE", help="Prepare exact inputs for an independent judge")
    views.add_argument("--record-review", type=Path, metavar="PATH", help="Record a judge response and readiness decisions")
    views.add_argument("--select", nargs="*", metavar="NODE", help="Select accepted nodes, or clear the selection")
    parser.add_argument("--base", help="Git ref/commit to pin; required with --init")
    parser.add_argument("--parent", help="Parent node for --add, or @base for root proposals")
    for item in fields(RunLimits):
        parser.add_argument("--" + item.name.replace("_", "-"), dest=item.name,
                            type=int, help="Initial recorded-work limit (only with --init)")
    args = parser.parse_args()
    limits = {item.name: getattr(args, item.name) for item in fields(RunLimits)
              if getattr(args, item.name) is not None}
    if args.init and not args.base:
        parser.error("--init requires --base")
    if not args.init and (args.base is not None or limits):
        parser.error("--base and initial limit flags require --init")
    if args.add is not None and args.parent is None:
        parser.error("--add requires --parent (use @base for root proposals)")
    if args.add is None and args.parent is not None:
        parser.error("--parent requires --add")
    try:
        repo = Path(_git(args.repo, "rev-parse", "--show-toplevel").rstrip("\r\n")).resolve()
        record_path = args.record.resolve()
        _check_archive_path(record_path, repo)
        mutating = args.init or args.add is not None or args.record_review is not None or args.select is not None
        if mutating:
            with _record_lock(record_path):
                if args.init:
                    if record_path.exists():
                        raise ValueError(f"Record already exists: {record_path}")
                    commit = _git(repo, "rev-parse", "--verify", "--end-of-options",
                                  f"{args.base}^{{commit}}").strip()
                    updated = {
                        "version": 2, "revision": 0, "mode": args.mode,
                        "base_commit": commit, "limits": asdict(RunLimits(**limits)),
                        "nodes": [], "reviews": [], "selected": [],
                    }
                    original = None
                    output = {"initialized": str(record_path), "base_commit": commit}
                else:
                    original = _load_record(record_path, args.mode)
                    if args.add is not None:
                        values = json.loads(args.add.read_text(encoding="utf-8-sig"))
                        updated, output = append_proposals(original, args.parent, values)
                    elif args.record_review is not None:
                        values = json.loads(args.record_review.read_text(encoding="utf-8-sig"))
                        updated, output = record_review(original, values)
                    else:
                        updated = deepcopy(original)
                        updated["selected"] = args.select
                        output = {"selected": args.select}
                    if updated != original:
                        updated["revision"] = original["revision"] + 1
                validate_record(updated)
                validate_snapshot(updated, repo)
                if updated != original:
                    _save_record(record_path, updated)
                output["revision"] = updated["revision"]
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        run = _load_record(record_path, args.mode)
        validate_snapshot(run, repo)
        if args.review_packet is not None:
            output = review_packet(run, args.review_packet)
            output["repo_root"] = str(repo)
        elif args.context is not None:
            output = parent_context(run, args.context)
        elif args.trajectory is not None:
            output = idea_trajectory(run, args.trajectory)
        elif args.export:
            target = args.export.resolve()
            _check_archive_path(target, repo)
            if target.exists():
                raise ValueError(f"Output already exists: {target}; choose a new file")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(render_tasks(run))
            output = {"exported": str(target), "tasks": len(run["selected"])}
        else:
            output = {
                "valid": True, "mode": run["mode"], "base_commit": run["base_commit"],
                "revision": run["revision"], **usage(run),
                "limits": asdict(RunLimits.from_record(run.get("limits", {}))),
            }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
