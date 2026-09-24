#!/usr/bin/env python3
"""Pin current Dream tooling outside code history, then dispatch its Python helpers.

The snapshot is a host-local run artifact. It is never committed to the target
repository or automatically deleted; keep it while children or resumed work
still use it. This helper does not require or invoke Bash.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import runpy
import shutil
import subprocess
import sys
import tempfile


MODES = ("broad", "coherent")
TOOLS = {
    "validate": "dream-validate.py",
    "reconcile": "dream-reconcile.py",
    "coverage": "dream-coverage.py",
}
DREAM_DIR = Path(__file__).resolve().parent
CORE_DIR = DREAM_DIR.parent / "shadow-frog"
MANIFEST_NAME = "tooling.json"


@dataclass(frozen=True)
class ToolingSnapshot:
    repo_root: Path
    mode: str
    files: dict[str, str]

    def __post_init__(self):
        if not isinstance(self.repo_root, Path) or not self.repo_root.is_absolute():
            raise ValueError("Tooling repo_root must be an absolute path")
        if not isinstance(self.mode, str) or self.mode not in MODES:
            raise ValueError("Tooling mode must be broad or coherent")
        if not isinstance(self.files, dict) or not self.files:
            raise ValueError("Tooling files must be a nonempty hash map")
        for name, digest in self.files.items():
            if (
                not isinstance(name, str)
                or "\\" in name
                or any(part in ("", ".", "..") for part in name.split("/"))
                or PurePosixPath(name).is_absolute()
                or name.split("/")[0] not in ("shadow-frog", "shadow-frog-dream")
                or not isinstance(digest, str)
                or not re.fullmatch(r"[0-9a-f]{64}", digest)
            ):
                raise ValueError(f"Invalid tooling file entry: {name}")


def _git_root(path: Path, *, required: bool) -> Path | None:
    env = os.environ.copy()
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
        env.pop(key, None)
    result = subprocess.run(
        ["git", "--no-pager", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, encoding="utf-8", timeout=10, env=env,
    )
    if result.returncode:
        if required:
            raise ValueError(f"Cannot resolve repository: {result.stderr.strip()}")
        return None
    return Path(result.stdout.rstrip("\r\n")).resolve()


def _source_files() -> dict[str, Path]:
    sources = {}
    for directory in (DREAM_DIR, CORE_DIR):
        for path in directory.iterdir():
            if path.name != "SKILL.md" and path.suffix not in (".py", ".sh"):
                continue
            if path.is_symlink() or not path.is_file():
                raise ValueError(f"Tooling assets must be regular files: {path}")
            sources[f"{directory.name}/{path.name}"] = path
    required = {
        "shadow-frog/SKILL.md", "shadow-frog/_coherence.py", "shadow-frog/_citations.py",
        "shadow-frog-dream/SKILL.md", "shadow-frog-dream/dream-tools.py",
        "shadow-frog-dream/_worktree_safety.py",
        *(f"shadow-frog-dream/{name}" for name in TOOLS.values()),
    }
    missing = required - sources.keys()
    if missing:
        raise ValueError(f"Incomplete current skill installation: {', '.join(sorted(missing))}")
    return sources


def pin_tooling(repo: Path, output: Path, mode: str) -> dict:
    """Create a fresh snapshot; publish its manifest only after all copies finish."""
    repo_root = _git_root(repo, required=True)
    source_repo = _git_root(DREAM_DIR, required=False)
    destination = output.resolve()
    for protected in (repo_root, source_repo, DREAM_DIR.parent.resolve()):
        if protected is not None and destination.is_relative_to(protected):
            raise ValueError("Tooling output must be outside the code and skill repositories")
    if output.exists() or output.is_symlink():
        raise ValueError(f"Tooling output already exists: {output}")
    sources = _source_files()
    expected = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in sources.items()}
    snapshot = ToolingSnapshot(repo_root, mode, expected)
    destination.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        if hashlib.sha256(target.read_bytes()).hexdigest() != expected[name]:
            raise ValueError("Source tooling changed while pinning; discard this incomplete snapshot")

    payload = {
        "version": 1, "repo_root": str(snapshot.repo_root),
        "mode": snapshot.mode, "files": snapshot.files,
    }
    raw = (json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")
    manifest = destination / MANIFEST_NAME
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=destination, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(raw)
        os.replace(temporary, manifest)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    digest = hashlib.sha256(raw).hexdigest()
    runner = destination / "shadow-frog-dream" / "dream-tools.py"
    prefix = [sys.executable, str(runner), "run", "--digest", digest]
    commands = {
        name: [*prefix, name, *([str(repo_root)] if name != "validate" else [])]
        for name in TOOLS
    }
    return {
        "manifest": str(manifest), "digest": digest, "mode": mode,
        "repo_root": str(repo_root), "skill_dir": str(runner.parent),
        "instructions": [
            str(destination / "shadow-frog/SKILL.md"),
            str(destination / "shadow-frog-dream/SKILL.md"),
        ],
        "commands": commands,
        "helper_paths": {
            name.removeprefix("shadow-frog-dream/"): str(destination / name)
            for name in sources if name.startswith("shadow-frog-dream/")
        },
    }


def _load_snapshot(root: Path, digest: str) -> ToolingSnapshot:
    manifest = root / MANIFEST_NAME
    if not manifest.is_file():
        raise ValueError("Missing tooling snapshot; pin current tools and use the returned commands")
    raw = manifest.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError("Tooling manifest changed or the wrong digest was supplied; pin a new run")
    payload = json.loads(raw.decode("utf-8"))
    if (
        not isinstance(payload, dict) or type(payload.get("version")) is not int
        or payload["version"] != 1 or not isinstance(payload.get("repo_root"), str)
    ):
        raise ValueError("Invalid tooling snapshot metadata")
    snapshot = ToolingSnapshot(Path(payload["repo_root"]), payload.get("mode"), payload.get("files"))
    actual = set()
    for path in root.rglob("*"):
        if "__pycache__" in path.relative_to(root).parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Tooling snapshot changed: unexpected symlink {path}")
        if path.is_file() and path != manifest:
            actual.add(path.relative_to(root).as_posix())
    if actual != snapshot.files.keys():
        raise ValueError("Tooling snapshot changed: missing or unexpected files")
    for name, expected in snapshot.files.items():
        path = root / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError(f"Tooling file changed: {name}; pin a new run")
    return snapshot


def run_tool(tool: str, arguments: list[str], digest: str) -> None:
    """Execute a checked helper in-process, preserving its output and exit code."""
    root = DREAM_DIR.parent
    snapshot = _load_snapshot(root, digest)
    script = DREAM_DIR / TOOLS[tool]
    if tool == "validate":
        if any(arg == "--mode" or arg.startswith("--mode=") for arg in arguments):
            raise ValueError("Validation mode is pinned; do not override --mode")
        arguments = ["--mode", snapshot.mode, *arguments]
    previous = sys.argv
    bytecode = sys.dont_write_bytecode
    try:
        sys.argv = [str(script), *arguments]
        sys.dont_write_bytecode = True
        runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = previous
        sys.dont_write_bytecode = bytecode


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    pin = commands.add_parser("pin", help="Snapshot current tools before entering historical worktrees")
    pin.add_argument("--repo-root", type=Path, required=True)
    pin.add_argument("--output", type=Path, required=True, help="New directory outside both repositories")
    pin.add_argument("--mode", choices=MODES, default="broad")
    run = commands.add_parser("run", help="Run a helper using the pinned snapshot's code and mode")
    run.add_argument("--digest", required=True)
    run.add_argument("tool", choices=TOOLS)
    run.add_argument("arguments", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        if args.command == "pin":
            print(json.dumps(pin_tooling(args.repo_root, args.output, args.mode),
                             ensure_ascii=False, indent=2))
        else:
            run_tool(args.tool, args.arguments, args.digest)
        return 0
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("ERROR: Interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
