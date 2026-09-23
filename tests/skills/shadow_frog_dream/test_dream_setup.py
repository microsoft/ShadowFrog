"""Tests for skills/shadow-frog-dream/dream-setup.py — Dream worktree setup.

Exercises: --help, happy-path worktree+branch creation, RUN_PREFIX detection,
namespace override, slug validation, the .shadow/ gitignore guard, dry-run,
and the throttled best-effort auto-GC.

Cross-platform: invokes the Python entry point directly (no bash). The two
auto-GC tests that assert an orphan is actually *swept* still need the bash
`dream-gc.sh`, so they are skipped on Windows until `dream-gc.py` exists;
every other test runs on all OSes.
"""
import json
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest
from tests._shell import BASH, HAVE_BASH, shell_path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DREAM_SETUP = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-setup.py"
DREAM_RECONCILE = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-reconcile.py"

# Cleared from the base env so a developer's shell can't leak into namespace /
# worktree resolution; each test sets exactly what it needs via `extras`.
_DREAM_ENV_KEYS = (
    "DREAM_NAMESPACE", "DREAM_WORKTREE_BASE", "DREAM_GC_AUTO",
    "DREAM_GC_INTERVAL_MIN", "DREAM_GC_AGE_MIN",
)


def _base_env(cwd: Path, extras: dict | None = None) -> dict:
    """Isolated env for the setup subprocess. Starts from the real environment
    (Python + git need SystemRoot/PATH/TEMP on Windows), then pins git config to
    devnull and drops any inherited DREAM_* vars."""
    env = os.environ.copy()
    for k in _DREAM_ENV_KEYS:
        env.pop(k, None)
    env["HOME"] = str(cwd)
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_SYSTEM"] = os.devnull
    env["LANG"] = "en_US.UTF-8"
    if extras:
        env.update(extras)
    return env


def _make_git_repo(path: Path, branch: str = "main") -> None:
    """Create a git repo with an initial commit and an origin/main ref."""
    env = _base_env(path)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "test@test.invalid"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=path, check=True, env=env)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=path, check=True, env=env)
    (path / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True, env=env)
    # Fake origin remote pointing at self, for the origin/main ref.
    subprocess.run(["git", "remote", "add", "origin", str(path)], cwd=path, check=True, env=env)
    subprocess.run(["git", "fetch", "-q", "origin"], cwd=path, check=True, env=env)


def run_dream_setup(
    args: list[str], cwd: Path, env_extra: dict | None = None
) -> subprocess.CompletedProcess:
    """Run dream-setup.py with the given args via the current interpreter."""
    env = _base_env(cwd, env_extra)
    return subprocess.run(
        [sys.executable, str(DREAM_SETUP), *args],
        capture_output=True, text=True, cwd=cwd, env=env, encoding="utf-8",
    )


def _load_dream_setup_module():
    spec = importlib.util.spec_from_file_location("dream_setup", DREAM_SETUP)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _load_dream_reconcile_module():
    spec = importlib.util.spec_from_file_location("dream_reconcile", DREAM_RECONCILE)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _plant_orphan(base: Path, ns: str, name: str = "dream-orphan") -> Path:
    """Plant an orphan worktree (broken .git pointer, ancient mtime)."""
    d = base / ns / name
    d.mkdir(parents=True)
    (d / ".git").write_text("gitdir: /nonexistent/path\n", encoding="utf-8")
    (d / "leftover.txt").write_text("orphaned\n", encoding="utf-8")
    ancient = 946684800
    os.utime(d, (ancient, ancient))
    return d


