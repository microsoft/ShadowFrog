"""Native sweeper behavior, namespace isolation, and failure reporting."""
import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "skills" / "shadow-frog-dream"
GC = SCRIPT_DIR / "dream-gc.py"


@pytest.fixture
def root(tmp_path, monkeypatch):
    root = tmp_path / "worktrees"
    root.mkdir()
    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(root))
    return root


@pytest.fixture
def orphan(root):
    def create(namespace="ns", name="dream-orphan", age=946684800):
        path = root / namespace / name
        path.mkdir(parents=True)
        (path / ".git").write_text(f"gitdir: {root.parent / 'absent' / namespace / name}\n", encoding="utf-8")
        (path / "payload").write_text("keep until swept", encoding="utf-8")
        os.utime(path, (age, age))
        return path
    return create


@pytest.fixture
def run_gc(root, lifecycle_env):
    def run(*args, extras=None, script=GC):
        return subprocess.run(
            [sys.executable, str(script), *map(str, args)],
            cwd=root.parent,
            env={**lifecycle_env, "DREAM_WORKTREE_BASE": str(root), **(extras or {})},
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    return run


def options(**overrides):
    return argparse.Namespace(**{
        "namespace": None, "task_complete": False, "min_age_min": 0,
        "quiet": False, "dry_run": False, **overrides,
    })


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help(run_gc, flag):
    result = run_gc(flag)
    assert result.returncode == 0
    assert "Exit codes" in result.stdout


@pytest.mark.parametrize("args", [
    ["--unknown"], ["--repo-root"], ["--repo-root", "--quiet"],
    ["--min-age-min"], ["--namespace"], ["--namespace", "--quiet"],
    ["--task-complete"], ["--task-complete", "--namespace", "../other"],
    ["--task-complete", "--namespace", "valid\n"], ["--task-complete", "-n", "."],
    ["--min-age", "10"],
])
def test_usage(run_gc, args):
    result = run_gc(*args)
    assert result.returncode == 2
    assert result.stderr


@pytest.mark.parametrize("age", ["", "-1", "-0", "1.1", "one", "1\n", "\u0661"])
def test_bad_age(run_gc, age):
    result = run_gc("--min-age-min", age)
    assert result.returncode == 2
    assert result.stderr


@pytest.mark.parametrize("namespace", ["", "valid\n", "../escape"])
def test_bad_environment_namespace(run_gc, namespace):
    result = run_gc("--task-complete", extras={"DREAM_NAMESPACE": namespace})
    assert result.returncode == 2


@pytest.mark.parametrize("quiet", [[], ["--quiet"], ["-q"]])
def test_orphans_across_namespaces(run_gc, orphan, quiet):
    first = orphan("first")
    second = orphan("second")
    result = run_gc("--min-age-min", "0", "--namespace", "first", *quiet)
    assert result.returncode == 0, result.stderr
    assert not first.exists() and not second.exists()
    assert ("removed=2" in result.stdout) == (not quiet)


def test_missing_empty_and_file_base(run_gc, root):
    result = run_gc()
    assert result.returncode == 0
    assert "removed=0 kept=0 refused=0" in result.stdout
    root.rmdir()
    result = run_gc()
    assert result.returncode == 0
    assert "does not exist" in result.stdout and "Summary" not in result.stdout
    root.write_text("not a directory", encoding="utf-8")
    result = run_gc()
    assert result.returncode == 1
    assert result.stderr


def test_sensitive_base_is_refused_before_missing_check(run_gc, root):
    result = run_gc("--dry-run", extras={"DREAM_WORKTREE_BASE": root.anchor})
    assert result.returncode == 1


@pytest.mark.parametrize("mode", ["default", "task", "dry-run", "locked", "wrong-repo"])
def test_real_registered_worktree(
    run_gc, root, lifecycle_repo, lifecycle_git, tmp_path, mode,
    seed_protected_files, preservation_snapshot,
):
    target = root / "ns" / "dream-real"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", target, "-b", "real")
    (target / "leftover.txt").write_text("completed", encoding="utf-8")
    selected = lifecycle_repo
    args = ["--min-age-min", "0"]
    if mode != "default":
        args += ["--task-complete", "-n", "ns"]
    if mode == "dry-run":
        args += ["--dry-run"]
    if mode == "locked":
        lifecycle_git(lifecycle_repo, "worktree", "lock", target)
    if mode == "wrong-repo":
        selected = tmp_path / "other"
        lifecycle_git(tmp_path, "init", "-q", selected)
    seed_protected_files(target)
    before = preservation_snapshot(target, repos=(lifecycle_repo, selected))
    assert str(target) in before["git"][str(lifecycle_repo)]["registrations"]
    result = run_gc(*args, "--repo-root", selected)
    assert result.returncode == 0, result.stderr
    assert target.exists() == (mode != "task")
    if mode != "task":
        assert preservation_snapshot(target, repos=(lifecycle_repo, selected)) == before
    if mode == "default":
        assert "kept=1" in result.stdout
    elif mode in ("locked", "wrong-repo"):
        assert result.stderr and "refused=1" in result.stdout
    else:
        assert "removed=1" in result.stdout and "stale-registered" in result.stdout
    if mode == "task":
        assert lifecycle_git(
            lifecycle_repo, "for-each-ref", "--format=%(refname) %(objectname)", "refs/heads",
        ) == before["git"][str(lifecycle_repo)]["branches"]
        data = lifecycle_git(lifecycle_repo, "worktree", "list", "--porcelain", "-z")
        paths = [Path(part[9:].decode("utf-8")).resolve() for part in data.split(b"\0")
                 if part.startswith(b"worktree ")]
        assert target.resolve() not in paths


@pytest.mark.parametrize("source", ["flag", "environment"])
def test_task_complete_never_crosses_namespaces(
    run_gc, orphan, root, source, lifecycle_repo, lifecycle_git,
    seed_protected_files, preservation_snapshot,
):
    target = orphan("ns")
    sibling = orphan("other")
    owned = root / "ns" / "dream-owned"
    protected = root / "other" / "dream-live"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", owned, "-b", "selected")
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", protected, "-b", "protected")
    seed_protected_files(sibling)
    seed_protected_files(protected)
    before = preservation_snapshot(sibling.parent, repos=(lifecycle_repo,))
    assert str(protected) in before["git"][str(lifecycle_repo)]["registrations"]
    args = ["--task-complete", "--min-age-min", "0", "--repo-root", lifecycle_repo]
    extras = {}
    if source == "flag":
        args += ["--namespace", "ns"]
    else:
        extras["DREAM_NAMESPACE"] = "ns"
    result = run_gc(*args, extras=extras)
    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert not owned.exists()
    assert preservation_snapshot(sibling.parent, repos=(lifecycle_repo,)) == before
    assert "removed=2" in result.stdout


def test_task_namespace_not_derived_from_repo(run_gc, lifecycle_repo):
    result = run_gc("--task-complete", "--repo-root", lifecycle_repo)
    assert result.returncode == 2


@pytest.mark.parametrize("task_complete", [False, True])
def test_cli_dry_run_preserves_files_and_git_state(
    run_gc, orphan, root, lifecycle_repo, lifecycle_git, task_complete,
    seed_protected_files, preservation_snapshot,
):
    abandoned = orphan()
    live = root / "ns" / "dream-live"
    missing = root / "ns" / "dream-missing"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", live, "-b", "live")
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", missing, "-b", "missing")
    shutil.rmtree(missing)
    seed_protected_files(abandoned)
    seed_protected_files(live)
    before = preservation_snapshot(root, repos=(lifecycle_repo,))
    assert set(before["git"][str(lifecycle_repo)]["registrations"]) == {str(live), str(missing)}
    args = ["--dry-run", "--min-age-min", "0", "--repo-root", lifecycle_repo]
    if task_complete:
        args += ["--task-complete", "--namespace", "ns"]
    result = run_gc(*args)
    assert result.returncode == 0, result.stderr
    assert "WOULD REMOVE" in result.stdout
    assert preservation_snapshot(root, repos=(lifecycle_repo,)) == before


def test_dry_run_never_removes_or_prunes(dream_gc, worktree_cleanup, root, orphan, monkeypatch, capsys):
    target = orphan()
    def forbidden(*args, **kwargs):
        pytest.fail("dry-run must not mutate")
    monkeypatch.setattr(worktree_cleanup, "remove_candidate", forbidden)
    monkeypatch.setattr(worktree_cleanup, "prune_repo", forbidden)
    assert dream_gc.sweep(worktree_cleanup, root, None, options(dry_run=True)) == 0
    assert "WOULD REMOVE (orphan)" in capsys.readouterr().out
    assert target.exists()


@pytest.mark.parametrize("age,elapsed,removed", [
    (1, 59.9, False), (1, 60, False), (1, 60.1, True),
    (1, -10, False), (0, 0, True), (0, -10, True),
])
def test_exact_age_threshold(
    dream_gc, worktree_cleanup, root, orphan, monkeypatch, age, elapsed, removed,
):
    now = 2000000000
    target = orphan(age=now - elapsed)
    monkeypatch.setattr(dream_gc.time, "time", lambda: now)
    assert dream_gc.sweep(worktree_cleanup, root, None, options(min_age_min=age)) == 0
    assert target.exists() == (not removed)


def test_non_dream_and_fresh_entries_not_counted(run_gc, orphan):
    fresh = orphan(age=4102444800)
    other = orphan(name="ordinary")
    result = run_gc("--min-age-min", "10")
    assert result.returncode == 0
    assert "removed=0 kept=0 refused=0" in result.stdout
    assert fresh.exists() and other.exists()


def test_malformed_metadata_refused_even_when_quiet(
    run_gc, orphan, seed_protected_files, preservation_snapshot,
):
    target = orphan()
    (target / ".git").write_bytes(b"invalid")
    seed_protected_files(target)
    before = preservation_snapshot(target)
    result = run_gc("--quiet", "--min-age-min", "0")
    assert result.returncode == 0
    assert not result.stdout and result.stderr
    assert preservation_snapshot(target) == before


@pytest.mark.parametrize("missing,code", [
    ("_worktree_safety.py", 4), ("_worktree_paths.py", 1), ("_worktree_cleanup.py", 1),
])
def test_missing_dependencies(run_gc, orphan, tmp_path, missing, code):
    target = orphan()
    directory = tmp_path / "copy"
    directory.mkdir()
    for name in ("dream-gc.py", "_worktree_cleanup.py", "_worktree_paths.py", "_worktree_safety.py"):
        if name != missing:
            shutil.copyfile(SCRIPT_DIR / name, directory / name)
    result = run_gc(script=directory / "dream-gc.py")
    assert result.returncode == code, result.stderr
    assert result.stderr and target.exists()


def test_nested_scan_failure_does_not_hide_siblings(
    dream_gc, worktree_cleanup, root, orphan, monkeypatch, capsys,
):
    denied = orphan("denied")
    safe = orphan("safe")
    original = os.scandir
    def scan(path):
        if not isinstance(path, int) and Path(path) == denied.parent:
            raise PermissionError("scan denied")
        return original(path)
    monkeypatch.setattr(dream_gc.os, "scandir", scan)
    assert dream_gc.sweep(worktree_cleanup, root, None, options(quiet=True)) == 0
    captured = capsys.readouterr()
    assert not captured.out and "scan denied" in captured.err
    assert denied.exists() and not safe.exists()


def test_root_scan_failure_is_exit_one(
    dream_gc, worktree_cleanup, root, monkeypatch, capsys,
):
    monkeypatch.chdir(root.parent)
    monkeypatch.setattr(sys, "argv", ["dream-gc.py"])
    monkeypatch.setattr(dream_gc, "_load_core", lambda: worktree_cleanup)
    def scan(path):
        raise PermissionError("root denied")
    monkeypatch.setattr(dream_gc.os, "scandir", scan)
    assert dream_gc.main() == 1
    assert "root denied" in capsys.readouterr().err


def test_missing_namespace_is_empty_sweep(run_gc):
    result = run_gc("--task-complete", "--namespace", "absent")
    assert result.returncode == 0
    assert "removed=0 kept=0 refused=0" in result.stdout


@pytest.mark.parametrize("mode", [[], ["--task-complete", "-n", "ns"]])
def test_namespace_link_not_followed(run_gc, root, tmp_path, make_symlink, mode):
    decoy = tmp_path / "decoy"
    target = decoy / "dream-test"
    target.mkdir(parents=True)
    make_symlink(root / "ns", decoy)
    result = run_gc("--min-age-min", "0", *mode)
    assert result.returncode == 0
    assert "refused=1" in result.stdout and result.stderr
    assert target.exists()


def test_final_prune_with_no_removals(dream_gc, worktree_cleanup, root, monkeypatch):
    called = []
    monkeypatch.setattr(worktree_cleanup, "prune_repo", lambda repo: called.append(repo))
    assert dream_gc.sweep(worktree_cleanup, root, root, options()) == 0
    assert called == [root]


def test_disappearing_candidate_not_counted_removed(
    dream_gc, worktree_cleanup, root, orphan, monkeypatch, capsys,
):
    target = orphan()
    monkeypatch.setattr(
        worktree_cleanup, "inspect_candidate",
        lambda *a: worktree_cleanup.Inspection("missing", target),
    )
    assert dream_gc.sweep(worktree_cleanup, root, None, options()) == 0
    assert "removed=0 kept=1 refused=0" in capsys.readouterr().out
