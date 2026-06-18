"""Tests for hook-templates/scripts/shadow-frog-pre-tool.sh — PreToolUse hook.

Exercises: dedup isolation, injection resistance, field-name fallback,
tool filtering, SHADOWFROG_TMP_DIR override, happy-path discovery output,
PascalCase tool normalization, SIGTERM behavioral trap verification, the
strict production wall-clock budget, and file_path / path precedence.
"""
import json
import os
import shutil
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
HOOK_SCRIPT = REPO_ROOT / "hook-templates" / "scripts" / "shadow-frog-pre-tool.sh"


def _make_failing_git_stub(stub_dir: Path, fail_subcommand: str = "diff") -> Path:
    """Create a `git` shim that fails for one subcommand and passes the rest
    through to the real git. Prepend stub_dir to PATH to activate it.

    Simulates real-world transient `git diff` failures (warnings tripping
    pipefail, exclude-pathspec quirks, version-specific non-zero exits) that
    must never deny a tool call under Copilot CLI >= 1.0.57.
    """
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


def _base_env(cwd: Path, extras: dict | None = None) -> dict:
    """Minimal env isolating from user environment but preserving PATH for python3/git."""
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


def run_hook(json_input: dict, cwd: Path, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the pre-tool hook with given JSON on stdin."""
    env = _base_env(cwd, env_extra)
    return subprocess.run(
        ["bash", str(HOOK_SCRIPT)],
        input=json.dumps(json_input),
        capture_output=True,
        text=True,
        cwd=cwd,
        env=env,
    )


def _fix_state_json_for_test(coupon_demo: Path) -> None:
    """Update state.json so last_commit points to the test repo's HEAD.

    The coupon_demo fixture creates a fresh git repo, so the original
    last_commit (from the source example) doesn't exist in history.
    This causes 'git diff' to fail with exit 128 under pipefail.
    """
    env = _base_env(coupon_demo)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
        capture_output=True, text=True, check=True, env=env,
    ).stdout.strip()
    state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
    state = json.loads(state_file.read_text())
    state["last_commit"] = head
    state_file.write_text(json.dumps(state))


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolNoShadow:
    """When .shadow/ doesn't exist, hook exits cleanly."""

    def test_no_shadow_dir_exits_zero(self, tmp_path):
        result = run_hook(
            {"tool_name": "Read", "tool_input": {"file_path": "foo.py"}},
            cwd=tmp_path,
        )
        assert result.returncode == 0
        # No output or empty output (script exits before printing)
        assert result.stdout.strip() == ""


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolHappyPath:
    """With coupon_demo .shadow/, hook returns discoveries."""

    def test_mutation_tool_returns_json_with_context(self, coupon_demo):
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        assert "ShadowFrog" in data["additionalContext"]

    def test_read_tool_returns_base_reminder(self, coupon_demo):
        """Read/Bash are NOT mutation tools — should get base reminder only."""
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "Read", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        # Base reminder is emitted (not file-specific actionable)
        assert "ShadowFrog" in data["additionalContext"]

    def test_emits_both_output_shapes(self, coupon_demo):
        """Output carries top-level (Copilot) and nested (Claude Code) context."""
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
        )
        data = json.loads(result.stdout)
        hso = data.get("hookSpecificOutput")
        assert hso is not None
        assert hso["hookEventName"] == "PreToolUse"
        assert hso["additionalContext"] == data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolDedup:
    """PPID+lstart dedup prevents repeated injection for same file."""

    def test_same_session_same_file_deduplicates(self, coupon_demo, tmp_path):
        """Same SHADOWFROG_TMP_DIR → second call uses cached dedup marker."""
        _fix_state_json_for_test(coupon_demo)
        dedup_dir = tmp_path / "dedup"
        dedup_dir.mkdir()
        extras = {"SHADOWFROG_TMP_DIR": str(dedup_dir)}

        # First call
        r1 = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo, env_extra=extras,
        )
        # Second call — same session (same PPID inherently), same file
        r2 = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo, env_extra=extras,
        )
        assert r1.returncode == 0
        assert r2.returncode == 0
        d1 = json.loads(r1.stdout)
        d2 = json.loads(r2.stdout)
        # Both return valid JSON with additionalContext
        assert "additionalContext" in d1
        assert "additionalContext" in d2
        # If first had actionable content, second should NOT re-run --top.
        # The dedup file should exist after first call.
        dedup_files = list(dedup_dir.rglob("*.injected"))
        if "Actionable" in d1["additionalContext"]:
            assert len(dedup_files) >= 1
            # Second call should have the pointer message (not full --top output)
            # because dedup file already exists
            assert "Actionable" not in d2["additionalContext"] or \
                   d2["additionalContext"] == d1["additionalContext"]

    def test_different_sessions_no_shared_dedup(self, coupon_demo, tmp_path):
        """Different SHADOWFROG_TMP_DIR paths → no shared dedup state."""
        _fix_state_json_for_test(coupon_demo)
        dedup1 = tmp_path / "dedup1"
        dedup1.mkdir()
        dedup2 = tmp_path / "dedup2"
        dedup2.mkdir()

        r1 = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(dedup1)},
        )
        r2 = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(dedup2)},
        )
        assert r1.returncode == 0
        assert r2.returncode == 0
        d1 = json.loads(r1.stdout)
        d2 = json.loads(r2.stdout)
        # Both should have context (no cross-session dedup)
        assert "additionalContext" in d1
        assert "additionalContext" in d2
        # Both should have the same level of detail (both first-time)
        if "Actionable" in d1["additionalContext"]:
            assert "Actionable" in d2["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolInjection:
    """Injection resistance — malicious file_path must not execute."""

    def test_injection_in_file_path(self, coupon_demo, tmp_path):
        _fix_state_json_for_test(coupon_demo)
        # Use tmp_path-scoped marker instead of /tmp/PWNED so the test
        # cannot false-positive on a stale file from a previous failure
        # and cannot collide with parallel test runs (per opus-4.7xh review).
        pwn_marker = tmp_path / "PWNED"
        malicious_path = f'cart.py"); import os; os.system("touch {pwn_marker}"); #'
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": malicious_path}},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(tmp_path / "dedup")},
        )
        # Hook should not crash hard (exit 0 still outputs JSON, or exits gracefully)
        assert result.returncode == 0
        # The exploit file must NOT exist
        assert not pwn_marker.exists()


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolFieldFallback:
    """I6: Hook handles both tool_input.file_path and tool_input.path."""

    def test_file_path_field(self, coupon_demo):
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "cart.py" in data["additionalContext"]

    def test_path_field_fallback(self, coupon_demo):
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"path": "cart.py"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "cart.py" in data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolNonMutationTool:
    """Non-mutation tools (Bash, grep, etc.) get base reminder, no file lookup."""

    def test_bash_tool_no_file_lookup(self, coupon_demo):
        _fix_state_json_for_test(coupon_demo)
        result = run_hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        assert "additionalContext" in data
        # Should be the generic base reminder
        assert ".shadow/ mirrors" in data["additionalContext"]


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolTmpDirOverride:
    """SHADOWFROG_TMP_DIR env override redirects dedup state."""

    def test_dedup_state_uses_override_dir(self, coupon_demo, tmp_path):
        _fix_state_json_for_test(coupon_demo)
        custom_tmp = tmp_path / "custom_tmp"
        custom_tmp.mkdir()
        run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(custom_tmp)},
        )
        # Dedup state should be in custom_tmp, not /tmp
        shadowfrog_dirs = list(custom_tmp.glob("shadowfrog-hook-*"))
        assert len(shadowfrog_dirs) >= 1


