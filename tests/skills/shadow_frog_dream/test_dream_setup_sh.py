"""Tests for skills/shadow-frog-dream/dream-setup.sh — Dream worktree setup.

Exercises: --help, happy path worktree+branch creation, RUN_PREFIX detection,
namespace override, slug validation, and dry-run mode.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DREAM_SETUP = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-setup.sh"


def _base_env(cwd: Path, extras: dict | None = None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": str(cwd),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "LANG": "en_US.UTF-8",
    }
    if extras:
        env.update(extras)
    return env


def _make_git_repo(path: Path, branch: str = "main") -> None:
    """Create a git repo with an initial commit and origin/main ref."""
    env = _base_env(path)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True, env=env)
    (path / "README.md").write_text("# test\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True, env=env)
    # Create a fake origin remote pointing to self for origin/main ref
    subprocess.run(["git", "remote", "add", "origin", str(path)], cwd=path, check=True, env=env)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=path, check=True, env=env)


def run_dream_setup(
    args: list[str], cwd: Path, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    """Run dream-setup.sh with given args."""
    env = _base_env(cwd, env_extra)
    return subprocess.run(
        ["bash", str(DREAM_SETUP), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupHelp:
    def test_help_exits_zero(self, tmp_path):
        # --help should work even outside a git repo (it just prints and exits)
        result = run_dream_setup(["--help"], cwd=tmp_path)
        assert result.returncode == 0
        assert "slug" in result.stdout.lower() or "slug" in result.stderr.lower() or "Usage" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupHappyPath:
    """Creates worktree and branch correctly."""

    def test_creates_worktree_and_branch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"

        result = run_dream_setup(
            ["--slug", "t01-test", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)

        assert "dream_ns" in data
        assert "branch_name" in data
        assert "worktree_dir" in data
        assert data["slug"] == "t01-test"
        assert "dream/" in data["branch_name"]
        assert "t01-test" in data["dream_id"]

        # Verify worktree exists
        wt_dir = Path(data["worktree_dir"])
        assert wt_dir.is_dir()

        # Verify branch exists in repo
        env = _base_env(repo)
        branches = subprocess.run(
            ["git", "branch", "--list", data["branch_name"]],
            cwd=repo, capture_output=True, text=True, env=env,
        )
        # Branch may be in worktree, check via worktree list
        wt_list = subprocess.run(
            ["git", "worktree", "list"], cwd=repo,
            capture_output=True, text=True, env=env,
        )
        assert str(wt_dir) in wt_list.stdout

    def test_worktree_has_same_head_as_base(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        env = _base_env(repo)

        # Get main HEAD
        main_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True, env=env,
        ).stdout.strip()

        result = run_dream_setup(
            ["--slug", "t02-head", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["base_commit"] == main_head


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupRunPrefix:
    """RUN_PREFIX detection based on lock files."""

    def test_no_lock_files_empty_prefix(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t03-nolock", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["run_prefix"] == ""

    def test_uv_lock_gives_uv_run_prefix(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        # Add uv.lock
        (repo / "uv.lock").write_text("")
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t04-uvlock", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["run_prefix"] == "uv run"

    def test_package_lock_gives_npx_prefix(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / "package-lock.json").write_text("{}")
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t05-npm", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["run_prefix"] == "npx"


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupIdempotent:
    """Re-running with same slug cleans and recreates (idempotent)."""

    def test_same_slug_twice_succeeds(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "wt"

        args = ["--slug", "t06-idem", "--repo-root", str(repo), "--print-json"]
        extras = {"DREAM_WORKTREE_BASE": str(worktree_base)}

        r1 = run_dream_setup(args, cwd=repo, env_extra=extras)
        assert r1.returncode == 0, f"stderr: {r1.stderr}"

        r2 = run_dream_setup(args, cwd=repo, env_extra=extras)
        assert r2.returncode == 0, f"stderr: {r2.stderr}"
        # Both should produce valid JSON with same worktree dir
        d1 = json.loads(r1.stdout)
        d2 = json.loads(r2.stdout)
        assert d1["worktree_dir"] == d2["worktree_dir"]


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupNamespace:
    """DREAM_NAMESPACE / --namespace honored in branch name."""

    def test_namespace_override_in_branch(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t07-ns", "--namespace", "my-custom-ns",
             "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["dream_ns"] == "my-custom-ns"
        assert "dream/my-custom-ns/" in data["branch_name"]

    def test_env_namespace_used(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t08-envns", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_NAMESPACE": "env-ns-test",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["dream_ns"] == "env-ns-test"


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupValidation:
    """Input validation prevents bad slugs."""

    def test_missing_slug_fails(self, tmp_path):
        result = run_dream_setup([], cwd=tmp_path)
        assert result.returncode != 0
        assert "slug" in result.stderr.lower()

    def test_invalid_slug_rejected(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        result = run_dream_setup(
            ["--slug", "bad slug!!", "--repo-root", str(repo)],
            cwd=repo,
        )
        assert result.returncode != 0
        assert "must match" in result.stderr


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupGitignoreGuard:
    """dream requires .shadow/ to be git-tracked, not gitignored."""

    def test_refuses_when_shadow_gitignored(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / ".gitignore").write_text(".shadow/\n")
        (repo / ".shadow").mkdir()
        result = run_dream_setup(
            ["--slug", "t10-ignored", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
        )
        assert result.returncode != 0
        assert ".shadow/ is gitignored" in result.stderr

    def test_proceeds_when_shadow_tracked(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        # .gitignore present but does NOT ignore .shadow/
        (repo / ".gitignore").write_text("build/\n__pycache__/\n")
        (repo / ".shadow").mkdir()
        worktree_base = tmp_path / "wt"
        result = run_dream_setup(
            ["--slug", "t10-tracked", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "gitignored" not in result.stderr

    def test_refuses_when_shadow_gitignored_but_already_tracked(self, tmp_path):
        """Edge case: .shadow/ is gitignored AND has previously-committed
        content. `git check-ignore .shadow` reports not-ignored (tracked wins),
        but `git add -A` still drops NEW children — so the guard must probe a
        child path and still refuse."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        # Commit some .shadow content FIRST, then add the gitignore rule.
        shadow_meta = repo / ".shadow" / "_meta"
        shadow_meta.mkdir(parents=True)
        (shadow_meta / "state.json").write_text("{}\n")
        env = _base_env(repo)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "track shadow"],
                       cwd=repo, check=True, env=env)
        (repo / ".gitignore").write_text(".shadow/\n")
        subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "ignore shadow"],
                       cwd=repo, check=True, env=env)

        result = run_dream_setup(
            ["--slug", "t10-tracked-ignored", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
        )
        assert result.returncode != 0
        assert ".shadow/ is gitignored" in result.stderr


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupDryRun:
    """--dry-run computes values without creating worktree."""

    def test_dry_run_no_worktree_created(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "wt"

        result = run_dream_setup(
            ["--slug", "t09-dry", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        # Worktree dir should NOT exist
        assert not Path(data["worktree_dir"]).exists()
        assert data["slug"] == "t09-dry"


# ===========================================================================
# Auto-GC throttle (Bug A fix from bug-cleanup-gaps.md)
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupAutoGC:
    """`dream-setup.sh` triggers `dream-gc.sh` periodically.

    Bug A from bug-cleanup-gaps.md: dream-gc.sh existed but had no caller
    in the skill flow, so long-running fleets accumulated orphans
    indefinitely. dream-setup.sh now invokes it at the start of each new
    dream, throttled to once per DREAM_GC_INTERVAL_MIN (default 60).
    """

    def _orphan(self, base: Path, ns: str, name: str = "dream-orphan") -> Path:
        """Plant an orphan worktree under the namespace dir."""
        d = base / ns / name
        d.mkdir(parents=True)
        (d / ".git").write_text("gitdir: /nonexistent/path\n")
        (d / "leftover.txt").write_text("orphaned\n")
        ancient = 946684800
        os.utime(d, (ancient, ancient))
        return d

    def test_auto_gc_runs_when_no_tombstone(self, tmp_path):
        """First invocation sweeps orphans (no tombstone yet)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        # Plant an orphan that the auto-GC should clean up.
        orphan = self._orphan(worktree_base, ns)
        assert orphan.exists()

        result = run_dream_setup(
            ["--slug", "t01-gc", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                # Force min-age-min=0 so the ancient orphan is in the find window
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Orphan must be gone — auto-GC ran.
        assert not orphan.exists(), (
            f"Auto-GC should have swept the orphan\nstderr: {result.stderr}"
        )
        # Tombstone created.
        tombstone = worktree_base / ns / ".last-gc"
        assert tombstone.exists()

    def test_auto_gc_throttled_by_recent_tombstone(self, tmp_path):
        """Fresh tombstone (< DREAM_GC_INTERVAL_MIN) suppresses the trigger."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        # Pre-create the tombstone with current mtime (fresh).
        (worktree_base / ns).mkdir(parents=True)
        tombstone = worktree_base / ns / ".last-gc"
        tombstone.touch()

        orphan = self._orphan(worktree_base, ns)

        result = run_dream_setup(
            ["--slug", "t02-throttle", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_INTERVAL_MIN": "60",  # tombstone is fresh, won't trigger
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Orphan must STILL exist — auto-GC was throttled.
        assert orphan.exists(), (
            f"Fresh tombstone should suppress auto-GC\nstderr: {result.stderr}"
        )

    def test_auto_gc_disabled_via_env(self, tmp_path):
        """`DREAM_GC_AUTO=0` opts out of the auto-trigger entirely."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        orphan = self._orphan(worktree_base, ns)

        result = run_dream_setup(
            ["--slug", "t03-disabled", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_AUTO": "0",
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Orphan must STILL exist — GC was opt'd out.
        assert orphan.exists(), (
            f"DREAM_GC_AUTO=0 should disable auto-GC\nstderr: {result.stderr}"
        )
        # Tombstone NOT created.
        assert not (worktree_base / ns / ".last-gc").exists()

    def test_auto_gc_invalid_env_warns_and_continues(self, tmp_path):
        """Non-integer interval/age must not break dream setup."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"

        result = run_dream_setup(
            ["--slug", "t04-badenv", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_INTERVAL_MIN": "not-a-number",
            },
        )
        # Dream setup must still succeed — auto-GC is best-effort.
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        # Worktree was still created.
        assert Path(data["worktree_dir"]).is_dir()

    def test_auto_gc_does_not_pollute_eval_stdout(self, tmp_path):
        """Auto-GC output MUST go to stderr to preserve the `eval` contract."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        # Plant an orphan so the GC actually has work to log about.
        self._orphan(worktree_base, ns)

        # Use --print-env (NOT --print-json) — this is the eval-consumed path.
        result = run_dream_setup(
            ["--slug", "t05-stdout", "--repo-root", str(repo)],  # default = --print-env
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # Stdout must contain ONLY export lines — nothing from the GC.
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            assert stripped.startswith("export "), (
                f"Non-export line on stdout would break `eval`: {line!r}\n"
                f"Full stdout:\n{result.stdout}"
            )

    def test_auto_gc_sweeps_other_namespace_orphans_too(self, tmp_path):
        """The auto-trigger sweeps the whole base, not just its own ns.

        That's deliberate — leaks in any ns count, and the find walk is cheap.
        """
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"

        # Orphan under a DIFFERENT namespace
        other_orphan = self._orphan(worktree_base, ns="other-repo")
        assert other_orphan.exists()

        result = run_dream_setup(
            ["--slug", "t06-cross", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # The cross-namespace orphan was swept too.
        assert not other_orphan.exists(), (
            f"Auto-GC sweeps the whole base\nstderr: {result.stderr}"
        )
