"""Fault-injection matrix for shadow-frog-pre-tool.sh and
shadow-frog-check-init.sh.

Both hooks MUST be fail-open (exit 0, no stderr, valid JSON or empty stdout)
under every plausible perturbation, because Copilot CLI >= 1.0.57 denies the
tool call if a preToolUse command hook exits non-zero.

This file systematically perturbs four axes and asserts the contract holds:

  1. Stubbed binary    — git (fail/hang/various subcommands), python3, ps,
                         mkdir (read-only), tr
  2. Input payload     — malformed JSON, missing fields, huge, non-ASCII paths,
                         shell-metacharacter paths, deeply nested
  3. CWD state         — non-git, no commits yet, missing _meta, corrupt
                         state.json, .shadow read-only
  4. Env state         — C locale, empty PPID, missing SHADOWFROG_TMP_DIR

Each parametrized case asserts:
    result.returncode == 0
    result.stderr.strip() == ''       (no shell or tool noise leaked)
    valid JSON OR empty stdout         (Copilot treats empty as default-allow)
    elapsed wall-clock < HOOK_BUDGET_SEC  (must beat the runner's timeoutSec
                                           or Copilot SIGTERM kills us)

When this file fails, the failure name pinpoints which axis × value broke the
contract, making future regressions easy to triage.

Why the matrix tests create `.shadow/<target>.md` (per Opus-4.7/4.8 review):
the pre-tool viewer-discovery branch only executes when a shadow exists for
the file being edited. Without seeding that shadow file, the entire branch
(including the git/viewer subprocesses that were the original bug class)
goes unexercised and bugs hide in plain sight.
"""
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
PRE_TOOL_HOOK = REPO_ROOT / "hook-templates" / "scripts" / "shadow-frog-pre-tool.sh"
CHECK_INIT_HOOK = REPO_ROOT / "hook-templates" / "scripts" / "shadow-frog-check-init.sh"

# Configured hook timeout in hook-templates/shadow-frog-hooks.json. Tests must complete
# below this or production would have been killed by the runner.
HOOK_BUDGET_SEC = 5.0
# Soft wall-clock cap. A real unbounded-call regression hits the 10s subprocess
# timeout in _run (raising TimeoutExpired), so this assertion exists to catch
# *creeping* slowdowns — e.g., a new ~2s subprocess added without a timeout
# bound. The cap is intentionally generous (well above the runner's 5s
# timeoutSec) because shared CI runners under parallel pytest load routinely
# show 3-5x Python cold-start latency vs a quiet developer machine; locally
# the hook completes in ~1s, on busy CI it has been observed at ~6.3s while
# doing identical bounded work. We accept that latency variance and rely on
# (a) the 10s hard subprocess timeout for true hangs, and (b) the CI guard
# (hook-templates/check-hook-failopen.py) for static unbounded-call
# detection. The bounded-work budget itself is ~3.5s; doubling that for CI
# headroom yields the 7.0s soft cap below.
WALL_CLOCK_LIMIT_SEC = 7.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _run(hook: Path, cwd: Path, stdin: str, env_extra: dict | None = None,
         timeout: float = 10.0) -> tuple[subprocess.CompletedProcess, float]:
    """Run hook and return (completed_process, elapsed_seconds).

    Per-test dedup isolation: without this, every parametrized matrix cell
    shares the same pytest PPID AND parent start-time, which collide on
    `${TMP_ROOT}/shadowfrog-hook-<PPID>-<start>/`. The first cell to touch
    `a.py` creates `a.py.injected`; every subsequent cell finds the dedup
    file present and SKIPS the entire viewer subprocess (pre-tool.sh
    `[ ! -f "$DEDUP_FILE" ]` guard). Result: the viewer-branch git rev-parse
    + viewer process are exercised exactly ONCE per pytest run, masking
    unbounded-call regressions (reproduced: replacing the bounded
    `_git(['rev-parse',...], 0.5)` with an unbounded call passed 70 cells
    even though production would hang for 31s on a slow `git rev-parse`).

    The fix: give each test a fresh SHADOWFROG_TMP_DIR rooted under its own
    `cwd` so dedup state cannot bleed across cells. Tests that explicitly
    set their own SHADOWFROG_TMP_DIR (e.g., the `*-tmpdir` env cells) take
    precedence.
    """
    extras = dict(env_extra or {})
    if "SHADOWFROG_TMP_DIR" not in extras:
        # Use a stable subdir of cwd so multiple invocations within ONE
        # test (e.g., dedup tests) share state, but cross-test invocations
        # do not (each test gets a unique cwd from pytest's tmp_path).
        extras["SHADOWFROG_TMP_DIR"] = str(cwd / "_sf_dedup")
    t0 = time.perf_counter()
    cp = subprocess.run(
        ["bash", str(hook)],
        input=stdin, capture_output=True, text=True,
        cwd=cwd, env=_base_env(cwd, extras), timeout=timeout,
    )
    return cp, time.perf_counter() - t0


