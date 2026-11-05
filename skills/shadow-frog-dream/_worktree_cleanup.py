"""Shared, fail-closed inspection and removal for native lifecycle commands."""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import importlib.util
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import stat
import subprocess
import sys
from typing import Literal, Optional


SCRIPT_DIR = Path(__file__).resolve().parent
State = Literal["missing", "orphan", "owned", "live-other-or-unknown", "indeterminate"]
Action = Literal["missing", "removed-git", "removed-orphan", "refused"]
COMPONENT_RE = re.compile(r"[A-Za-z0-9._-]+")
GIT_ENV_KEYS = ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")


class CleanupError(Exception):
    def __init__(self, message: str, exit_code: int = 1):
        super().__init__(message)
        self.exit_code = exit_code


class MetadataError(ValueError):
    pass


@dataclass(frozen=True)
class Inspection:
    state: State
    path: Path
    identity: Optional[tuple[int, int]] = None
    reason: str = ""


@dataclass(frozen=True)
class CleanupResult:
    exit_code: int
    action: Action
    message: str


def _load_helper(filename):
    path = SCRIPT_DIR / filename
    if path.resolve().parent != SCRIPT_DIR:
        raise ImportError(f"helper is outside the skill directory: {path}")
    spec = importlib.util.spec_from_file_location(f"_cleanup_{path.stem}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load helper: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache(maxsize=1)
def load_helpers():
    """Load this invocation's adjacent helpers, never a sys.path lookalike."""
    try:
        (SCRIPT_DIR / "_worktree_safety.py").stat()
    except FileNotFoundError as exc:
        raise CleanupError(f"safety module not found: {exc.filename}", 4) from exc
    except OSError as exc:
        raise CleanupError(f"cannot inspect safety module: {exc}") from exc
    try:
        safety = _load_helper("_worktree_safety.py")
        paths = _load_helper("_worktree_paths.py")
        for function in (safety.safe_worktree_path, paths.resolve_worktree_root,
                         paths.canonical_worktree_path):
            if not callable(function):
                raise ImportError("invalid worktree helper function")
        return safety, paths
    except (ImportError, OSError, ValueError, SyntaxError, AttributeError) as exc:
        raise CleanupError(f"cannot load worktree helpers: {exc}") from exc


def _require_absolute(value):
    if not value or "\0" in value or not os.path.isabs(value):
        raise ValueError(f"path must be absolute: {value!r}")
    if os.name == "nt" and not PureWindowsPath(value).is_absolute():
        raise ValueError(f"path must be drive/UNC-qualified: {value!r}")


def resolve_root() -> Path:
    safety, paths = load_helpers()
    raw = os.environ.get("DREAM_WORKTREE_BASE")
    try:
        if raw:
            _require_absolute(raw)
            safety.safe_worktree_path(os.path.join(raw, "__probe__", "dream-_probe_"), raw)
        root = paths.resolve_worktree_root(raw)
        _require_absolute(root)
        safety.safe_worktree_path(os.path.join(root, "__probe__", "dream-_probe_"), root)
        return Path(root)
    except (OSError, ValueError) as exc:
        raise CleanupError(f"unsafe worktree base: {exc}") from exc


def is_link(info) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def is_plain_directory(info) -> bool:
    return stat.S_ISDIR(info.st_mode) and not is_link(info)


def _git(args, *, cwd=None, repo=None, timeout=10):
    env = os.environ.copy()
    for key in GIT_ENV_KEYS:
        env.pop(key, None)
    prefix = ["git"] if repo is None else ["git", f"--git-dir={repo}"]
    return subprocess.run(
        [*prefix, *args], cwd=cwd, env=env, capture_output=True, timeout=timeout,
    )


def _git_path(output: bytes) -> str:
    path = output.removesuffix(b"\n").decode("utf-8")
    if not path or "\0" in path:
        raise ValueError("empty or NUL-containing Git path")
    return path


def resolve_repo(selected: Optional[str], candidate: Optional[Path] = None) -> Optional[Path]:
    """Return a common gitdir; no inferred repository is a valid orphan-only mode."""
    cwd = os.path.abspath(selected) if selected else str(candidate or Path.cwd())
    try:
        result = _git(["rev-parse", "--git-common-dir"], cwd=cwd)
        if result.returncode != 0:
            if not selected:
                return None
            bare = _git(["rev-parse", "--is-bare-repository"], repo=cwd)
            if bare.returncode != 0 or bare.stdout != b"true\n":
                raise CleanupError(
                    f"unusable repository {selected!r}: "
                    f"{result.stderr.decode('utf-8', errors='replace').strip()}"
                )
            return Path(os.path.realpath(cwd))
        common = _git_path(result.stdout)
        root = Path(os.path.realpath(os.path.join(cwd, common)))
        check = _git(["rev-parse", "--git-dir"], repo=root)
        if check.returncode != 0:
            raise CleanupError(f"cannot use common gitdir: {root}")
        return root
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        if selected:
            raise CleanupError(f"cannot resolve repository {selected!r}: {exc}") from exc
        raise CleanupError(f"repository discovery failed: {exc}") from exc


def _registered_paths(repo: Path) -> set[str]:
    result = _git(["worktree", "list", "--porcelain", "-z"], repo=repo)
    if result.returncode != 0:
        raise MetadataError(
            f"cannot read worktree registrations: {result.stderr.decode('utf-8', errors='replace').strip()}"
        )
    output = result.stdout.decode("utf-8")
    if not output or not output.endswith("\0\0"):
        raise MetadataError("incomplete worktree registration output")
    _, paths = load_helpers()
    registered = set()
    for record in output[:-2].split("\0\0"):
        first = record.split("\0", 1)[0]
        if not first.startswith("worktree ") or not first[len("worktree "):]:
            raise MetadataError("malformed worktree registration")
        registered.add(paths.canonical_worktree_path(first[len("worktree "):]))
    return registered


def _gitdir_targets(raw: bytes, candidate: Path) -> tuple[tuple[Path, ...], bool]:
    if not raw.startswith(b"gitdir: "):
        raise MetadataError("invalid gitdir prefix")
    payload = raw[len(b"gitdir: "):]
    payloads = [payload[:-1] if payload.endswith(b"\n") else payload]
    if payload.endswith(b"\r\n"):
        # A literal final CR is a possible path only on POSIX.
        payloads = [payload[:-2]] if os.name == "nt" else [*payloads, payload[:-2]]
    if any(not value or b"\0" in value for value in payloads):
        raise MetadataError("empty or NUL-containing gitdir")
    names = tuple(dict.fromkeys(value.decode("utf-8") for value in payloads))
    targets = []
    for name in names:
        if os.name == "nt":
            windows = PureWindowsPath(name)
            if (windows.drive or windows.root) and not windows.is_absolute():
                raise MetadataError(f"ambiguous Windows gitdir: {name!r}")
        targets.append(Path(name) if os.path.isabs(name) else candidate / name)
    return tuple(targets), any(char in names[-1] for char in "\r\n")


def _pointer_state(candidate: Path) -> Literal["orphan", "live"]:
    gitfile = candidate / ".git"
    try:
        info = gitfile.lstat()
    except FileNotFoundError:
        return "orphan"
    if is_link(info) or not stat.S_ISREG(info.st_mode):
        raise MetadataError(".git is not a regular, unlinked file")
    targets, multiline = _gitdir_targets(gitfile.read_bytes(), candidate)
    live = False
    for target in targets:
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue
        if not is_plain_directory(info):
            raise MetadataError(f"gitdir is not a plain directory: {target}")
        live = True
    if live:
        return "live"
    if multiline:
        raise MetadataError("missing multiline gitdir is ambiguous")
    return "orphan"


def inspect_candidate(candidate: str, root: Path, repo: Optional[Path]) -> Inspection:
    safety, paths = load_helpers()
    resolved = Path(root)
    try:
        _require_absolute(candidate)
        resolved = safety.safe_worktree_path(candidate, str(root))
        namespace, leaf = resolved.relative_to(root).parts
        if not COMPONENT_RE.fullmatch(namespace) or not COMPONENT_RE.fullmatch(leaf[6:]):
            raise MetadataError("invalid worktree path components")
        literal = Path(candidate)
        try:
            parent_info = literal.parent.lstat()
        except FileNotFoundError:
            parent_info = None
        if parent_info is not None and not is_plain_directory(parent_info):
            raise MetadataError("namespace is not a plain directory")
        try:
            info = literal.lstat()
        except FileNotFoundError:
            return Inspection("missing", resolved)
        if not is_plain_directory(info):
            raise MetadataError("target is not a plain directory")
        identity = (info.st_dev, info.st_ino)
        pointer = _pointer_state(resolved)
        after = literal.lstat()
        if not is_plain_directory(after) or identity != (after.st_dev, after.st_ino):
            raise MetadataError("candidate changed during inspection")
        if repo is not None and paths.canonical_worktree_path(str(resolved)) in _registered_paths(repo):
            return Inspection("owned", resolved, identity)
        if pointer == "live":
            return Inspection(
                "live-other-or-unknown", resolved, identity,
                f"no matching repository ownership for live worktree: {resolved}",
            )
        return Inspection("orphan", resolved, identity)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return Inspection("indeterminate", resolved, reason=f"{candidate!r}: {exc}")


def _confirm_removed(path: Path, action: Action, error_code: int) -> CleanupResult:
    try:
        path.lstat()
    except FileNotFoundError:
        return CleanupResult(0, action, f"Removed ({action}): {path}")
    except OSError as exc:
        return CleanupResult(error_code, "refused", f"cannot confirm removal of {path}: {exc}")
    return CleanupResult(error_code, "refused", f"target still exists: {path}")


def remove_candidate(
    inspection: Inspection, root: Path, repo: Optional[Path], allow_registered: bool,
) -> CleanupResult:
    current = inspect_candidate(str(inspection.path), root, repo)
    if current.state == "missing":
        return CleanupResult(0, "missing", f"Nothing to clean: {current.path}")
    if current.state != inspection.state or current.identity != inspection.identity:
        return CleanupResult(1, "refused", f"candidate changed: {current.path}; {current.reason}")
    if current.state == "owned" and allow_registered and repo is not None:
        try:
            result = _git(["worktree", "remove", "--force", str(current.path)], repo=repo, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return CleanupResult(1, "refused", f"Git removal failed for {current.path}: {exc}")
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="replace").strip()
            return CleanupResult(1, "refused", f"Git refused {current.path} (exit {result.returncode}): {detail}")
        return _confirm_removed(current.path, "removed-git", 1)
    if current.state != "orphan":
        return CleanupResult(1, "refused", current.reason or f"no orphan fallback for {current.path}")
    try:
        shutil.rmtree(current.path)
    except OSError as exc:
        return CleanupResult(3, "refused", f"rmtree failed for {current.path}: {exc}")
    return _confirm_removed(current.path, "removed-orphan", 3)


def prune_repo(repo: Optional[Path]) -> Optional[str]:
    if repo is None:
        return None
    try:
        result = _git(["worktree", "prune"], repo=repo)
        if result.returncode != 0:
            return f"worktree prune failed (exit {result.returncode}): {result.stderr.decode('utf-8', errors='replace').strip()}"
    except (OSError, subprocess.SubprocessError) as exc:
        return f"worktree prune failed: {exc}"
    return None
