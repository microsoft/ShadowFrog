"""Tests for skills/shadow-frog-dream/dream-gc.sh.

Sweeper for orphan dream worktrees — defense in depth for the cases
`dream-cleanup.sh` missed (machine crash, OOM-killed agent, …). Every
candidate is re-validated through the safety gate before removal, so
even a misconfigured `$DREAM_WORKTREE_BASE` cannot cause data loss.
"""
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
GC_SH = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-gc.sh"


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
    env = _base_env()
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.email", "t@t.invalid"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "user.name", "T"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "config", "commit.gpgsign", "false"], check=True, env=env)
    (path / "r.md").write_text("hi\n")
    subprocess.run(["git", "-C", str(path), "add", "-A"], check=True, env=env)
    subprocess.run(["git", "-C", str(path), "commit", "-q", "-m", "init"], check=True, env=env)
    return path


def _orphan_worktree(parent: Path, name: str = "dream-orphan", old: bool = True) -> Path:
    """Build a dir under `parent` that looks like an abandoned worktree."""
    d = parent / name
    d.mkdir(parents=True)
    (d / ".git").write_text("gitdir: /nonexistent/path/worktrees/missing\n")
    (d / "leftover.txt").write_text("orphaned\n")
    if old:
        # Force ancient mtime so --min-age-min finds it. Use os.utime
        # because `touch -t` syntax differs across platforms.
        ancient = 946684800  # 2000-01-01 00:00 UTC
        os.utime(d, (ancient, ancient))
    return d


def _run(args: list[str], env_extra: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(GC_SH), *args],
        capture_output=True, text=True, env=_base_env(env_extra),
    )


# ===========================================================================
# Usage
# ===========================================================================

class TestUsage:
    def test_help_exits_zero(self):
        r = _run(["--help"])
        assert r.returncode == 0
        assert "Usage" in r.stdout or "dream-gc.sh" in r.stdout

    def test_unknown_flag_errors(self):
        r = _run(["--bogus"])
        assert r.returncode == 2

    @pytest.mark.parametrize("bad_age", ["foo", "-5", "5min", "1.5", ""])
    def test_invalid_min_age_errors(self, bad_age):
        r = _run(["--min-age-min", bad_age])
        assert r.returncode == 2, f"--min-age-min {bad_age!r} should reject"


# ===========================================================================
# Safety: base validation
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestBaseSafety:
    @pytest.mark.parametrize("base", [
        "/", "/tmp", "/etc", "/var", "/home", "/Users",
        "/private/tmp", "/private/etc",
    ])
    def test_refuses_sensitive_base(self, base):
        r = _run(["--dry-run"], env_extra={"DREAM_WORKTREE_BASE": base})
        assert r.returncode == 1, (
            f"sensitive base {base!r} should be refused. stdout: {r.stdout}\n"
            f"stderr: {r.stderr}"
        )

    def test_noop_when_base_missing(self, tmp_path):
        base = tmp_path / "never-existed"
        # No mkdir — base must not exist
        r = _run([], env_extra={"DREAM_WORKTREE_BASE": str(base)})
        assert r.returncode == 0
        assert "does not exist" in r.stdout