def _assert_fail_open(result: subprocess.CompletedProcess, case: str,
                      elapsed: float | None = None) -> None:
    """The contract every cell must satisfy. Empty stdout is acceptable
    (Copilot treats it as default-allow); non-empty must be valid JSON."""
    assert result.returncode == 0, (
        f"[{case}] HOOK DENIED TOOL (exit={result.returncode})\n"
        f"stderr={result.stderr!r}\nstdout={result.stdout!r}"
    )
    assert result.stderr.strip() == "", (
        f"[{case}] stderr leak (would be visible to user):\n{result.stderr}"
    )
    if elapsed is not None:
        assert elapsed < WALL_CLOCK_LIMIT_SEC, (
            f"[{case}] WALL CLOCK BUDGET EXCEEDED: {elapsed:.2f}s "
            f">= {WALL_CLOCK_LIMIT_SEC}s. Runner's timeoutSec={HOOK_BUDGET_SEC}s "
            f"would have SIGTERM-killed us → tool DENY in production."
        )
    out = result.stdout.strip()
    if out:
        try:
            data = json.loads(out)
        except json.JSONDecodeError as e:
            pytest.fail(f"[{case}] invalid JSON output: {e}\nstdout={out!r}")
        # If JSON is produced, it must carry advisory context (the hook's job)
        assert "additionalContext" in data, (
            f"[{case}] JSON missing additionalContext: {data!r}"
        )


def _make_binary_stub(stub_dir: Path, name: str, body: str) -> Path:
    """Create an executable shim at stub_dir/<name>. Prepend stub_dir to PATH
    to activate it."""
    stub_dir.mkdir(parents=True, exist_ok=True)
    shim = stub_dir / name
    shim.write_text("#!/bin/bash\n" + body)
    shim.chmod(0o755)
    return shim


def _git_passthrough_with_failure(stub_dir: Path, fail_pattern: str,
                                  exit_code: int = 1,
                                  stderr_msg: str = "warning: simulated") -> None:
    """Stub git to fail when the first arg matches `fail_pattern`
    (shell-glob), passthrough otherwise. Use 'ALL' to fail every call."""
    real = shutil.which("git")
    if fail_pattern == "ALL":
        body = f'echo "{stderr_msg}" >&2\nexit {exit_code}\n'
    else:
        body = (
            f'case "$1" in\n'
            f'    {fail_pattern}) echo "{stderr_msg}" >&2; exit {exit_code} ;;\n'
            f'esac\n'
            f'exec "{real}" "$@"\n'
        )
    _make_binary_stub(stub_dir, "git", body)


