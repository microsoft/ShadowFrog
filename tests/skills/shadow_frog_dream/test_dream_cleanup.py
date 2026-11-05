"""Native CLI and shared-core contracts for guarded worktree cleanup."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

import pytest


SCRIPT_DIR = Path(__file__).resolve().parents[3] / "skills" / "shadow-frog-dream"
CLEANUP = SCRIPT_DIR / "dream-cleanup.py"


@pytest.fixture
def root(tmp_path, monkeypatch):
    path = tmp_path / "worktrees"
    path.mkdir()
    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(path))
    return path


@pytest.fixture
def orphan(root):
    path = root / "ns" / "dream-test"
    path.mkdir(parents=True)
    (path / "keep.txt").write_text("payload", encoding="utf-8")
    return path


@pytest.fixture
def run_cleanup(root, lifecycle_env):
    def run(*args, extras=None, script=CLEANUP):
        return subprocess.run(
            [sys.executable, str(script), *map(str, args)],
            cwd=root.parent,
            env={**lifecycle_env, "DREAM_WORKTREE_BASE": str(root), **(extras or {})},
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
    return run


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_help(run_cleanup, flag):
    result = run_cleanup(flag)
    assert result.returncode == 0
    assert "Exit codes" in result.stdout


@pytest.mark.parametrize("args", [
    [], ["--unknown"], ["a", "b"], ["a", "--repo-root"],
    ["a", "--repo-root", "--quiet"], ["a", "--repo", "b"],
])
def test_usage(run_cleanup, args):
    result = run_cleanup(*args)
    assert result.returncode == 2
    assert result.stderr


@pytest.mark.parametrize("quiet", [[], ["--quiet"], ["-q"]])
def test_orphan_and_idempotence(run_cleanup, orphan, quiet):
    result = run_cleanup(orphan, *quiet)
    assert result.returncode == 0, result.stderr
    assert not orphan.exists()
    assert bool(result.stdout) == (not quiet)
    result = run_cleanup(orphan, *quiet)
    assert result.returncode == 0, result.stderr
    assert bool(result.stdout) == (not quiet)


def test_broken_pointer_fallback(run_cleanup, orphan, tmp_path):
    (orphan / ".git").write_text(f"gitdir: {tmp_path / 'absent'}\n", encoding="utf-8")
    result = run_cleanup(orphan)
    assert result.returncode == 0, result.stderr
    assert not orphan.exists()


@pytest.mark.parametrize("selection", ["flag", "env", "derive", "linked", "bare", "separate"])
def test_registered_removal(
    run_cleanup, root, lifecycle_repo, lifecycle_git, tmp_path, selection,
):
    repo = lifecycle_repo
    if selection == "bare":
        repo = tmp_path / "bare.git"
        lifecycle_git(tmp_path, "clone", "-q", "--bare", lifecycle_repo, repo)
    elif selection == "separate":
        repo = tmp_path / "separate"
        lifecycle_git(tmp_path, "clone", "-q", "--separate-git-dir",
                      tmp_path / "metadata", lifecycle_repo, repo)
    gitdir = repo if selection == "bare" else repo / ".git"
    command_repo = repo
    if selection == "bare":
        # Use an explicit gitdir even with safe.bareRepository=explicit.
        lifecycle_git(tmp_path, f"--git-dir={repo}", "worktree", "add", "-q",
                      root / "ns" / "dream-test", "-b", "dream/ns/test")
    else:
        lifecycle_git(repo, "worktree", "add", "-q", root / "ns" / "dream-test",
                      "-b", "dream/ns/test")
    if selection == "linked":
        command_repo = tmp_path / "controller"
        lifecycle_git(repo, "worktree", "add", "-q", command_repo, "-b", "controller")
    target = root / "ns" / "dream-test"
    (target / "dirty.txt").write_text("explicit post-completion cleanup\n", encoding="utf-8")
    args = [] if selection in ("env", "derive") else ["--repo-root", command_repo]
    extras = {"REPO_ROOT": str(repo)} if selection == "env" else {}
    result = run_cleanup(target, *args, extras=extras)
    assert result.returncode == 0, result.stderr
    assert "removed-git" in result.stdout
    assert not target.exists()
    if selection == "bare":
        inventory = lifecycle_git(tmp_path, f"--git-dir={gitdir}", "worktree", "list", "--porcelain", "-z")
        refs = lifecycle_git(tmp_path, f"--git-dir={gitdir}", "show-ref", "--verify", "refs/heads/dream/ns/test")
    else:
        inventory = lifecycle_git(repo, "worktree", "list", "--porcelain", "-z")
        refs = lifecycle_git(repo, "show-ref", "--verify", "refs/heads/dream/ns/test")
    names = [part[len(b"worktree "):].decode("utf-8") for part in inventory.split(b"\0")
             if part.startswith(b"worktree ")]
    assert target.resolve() not in [Path(name).resolve() for name in names]
    assert refs


@pytest.mark.parametrize("condition", ["locked", "wrong-repo", "missing-metadata"])
def test_registered_failures_preserve_data(
    run_cleanup, root, lifecycle_repo, lifecycle_git, tmp_path, condition,
    seed_protected_files, preservation_snapshot,
):
    target = root / "ns" / "dream-test"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", target, "-b", "test")
    selected = lifecycle_repo
    if condition == "locked":
        lifecycle_git(selected, "worktree", "lock", target)
    elif condition == "wrong-repo":
        selected = tmp_path / "other"
        lifecycle_git(tmp_path, "init", "-q", selected)
    else:
        (target / ".git").unlink()
        lifecycle_git(selected, "worktree", "lock", target)
    seed_protected_files(target)
    before = preservation_snapshot(target, repos=(lifecycle_repo, selected))
    assert str(target) in before["git"][str(lifecycle_repo)]["registrations"]
    result = run_cleanup(target, "--repo-root", selected, "--quiet")
    assert result.returncode == 1
    assert result.stderr and not result.stdout
    assert preservation_snapshot(target, repos=(lifecycle_repo, selected)) == before


def test_flag_beats_env_and_inherited_git_context(
    run_cleanup, root, lifecycle_repo, lifecycle_git, tmp_path,
):
    target = root / "ns" / "dream-test"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", target, "-b", "test")
    other = tmp_path / "other"
    lifecycle_git(tmp_path, "init", "-q", other)
    result = run_cleanup(target, "--repo-root", lifecycle_repo, extras={
        "REPO_ROOT": str(other), "GIT_DIR": str(other / ".git"),
        "GIT_WORK_TREE": str(other), "GIT_COMMON_DIR": str(other / ".git"),
        "GIT_INDEX_FILE": str(other / "decoy-index"),
    })
    assert result.returncode == 0, result.stderr
    assert not target.exists()
    assert (other / ".git").is_dir()
    assert not (other / "decoy-index").exists()


@pytest.mark.parametrize("bad", ["outside", "base", "shape", "traversal", "relative"])
def test_unsafe_target(
    run_cleanup, root, tmp_path, bad, seed_protected_files, preservation_snapshot,
):
    candidates = {
        "outside": str(tmp_path / "outside" / "ns" / "dream-no"),
        "base": str(root),
        "shape": str(root / "ns" / "ordinary"),
        "traversal": os.path.join(str(root), "ns", "..", "ns", "dream-no"),
        "relative": os.path.join(root.name, "ns", "dream-no"),
    }
    protected = Path(candidates[bad])
    if not protected.is_absolute():
        protected = root.parent / protected
    protected.mkdir(parents=True, exist_ok=True)
    seed_protected_files(protected)
    before = preservation_snapshot(tmp_path)
    result = run_cleanup(candidates[bad])
    assert result.returncode == 1
    assert result.stderr
    assert preservation_snapshot(tmp_path) == before


@pytest.mark.parametrize("base", ["relative", "traversal", "sensitive"])
def test_raw_base_is_checked(run_cleanup, root, tmp_path, base):
    value = {
        "relative": "relative",
        "traversal": os.path.join(str(root), "..", "worktrees"),
        "sensitive": str(Path(root.anchor)),
    }[base]
    result = run_cleanup(root / "ns" / "dream-no", extras={"DREAM_WORKTREE_BASE": value})
    assert result.returncode == 1
    assert "base" in result.stderr


@pytest.mark.parametrize("missing,expected", [
    ("_worktree_safety.py", 4), ("_worktree_paths.py", 1), ("_worktree_cleanup.py", 1),
])
def test_missing_adjacent_helper(run_cleanup, orphan, tmp_path, missing, expected):
    destination = tmp_path / "copied"
    destination.mkdir()
    for name in ("dream-cleanup.py", "_worktree_cleanup.py", "_worktree_paths.py", "_worktree_safety.py"):
        if name != missing:
            shutil.copyfile(SCRIPT_DIR / name, destination / name)
    result = run_cleanup(orphan, script=destination / "dream-cleanup.py")
    assert result.returncode == expected, result.stderr
    assert result.stderr
    assert orphan.is_dir()


@pytest.mark.parametrize("raw", [
    b"invalid", b"junk\ngitdir: absent\n", b"gitdir: \n",
    b"gitdir: bad\0path\n", b"gitdir: bad\xffpath\n",
    b"gitdir: missing\nsecond-line\n",
])
def test_malformed_metadata_is_not_orphan(
    run_cleanup, orphan, raw, seed_protected_files, preservation_snapshot,
):
    (orphan / ".git").write_bytes(raw)
    seed_protected_files(orphan)
    before = preservation_snapshot(orphan)
    result = run_cleanup(orphan, "--quiet")
    assert result.returncode == 1, result.stderr
    assert result.stderr and not result.stdout
    assert preservation_snapshot(orphan) == before


@pytest.mark.parametrize("name,ending", [
    ("metadata", b"\n"), (" metadata", b"\n"), ("m\u00e9tadata", b"\n"),
    ("metadata", b"\r\n"),
    pytest.param("meta:with:colons", b"\n", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX filename")),
    pytest.param("meta\tline\nmore", b"\n", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX filename")),
    pytest.param("metadata\r", b"\n", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX filename")),
])
def test_live_pointer_preserves_path(worktree_cleanup, orphan, name, ending):
    (orphan / name).mkdir()
    (orphan / ".git").write_bytes(b"gitdir: " + name.encode("utf-8") + ending)
    assert worktree_cleanup._pointer_state(orphan) == "live"


def test_native_absolute_pointer(worktree_cleanup, orphan, tmp_path):
    metadata = tmp_path / "metadata"
    metadata.mkdir()
    (orphan / ".git").write_text(f"gitdir: {metadata}\n", encoding="utf-8")
    assert worktree_cleanup._pointer_state(orphan) == "live"


@pytest.mark.parametrize("where", ["namespace", "leaf", "metadata", "dangling"])
def test_links_are_refused(run_cleanup, root, tmp_path, make_symlink, where):
    target = root / "ns" / "dream-link"
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "keep").write_text("keep", encoding="utf-8")
    if where == "namespace":
        make_symlink(root / "ns", decoy)
    else:
        target.parent.mkdir()
        if where == "leaf":
            make_symlink(target, decoy)
        elif where == "dangling":
            make_symlink(target, tmp_path / "missing")
        else:
            target.mkdir()
            make_symlink(target / ".git", decoy)
    result = run_cleanup(target)
    assert result.returncode == 1, result.stderr
    assert (decoy / "keep").read_text(encoding="utf-8") == "keep"


def test_reparse_directory_is_not_plain(worktree_cleanup):
    class ReparseDirectory:
        st_mode = stat.S_IFDIR
        st_file_attributes = stat.FILE_ATTRIBUTE_REPARSE_POINT
    assert not worktree_cleanup.is_plain_directory(ReparseDirectory())


def test_missing_file_is_not_inaccessible_file(worktree_cleanup, root, orphan, monkeypatch):
    original = Path.lstat
    def inaccessible(path, *args, **kwargs):
        if path == orphan / ".git":
            raise PermissionError("metadata denied")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", inaccessible)
    result = worktree_cleanup.inspect_candidate(str(orphan), root, None)
    assert result.state == "indeterminate"
    assert "metadata denied" in result.reason


@pytest.mark.parametrize("returncode,stderr", [(1, b""), (1, b"locked"), (0, b"notice"), (0, b"")])
def test_git_exit_status_not_stderr(
    worktree_cleanup, root, orphan, monkeypatch, returncode, stderr,
):
    inspection = worktree_cleanup.Inspection("owned", orphan, (1, 2))
    monkeypatch.setattr(worktree_cleanup, "inspect_candidate", lambda *args: inspection)
    def run(*args, **kwargs):
        if returncode == 0:
            shutil.rmtree(orphan)
        return subprocess.CompletedProcess(args, returncode, b"", stderr)
    monkeypatch.setattr(worktree_cleanup, "_git", run)
    result = worktree_cleanup.remove_candidate(inspection, root, root, True)
    assert result.exit_code == (0 if returncode == 0 else 1)
    assert orphan.exists() == (returncode != 0)


@pytest.mark.parametrize("failure", [OSError("launch failed"), subprocess.TimeoutExpired("git", 120)])
def test_git_failure_never_falls_back(worktree_cleanup, root, orphan, monkeypatch, failure):
    inspection = worktree_cleanup.Inspection("owned", orphan, (1, 2))
    monkeypatch.setattr(worktree_cleanup, "inspect_candidate", lambda *args: inspection)
    def fail(*args, **kwargs):
        raise failure
    monkeypatch.setattr(worktree_cleanup, "_git", fail)
    result = worktree_cleanup.remove_candidate(inspection, root, root, True)
    assert result.exit_code == 1
    assert orphan.exists()


def test_git_success_must_remove_target(worktree_cleanup, root, orphan, monkeypatch):
    inspection = worktree_cleanup.Inspection("owned", orphan, (1, 2))
    monkeypatch.setattr(worktree_cleanup, "inspect_candidate", lambda *args: inspection)
    monkeypatch.setattr(worktree_cleanup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"", b""))
    assert worktree_cleanup.remove_candidate(inspection, root, root, True).exit_code == 1
    assert orphan.exists()


def test_rmtree_failure_is_exit_three(worktree_cleanup, root, orphan, monkeypatch):
    inspection = worktree_cleanup.inspect_candidate(str(orphan), root, None)
    def fail(path):
        raise PermissionError("removal denied")
    monkeypatch.setattr(worktree_cleanup.shutil, "rmtree", fail)
    result = worktree_cleanup.remove_candidate(inspection, root, None, True)
    assert result.exit_code == 3
    assert "removal denied" in result.message
    assert orphan.exists()


def test_changed_registration_is_not_removed(worktree_cleanup, root, orphan, monkeypatch):
    inspection = worktree_cleanup.inspect_candidate(str(orphan), root, None)
    (orphan / ".git").write_text("invalid metadata", encoding="utf-8")
    result = worktree_cleanup.remove_candidate(inspection, root, None, True)
    assert result.exit_code == 1
    assert orphan.exists()


@pytest.mark.parametrize("output", [b"", b"garbage\0\0", b"worktree \0\0", b"\xff\0\0"])
def test_invalid_inventory_is_refusal(worktree_cleanup, root, orphan, monkeypatch, output):
    monkeypatch.setattr(worktree_cleanup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, output, b""))
    result = worktree_cleanup.inspect_candidate(str(orphan), root, root)
    assert result.state == "indeterminate"
    assert orphan.exists()


def test_explicit_invalid_repo_does_not_fall_back(run_cleanup, orphan, tmp_path):
    result = run_cleanup(orphan, "--repo-root", tmp_path / "not-a-repo")
    assert result.returncode == 1
    assert orphan.exists()


def test_default_root_uses_native_temp(run_cleanup, tmp_path, lifecycle_env):
    system_temp = tmp_path / "native-temp"
    system_temp.mkdir()
    target = system_temp / "shadowfrog-dreams" / "ns" / "dream-default"
    target.mkdir(parents=True)
    result = run_cleanup(target, extras={
        "DREAM_WORKTREE_BASE": "", "TMPDIR": str(system_temp),
        "TEMP": str(system_temp), "TMP": str(system_temp),
    })
    assert result.returncode == 0, result.stderr
    assert not target.exists()


@pytest.mark.parametrize("kind", ["directory-metadata", "file-target", "invalid-helper"])
def test_ambiguous_inputs_are_refused(run_cleanup, orphan, tmp_path, kind):
    script = CLEANUP
    if kind == "directory-metadata":
        (orphan / ".git").mkdir()
    elif kind == "file-target":
        shutil.rmtree(orphan)
        orphan.write_text("not a worktree", encoding="utf-8")
    else:
        destination = tmp_path / "broken"
        shutil.copytree(SCRIPT_DIR, destination, ignore=shutil.ignore_patterns("__pycache__"))
        (destination / "_worktree_safety.py").write_text("invalid python !", encoding="utf-8")
        script = destination / "dream-cleanup.py"
    result = run_cleanup(orphan, script=script)
    assert result.returncode == 1
    assert result.stderr
    assert orphan.exists()


def test_cached_module_cannot_replace_missing_safety(worktree_cleanup, tmp_path, monkeypatch):
    import types
    monkeypatch.setitem(sys.modules, "_worktree_safety", types.SimpleNamespace(
        safe_worktree_path=lambda path, base: Path(path),
    ))
    monkeypatch.setattr(worktree_cleanup, "SCRIPT_DIR", tmp_path)
    with pytest.raises(worktree_cleanup.CleanupError) as error:
        worktree_cleanup.load_helpers()
    assert error.value.exit_code == 4


def test_detached_owned_worktree_uses_git(run_cleanup, root, lifecycle_repo, lifecycle_git):
    target = root / "ns" / "dream-detached"
    lifecycle_git(lifecycle_repo, "worktree", "add", "-q", "--detach", target)
    result = run_cleanup(target, "--repo-root", lifecycle_repo)
    assert result.returncode == 0, result.stderr
    assert "removed-git" in result.stdout
    assert not target.exists()


def test_root_alias_is_allowed_but_leaf_alias_is_not(
    run_cleanup, root, orphan, tmp_path, make_symlink,
):
    alias = tmp_path / "root-alias"
    make_symlink(alias, root)
    leaf_alias = root / "ns" / "dream-alias"
    make_symlink(leaf_alias, orphan)
    result = run_cleanup(leaf_alias)
    assert result.returncode == 1
    assert orphan.is_dir()
    result = run_cleanup(alias / "ns" / "dream-test", extras={"DREAM_WORKTREE_BASE": str(alias)})
    assert result.returncode == 0, result.stderr
    assert not orphan.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows junction")
def test_junction_is_refused(run_cleanup, root, tmp_path):
    namespace = root / "junction"
    decoy = tmp_path / "decoy"
    target = decoy / "dream-test"
    target.mkdir(parents=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(namespace), str(decoy)],
        capture_output=True, timeout=10,
    )
    if result.returncode:
        pytest.skip(f"junction creation unavailable: {result.stderr!r}")
    result = run_cleanup(namespace / "dream-test")
    assert result.returncode == 1
    assert target.exists()


def test_prune_failure_is_reported(worktree_cleanup, root, monkeypatch):
    monkeypatch.setattr(worktree_cleanup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 1, b"", b"denied"))
    assert "denied" in worktree_cleanup.prune_repo(root)


def test_unexpected_gate_failure_is_refused(worktree_cleanup, root, orphan, monkeypatch):
    safety, _ = worktree_cleanup.load_helpers()
    def fail(*args):
        raise OSError("gate failed")
    monkeypatch.setattr(safety, "safe_worktree_path", fail)
    result = worktree_cleanup.inspect_candidate(str(orphan), root, None)
    assert result.state == "indeterminate"
    assert "gate failed" in result.reason


@pytest.mark.parametrize("name", [
    "space repo", "caf\u00e9",
    pytest.param("tabs\tand\nlines\r", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX filename")),
])
def test_inventory_paths_are_lossless(worktree_cleanup, tmp_path, monkeypatch, name):
    path = tmp_path / name
    data = b"worktree " + str(path).encode("utf-8") + b"\0HEAD abc\0detached\0\0"
    monkeypatch.setattr(worktree_cleanup, "_git", lambda *a, **k: subprocess.CompletedProcess(a, 0, data, b""))
    _, paths = worktree_cleanup.load_helpers()
    assert worktree_cleanup._registered_paths(tmp_path) == {paths.canonical_worktree_path(str(path))}