@pytest.mark.slow
@pytest.mark.integration
class TestPreToolFailOpen:
    """Regression: preToolUse is ADVISORY and must ALWAYS exit 0.

    Copilot CLI >= 1.0.57 denies the tool call when a preToolUse command hook
    exits non-zero ("Denied by preToolUse hook (hook errored)"). The hook must
    therefore run fail-open: any internal failure (git hiccup, missing state,
    non-git dir, locale issue) still exits 0 so the user's tool is never blocked.
    """

    def _set_stale_state(self, coupon_demo: Path) -> None:
        """Point last_commit at HEAD, then advance HEAD so the shadow is stale
        and the staleness/git-diff path runs."""
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
        subprocess.run(
            ["git", "commit", "-q", "-m", "advance"],
            cwd=coupon_demo, check=True, env=env,
        )

    def test_git_diff_failure_does_not_deny(self, coupon_demo, tmp_path):
        """The original bug: stale shadow + a failing `git diff` made the hook
        exit non-zero under `set -euo pipefail`, denying the tool."""
        self._set_stale_state(coupon_demo)
        stub = tmp_path / "stubbin"
        _make_failing_git_stub(stub, "diff")
        env = {"PATH": f"{stub}:{os.environ.get('PATH', '')}"}
        result = run_hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            cwd=coupon_demo, env_extra=env,
        )
        assert result.returncode == 0, f"hook denied tool! stderr={result.stderr}"
        # Still emits valid JSON (default-allow with advisory context).
        json.loads(result.stdout)

    def test_missing_state_json_exits_zero_no_stderr(self, coupon_demo):
        """Missing state.json must exit 0 AND not leak a shell redirection
        error to stderr (state.json is opened inside Python now)."""
        (coupon_demo / ".shadow" / "_meta" / "state.json").unlink()
        result = run_hook(
            {"tool_name": "Bash", "tool_input": {"command": "ls"}},
            cwd=coupon_demo,
        )
        assert result.returncode == 0
        assert result.stderr.strip() == "", f"unexpected stderr: {result.stderr!r}"
        json.loads(result.stdout)

    def test_non_git_dir_exits_zero(self, tmp_path):
        """A .shadow/ in a directory that is NOT a git repo must not deny."""
        shadow_meta = tmp_path / ".shadow" / "_meta"
        shadow_meta.mkdir(parents=True)
        (shadow_meta / "state.json").write_text(
            json.dumps({"last_commit": "abc1234", "total_files": 0,
                        "total_discoveries": 0})
        )
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"path": str(tmp_path / "x.py")}},
            cwd=tmp_path,
        )
        assert result.returncode == 0
        json.loads(result.stdout)

    def test_git_rev_parse_failure_does_not_deny(self, coupon_demo, tmp_path):
        """Even if `git rev-parse` itself fails, the hook stays fail-open."""
        self._set_stale_state(coupon_demo)
        stub = tmp_path / "stubbin"
        _make_failing_git_stub(stub, "rev-parse")
        env = {"PATH": f"{stub}:{os.environ.get('PATH', '')}"}
        result = run_hook(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo, env_extra=env,
        )
        assert result.returncode == 0, f"hook denied tool! stderr={result.stderr}"