def _git_hanging_stub(stub_dir: Path, hang_pattern: str, sleep_secs: int = 30) -> None:
    """Stub git to hang for `hang_pattern` calls (simulates a locked repo /
    network filesystem). Other calls passthrough. This proves our subprocess
    timeouts catch the hang well below the hook's 5s budget."""
    real = shutil.which("git")
    body = (
        f'case "$1" in\n'
        f'    {hang_pattern}) sleep {sleep_secs}; exit 0 ;;\n'
        f'esac\n'
        f'exec "{real}" "$@"\n'
    )
    _make_binary_stub(stub_dir, "git", body)


def _init_minimal_shadow(cwd: Path, last_commit: str = "deadbeef0000",
                         state_extra: dict | None = None,
                         seed_target: str | None = "a.py") -> None:
    """Create a minimal .shadow/ layout in cwd.

    When `seed_target` is non-empty, also creates .shadow/<seed_target>.md
    with a bug-labeled discovery so the pre-tool viewer-discovery branch
    (which only runs when the per-file shadow exists) is reachable in
    binary-fault and cwd-state tests. The original matrix omitted this
    seeding, leaving the viewer branch — including the very git rev-parse
    call that was a latent unbounded-hang vector — completely uncovered.
    """
    meta = cwd / ".shadow" / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    state = {"version": 1, "last_commit": last_commit,
             "total_files": 0, "total_discoveries": 0}
    if state_extra:
        state.update(state_extra)
    (meta / "state.json").write_text(json.dumps(state))
    if seed_target:
        shadow_md = cwd / ".shadow" / f"{seed_target}.md"
        shadow_md.parent.mkdir(parents=True, exist_ok=True)
        # Use a `bug`-labeled discovery so the viewer's `--top-labels bug,security`
        # filter actually returns content (forcing the viewer subprocess to run
        # to completion under fault injection, not exit early).
        shadow_md.write_text(
            f"# {seed_target}\n\n"
            f"## `dummy_function`\n\n"
            f"- Returns None on empty input instead of raising — historical bug.\n"
            f"  _(verified, source: exploration, labels: [bug])_\n"
        )


def _make_git_repo(cwd: Path, with_commit: bool = True,
                   advance_head: bool = False) -> str:
    """Initialize a real git repo at cwd; return HEAD SHA (or '' if no commit)."""
    env = _base_env(cwd)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=cwd, check=True, env=env)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=cwd, check=True, env=env)
    subprocess.run(["git", "config", "user.name", "t"], cwd=cwd, check=True, env=env)
    if not with_commit:
        return ""
    (cwd / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=cwd, check=True, env=env)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True,
                          capture_output=True, text=True, env=env).stdout.strip()
    if advance_head:
        (cwd / "b.py").write_text("y = 2\n")
        subprocess.run(["git", "add", "-A"], cwd=cwd, check=True, env=env)
        subprocess.run(["git", "commit", "-qm", "advance"], cwd=cwd, check=True, env=env)
    return head


# ---------------------------------------------------------------------------
# Axis 1: Stubbed binary failures (preToolUse + sessionStart)
# ---------------------------------------------------------------------------

