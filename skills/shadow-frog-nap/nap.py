#!/usr/bin/env python3
"""Validate, inspect, and export lightweight feature-ideation records.

Uses only Python's standard library and Git. It never runs recorded probes,
implements features, changes branches, pushes, or writes shadow discoveries.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, fields
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

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
    max_depth: int = 2
    max_probes: int = 2
    max_tasks: int = 2

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            minimum = 0 if item.name in ("max_depth", "max_probes") else 1
            if type(value) is not int or value < minimum:
                raise ValueError(f"limits.{item.name} must be an integer >= {minimum}")

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
    for field in ("current_behavior", "desired_behavior"):
        _text(task.get(field), f"{name}.{field}")
    _strings(task.get("acceptance_criteria"), f"{name}.acceptance_criteria", nonempty=True)
    _strings(task.get("non_goals"), f"{name}.non_goals")
    questions = _strings(task.get("open_questions"), f"{name}.open_questions")
    if ready and questions:
        raise ValueError(f"{name}.open_questions must be resolved before marking ready")


def _validate_node(value: object, mode: str) -> dict:
    node = _object(value, "node")
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


def validate_record(value: object) -> None:
    """Validate shapes, lineage, recorded budgets, and selected task readiness."""
    run = _object(value, "run record")
    if type(run.get("version")) is not int or run["version"] != 1:
        raise ValueError("version must be 1")
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
        if len(_path_to(nodes, node_id)) - 1 > limits.max_depth:
            raise ValueError(f"max_depth exceeded by node {node_id}")
    if probes > limits.max_probes:
        raise ValueError(f"max_probes exceeded: {probes} > {limits.max_probes}")
    selected = _strings(run.get("selected"), "selected")
    if len(set(selected)) != len(selected):
        raise ValueError("selected contains duplicate node ids")
    if len(selected) > limits.max_tasks:
        raise ValueError(f"max_tasks exceeded: {len(selected)} > {limits.max_tasks}")
    for node_id in selected:
        if node_id not in nodes or nodes[node_id]["status"] != "ready":
            raise ValueError(f"selected node {node_id} must exist and be ready")


def _summary(node: dict) -> dict:
    return {field: node[field] for field in ("id", "title", "goal", "status")}


def parent_context(run: dict, node_id: str) -> dict:
    """Return one parent's evidence plus small lineage/diversity summaries."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    path = _path_to(nodes, node_id)
    return {
        "base_commit": run["base_commit"],
        "mode": run["mode"],
        "limits": asdict(RunLimits.from_record(run.get("limits", {}))),
        "parent": path[-1],
        "ancestors": [_summary(node) for node in path[:-1]],
        "children": [
            _summary(node) for node in nodes.values() if node.get("parent_id") == node_id
        ],
    }


def idea_trajectory(run: dict, node_id: str) -> dict:
    """Select a root-to-node idea path, never a concatenation of siblings."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    return {
        "kind": "idea-trajectory",
        "base_commit": run["base_commit"],
        "mode": run["mode"],
        "nodes": _path_to(nodes, node_id),
    }


def render_tasks(run: dict) -> str:
    """Render only selected nodes' final contracts, not inherited requirements."""
    validate_record(run)
    nodes = {node["id"]: node for node in run["nodes"]}
    lines = [
        "# Nap task briefs", "",
        f"Base commit: `{run['base_commit']}`", "",
        "These are source-grounded proposals, not verified implementations.",
        "Parent links are idea provenance, not implementation dependencies.", "",
    ]
    if not run["selected"]:
        lines += ["No tasks selected. Do not invent filler tasks.", ""]
    for node_id in run["selected"]:
        node = nodes[node_id]
        task = node["task"]
        lines += [
            f"## {node['title']}", "",
            f"ID: `{node_id}`",
            f"Parent idea: `{node.get('parent_id') or 'none'}`", "",
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


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record", type=Path, help="Canonical nap JSON record")
    parser.add_argument("--repo", type=Path, default=Path.cwd(), help="Source repository")
    parser.add_argument("--mode", choices=MODES, help="Require the requested mode")
    views = parser.add_mutually_exclusive_group()
    views.add_argument("--context", metavar="NODE", help="Compact context for proposing children")
    views.add_argument("--trajectory", metavar="NODE", help="One root-to-node idea path as JSON")
    views.add_argument("--export", type=Path, metavar="PATH", help="Write selected task briefs to a new file")
    args = parser.parse_args()
    try:
        run = json.loads(args.record.read_text(encoding="utf-8-sig"))
        validate_record(run)
        if args.mode is not None and run["mode"] != args.mode:
            raise ValueError(f"Requested mode {args.mode} does not match record mode {run['mode']}")
        repo = Path(_git(args.repo, "rev-parse", "--show-toplevel").rstrip("\r\n")).resolve()
        _check_archive_path(args.record.resolve(), repo)
        validate_snapshot(run, repo)
        if args.context is not None:
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
                "nodes": len(run["nodes"]), "tasks": len(run["selected"]),
                "probes": sum(len(node.get("probes", [])) for node in run["nodes"]),
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