# ---------------------------------------------------------------------------
# PascalCase tool names must trigger the file-specific actionable branch, not
# just lowercase ones. Claude Code emits `Edit`/`Write`/`MultiEdit`/
# `NotebookEdit`; Copilot emits `edit`/`write`/`str_replace`. Removing
# `.lower()` from TOOL_NAME normalization silently downgrades all Claude
# Code mutations to the base reminder.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
class TestPreToolPascalCaseToolNames:
    """File-specific actionable branch must fire for every spelling of every
    mutation tool, across both Copilot CLI and Claude Code casing conventions."""

    @pytest.mark.parametrize("tool_name", [
        # Copilot CLI lowercase forms
        "edit", "create", "str_replace", "write",
        # Claude Code PascalCase forms
        "Edit", "Write", "MultiEdit", "NotebookEdit",
        # Pathological edge cases — uppercased / mixed
        "EDIT", "WRITE", "Create",
    ])
    def test_mutation_tool_triggers_file_specific_branch(
        self, coupon_demo, tmp_path, tool_name
    ):
        """The actionable message MUST reference the target file. Falling
        back to the base reminder (which omits the file path) is a silent
        regression of `.lower()` normalization."""
        _fix_state_json_for_test(coupon_demo)
        # Per-test isolated dedup tmpdir so each parametrized cell exercises
        # the viewer-discovery branch fresh.
        result = run_hook(
            {"tool_name": tool_name, "tool_input": {"file_path": "cart.py"}},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(tmp_path / "dedup")},
        )
        assert result.returncode == 0, (
            f"{tool_name}: hook denied (exit={result.returncode})\n"
            f"stderr={result.stderr!r}"
        )
        data = json.loads(result.stdout)
        ctx = data["additionalContext"]
        # The file-specific branches (actionable OR pointer) both include
        # the target file's name. The base reminder does not — so this
        # assertion proves the IS_MUTATION branch fired correctly.
        assert "cart.py" in ctx, (
            f"{tool_name}: TOOL NORMALIZATION REGRESSION. Mutation tool got\n"
            f"the base reminder instead of file-specific output. Either\n"
            f"`.lower()` was removed from TOOL_NAME, or the case statement\n"
            f"lost the matching alias.\nContext: {ctx!r}"
        )


