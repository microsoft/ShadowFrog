"""Tests for skills/shadow-frog-dream/dream-cleanup.sh.

Two layers to exercise:
1. Happy paths — proper worktrees get cleaned (git path succeeds).
2. Fallback paths — when `git worktree remove` fails (dead .git pointer,
   no repo-root available, …) rm -rf takes over AND the safety gate
   refuses anything outside `$DREAM_WORKTREE_BASE/<ns>/dream-<slug>`.

The bug this fixes — `git worktree remove ... 2>/dev/null` swallowing
the error AND leaving the directory on disk — is the test below
`test_fallback_rm_when_gitdir_broken`.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CLEANUP_SH = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-cleanup.sh"


def _base_env(extras: dict | None = None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "LANG": "en_US.UTF-8",
    }
    if extras:
        env.update(extras)
    return env


def _make_repo(path: Path) -> Path:
    """Create a tiny git repo to host worktrees."""
    env = _base_env()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.invalid"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True, env=env)
    (path / "r.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True, env=env)
    return path


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(CLEANUP_SH), *args],
        capture_output=True, text=True, env=_base_env(env_extra),
    )


# ===========================================================================
# Usage / arg parsing
# ===========================================================================

class TestUsage:
    def test_help_exits_zero(self):
        r = _run(["--help"])
        assert r.returncode == 0
        assert "Usage" in r.stdout or "dream-cleanup.sh" in r.stdout

    def test_no_args_errors(self):
        r = _run([])
        assert r.returncode == 2
        assert "required" in r.stderr.lower()

    def test_unknown_flag_errors(self):
        r = _run(["/tmp/x", "--bogus"])
        assert r.returncode == 2

    def test_extra_positional_errors(self):
        r = _run(["/tmp/a", "/tmp/b"])
        assert r.returncode == 2


# ===========================================================================
# Happy path: git worktree remove succeeds
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestHappyPath:
    def test_removes_real_worktree(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        target = base / "proj" / "dream-foo"
        target.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q",
             str(target), "-b", "dream/proj/20260101-000000Z-foo"],
            check=True, env=_base_env(),
        )
        assert target.is_dir()

        r = _run(
            [str(target), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        assert not target.exists()
        # git worktree bookkeeping is clean.
        list_r = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list"],
            capture_output=True, text=True, env=_base_env(),
        )
        assert str(target) not in list_r.stdout

    def test_idempotent_when_path_missing(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        base.mkdir()
        # Path never existed.
        r = _run(
            [str(base / "proj" / "dream-foo"), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert "Nothing to clean" in r.stdout


# ===========================================================================
# Fallback path: rm -rf fires when git can't help
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestFallback:
    def test_fallback_rm_when_gitdir_broken(self, tmp_path):
        """Repro for bug-worktree-leak.md: a dream worktree whose .git
        pointer is dangling (git's perspective: gone; disk's perspective:
        still there). The pre-fix snippet silently leaked this — this test
        FAILS on the old `git worktree remove ... 2>/dev/null` snippet."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        target = base / "proj" / "dream-leaked"
        target.mkdir(parents=True)
        # Dangling gitdir pointer.
        (target / ".git").write_text("gitdir: /nonexistent/path\n")
        (target / "leaked.pyc").write_text("# nobody owns me\n")

        r = _run(
            [str(target), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        assert not target.exists(), "fallback rm -rf should have cleared it"

    def test_fallback_rm_without_repo_root(self, tmp_path):
        """When the caller doesn't pass --repo-root and the dream worktree
        is dead, the script should still remove the directory."""
        base = tmp_path / "wt-base"
        target = base / "proj" / "dream-orphan"
        target.mkdir(parents=True)
        (target / "leaked.txt").write_text("orphan\n")
        # No .git file at all → looks like never-was-a-worktree.

        r = _run(
            [str(target)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}\nstdout: {r.stdout}"
        assert not target.exists()


# ===========================================================================
# Safety gate refuses
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestSafetyGate:
    def test_refuses_path_outside_base(self, tmp_path):
        """If WORKTREE_DIR is outside $DREAM_WORKTREE_BASE the script
        refuses AND leaves the directory untouched."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        base.mkdir()
        # Decoy outside the base.
        outside = tmp_path / "decoy" / "proj" / "dream-foo"
        outside.mkdir(parents=True)
        (outside / "important.txt").write_text("DO NOT DELETE\n")

        r = _run(
            [str(outside), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 1, f"expected refuse: {r.stdout}\n{r.stderr}"
        assert outside.is_dir(), "directory must survive a refused cleanup"
        assert (outside / "important.txt").read_text() == "DO NOT DELETE\n"

    def test_refuses_base_itself(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        base.mkdir()
        r = _run(
            [str(base), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 1
        assert base.is_dir()

    def test_refuses_sensitive_base_via_env(self, tmp_path):
        """DREAM_WORKTREE_BASE=/tmp must be refused even though /tmp/x/
        looks shape-correct. Verifies the macOS /private/tmp bypass is
        closed end-to-end through the bash wrapper."""
        repo = _make_repo(tmp_path / "repo")
        # The path lives under /tmp — DON'T create it; the safety gate
        # should refuse based on base alone.
        r = _run(
            ["/tmp/should-never-rm/proj/dream-foo",
             "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": "/tmp"},
        )
        # Note: refused-because-base-is-/tmp surfaces as rc=1 from the
        # safety gate. rc=0 with "Nothing to clean" would mean the gate
        # accepted /tmp as a valid base — regression alarm.
        assert r.returncode == 1, (
            f"sensitive base /tmp was NOT refused — gate regression!\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_refuses_path_with_traversal(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        base.mkdir()
        bad = f"{base}/proj/../escape/dream-foo"
        r = _run(
            [bad, "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 1

    def test_refuses_path_with_wrong_shape(self, tmp_path):
        """`<base>/proj/notdream-foo` shouldn't be deletable — the leaf
        must start with `dream-`."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        target = base / "proj" / "notdream-foo"
        target.mkdir(parents=True)
        r = _run(
            [str(target), "--repo-root", str(repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 1
        assert target.is_dir()


# ===========================================================================
# Regressions for the 5-model review panel findings (S1-S3, C1-C2)
# ===========================================================================

class TestSafetyModuleMissing:
    """C1: if _worktree_safety.py is missing, python3 itself exits 2 —
    the same code as the gate's "safe but path missing" sentinel. The
    script MUST distinguish these and refuse loudly when the gate is
    unloadable, not silently report success.
    """

    def _copy_script_without_safety(self, dst: Path) -> Path:
        """Copy dream-cleanup.sh + _worktree_safety.py to dst/, then
        DELETE the safety module to simulate a broken install."""
        import shutil
        src = REPO_ROOT / "skills" / "shadow-frog-dream"
        dst.mkdir()
        shutil.copy(src / "dream-cleanup.sh", dst / "dream-cleanup.sh")
        # intentionally do NOT copy _worktree_safety.py
        os.chmod(dst / "dream-cleanup.sh", 0o755)
        return dst / "dream-cleanup.sh"

    def test_missing_safety_module_exits_4(self, tmp_path):
        cleanup = self._copy_script_without_safety(tmp_path / "broken")
        base = tmp_path / "wt-base"
        wt = base / "proj" / "dream-foo"
        wt.mkdir(parents=True)
        r = subprocess.run(
            ["bash", str(cleanup), str(wt)],
            capture_output=True, text=True,
            env=_base_env({"DREAM_WORKTREE_BASE": str(base)}),
        )
        assert r.returncode == 4, (
            f"missing _worktree_safety.py should exit 4, got {r.returncode}\n"
            f"stderr: {r.stderr}"
        )
        assert "safety module" in r.stderr.lower()
        # CRITICAL: the worktree must NOT be deleted when the gate is unloadable.
        assert wt.is_dir(), "worktree was deleted without a safety gate!"


class TestRepoRootEnvInherited:
    """C2: $REPO_ROOT env var must be respected when --repo-root flag is
    absent. The previous `REPO_ROOT="$REPO_ROOT_OVERRIDE"` clobbered any
    inherited value."""

    def test_repo_root_from_env_is_used(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        base.mkdir()
        wt = base / "proj" / "dream-foo"
        # Real worktree registered to repo (so git can remove it).
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-b", "tmp-test", str(wt)],
            check=True, capture_output=True, env=_base_env(),
        )
        r = _run(
            [str(wt)],   # NO --repo-root flag
            env_extra={
                "DREAM_WORKTREE_BASE": str(base),
                "REPO_ROOT": str(repo),
            },
        )
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert not wt.exists(), "worktree should have been removed via env REPO_ROOT"
        # The polite git path should have run (not just the fallback rm).
        assert "Removed via git worktree" in r.stdout


class TestRepoRootAsWorktree:
    """S3: when REPO_ROOT is itself a worktree, `.git` is a file (not a
    directory). The previous `[[ -d "$REPO_ROOT/.git" ]]` check silently
    skipped the polite git-worktree path."""

    def test_repo_root_as_worktree(self, tmp_path):
        main_repo = _make_repo(tmp_path / "main")
        # Add a worktree that we'll USE AS THE REPO_ROOT for the cleanup call.
        worktree_repo = tmp_path / "wt-as-root"
        subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "add",
             "-b", "tmp-root", str(worktree_repo)],
            check=True, capture_output=True, env=_base_env(),
        )
        # Sanity: this is a worktree (`.git` is a file, not a directory).
        assert (worktree_repo / ".git").is_file()

        # Now register a dream-shaped worktree against the SAME main repo.
        base = tmp_path / "wt-base"
        base.mkdir()
        dream_wt = base / "proj" / "dream-foo"
        subprocess.run(
            ["git", "-C", str(main_repo), "worktree", "add",
             "-b", "tmp-dream", str(dream_wt)],
            check=True, capture_output=True, env=_base_env(),
        )

        # Cleanup, passing the WORKTREE (not the main repo) as --repo-root.
        # Pre-fix this would skip the polite git path silently.
        r = _run(
            [str(dream_wt), "--repo-root", str(worktree_repo)],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        assert not dream_wt.exists()
        # The polite git path SHOULD have fired — that's the regression.
        assert "Removed via git worktree" in r.stdout, (
            f"polite git path was skipped (regression of -d .git check):\n"
            f"stdout: {r.stdout}"
        )