# Each tuple is (binary_name, body, scenario_id). Each test runs the hook in a
# real git repo with a stale shadow so the staleness/git path executes.
BINARY_FAULTS = [
    # git failures by subcommand
    ("git-fail-all",         lambda d: _git_passthrough_with_failure(d, "ALL")),
    ("git-fail-diff",        lambda d: _git_passthrough_with_failure(d, "diff")),
    ("git-fail-rev-parse",   lambda d: _git_passthrough_with_failure(d, "rev-parse")),
    ("git-fail-show-toplevel", lambda d: _git_passthrough_with_failure(d, "rev-parse", stderr_msg="not a repo")),
    ("git-noisy-warnings",   lambda d: _git_passthrough_with_failure(d, "diff", exit_code=128, stderr_msg="fatal: bad ref")),
    # git hangs (proves bounded timeouts work). The "rev-parse" hang exercises
    # both the staleness-check rev-parse AND — critically — the viewer-branch
    # rev-parse --show-toplevel at pre-tool.sh that was previously unbounded
    # (caught by Opus-4.7/4.8 review, reproduced as a 31s hang in production).
    # The seed_target=.shadow/a.py.md added by _init_minimal_shadow now ensures
    # the viewer branch actually executes under this fault.
    ("git-hang-diff",        lambda d: _git_hanging_stub(d, "diff", sleep_secs=30)),
    ("git-hang-rev-parse",   lambda d: _git_hanging_stub(d, "rev-parse", sleep_secs=30)),
    # missing python3 (rare but possible in stripped containers)
    ("python3-missing",      lambda d: _make_binary_stub(d, "python3", "exit 127\n")),
    # missing ps (some sandboxed containers)
    ("ps-missing",           lambda d: _make_binary_stub(d, "ps", "exit 127\n")),
    # tr complaining on locale (macOS quirk)
    ("tr-fail",              lambda d: _make_binary_stub(d, "tr", "exit 1\n")),
]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("hook", [PRE_TOOL_HOOK, CHECK_INIT_HOOK],
                         ids=["pre-tool", "check-init"])
@pytest.mark.parametrize("scenario_id,stub_factory", BINARY_FAULTS,
                         ids=[s[0] for s in BINARY_FAULTS])
def test_binary_fault_injection(tmp_path, hook, scenario_id, stub_factory):
    """For every (hook × stubbed-binary-failure) combination, the hook must
    still exit 0 with no stderr and either empty stdout or valid JSON."""
    repo = tmp_path / "repo"
    repo.mkdir()
    head = _make_git_repo(repo, with_commit=True, advance_head=True)
    # Set state.json one commit behind so the staleness path triggers
    behind = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=repo,
        capture_output=True, text=True, check=True, env=_base_env(repo),
    ).stdout.strip()
    _init_minimal_shadow(repo, last_commit=behind)
    # Regression guard: the viewer-discovery branch only fires when the
    # per-file shadow exists. If a future change ever defaults seed_target
    # to None, the entire viewer branch (git rev-parse, viewer subprocess)
    # silently goes uncovered — exactly the blind spot that hid the
    # original unbounded-git regression. Asserting
    # here makes that drift impossible to merge.
    assert (repo / ".shadow" / "a.py.md").exists(), (
        "test setup regression: _init_minimal_shadow did not seed the shadow "
        "file. The viewer-discovery branch will not execute under fault "
        "injection. Check seed_target default in _init_minimal_shadow."
    )

    stub_dir = tmp_path / "stubbin"
    stub_factory(stub_dir)
    env = {"PATH": f"{stub_dir}:{os.environ.get('PATH', '')}"}

    payload = json.dumps({"toolName": "edit",
                          "toolInput": {"file_path": "a.py"}})
    # Bound test execution at 10s — proves the hook itself stays within budget
    # even when stubs hang for 30s. _assert_fail_open additionally asserts
    # the wall clock is under WALL_CLOCK_LIMIT_SEC, which catches creeping
    # slowdowns while tolerating CI cold-start latency.
    result, elapsed = _run(hook, repo, stdin=payload, env_extra=env, timeout=10.0)
    _assert_fail_open(result, f"{hook.name} | {scenario_id}", elapsed=elapsed)


# ---------------------------------------------------------------------------
# Axis 2: Malicious / malformed input payloads (preToolUse only — check-init
# ignores stdin)
# ---------------------------------------------------------------------------

