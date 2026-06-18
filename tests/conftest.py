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
import shutil
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
def dream_coverage(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-dream/dream-coverage.py")


@pytest.fixture(scope="session")
def dream_lineage(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-viewer/dream-lineage.py")


@pytest.fixture(scope="session")
def meditate_repair(repo_root):
    return _load_script(repo_root / "skills/shadow-frog-meditate/meditate-repair.py")


# --- Filesystem fixtures ---

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
