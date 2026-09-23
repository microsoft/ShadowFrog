"""Pin real tooling outside historical code worktrees; never mock its validation."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "skills/shadow-frog-dream/dream-tools.py"


def run(*args, cwd=None, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
    )


def pin(repo, output, mode="coherent", env=None):
    result = run("pin", "--repo-root", repo, "--output", output, "--mode", mode, env=env)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def invoke(packet, tool, *args, cwd=None, env=None):
    return subprocess.run(
        [*packet["commands"][tool], *map(str, args)],
        cwd=cwd, env=env, capture_output=True, text=True, encoding="utf-8",
    )


def git(repo, *args):
    env = os.environ.copy()
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    return subprocess.run(
        ["git", "--no-pager", "-C", str(repo), *args],
        env=env, check=True, capture_output=True, text=True, encoding="utf-8",
    ).stdout.strip()


def test_pin_creates_complete_external_snapshot(tmp_git_repo, tmp_path):
    output = tmp_path / "tools caf\u00e9"
    packet = pin(tmp_git_repo, output)
    assert Path(packet["manifest"]).is_absolute()
    assert Path(packet["skill_dir"]) == output / "shadow-frog-dream"
    assert (output / "shadow-frog/_coherence.py").is_file()
    assert all(Path(path).is_file() for path in packet["instructions"])
    metadata = json.loads(Path(packet["manifest"]).read_text(encoding="utf-8"))
    assert metadata["mode"] == "coherent"
    assert metadata["repo_root"] == str(tmp_git_repo.resolve())
    assert "shadow-frog-dream/dream-tools.py" in metadata["files"]
    result = invoke(packet, "coverage", "--help", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "--scope" in result.stdout


def test_modes_match_shared_contract(dream_tools, coherence):
    assert dream_tools.MODES == coherence.MODES


@pytest.mark.parametrize("agent_dir", [".github", ".claude"])
def test_pin_from_each_installed_layout(tmp_git_repo, tmp_path, agent_dir):
    for name in ("shadow-frog", "shadow-frog-dream"):
        shutil.copytree(
            REPO_ROOT / "skills" / name, tmp_git_repo / agent_dir / "skills" / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    installed = tmp_git_repo / agent_dir / "skills/shadow-frog-dream/dream-tools.py"
    result = subprocess.run(
        [
            sys.executable, str(installed), "pin", "--repo-root", str(tmp_git_repo),
            "--output", str(tmp_path / "tooling"), "--mode", "coherent",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)
    assert not Path(packet["skill_dir"]).is_relative_to(tmp_git_repo)
    result = invoke(packet, "coverage", "--help")
    assert result.returncode == 0, result.stderr


def test_pin_refuses_existing_or_in_repository_output(tmp_git_repo, tmp_path):
    inside = tmp_git_repo / "tools"
    result = run("pin", "--repo-root", tmp_git_repo, "--output", inside)
    assert result.returncode == 1
    assert not inside.exists()
    existing = tmp_path / "existing"
    existing.mkdir()
    sentinel = existing / "keep"
    sentinel.write_text("keep", encoding="utf-8")
    result = run("pin", "--repo-root", tmp_git_repo, "--output", existing)
    assert result.returncode == 1
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_snapshot_validates_artifacts_not_historical_helpers(tmp_git_repo, tmp_path):
    repo = tmp_git_repo
    old_validator = repo / ".github/skills/shadow-frog-dream/dream-validate.py"
    old_validator.parent.mkdir(parents=True)
    old_validator.write_text('print("Historical helper must not run")\n', encoding="utf-8")
    (repo / "feature.txt").write_text("parent feature\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "historical parent")
    parent_tip = git(repo, "rev-parse", "HEAD")
    parent = "dream/p/historical"
    git(repo, "branch", parent)
    for name in ("shadow-frog", "shadow-frog-dream"):
        shutil.copytree(
            REPO_ROOT / "skills" / name, repo / ".github/skills" / name,
            dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "current installed tooling")
    installed_pin = repo / ".github/skills/shadow-frog-dream/dream-tools.py"
    result = subprocess.run(
        [
            sys.executable, str(installed_pin), "pin", "--repo-root", str(repo),
            "--output", str(tmp_path / "pinned"), "--mode", "coherent",
        ], capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    packet = json.loads(result.stdout)
    worktree = tmp_path / "historical worktree"
    git(repo, "worktree", "add", "-q", "-b", "dream/p/child", str(worktree), parent)
    git(repo, "switch", "-q", parent)
    assert not installed_pin.exists()
    dream_id = "20260921-020000Z-child"
    artifacts = worktree / ".shadow/_dreams" / dream_id
    artifacts.mkdir(parents=True)
    manifest = {
        "dream_id": dream_id, "branch": "dream/p/child", "parent_branch": parent,
        "mode": "coherent", "goal": "Extend the parent",
        "category": "feature design", "verdict": "useful", "title": "Child",
        "discoveries": [],
    }
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (artifacts / "report.md").write_text(
        f"---\ndream_id: {dream_id}\nmode: coherent\nparent_branch: {parent}\n"
        f"base_commit: {parent_tip}\n---\n\n# Child\n", encoding="utf-8",
    )
    (artifacts / "patch.diff").write_text(
        "diff --git a/feature.txt b/feature.txt\n--- a/feature.txt\n"
        "+++ b/feature.txt\n@@ -1 +1 @@\n-parent feature\n+child feature\n",
        encoding="utf-8",
    )
    result = invoke(packet, "validate", dream_id, worktree, cwd=worktree)
    assert result.returncode == 1
    assert "parent_connection" in result.stdout
    assert "Historical helper" not in result.stdout
    manifest["parent_connection"] = {
        "relation": "alternative", "basis": "Parent exposes this capability",
        "delta": "Provide a different implementation", "preserves": [], "supersedes": [],
    }
    (artifacts / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    result = invoke(packet, "validate", dream_id, worktree, cwd=worktree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation passed" in result.stdout
    result = invoke(packet, "reconcile", "--dry-run", "--namespace", "p", cwd=worktree)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"Repo: {repo.resolve()}" in result.stdout
    assert "Namespace: p" in result.stdout


@pytest.mark.parametrize("target", ["shadow-frog/_coherence.py", "tooling.json"])
def test_changed_snapshot_is_rejected(tmp_git_repo, tmp_path, target):
    output = tmp_path / "bundle"
    packet = pin(tmp_git_repo, output)
    path = output / target
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = invoke(packet, "coverage", "--help")
    assert result.returncode == 1
    assert "changed" in result.stderr.lower()


def test_missing_snapshot_dependency_is_rejected(tmp_git_repo, tmp_path):
    output = tmp_path / "bundle"
    packet = pin(tmp_git_repo, output)
    (output / "shadow-frog/_coherence.py").unlink()
    result = invoke(packet, "coverage", "--help")
    assert result.returncode == 1
    assert "ERROR" in result.stderr
    assert "Traceback" not in result.stderr


def test_validation_mode_cannot_be_overridden_after_pinning(tmp_git_repo, tmp_path):
    packet = pin(tmp_git_repo, tmp_path / "bundle")
    result = invoke(packet, "validate", "id", tmp_git_repo, "--mode", "broad")
    assert result.returncode == 1
    assert "mode" in result.stderr


def test_source_and_snapshot_are_never_modified_by_dispatch(tmp_git_repo, tmp_path):
    packet = pin(tmp_git_repo, tmp_path / "bundle", mode="broad")
    before = Path(packet["manifest"]).read_bytes()
    result = invoke(packet, "reconcile", "--dry-run", "--namespace", "p")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Namespace: p" in result.stdout
    assert not (tmp_git_repo / ".shadow").exists()
    assert Path(packet["manifest"]).read_bytes() == before


def test_dispatch_preserves_helper_error_status(tmp_git_repo, tmp_path):
    packet = pin(tmp_git_repo, tmp_path / "bundle")
    result = invoke(packet, "coverage", "--scope", "missing/")
    assert result.returncode == 2
    assert "No source files matched scope filters" in result.stdout


def test_pin_and_python_dispatch_need_no_bash(tmp_git_repo, tmp_path):
    env = os.environ.copy()
    if os.name == "nt":
        env["PATH"] = os.pathsep.join(
            item for item in env["PATH"].split(os.pathsep)
            if not (Path(item) / "bash.exe").is_file()
        )
    else:
        tools = tmp_path / "native"
        tools.mkdir()
        (tools / "git").symlink_to(shutil.which("git"))
        env["PATH"] = str(tools)
    assert shutil.which("bash", path=env["PATH"]) is None
    packet = pin(tmp_git_repo, tmp_path / "bundle", env=env)
    result = invoke(packet, "coverage", "--help", env=env)
    assert result.returncode == 0, result.stderr


def test_skill_has_one_guarded_branch_cleanup_path():
    text = (REPO_ROOT / "skills/shadow-frog-dream/SKILL.md").read_text(encoding="utf-8")
    assert "git push origin --delete" not in text
    assert "git branch -D" not in text
    assert 'VALIDATE_SCRIPT="$DIR/dream-validate.py"' not in text
    assert "dream-tools.py pin" in text
    assert "Broad mode" in next(
        line for line in text.splitlines()
        if line.startswith("6. ") and "fetch" in line.lower()
    )