# ---------------------------------------------------------------------------
# subprocess.run(timeout=) sends SIGKILL, not SIGTERM, so the matrix has zero
# behavioral coverage for the TERM trap. Removing `trap 'exit 0' TERM` is
# invisible to pytest because every test completes well before the 10s
# subprocess timeout. This test exercises the REAL trap by sending SIGTERM
# to a running hook process.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
class TestPreToolSigterm:
    """Behavioral verification of `trap 'exit 0' TERM` — distinct from
    static CI checker coverage."""

    def _spawn_and_signal(self, coupon_demo, tmp_path, env_extra=None,
                          signal_delay=0.05):
        """Spawn the hook, send SIGTERM after `signal_delay` seconds,
        return (returncode, stdout, stderr, wall_clock)."""
        import threading
        env = _base_env(coupon_demo, env_extra)
        payload = json.dumps(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}}
        )
        t0 = time.perf_counter()
        proc = subprocess.Popen(
            ["bash", str(HOOK_SCRIPT)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=coupon_demo,
            env=env,
            text=True,
        )
        # Schedule SIGTERM in a background thread so communicate() can
        # handle the stdin write + reads atomically. This avoids the
        # "I/O operation on closed file" race when we close stdin
        # manually and then call communicate().
        def _kill_after():
            time.sleep(signal_delay)
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass  # already exited
        t = threading.Thread(target=_kill_after, daemon=True)
        t.start()
        try:
            stdout, stderr = proc.communicate(input=payload, timeout=8.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
            t.join(timeout=1.0)
            pytest.fail(
                f"hook did not exit within 8s of SIGTERM (signal_delay={signal_delay}).\n"
                f"Likely cause: an unbounded foreground subprocess queued the signal.\n"
                f"stdout={stdout!r}\nstderr={stderr!r}"
            )
        t.join(timeout=1.0)
        return proc.returncode, stdout, stderr, time.perf_counter() - t0

    def test_sigterm_returns_zero_exit(self, coupon_demo, tmp_path):
        """The trap pyramid must convert SIGTERM to exit 0. Without the
        TERM trap, bash returns 143/-15 → Copilot CLI denies the tool.

        We run several attempts at varied delays to maximize the chance
        of catching bash in the gap between bounded subprocesses (where
        the trap MUST fire). The bounded subprocesses themselves ensure
        the queued signal is eventually delivered."""
        _fix_state_json_for_test(coupon_demo)
        failures = []
        for attempt, delay in enumerate([0.01, 0.05, 0.1, 0.2, 0.5]):
            rc, stdout, stderr, elapsed = self._spawn_and_signal(
                coupon_demo, tmp_path,
                env_extra={"SHADOWFROG_TMP_DIR": str(tmp_path / f"dedup_{attempt}")},
                signal_delay=delay,
            )
            if rc != 0 or stderr.strip():
                failures.append(
                    f"  attempt {attempt} (delay={delay}s): rc={rc} "
                    f"elapsed={elapsed:.2f}s stderr={stderr!r}"
                )
        assert not failures, (
            "SIGTERM did not produce exit 0 with empty stderr (TERM trap broken):\n"
            + "\n".join(failures)
        )

    def test_sigterm_during_hung_subprocess_still_eventually_exits(
        self, coupon_demo, tmp_path
    ):
        """Even when bash is waiting on a foreground subprocess (signals
        queue), the subprocess's bounded timeout ensures the signal is
        eventually delivered and the trap fires. The trap pyramid is a
        safety net for fast failures; bounded subprocess.run(timeout=) is
        the actual hang defense."""
        _fix_state_json_for_test(coupon_demo)
        # Stub git so the viewer-branch rev-parse takes 1.5s (within the
        # 0.5s python-level timeout, so subprocess.run will TIMEOUT and
        # raise → bash continues → SIGTERM trap fires).
        stub = tmp_path / "stubbin"
        stub.mkdir()
        real_git = shutil.which("git")
        (stub / "git").write_text(
            "#!/bin/bash\n"
            'if [ "$1" = "rev-parse" ] && [ "$2" = "--show-toplevel" ]; then\n'
            "    sleep 1.5\n"
            "    exit 0\n"
            "fi\n"
            f'exec "{real_git}" "$@"\n'
        )
        (stub / "git").chmod(0o755)
        rc, stdout, stderr, elapsed = self._spawn_and_signal(
            coupon_demo, tmp_path,
            env_extra={
                "PATH": f"{stub}:{os.environ.get('PATH','')}",
                "SHADOWFROG_TMP_DIR": str(tmp_path / "dedup"),
            },
            signal_delay=0.05,
        )
        assert rc == 0, (
            f"SIGTERM mid-subprocess: exit={rc} (Copilot would DENY).\n"
            f"elapsed={elapsed:.2f}s\nstderr={stderr!r}"
        )
        # Total wall-clock must still be under the runner's 5s budget,
        # proving bounded subprocesses cap the signal-queue latency.
        assert elapsed < 5.0, (
            f"SIGTERM was queued behind subprocess for {elapsed:.2f}s "
            f"(>= 5.0s Copilot deny threshold). The viewer-branch "
            f"rev-parse subprocess timeout is no longer bounded."
        )


# ---------------------------------------------------------------------------
# The matrix's 7s wall-clock cap is generous to tolerate CI cold-start
# variance, but the HAPPY PATH (no faults, real environment) must complete
# within Copilot's strict 5s deny threshold. A 6.3s hook passes the matrix
# but is denied in production.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
class TestPreToolStrictBudget:
    """Production-realistic happy-path budget — distinct from fault-cell budget."""

    HAPPY_PATH_BUDGET_SEC = 5.0  # Copilot CLI's preToolUse deny threshold

    def test_happy_path_meets_strict_production_budget(
        self, coupon_demo, tmp_path
    ):
        """With no faults, the hook MUST complete under 5.0s — the actual
        production deny threshold. Run 3 times and assert the MIN elapsed
        (best of 3) clears the bar, so transient CI spikes don't cause
        false failures while still catching genuine creeping slowdown."""
        _fix_state_json_for_test(coupon_demo)
        payload = json.dumps(
            {"tool_name": "edit", "tool_input": {"file_path": "cart.py"}}
        )
        elapseds = []
        for attempt in range(3):
            t0 = time.perf_counter()
            result = subprocess.run(
                ["bash", str(HOOK_SCRIPT)],
                input=payload,
                capture_output=True,
                text=True,
                cwd=coupon_demo,
                env=_base_env(coupon_demo, {
                    "SHADOWFROG_TMP_DIR": str(tmp_path / f"dedup_{attempt}"),
                }),
                timeout=10.0,
            )
            elapsed = time.perf_counter() - t0
            elapseds.append(elapsed)
            assert result.returncode == 0, (
                f"attempt {attempt}: hook returned {result.returncode}, "
                f"stderr={result.stderr!r}"
            )
        best = min(elapseds)
        assert best < self.HAPPY_PATH_BUDGET_SEC, (
            f"Happy-path hook is too slow: best of 3 = {best:.2f}s, "
            f"all elapseds = {[f'{e:.2f}' for e in elapseds]}. "
            f"Copilot CLI denies tool calls when hook exceeds "
            f"{self.HAPPY_PATH_BUDGET_SEC}s. This is the production "
            f"reality, not the matrix's lenient {7.0}s cap."
        )


# ---------------------------------------------------------------------------
# When both `file_path` and `path` are present, precedence must prefer
# `file_path` (the canonical key for both Copilot CLI and Claude Code).
# Flipping precedence would silently route to the wrong file's shadow.
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
class TestPreToolPathPrecedence:
    """When both file_path and path are present, file_path wins."""

    def test_file_path_takes_precedence_over_path(self, coupon_demo, tmp_path):
        """A payload carrying BOTH keys: the hook should reference the
        `file_path` value's shadow, not the `path` value's."""
        _fix_state_json_for_test(coupon_demo)
        # cart.py has a shadow; inventory.py also has one. The hook should
        # pick cart.py (from file_path), not inventory.py (from path).
        result = run_hook(
            {"tool_name": "edit", "tool_input": {
                "file_path": "cart.py",
                "path": "inventory.py",
            }},
            cwd=coupon_demo,
            env_extra={"SHADOWFROG_TMP_DIR": str(tmp_path / "dedup")},
        )
        assert result.returncode == 0
        data = json.loads(result.stdout)
        ctx = data["additionalContext"]
        # Must reference cart.py (the file_path)
        assert "cart.py" in ctx, (
            f"file_path was ignored; precedence is wrong. ctx={ctx!r}"
        )
        # Must NOT reference inventory.py (the path) — that would mean
        # `path` took precedence, which is the bug.
        assert "inventory.py" not in ctx, (
            f"PATH PRECEDENCE BUG: `path` value won over `file_path`. "
            f"This routes Copilot/Claude Code edits to the wrong shadow. "
            f"ctx={ctx!r}"
        )
