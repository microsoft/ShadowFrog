"""Tests for hook-templates/scripts/shadow-frog-check-init.sh — SessionStart hook.

Exercises: no-shadow guidance, fresh shadow status, B2 sentinel handling,
staleness detection.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_SCRIPT = REPO_ROOT / "hook-templates" / "scripts" / "shadow-frog-check-init.sh"


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


def run_hook(cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the check-init hook (stdin is ignored but must exist)."""
    env = _base_env(cwd, env_extra)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        input="{}",
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitDualOutputFormat:
    """Output carries both Copilot (top-level) and Claude Code (nested) shapes."""

    def test_no_shadow_emits_both_shapes(self, tmp_git_repo):
        result = run_hook(cwd=tmp_git_repo)
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        hso = data.get("hookSpecificOutput")
        assert hso is not None
        assert hso["hookEventName"] == "SessionStart"
        assert hso["additionalContext"] == data["additionalContext"]

    def test_fresh_shadow_emits_both_shapes(self, coupon_demo):
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = head
        state_file.write_text(json.dumps(state))

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        hso = data.get("hookSpecificOutput")
        assert hso is not None
        assert hso["hookEventName"] == "SessionStart"
        assert hso["additionalContext"] == data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitNoShadow:
    """No .shadow/ → emits init guidance."""

    def test_no_shadow_emits_init_guidance(self, tmp_git_repo):
        result = run_hook(cwd=tmp_git_repo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        assert "shadow-frog-init" in data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitFreshShadow:
    """Fresh .shadow/ with current commit → no staleness warning."""

    def test_fresh_shadow_no_staleness(self, coupon_demo):
        # Update state.json to point at current HEAD
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = head
        state_file.write_text(json.dumps(state))

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        assert "Shadow loaded" in data["additionalContext"]
        assert "WARNING" not in data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitSentinels:
    """B2: Sentinel values for last_commit don't crash the hook."""

    def test_last_commit_none_sentinel(self, coupon_demo):
        """state.json with last_commit='none' — no crash, no false staleness."""
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = "none"
        state_file.write_text(json.dumps(state))

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        # "none" is not a valid git ref, so git rev-parse --verify fails,
        # which means the staleness check is skipped entirely.
        assert "WARNING" not in data["additionalContext"]

    def test_missing_last_commit_field(self, coupon_demo):
        """state.json without last_commit key — no crash."""
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state.pop("last_commit", None)
        state_file.write_text(json.dumps(state))

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        # Default is 'none' which won't verify, so no false warning
        assert "WARNING" not in data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitStaleness:
    """Stale shadow → emits staleness warning."""

    def test_stale_shadow_emits_warning(self, coupon_demo):
        # Get the initial commit
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        # Set state to current HEAD
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = head
        state_file.write_text(json.dumps(state))

        # Make new commits with file changes to create staleness
        env = _base_env(coupon_demo)
        for i in range(3):
            (coupon_demo / f"file{i}.py").write_text(f"# file {i}\n")
        subprocess.run(["git", "add", "-A"], cwd=coupon_demo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "-m", "add files"],
            cwd=coupon_demo, check=True, env=env,
        )

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "WARNING" in data["additionalContext"]
        assert "shadow-frog-update" in data["additionalContext"]

    def test_shadow_only_commit_no_staleness(self, coupon_demo):
        """Committing only .shadow/ changes must NOT trigger a staleness
        warning — otherwise git-tracked shadows warn forever after every
        update commit."""
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = head
        state_file.write_text(json.dumps(state))

        # Advance HEAD with a commit that touches ONLY .shadow/
        env = _base_env(coupon_demo)
        (coupon_demo / ".shadow" / "newshadow.py.md").write_text("# x\n")
        subprocess.run(["git", "add", "-A"], cwd=coupon_demo, check=True, env=env)
        subprocess.run(
            ["git", "commit", "-q", "-m", "shadow only"],
            cwd=coupon_demo, check=True, env=env,
        )

        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "WARNING" not in data["additionalContext"]


def _make_failing_git_stub(stub_dir: Path, fail_subcommand: str = "diff") -> Path:
    """Create a `git` shim that fails for one subcommand, passing the rest to
    the real git. Used to prove the hook stays robust when git misbehaves."""
    import shutil
    stub_dir.mkdir(parents=True, exist_ok=True)
    real_git = shutil.which("git")
    shim = stub_dir / "git"
    shim.write_text(
        "#!/bin/bash\n"
        f'if [ "$1" = "{fail_subcommand}" ]; then\n'
        '    echo "warning: simulated git failure" >&2\n'
        "    exit 1\n"
        "fi\n"
        f'exec "{real_git}" "$@"\n'
    )
    shim.chmod(0o755)
    return shim


@pytest.mark.slow
@pytest.mark.integration
class TestCheckInitRobustness:
    """sessionStart is fail-open by contract, but must also stay quiet and
    clean (exit 0, no stderr leak) when state.json is missing or git hiccups."""

    def test_missing_state_json_exits_zero_no_stderr(self, coupon_demo):
        (coupon_demo / ".shadow" / "_meta" / "state.json").unlink()
        result = run_hook(cwd=coupon_demo)
        assert result.returncode == 0
        assert result.stderr.strip() == "", f"unexpected stderr: {result.stderr!r}"
        data = json.loads(result.stdout)
        assert "additionalContext" in data

    def test_git_diff_failure_exits_zero(self, coupon_demo, tmp_path):
        # Make the shadow stale so the staleness/git-diff path runs.
        env = _base_env(coupon_demo)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
            capture_output=True, text=True, check=True, env=env,
        ).stdout.strip()
        state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
        state = json.loads(state_file.read_text())
        state["last_commit"] = head
        state_file.write_text(json.dumps(state))
        (coupon_demo / "newcode.py").write_text("x = 1\n")
        subprocess.run(["git", "add", "-A"], cwd=coupon_demo, check=True, env=env)
        subprocess.run(["git", "commit", "-q", "-m", "advance"],
                       cwd=coupon_demo, check=True, env=env)

        stub = tmp_path / "stubbin"
        _make_failing_git_stub(stub, "diff")
        result = run_hook(
            cwd=coupon_demo,
            env_extra={"PATH": f"{stub}:{os.environ.get('PATH', '')}"},
        )
        assert result.returncode == 0, f"stderr={result.stderr}"
        json.loads(result.stdout)
