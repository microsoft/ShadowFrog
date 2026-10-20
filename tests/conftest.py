"""Repo-wide pytest fixtures for ShadowFrog tests.

Most scripts in this repo are hyphenated CLI files (e.g. `shadow-init.py`)
that aren't importable as regular Python modules. The `_load_script`
helper provides an importlib-based loader so tests can reach the internal
functions without subprocess overhead — but every public CLI path
(argparse dispatcher) should still also be exercised via subprocess in
at least one integration test, since the importable view of a script
bypasses the argument parser.
"""
import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_script(path):
    """Load a hyphenated Python script as a callable module.

    Subsequent loads of the same path return a fresh module — tests that
    need isolated module-level state (e.g. tweaking module-level
    constants) should NOT share the fixture across tests.
    """
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location(f"_loaded_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


# --- Script-loader fixtures (session-scoped — module state is read-only) ---

@pytest.fixture(scope="session")
def repo_root():
    """Absolute path to the ShadowFrog repo root."""
    return REPO_ROOT


@pytest.fixture(scope="session")
def shadow_init(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-init/shadow-init.py")


@pytest.fixture(scope="session")
def shadow_viewer(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-viewer/shadow-viewer.py")


@pytest.fixture(scope="session")
def dream_reconcile(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-reconcile.py")


@pytest.fixture(scope="session")
def dream_validate(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-validate.py")


@pytest.fixture(scope="session")
def dream_tools(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-tools.py")


@pytest.fixture
def dream_cleanup(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-cleanup.py")


@pytest.fixture
def worktree_cleanup(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/_worktree_cleanup.py")


@pytest.fixture
def dream_gc(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-gc.py")


@pytest.fixture
def lifecycle_env(tmp_path):
    env = os.environ.copy()
    for key in (
        "DREAM_NAMESPACE", "DREAM_WORKTREE_BASE", "DREAM_GC_AUTO",
        "DREAM_GC_INTERVAL_MIN", "DREAM_GC_AGE_MIN", "REPO_ROOT",
        "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    ):
        env.pop(key, None)
    env.update(
        HOME=str(tmp_path), GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
    )
    return env


@pytest.fixture
def lifecycle_git(lifecycle_env):
    def run(repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *map(str, args)],
            env=lifecycle_env, check=True, capture_output=True, timeout=30,
        ).stdout
    return run


@pytest.fixture
def lifecycle_repo(tmp_path, lifecycle_git):
    repo = tmp_path / "source repo caf\u00e9"
    lifecycle_git(tmp_path, "init", "-q", "-b", "main", repo)
    lifecycle_git(repo, "config", "user.email", "test@shadowfrog.invalid")
    lifecycle_git(repo, "config", "user.name", "Test")
    lifecycle_git(repo, "config", "commit.gpgsign", "false")
    (repo / "file.txt").write_text("initial\n", encoding="utf-8")
    lifecycle_git(repo, "add", "-A")
    lifecycle_git(repo, "commit", "-q", "-m", "initial")
    return repo


@pytest.fixture
def seed_protected_files():
    def seed(target):
        tracked = target / "file.txt"
        if tracked.is_file():
            tracked.write_bytes(tracked.read_bytes() + b"uncommitted tracked edit\n")
        nested = target / "protected" / "nested"
        nested.mkdir(parents=True)
        (nested / "empty").mkdir()
        (nested / "payload.bin").write_bytes(b"\x00\xff\x80\r\nprotected\x00")
        (target / "protected" / "caf\u00e9.txt").write_bytes("untracked caf\u00e9\n".encode("utf-8"))
    return seed


@pytest.fixture
def preservation_snapshot(lifecycle_git):
    """Observe protected files and Git state without using lifecycle implementation code."""
    def snapshot(target, *, repos=()):
        target = Path(os.path.abspath(target))
        tree = {}

        def visit(path):
            info = path.lstat()
            name = path.relative_to(target).as_posix()
            if stat.S_ISLNK(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                tree[name] = ("link", os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                tree[name] = ("directory",)
                for child in sorted(path.iterdir()):
                    visit(child)
            elif stat.S_ISREG(info.st_mode):
                tree[name] = ("file", path.read_bytes())
            else:
                pytest.fail(f"unexpected protected entry type: {path}")

        visit(target)
        git_state = {}
        for repo in dict.fromkeys(repos):
            refs = lifecycle_git(repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads")
            registrations = {}
            for record in lifecycle_git(repo, "worktree", "list", "--porcelain", "-z").split(b"\0\0"):
                if not record:
                    continue
                fields = record.split(b"\0")
                assert fields[0].startswith(b"worktree "), record
                path = Path(os.path.abspath(os.fsdecode(fields[0][len(b"worktree "):])))
                if path == target or target in path.parents:
                    registrations[str(path)] = tuple(fields[1:])
            git_state[str(repo)] = {"branches": refs, "registrations": registrations}
        return {"tree": tree, "git": git_state}
    return snapshot


@pytest.fixture(scope="session")
def dream_coverage(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-coverage.py")


@pytest.fixture(scope="session")
def dream_lineage(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-viewer/dream-lineage.py")


@pytest.fixture(scope="session")
def meditate_repair(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-meditate/meditate-repair.py")


@pytest.fixture(scope="session")
def coherence(repo_root):
    return _load_script(repo_root / "skills/shadow-frog/_coherence.py")


@pytest.fixture(scope="session")
def citations(repo_root):
    return _load_script(repo_root / "skills/shadow-frog/_citations.py")


@pytest.fixture(scope="session")
def nap(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-nap/nap.py")


# --- Filesystem fixtures ---

@pytest.fixture
def make_symlink():
    """Create a symlink, skipping only when Windows denies the privilege."""
    def create(link, target, *, target_is_directory=False):
        try:
            link.symlink_to(target, target_is_directory=target_is_directory)
        except OSError as exc:
            if os.name == "nt" and exc.winerror == 1314:
                pytest.skip(
                    "Windows symlink privilege is unavailable; enable "
                    "Developer Mode or run elevated"
                )
            raise

    return create


@pytest.fixture(scope="session")
def coupon_demo_src(repo_root):
    """Read-only path to the canonical coupon-demo. Tests MUST NOT mutate this."""
    return repo_root / "examples/coupon-demo"


@pytest.fixture
def coupon_demo(tmp_path, coupon_demo_src):
    """A mutable, fresh-per-test copy of coupon-demo, initialized as a git repo.

    Use this for any test that needs to read/write `.shadow/` or any
    source file. Each test gets its own copy.
    """
    dst = tmp_path / "coupon-demo"
    shutil.copytree(
        coupon_demo_src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    _git_init(dst, commit_all=True)
    return dst


@pytest.fixture
def tmp_git_repo(tmp_path):
    """An empty initialized git repo (no files committed). For init tests."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git_init(repo, commit_all=False)
    return repo


def _git_init(path, commit_all):
    """Init a git repo at `path` with deterministic identity. Optional initial commit."""
    env = {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(path),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "test@shadowfrog.invalid"],
                   cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "ShadowFrog Test"],
                   cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "commit.gpgsign", "false"],
                   cwd=path, check=True, env=env)
    if commit_all:
        subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "test initial"],
                       cwd=path, check=True, env=env)