PAYLOAD_FAULTS = [
    ("empty",              ""),
    ("not-json",           "this is not json at all"),
    ("empty-object",       "{}"),
    ("null",               "null"),
    ("array-not-object",   '[1, 2, 3]'),
    ("missing-toolName",   '{"toolInput": {"file_path": "x.py"}}'),
    ("missing-toolInput",  '{"toolName": "edit"}'),
    ("wrong-type-toolInput", '{"toolName": "edit", "toolInput": "not-an-object"}'),
    ("wrong-type-toolName",  '{"toolName": 123, "toolInput": {}}'),
    ("huge-payload",       '{"toolName": "edit", "toolInput": {"file_path": "' + "A" * 100_000 + '.py"}}'),
    ("non-ascii-path",     '{"toolName": "edit", "toolInput": {"file_path": "café/日本語/файл.py"}}'),
    ("shell-meta-path",    '{"toolName": "edit", "toolInput": {"file_path": "x.py; rm -rf /; #"}}'),
    ("backtick-path",      '{"toolName": "edit", "toolInput": {"file_path": "`whoami`.py"}}'),
    ("quote-injection",    '{"toolName": "edit", "toolInput": {"file_path": "a\\"); import os; os.system(\\"touch /tmp/PWNED_FUZZ\\"); #"}}'),
    ("newline-in-path",    '{"toolName": "edit", "toolInput": {"file_path": "x\\ny.py"}}'),
    ("null-byte-payload",  '{"toolName": "edit", "toolInput": {"file_path": "x\\u0000y.py"}}'),
    ("deeply-nested",      json.dumps({"toolName": "edit", "toolInput": {"path": "x.py", "deep": {"a": {"b": {"c": {"d": {"e": {"f": "g"}}}}}}}})),
    ("unknown-tool",       '{"toolName": "frobnicate", "toolInput": {"file_path": "x.py"}}'),
    ("snake-case-fields",  '{"tool_name": "edit", "tool_input": {"file_path": "x.py"}}'),
    ("toolArgs-fallback",  '{"toolName": "edit", "toolArgs": {"path": "x.py"}}'),
]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("scenario_id,payload", PAYLOAD_FAULTS,
                         ids=[s[0] for s in PAYLOAD_FAULTS])
def test_pretool_payload_fault_injection(coupon_demo, scenario_id, payload,
                                         tmp_path):
    """preToolUse hook must survive any plausible / malicious JSON payload
    without denying the tool or executing the payload."""
    # Align state.json with HEAD so the git/staleness path runs cleanly
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
        capture_output=True, text=True, check=True, env=_base_env(coupon_demo),
    ).stdout.strip()
    state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
    state = json.loads(state_file.read_text())
    state["last_commit"] = head
    state_file.write_text(json.dumps(state))

    # Inject a test-scoped sentinel path. Replace the hardcoded /tmp/PWNED_FUZZ
    # marker (which made tests flaky across runs and could false-positive when
    # an unrelated leftover existed) with a tmp_path-scoped file.
    pwn_marker = tmp_path / "PWNED_FUZZ"
    scoped_payload = payload.replace("/tmp/PWNED_FUZZ", str(pwn_marker))
    result, elapsed = _run(PRE_TOOL_HOOK, coupon_demo, stdin=scoped_payload,
                           timeout=10.0)
    _assert_fail_open(result, f"pre-tool | payload={scenario_id}",
                      elapsed=elapsed)
    # Confirm no injection payload was executed
    assert not pwn_marker.exists(), \
        f"[{scenario_id}] payload was EXECUTED — RCE!"


# ---------------------------------------------------------------------------
# Axis 3: Filesystem / CWD state perturbations
# ---------------------------------------------------------------------------

CWD_FAULTS = [
    "non-git-dir",
    "git-no-commits",
    "missing-meta-dir",
    "missing-state-json",
    "state-json-empty",
    "state-json-corrupt",
    "state-json-wrong-type",
    "state-json-no-last-commit",
    "state-json-last-commit-not-a-sha",
    "shadow-read-only",
    "detached-head",
    # Pathological-but-recoverable state.json variants that the hook MUST
    # silently absorb without exiting non-zero.
    "state-json-symlink-to-devnull",
    "state-json-is-a-directory",
    "state-json-huge-file",
    "state-json-future-version",
    "state-json-null-bytes",
    "state-json-deeply-nested",
]