@pytest.mark.skipif(not HAVE_BASH, reason="requires a POSIX shell")
def test_documented_json_bridge_preserves_tabs_and_newlines(tmp_path):
    """The NUL-delimited bridge must not corrupt valid POSIX path characters."""
    worktree_dir = (tmp_path / "tab\tand\nnewline").as_posix()
    setup_json = json.dumps({
        "repo_root": "/repo",
        "default_branch": "main",
        "dream_ns": "namespace",
        "dream_id": "20260915-000000Z-bridge",
        "branch_name": "dream/namespace/20260915-000000Z-bridge",
        "parent_branch": "main",
        "worktree_dir": worktree_dir,
        "worktree_root": "/tmp/shadowfrog-dreams",
        "worktree_base": "/tmp/shadowfrog-dreams/namespace",
        "base_commit": "abc123",
        "run_prefix": "",
        "slug": "bridge",
    })
    bridge = r'''
while IFS= read -r -d '' key && IFS= read -r -d '' value; do
    case "$key" in
        REPO_ROOT|DEFAULT_BRANCH|DREAM_NS|DREAM_ID|BRANCH_NAME|PARENT_BRANCH|WORKTREE_DIR|WORKTREE_ROOT|WORKTREE_BASE|BASE_COMMIT|RUN_PREFIX|SLUG)
            printf -v "$key" '%s' "$value"
            export "$key"
            ;;
    esac
done < <(python3 -c '
import json, sys
data = json.loads(sys.argv[1])
out = sys.stdout.buffer
for env_key, json_key in (
    ("REPO_ROOT", "repo_root"), ("DEFAULT_BRANCH", "default_branch"),
    ("DREAM_NS", "dream_ns"), ("DREAM_ID", "dream_id"),
    ("BRANCH_NAME", "branch_name"), ("PARENT_BRANCH", "parent_branch"),
    ("WORKTREE_DIR", "worktree_dir"), ("WORKTREE_ROOT", "worktree_root"),
    ("WORKTREE_BASE", "worktree_base"), ("BASE_COMMIT", "base_commit"),
    ("RUN_PREFIX", "run_prefix"), ("SLUG", "slug"),
):
    out.write(env_key.encode() + b"\0" + data[json_key].encode() + b"\0")
' "$1")
printf '%s' "$WORKTREE_DIR"
'''
    result = subprocess.run(
        [BASH, "-c", bridge, "--", setup_json],
        capture_output=True, text=True, encoding="utf-8",
        env={**_base_env(tmp_path), "PATH": shell_path()},
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == worktree_dir


@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupHelp:
    def test_help_exits_zero(self, tmp_path):
        # --help should work even outside a git repo (it just prints and exits).
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

        # Worktree exists on disk.
        wt_dir = Path(data["worktree_dir"])
        assert wt_dir.is_dir()

        # And is registered with git. `git worktree list` prints '/'-paths on
        # Windows while the JSON path is OS-native; compare resolved Paths.
        env = _base_env(repo)
        wt_list = subprocess.run(
            ["git", "worktree", "list", "--porcelain"], cwd=repo,
            capture_output=True, text=True, env=env, encoding="utf-8",
        )
        registered = {
            Path(line[len("worktree "):]).resolve()
            for line in wt_list.stdout.splitlines()
            if line.startswith("worktree ")
        }
        assert wt_dir.resolve() in registered

    def test_worktree_has_same_head_as_base(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        env = _base_env(repo)

        main_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo,
            capture_output=True, text=True, check=True, env=env, encoding="utf-8",
        ).stdout.strip()

        result = run_dream_setup(
            ["--slug", "t02-head", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        assert data["base_commit"] == main_head

    def test_uses_system_temp_root_without_override(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        system_temp = tmp_path / "system-temp"
        system_temp.mkdir()

        result = run_dream_setup(
            ["--slug", "t02-default-root", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_GC_AUTO": "0",
                "TEMP": str(system_temp),
                "TMP": str(system_temp),
            },
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        expected_root = system_temp / "shadowfrog-dreams"
        assert Path(data["worktree_root"]) == expected_root
        assert Path(data["worktree_base"]) == expected_root / repo.name
        assert Path(data["worktree_dir"]) == expected_root / repo.name / "dream-t02-default-root"

    def test_relative_worktree_root_is_emitted_as_absolute(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)

        result = run_dream_setup(
            ["--slug", "t02-relative-root", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_GC_AUTO": "0",
                "DREAM_WORKTREE_BASE": "../dream-worktrees",
            },
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        data = json.loads(result.stdout)
        expected_root = (repo / ".." / "dream-worktrees").resolve()
        assert Path(data["worktree_root"]) == expected_root
        assert Path(data["worktree_dir"]) == expected_root / repo.name / "dream-t02-relative-root"

    def test_setup_context_drives_reconciler_cleanup_at_same_root(self, tmp_path):
        """Review 01: setup JSON must identify the exact worktree cleanup removes."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        setup = run_dream_setup(
            ["--slug", "t02-lifecycle", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_GC_AUTO": "0",
                "DREAM_WORKTREE_BASE": "../dream-worktrees",
            },
        )
        assert setup.returncode == 0, f"stderr: {setup.stderr}"
        context = json.loads(setup.stdout)
        worktree_dir = Path(context["worktree_dir"])
        assert worktree_dir.is_dir()

        dream_dir = repo / ".shadow" / "_dreams" / context["dream_id"]
        dream_dir.mkdir(parents=True)
        for name in ("report.md", "manifest.json", "patch.diff"):
            (dream_dir / name).write_text("{}\n", encoding="utf-8")
        index = repo / ".shadow" / "_dreams" / "_index.md"
        index.write_text(
            "# Dream Experiments\n\n"
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|----------|----------|---------|-------|--------|--------|------------|\n"
            f"| {context['dream_id']} | test | useful | Test | "
            f"{context['branch_name']} | main | deadbeef |\n",
            encoding="utf-8",
        )
        env = _base_env(repo)
        subprocess.run(["git", "add", ".shadow"], cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-qm", "reconcile"], cwd=repo, check=True, env=env,
        )
        bare_remote = repo.parent / "remote.git"
        subprocess.run(
            ["git", "init", "--bare", "-q", str(bare_remote)],
            cwd=repo.parent, check=True, env=env,
        )
        subprocess.run(
            ["git", "remote", "set-url", "origin", f"file://{bare_remote}"],
            cwd=repo, check=True, env=env,
        )
        subprocess.run(["git", "push", "-qu", "origin", "main"],
                       cwd=repo, check=True, env=env)
        subprocess.run(
            ["git", "push", "-q", "origin", context["branch_name"]],
            cwd=repo, check=True, env=env,
        )

        reconcile = _load_dream_reconcile_module()
        deleted, kept = reconcile.cleanup_branches(
            str(repo),
            [(context["branch_name"], context["dream_id"], {})],
            context["dream_ns"],
            worktree_root=context["worktree_root"],
        )

        assert (deleted, kept) == (1, 0)
        assert not worktree_dir.exists()

    def test_repo_root_subdir_is_canonicalized_for_isolation(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        subdir = repo / "subdir"
        subdir.mkdir()
        worktree_base = repo / ".dream-worktrees"

        result = run_dream_setup(
            ["--slug", "t02-subdir", "--repo-root", str(subdir),
             "--dry-run", "--print-json"],
            cwd=subdir,
            env_extra={"DREAM_WORKTREE_BASE": str(worktree_base)},
        )

        assert result.returncode != 0
        assert "Worktree would be inside project" in result.stderr


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
        (repo / "uv.lock").write_text("", encoding="utf-8")
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
        (repo / "package-lock.json").write_text("{}", encoding="utf-8")
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

    def test_quoted_dotenv_namespace_used(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / ".env").write_text(
            'DREAM_NAMESPACE="quoted-ns"\n', encoding="utf-8",
        )

        result = run_dream_setup(
            ["--slug", "t08-quoted-dotenv", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
        )

        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert json.loads(result.stdout)["dream_ns"] == "quoted-ns"

    def test_setup_namespace_is_explicitly_usable_by_reconciliation(self, tmp_path):
        """The namespace emitted by setup must select its remote dream branch."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        setup = run_dream_setup(
            ["--slug", "t08-reconcile", "--namespace", "setup-ns",
             "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={"DREAM_WORKTREE_BASE": str(tmp_path / "worktrees")},
        )
        assert setup.returncode == 0, f"stderr: {setup.stderr}"
        context = json.loads(setup.stdout)

        env = _base_env(repo)
        subprocess.run(
            ["git", "push", "-q", "origin", context["branch_name"]],
            cwd=repo, check=True, env=env,
        )
        reconcile = subprocess.run(
            [
                sys.executable, str(DREAM_RECONCILE), str(repo),
                "--namespace", context["dream_ns"], "--dry-run",
            ],
            capture_output=True, text=True, encoding="utf-8",
            cwd=repo,
            env=_base_env(repo, {"DREAM_NAMESPACE": "wrong-ns"}),
        )

        assert reconcile.returncode == 0, reconcile.stdout + reconcile.stderr
        assert context["branch_name"] in reconcile.stdout

    def test_task_info_namespace_must_be_string(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / "TASK_INFO.json").write_text(
            '{"dream_namespace": 123}\n', encoding="utf-8",
        )

        result = run_dream_setup(
            ["--slug", "t08-taskns", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
        )

        assert result.returncode != 0
        assert "dream_namespace must be a string" in result.stderr

    @pytest.mark.parametrize("task_info", ["[]", '"not an object"'])
    def test_task_info_must_be_object(self, tmp_path, task_info):
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        (repo / "TASK_INFO.json").write_text(task_info, encoding="utf-8")

        result = run_dream_setup(
            ["--slug", "t08-taskinfo-object", "--repo-root", str(repo),
             "--dry-run", "--print-json"],
            cwd=repo,
        )

        assert result.returncode != 0
        assert "TASK_INFO.json must contain a JSON object" in result.stderr


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
        (repo / ".gitignore").write_text(".shadow/\n", encoding="utf-8")
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
        (repo / ".gitignore").write_text("build/\n__pycache__/\n", encoding="utf-8")
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
        shadow_meta = repo / ".shadow" / "_meta"
        shadow_meta.mkdir(parents=True)
        (shadow_meta / "state.json").write_text("{}\n", encoding="utf-8")
        env = _base_env(repo)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "track shadow"],
                       cwd=repo, check=True, env=env)
        (repo / ".gitignore").write_text(".shadow/\n", encoding="utf-8")
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
        assert not Path(data["worktree_dir"]).exists()
        assert data["slug"] == "t09-dry"


# ===========================================================================
# Auto-GC throttle (Bug A fix from bug-cleanup-gaps.md)
#
# These four exercise dream-setup's OWN throttle/opt-out logic, which never
# invokes the GC sweeper — so they run on every OS. The two tests that assert
# an orphan is actually swept need bash `dream-gc.sh` and are skipped on
# Windows until a cross-platform `dream-gc.py` exists.
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
class TestDreamSetupAutoGCThrottle:
    """`dream-setup.py` decides *whether* to trigger the sweeper, throttled by
    a per-namespace `.last-gc` tombstone."""

    def test_auto_gc_throttled_by_recent_tombstone(self, tmp_path):
        """Fresh tombstone (< DREAM_GC_INTERVAL_MIN) suppresses the trigger."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        (worktree_base / ns).mkdir(parents=True)
        tombstone = worktree_base / ns / ".last-gc"
        tombstone.touch()

        orphan = _plant_orphan(worktree_base, ns)

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
        # Orphan must STILL exist — auto-GC was throttled (no sweeper invoked).
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

        orphan = _plant_orphan(worktree_base, ns)

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
        # Orphan must STILL exist — GC was opted out.
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
        assert Path(data["worktree_dir"]).is_dir()

    def test_auto_gc_does_not_pollute_json_stdout(self, tmp_path):
        """Auto-GC output MUST go to stderr so stdout stays pure JSON."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        # Plant an orphan so the GC actually has work to log about.
        _plant_orphan(worktree_base, ns)

        result = run_dream_setup(
            ["--slug", "t05-stdout", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        # stdout must parse as a single clean JSON object — any GC noise leaking
        # onto stdout would break json.loads.
        data = json.loads(result.stdout)
        assert data["slug"] == "t05-stdout"

    def test_auto_gc_launch_failure_does_not_touch_tombstone(self, tmp_path, monkeypatch):
        dream_setup = _load_dream_setup_module()
        script_dir = tmp_path / "skill"
        script_dir.mkdir()
        (script_dir / "dream-gc.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        worktree_base = tmp_path / "worktrees" / "repo"
        worktree_base.mkdir(parents=True)
        tombstone = worktree_base / ".last-gc"

        def fail_to_launch(*args, **kwargs):
            raise FileNotFoundError("bash")

        monkeypatch.setattr(dream_setup, "SCRIPT_DIR", str(script_dir))
        monkeypatch.setattr(dream_setup.subprocess, "run", fail_to_launch)

        dream_setup._maybe_auto_gc(str(tmp_path), str(worktree_base))

        assert not tombstone.exists()

    def test_auto_gc_nonzero_result_does_not_touch_tombstone(
        self, tmp_path, monkeypatch, capsys
    ):
        dream_setup = _load_dream_setup_module()
        script_dir = tmp_path / "skill"
        script_dir.mkdir()
        (script_dir / "dream-gc.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        worktree_base = tmp_path / "worktrees" / "repo"
        worktree_base.mkdir(parents=True)
        tombstone = worktree_base / ".last-gc"

        monkeypatch.setattr(dream_setup, "SCRIPT_DIR", str(script_dir))
        monkeypatch.setattr(dream_setup.os, "name", "posix")
        monkeypatch.setattr(
            dream_setup.subprocess,
            "run",
            lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1),
        )

        dream_setup._maybe_auto_gc(
            str(tmp_path), str(worktree_base), str(tmp_path / "worktrees")
        )

        assert not tombstone.exists()
        assert "auto-GC exited with code 1" in capsys.readouterr().err

    def test_auto_gc_does_not_launch_bash_fallback_on_windows(
        self, tmp_path, monkeypatch, capsys
    ):
        dream_setup = _load_dream_setup_module()
        script_dir = tmp_path / "skill"
        script_dir.mkdir()
        (script_dir / "dream-gc.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        worktree_base = tmp_path / "worktrees" / "repo"
        worktree_base.mkdir(parents=True)

        monkeypatch.setattr(dream_setup, "SCRIPT_DIR", str(script_dir))
        monkeypatch.setattr(dream_setup.os, "name", "nt")
        monkeypatch.setattr(
            dream_setup.subprocess,
            "run",
            lambda *args, **kwargs: pytest.fail("Bash GC must not run on Windows"),
        )

        dream_setup._maybe_auto_gc(str(tmp_path), str(worktree_base))

        assert not (worktree_base / ".last-gc").exists()
        assert "auto-GC skipped on Windows" in capsys.readouterr().err

@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.skipif(
    os.name == "nt",
    reason="dream-gc.sh sweep uses POSIX path/realpath semantics",
)
class TestDreamSetupAutoGCSweep:
    """These assert the orphan is actually *removed*, which needs the bash
    `dream-gc.sh` sweeper. Skipped on Windows until a cross-platform
    `dream-gc.py` exists."""

    def test_auto_gc_runs_when_no_tombstone(self, tmp_path):
        """First invocation sweeps orphans (no tombstone yet)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"
        ns = repo.name

        orphan = _plant_orphan(worktree_base, ns)
        assert orphan.exists()

        result = run_dream_setup(
            ["--slug", "t01-gc", "--repo-root", str(repo), "--print-json"],
            cwd=repo,
            env_extra={
                "DREAM_WORKTREE_BASE": str(worktree_base),
                # Force min-age-min=0 so the ancient orphan is in the sweep window.
                "DREAM_GC_AGE_MIN": "0",
            },
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert not orphan.exists(), (
            f"Auto-GC should have swept the orphan\nstderr: {result.stderr}"
        )
        tombstone = worktree_base / ns / ".last-gc"
        assert tombstone.exists()

    def test_auto_gc_sweeps_other_namespace_orphans_too(self, tmp_path):
        """The auto-trigger sweeps the whole base, not just its own ns."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _make_git_repo(repo)
        worktree_base = tmp_path / "worktrees"

        other_orphan = _plant_orphan(worktree_base, ns="other-repo")
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
        assert not other_orphan.exists(), (
            f"Auto-GC sweeps the whole base\nstderr: {result.stderr}"
        )