# ===========================================================================
# Sweep behavior
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestSweep:
    def test_removes_orphan(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        ns_dir = base / "proj"
        orphan = _orphan_worktree(ns_dir, "dream-foo")

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert not orphan.exists(), "orphan should be swept"

    def test_keeps_live_worktree(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        live = base / "proj" / "dream-live"
        live.parent.mkdir(parents=True)
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q",
             str(live), "-b", "dream/proj/20260101-000000Z-live"],
            check=True, env=_base_env(),
        )
        # Force ancient mtime so --min-age-min doesn't save it. Only the
        # orphan-check should save it.
        ancient = 946684800
        os.utime(live, (ancient, ancient))

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert live.is_dir(), "live worktree must NOT be swept"

    def test_min_age_skips_fresh_orphan(self, tmp_path):
        """A fresh orphan (mtime = now) gets skipped — protects races
        with `dream-setup.sh` that just created the dir but hasn't
        finished registering the worktree yet."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        fresh = _orphan_worktree(base / "proj", "dream-fresh", old=False)
        # leave mtime at "now"

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "5"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert fresh.is_dir(), "fresh dir must NOT be swept"

    def test_dry_run_removes_nothing(self, tmp_path):
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        orphan = _orphan_worktree(base / "proj", "dream-foo")

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "0", "--dry-run"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert orphan.is_dir(), "dry-run must NOT remove"
        assert "WOULD REMOVE" in r.stdout

    def test_skips_non_dream_dir(self, tmp_path):
        """`<base>/proj/notdream` (no dream- prefix) must be ignored
        outright — the shape check filters it before the gate."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        non_dream = base / "proj" / "notdream-foo"
        non_dream.mkdir(parents=True)
        (non_dream / "data.txt").write_text("important\n")
        ancient = 946684800
        os.utime(non_dream, (ancient, ancient))

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert non_dream.is_dir(), "non-dream-prefix dir must be ignored"

    def test_orphan_with_missing_git_file(self, tmp_path):
        """A dir with no .git at all is also considered orphan."""
        repo = _make_repo(tmp_path / "repo")
        base = tmp_path / "wt-base"
        d = base / "proj" / "dream-bare"
        d.mkdir(parents=True)
        (d / "stuff.txt").write_text("x\n")
        ancient = 946684800
        os.utime(d, (ancient, ancient))

        r = _run(
            ["--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert not d.exists()


# ===========================================================================
# Regressions for the 5-model review panel findings (S1, C1)
# ===========================================================================

class TestSafetyModuleMissingGc:
    """C1: a missing _worktree_safety.py file makes python3 exit 2 —
    the same code as the gate's success-but-missing-path. dream-gc.sh
    must refuse to sweep when its gate is unloadable, not silently
    sweep with no gate."""

    def test_missing_safety_module_exits_4(self, tmp_path):
        import shutil
        broken = tmp_path / "broken"
        broken.mkdir()
        shutil.copy(GC_SH, broken / "dream-gc.sh")
        # No _worktree_safety.py copied — gate is unloadable.
        os.chmod(broken / "dream-gc.sh", 0o755)

        base = tmp_path / "wt-base"
        wt = _orphan_worktree(base / "proj", "dream-foo", old=True)
        assert wt.exists()

        r = subprocess.run(
            ["bash", str(broken / "dream-gc.sh")],
            capture_output=True, text=True,
            env=_base_env({"DREAM_WORKTREE_BASE": str(base)}),
        )
        assert r.returncode == 4, (
            f"missing safety must exit 4, got {r.returncode}\n"
            f"stderr: {r.stderr}"
        )
        assert "safety module" in r.stderr.lower()
        # CRITICAL: nothing was removed.
        assert wt.exists(), "orphan was swept without a safety gate!"


class TestGitdirParserRobust:
    """S1: the old `awk -F': *'` parser split on every `:`, so a valid
    gitdir line like `gitdir: /repo:with-colon/.git/worktrees/foo` got
    truncated and the live worktree was misclassified as orphan and
    DELETED. New parser must preserve `:` chars after the `gitdir: ` prefix.
    """

    def test_gitdir_path_with_colon_is_not_orphan(self, tmp_path):
        # Build a fake target the parser will think exists.
        gitdir_real = tmp_path / "container:with:colons" / "worktrees" / "foo"
        gitdir_real.mkdir(parents=True)
        # Build the candidate with a `.git` file pointing at the colon-laden path.
        base = tmp_path / "wt-base"
        candidate = base / "proj" / "dream-foo"
        candidate.mkdir(parents=True)
        (candidate / ".git").write_text(f"gitdir: {gitdir_real}\n")
        # Age it so --min-age-min finds it.
        ancient = 946684800
        os.utime(candidate, (ancient, ancient))

        r = _run(
            ["--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stdout: {r.stdout}\nstderr: {r.stderr}"
        # The candidate must NOT have been removed — its gitdir target exists.
        assert candidate.exists(), (
            f"live worktree with colon-in-gitdir was DELETED — awk parser regression\n"
            f"stdout: {r.stdout}"
        )
        assert "removed=0" in r.stdout
        assert "kept=1" in r.stdout

    def test_gitdir_with_crlf_endings_is_not_orphan(self, tmp_path):
        """Opus 4.7-xhigh nit: CRLF endings would leave a trailing \\r
        in the parsed gitdir, making `-e` falsely return false."""
        gitdir_real = tmp_path / "worktrees" / "foo"
        gitdir_real.mkdir(parents=True)
        base = tmp_path / "wt-base"
        candidate = base / "proj" / "dream-foo"
        candidate.mkdir(parents=True)
        # Write the .git file with CRLF line endings.
        (candidate / ".git").write_bytes(f"gitdir: {gitdir_real}\r\n".encode())
        ancient = 946684800
        os.utime(candidate, (ancient, ancient))

        r = _run(
            ["--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert candidate.exists(), (
            f"CRLF-terminated gitdir was misparsed → live worktree DELETED\n"
            f"stdout: {r.stdout}"
        )
        assert "kept=1" in r.stdout

    def test_gitdir_with_relative_path(self, tmp_path):
        """Rare-but-legal: relative `gitdir:` paths must resolve relative
        to the .git file's directory, not the cwd."""
        base = tmp_path / "wt-base"
        candidate = base / "proj" / "dream-foo"
        candidate.mkdir(parents=True)
        # Build a real target adjacent to the candidate.
        target = candidate / ".relative-gitdir"
        target.mkdir()
        (candidate / ".git").write_text("gitdir: .relative-gitdir\n")
        ancient = 946684800
        os.utime(candidate, (ancient, ancient))

        r = _run(
            ["--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert candidate.exists(), (
            f"relative gitdir was treated as orphan → live worktree DELETED\n"
            f"stdout: {r.stdout}"
        )
        assert "kept=1" in r.stdout


# ===========================================================================
# --task-complete mode (Bug B fix from bug-cleanup-gaps.md)
#
# Round-2 review (5-model panel on commit 5ab361d) flagged that the first
# implementation of these tests planted FAKE `.git` files instead of using
# real `git worktree add`, so every assertion was satisfied by the dangerous
# `rm -rf` fallback rather than the polite `git worktree remove` path. The
# helper below uses a real `git worktree add` so:
#   1. `git worktree remove --force` actually succeeds in the happy path.
#   2. The cross-namespace and locked-worktree regression tests can plant
#      worktrees that git ACTUALLY recognizes (and refuses to touch from
#      the wrong repo, or when locked).
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestTaskComplete:
    """--task-complete mode sweeps registered worktrees in a single namespace.

    Bug B from bug-cleanup-gaps.md: worktrees that finished pushing but
    never had `dream-cleanup.sh` called on them have VALID `.git` pointers
    and so survive the standard orphan check forever. --task-complete
    broadens the sweep to catch them. The round-2 review surfaced that
    the original implementation walked the WHOLE base, which would have
    swept other repos' live worktrees — so the flag is now strictly
    namespace-scoped and refuses without `--namespace`.
    """

    def _real_registered_worktree(
        self,
        base: Path,
        ns: str,
        name: str,
        repo: Path | None = None,
        old: bool = True,
        repos_root: Path | None = None,
    ) -> tuple[Path, Path]:
        """Plant a REAL `git worktree add`'d dream-* dir under <base>/<ns>.

        Returns (candidate_path, repo_path). When repo is None, creates a
        fresh repo under `repos_root` (or `base.parent` if not given).
        Uses a unique branch name so multiple worktrees per repo don't
        collide.
        """
        if repo is None:
            parent = repos_root if repos_root is not None else base.parent
            parent.mkdir(parents=True, exist_ok=True)
            repo = parent / f"repo-{ns}"
            if not (repo / ".git").exists():
                _make_repo(repo)
        candidate = base / ns / name
        candidate.parent.mkdir(parents=True, exist_ok=True)
        branch = f"dream/{ns}/{name}"
        env = _base_env()
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "add", "-q", str(candidate), "-b", branch],
            check=True, env=env,
        )
        (candidate / "leftover.txt").write_text("stale\n")
        if old:
            ancient = 946684800  # 2000-01-01
            os.utime(candidate, (ancient, ancient))
        return candidate, repo

    # --- The happy path (real git worktree, polite remove succeeds) ----

    def test_default_mode_keeps_registered_worktree(self, tmp_path):
        """Sanity: default mode (no --task-complete) leaves registered dirs alone."""
        base = tmp_path / "wt-base"
        candidate, _ = self._real_registered_worktree(base, ns="proj", name="dream-stale")
        r = _run(
            ["--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert candidate.exists(), (
            f"Default mode must NOT touch registered worktrees\nstdout: {r.stdout}"
        )
        assert "kept=1" in r.stdout

    def test_task_complete_sweeps_registered_worktree_via_polite_path(self, tmp_path):
        """--task-complete uses `git worktree remove --force` for registered dirs.

        Verifies BOTH: (1) the dir is gone, (2) git's bookkeeping was
        updated (`git worktree list` no longer shows it). If the script
        ever silently regressed to the unsafe `rm -rf` fallback, the
        bookkeeping assertion would still pass file-existence but git
        would still list it as "prunable" — so we assert it's gone from
        the list entirely.
        """
        base = tmp_path / "wt-base"
        candidate, repo = self._real_registered_worktree(base, ns="proj", name="dream-stale")
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, (
            f"--task-complete should succeed\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert not candidate.exists(), (
            f"Registered worktree must be removed\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "removed=1" in r.stdout
        assert "stale-registered" in r.stdout
        # Git's worktree list must no longer show it (polite path succeeded).
        wt_list = subprocess.run(
            ["git", "-C", str(repo), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, env=_base_env(),
        )
        assert str(candidate) not in wt_list.stdout, (
            f"`git worktree list` still shows the path — polite remove must have failed\n"
            f"list: {wt_list.stdout}\n"
            f"gc-stdout: {r.stdout}\n"
            f"gc-stderr: {r.stderr}"
        )

    def test_task_complete_sweeps_orphans_AND_registered(self, tmp_path):
        """--task-complete is a superset: catches both orphans and registered."""
        base = tmp_path / "wt-base"
        # Both must live under the same namespace now that scoping is enforced.
        orphan = _orphan_worktree(base / "proj", name="dream-orphan")
        registered, repo = self._real_registered_worktree(base, ns="proj", name="dream-reg")
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert not orphan.exists(), "orphan should be swept"
        assert not registered.exists(), "registered should be swept"
        assert "removed=2" in r.stdout

    def test_task_complete_respects_min_age(self, tmp_path):
        """Fresh dirs (< --min-age-min) survive even in task-complete mode.

        NOTE: this is an mtime gate, NOT a liveness check. A real long-running
        dream that simply hasn't written to disk in N minutes is still
        eligible — the agent is responsible for asserting end-of-session.
        """
        base = tmp_path / "wt-base"
        candidate, repo = self._real_registered_worktree(base, ns="proj", name="dream-fresh", old=False)
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo), "--min-age-min", "1"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert candidate.exists(), (
            f"Fresh dir must survive --min-age-min\nstdout: {r.stdout}"
        )
        assert "removed=0" in r.stdout

    def test_task_complete_dry_run_only_logs(self, tmp_path):
        """--task-complete + --dry-run logs but removes nothing."""
        base = tmp_path / "wt-base"
        candidate, repo = self._real_registered_worktree(base, ns="proj", name="dream-stale")
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo), "--dry-run", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0
        assert candidate.exists(), "Dry-run must NOT remove"
        assert "WOULD REMOVE" in r.stdout
        assert "stale-registered" in r.stdout

    def test_task_complete_safety_gate_still_holds(self, tmp_path):
        """Safety gate cannot be bypassed by --task-complete.

        Even an asserted task_complete sweep must refuse a sensitive base.
        """
        r = _run(
            ["--task-complete", "--namespace", "proj", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": "/tmp"},
        )
        assert r.returncode == 1, "Sensitive base refused regardless of mode"

    # --- Round-2 regressions (the panel's blocker + criticals) -----------

    def test_task_complete_requires_namespace(self, tmp_path):
        """`--task-complete` without a namespace must exit 2 (not silently sweep all).

        BLOCKER from the 5-model panel: the prior implementation walked the
        WHOLE `$DREAM_WORKTREE_BASE`, so an end-of-session sweep in repo A
        would delete repo B's live worktrees in a shared-base fleet. The
        script now refuses unless a namespace can be resolved.
        """
        base = tmp_path / "wt-base"
        base.mkdir()
        r = _run(
            ["--task-complete", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        # No namespace, no DREAM_NAMESPACE env, no --repo-root → refuse.
        assert r.returncode == 2, (
            f"Expected refusal exit 2\nstdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "requires --namespace" in r.stderr or "Refusing" in r.stderr

    def test_task_complete_refuses_unsafe_namespace_chars(self, tmp_path):
        """Namespace input is validated before reaching `find`."""
        base = tmp_path / "wt-base"
        base.mkdir()
        r = _run(
            ["--task-complete", "--namespace", "../escape", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 2
        assert "namespace must match" in r.stderr.lower() or "--namespace" in r.stderr

    def test_task_complete_does_NOT_cross_namespaces(self, tmp_path):
        """Cross-namespace data-loss prevention: nsA's task-complete must NEVER touch nsB.

        This is the round-2 BLOCKER from all 5 reviewers — reproduced
        empirically against the prior implementation. Two real
        git-worktree-add'd dreams in two namespaces, A's sweep must leave
        B intact.
        """
        base = tmp_path / "wt-base"
        cand_a, repo_a = self._real_registered_worktree(base, ns="nsA", name="dream-a")
        cand_b, repo_b = self._real_registered_worktree(base, ns="nsB", name="dream-b")
        r = _run(
            ["--task-complete", "--namespace", "nsA", "--repo-root", str(repo_a), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert not cand_a.exists(), "nsA dream should be removed"
        assert cand_b.exists(), (
            f"nsB dream MUST survive nsA's sweep — cross-namespace data loss!\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        # And git's bookkeeping for repoB is intact.
        list_b = subprocess.run(
            ["git", "-C", str(repo_b), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, env=_base_env(),
        )
        assert str(cand_b) in list_b.stdout

    def test_task_complete_refuses_locked_worktree_no_rm_fallback(self, tmp_path):
        """If `git worktree remove --force` refuses (locked), we WARN and skip.

        CRITICAL from the round-2 review: the prior code fell through to
        `rm -rf` whenever git failed, silently destroying deliberately
        locked worktrees. New behavior: WARN, refuse, leave the dir.
        """
        base = tmp_path / "wt-base"
        candidate, repo = self._real_registered_worktree(base, ns="proj", name="dream-locked")
        # Lock it via git — `--force` cannot override a lock.
        subprocess.run(
            ["git", "-C", str(repo), "worktree", "lock", str(candidate), "--reason", "test"],
            check=True, env=_base_env(),
        )
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        # Locked worktree MUST survive — no rm -rf fallback.
        assert candidate.exists(), (
            f"Locked worktree must not be force-removed via rm fallback\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        # And the script logged a WARN for diagnosability.
        assert "WARN" in r.stderr
        assert "refused=1" in r.stdout

    def test_task_complete_skips_other_repos_worktree_no_rm_fallback(self, tmp_path):
        """Defense-in-depth: even if scoping were bypassed, the rm fallback
        no longer destroys worktrees registered with a DIFFERENT repo.

        Plants nsA's worktree but invokes --task-complete with a UNRELATED
        repo's --repo-root. `git worktree remove` errors out ("not a working
        tree"); the script must NOT fall through to rm -rf.
        """
        base = tmp_path / "wt-base"
        candidate, repo_owner = self._real_registered_worktree(base, ns="nsA", name="dream-owned")
        # An unrelated repo — must be a valid git repo so `_is_git_root` passes
        # and we actually exercise the `git worktree remove` failure path.
        other_repo = tmp_path / "other-repo"
        _make_repo(other_repo)
        r = _run(
            ["--task-complete", "--namespace", "nsA", "--repo-root", str(other_repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert candidate.exists(), (
            f"Worktree owned by another repo MUST survive\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "WARN" in r.stderr
        # And the rightful owner's bookkeeping is still intact.
        list_owner = subprocess.run(
            ["git", "-C", str(repo_owner), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, env=_base_env(),
        )
        assert str(candidate) in list_owner.stdout

    def test_task_complete_dream_namespace_env_works(self, tmp_path):
        """DREAM_NAMESPACE env satisfies the --namespace requirement."""
        base = tmp_path / "wt-base"
        candidate, repo = self._real_registered_worktree(base, ns="env-ns", name="dream-x")
        r = _run(
            ["--task-complete", "--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={
                "DREAM_WORKTREE_BASE": str(base),
                "DREAM_NAMESPACE": "env-ns",
            },
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert not candidate.exists()

    def test_task_complete_does_not_auto_derive_from_repo_root_basename(self, tmp_path):
        """`--task-complete --repo-root <repo>` alone must NOT derive ns from basename.

        REPO_ROOT can itself be inferred from cwd, so deriving namespace
        from `basename "$REPO_ROOT"` could silently sweep the wrong
        namespace. The script must require an explicit --namespace or
        DREAM_NAMESPACE env.
        """
        base = tmp_path / "wt-base"
        repo = tmp_path / "some-repo"
        _make_repo(repo)
        r = _run(
            ["--task-complete", "--repo-root", str(repo), "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 2, (
            f"Expected refusal (no --namespace, no DREAM_NAMESPACE)\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )
        assert "requires --namespace" in r.stderr

    def test_task_complete_min_age_min_0_catches_fresh_worktree(self, tmp_path):
        """Regression: `--min-age-min 0` MUST sweep a worktree just created.

        Previously the find walk passed `-mmin "+$MIN_AGE_MIN"` unconditionally.
        Bash arithmetic note: `find -mmin +0` matches files modified MORE than
        0 minutes ago — i.e. it EXCLUDES anything touched in the last ~1 min.
        End-of-session cleanup (`--min-age-min 0` per SKILL.md:1102-1115) is
        specifically intended to catch the freshly-pushed final batch, so the
        prior implementation silently kept the very leak it was supposed to
        catch. Reviewers GPT-5.5 and Claude Opus 4.8 both flagged this.

        This test creates a registered worktree with a FRESH mtime (no
        backdating via os.utime), then invokes --task-complete --min-age-min 0
        and asserts the dir is gone.
        """
        base = tmp_path / "wt-base"
        # old=False ⇒ mtime is "now", so any positive -mmin gate would skip it.
        candidate, repo = self._real_registered_worktree(
            base, ns="proj", name="dream-fresh", old=False,
        )
        assert candidate.exists()
        r = _run(
            ["--task-complete", "--namespace", "proj", "--repo-root", str(repo),
             "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 0, f"stderr: {r.stderr}"
        assert not candidate.exists(), (
            "Fresh registered worktree MUST be swept with --min-age-min 0 — "
            "otherwise end-of-session cleanup silently misses the final batch.\n"
            f"stdout: {r.stdout}\nstderr: {r.stderr}"
        )

    def test_task_complete_refuses_bare_dot_namespace(self, tmp_path):
        """Defense-in-depth: `--namespace .` is rejected at input validation.

        The depth-1 + dream-* filename filter + _worktree_safety.py gate
        already prevent damage, but tightening the regex to require a
        non-`.` first char makes the contract explicit (Opus 4.7-xhigh,
        Opus 4.8, Gemini all flagged the loose regex as a nit).
        """
        base = tmp_path / "wt-base"
        (base / "proj").mkdir(parents=True)
        r = _run(
            ["--task-complete", "--namespace", ".", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 2
        assert "namespace must match" in r.stderr.lower()

    def test_task_complete_refuses_double_dot_namespace(self, tmp_path):
        """`--namespace ..` must be rejected — it would otherwise resolve the
        walk root to `<base>/..` (the parent of the base)."""
        base = tmp_path / "wt-base"
        base.mkdir(parents=True)
        r = _run(
            ["--task-complete", "--namespace", "..", "--min-age-min", "0"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 2
        assert "namespace must match" in r.stderr.lower()

    def test_task_complete_rejects_non_integer_min_age_min(self, tmp_path):
        """`--min-age-min` must be a non-negative integer.

        Previously this flowed straight to `find -mmin "+$MIN_AGE_MIN"` and
        relied on find's error message. With the new `if [[ "$MIN_AGE_MIN"
        -gt 0 ]]` branching for the -mmin +0 fix, an unvalidated string
        would also break bash arithmetic.
        """
        base = tmp_path / "wt-base"
        (base / "proj").mkdir(parents=True)
        r = _run(
            ["--task-complete", "--namespace", "proj", "--min-age-min", "abc"],
            env_extra={"DREAM_WORKTREE_BASE": str(base)},
        )
        assert r.returncode == 2
        assert "min-age-min" in r.stderr.lower()