def _setup_cwd_state(tmp_path: Path, scenario: str) -> Path:
    """Build a cwd that exhibits `scenario`. Returns the cwd path."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()

    if scenario == "non-git-dir":
        _init_minimal_shadow(cwd)
        return cwd

    if scenario == "git-no-commits":
        _make_git_repo(cwd, with_commit=False)
        _init_minimal_shadow(cwd)
        return cwd

    _make_git_repo(cwd, with_commit=True)

    if scenario == "missing-meta-dir":
        (cwd / ".shadow").mkdir()  # exists but no _meta/
        return cwd
    if scenario == "missing-state-json":
        (cwd / ".shadow" / "_meta").mkdir(parents=True)
        return cwd
    if scenario == "state-json-empty":
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_text("")
        return cwd
    if scenario == "state-json-corrupt":
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_text("{this is not valid json")
        return cwd
    if scenario == "state-json-wrong-type":
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_text('"a string, not an object"')
        return cwd
    if scenario == "state-json-no-last-commit":
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_text('{"version": 1, "total_files": 0}')
        return cwd
    if scenario == "state-json-last-commit-not-a-sha":
        _init_minimal_shadow(cwd, last_commit="totally not a sha 🐸")
        return cwd
    if scenario == "shadow-read-only":
        # The hook only writes to SHADOWFROG_TMP_DIR (dedup markers); .shadow/
        # is read-only by design. Making state.json read-only doesn't actually
        # exercise any failure path. Instead, make the dedup tmpdir target
        # read-only — that's where a real write failure could occur (and
        # where the hook must fail-open silently).
        _init_minimal_shadow(cwd)
        readonly_tmp = cwd / "readonly_tmp"
        readonly_tmp.mkdir()
        readonly_tmp.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
        # Also keep state.json read-only as a defensive check that read-only
        # state still works (it shouldn't — the hook only reads it).
        (cwd / ".shadow" / "_meta" / "state.json").chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        return cwd
    if scenario == "detached-head":
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=cwd, check=True,
                              capture_output=True, text=True,
                              env=_base_env(cwd)).stdout.strip()
        subprocess.run(["git", "checkout", "-q", head], cwd=cwd, check=True,
                       env=_base_env(cwd))
        _init_minimal_shadow(cwd, last_commit=head)
        return cwd
    # ----- pathological state.json variants -----
    if scenario == "state-json-symlink-to-devnull":
        # Adversarial: state.json is a symlink to /dev/null. Reading it
        # returns empty → `json.load` raises → except absorbs → hook OK.
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        sj = meta / "state.json"
        try:
            sj.symlink_to("/dev/null")
        except (OSError, NotImplementedError):
            # If the platform can't make symlinks, fall back to empty file
            # (same effective failure mode).
            sj.write_text("")
        return cwd
    if scenario == "state-json-is-a-directory":
        # Pathological: state.json is a DIRECTORY, not a file. `open()`
        # raises IsADirectoryError → except absorbs.
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").mkdir()
        return cwd
    if scenario == "state-json-huge-file":
        # Defense-in-depth: an oversized state.json (~10 MB of valid JSON
        # padding). Reading it is bounded by the python subprocess timeout
        # in the staleness check.
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        big = {"version": 1, "last_commit": "deadbeef0000",
               "padding": "x" * 10_000_000}
        (meta / "state.json").write_text(json.dumps(big))
        return cwd
    if scenario == "state-json-future-version":
        # Forward-compat: version field with an unexpected schema marker.
        # Hook should ignore unknown fields and read what it can.
        _init_minimal_shadow(cwd, state_extra={"version": 9999,
                                               "future_field": {"x": 1}})
        return cwd
    if scenario == "state-json-null-bytes":
        # Corruption: file contains embedded NUL bytes. `json.load` raises;
        # except absorbs.
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_bytes(b'{"last_commit":"abc\x00\x00def"}')
        return cwd
    if scenario == "state-json-deeply-nested":
        # Pathological nesting that some JSON decoders might choke on.
        # Python's json module accepts ~1000 levels by default, so this
        # parses fine — but we want to verify the hook doesn't blow up
        # building any data structures from it.
        deep = {"version": 1, "last_commit": "abc"}
        cur = deep
        for _ in range(200):
            cur["x"] = {}
            cur = cur["x"]
        meta = cwd / ".shadow" / "_meta"; meta.mkdir(parents=True)
        (meta / "state.json").write_text(json.dumps(deep))
        return cwd

    raise ValueError(f"unknown scenario {scenario}")


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("hook", [PRE_TOOL_HOOK, CHECK_INIT_HOOK],
                         ids=["pre-tool", "check-init"])
@pytest.mark.parametrize("scenario", CWD_FAULTS)
def test_cwd_state_fault_injection(tmp_path, hook, scenario):
    """Both hooks must survive any plausible filesystem/git state."""
    cwd = _setup_cwd_state(tmp_path, scenario)
    payload = json.dumps({"toolName": "edit",
                          "toolInput": {"file_path": "a.py"}})
    result, elapsed = _run(hook, cwd, stdin=payload, timeout=10.0)
    _assert_fail_open(result, f"{hook.name} | cwd={scenario}", elapsed=elapsed)


# ---------------------------------------------------------------------------
# Axis 4: Environment perturbations
# ---------------------------------------------------------------------------

ENV_FAULTS = [
    ("C-locale",            {"LANG": "C", "LC_ALL": "C"}),
    ("posix-locale",        {"LANG": "POSIX", "LC_ALL": "POSIX"}),
    ("empty-tmpdir-override", {"SHADOWFROG_TMP_DIR": ""}),
    ("nonexistent-tmpdir",  {"SHADOWFROG_TMP_DIR": "/nonexistent/path/xyz"}),
    # Cross-platform readonly-tmpdir cell: /proc is Linux-only and was
    # silently skipped on macOS. Sentinel triggers a chmod 0500 tmp dir
    # wired up by the test body so both platforms exercise the case.
    ("readonly-tmpdir",     {"__SF_TEST_USE_READONLY_TMPDIR__": "1"}),
]


@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("hook", [PRE_TOOL_HOOK, CHECK_INIT_HOOK],
                         ids=["pre-tool", "check-init"])
@pytest.mark.parametrize("scenario,env_extra", ENV_FAULTS,
                         ids=[s[0] for s in ENV_FAULTS])
def test_env_fault_injection(coupon_demo, hook, scenario, env_extra, tmp_path):
    """Both hooks must survive constrained environments (C locale, hostile
    tmpdir overrides)."""
    # For the readonly-tmpdir cell, dynamically wire a read-only tmpdir.
    # chmod 0500 (r-x, no write) on a tmp_path subdir works on both macOS
    # and Linux — the previous /proc approach was Linux-only.
    if "__SF_TEST_USE_READONLY_TMPDIR__" in env_extra:
        ro = tmp_path / "readonly_tmp"
        ro.mkdir()
        ro.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x, no write
        env_extra = {"SHADOWFROG_TMP_DIR": str(ro)}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=coupon_demo,
        capture_output=True, text=True, check=True, env=_base_env(coupon_demo),
    ).stdout.strip()
    state_file = coupon_demo / ".shadow" / "_meta" / "state.json"
    state = json.loads(state_file.read_text())
    state["last_commit"] = head
    state_file.write_text(json.dumps(state))

    payload = json.dumps({"toolName": "edit",
                          "toolInput": {"file_path": "cart.py"}})
    result, elapsed = _run(hook, coupon_demo, stdin=payload, env_extra=env_extra,
                           timeout=10.0)
    _assert_fail_open(result, f"{hook.name} | env={scenario}", elapsed=elapsed)
