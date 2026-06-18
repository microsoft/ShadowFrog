"""Tests for hook-templates/check-hook-failopen.py — the static guard that
enforces the fail-open contract on advisory hook scripts.

Every known regression vector must be detected by this checker. These tests
are the unit-level mirror of the adversarial probe used to develop it."""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CHECKER = REPO_ROOT / "hook-templates" / "check-hook-failopen.py"


def _run(tmp_path: Path, body: str) -> tuple[int, str]:
    """Write `body` to a hook file under tmp_path and run the checker on it."""
    p = tmp_path / "hook.sh"
    p.write_text(body)
    r = subprocess.run(
        ["python3", str(CHECKER), str(p)],
        capture_output=True, text=True, timeout=10,
    )
    return r.returncode, r.stdout + r.stderr


# Good hook patterns that must PASS the checker
GOOD_CASES = [
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
INPUT=$(cat)
echo "ok"
""", id="canonical-pattern"),
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT TERM HUP INT
echo "ok"
""", id="combined-trap-form"),
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
X=$(python3 - <<'PYEOF'
import subprocess
subprocess.run(["git", "diff"], timeout=1.0)
PYEOF
)
echo "ok"
""", id="git-inside-python-heredoc-allowed"),
]


@pytest.mark.parametrize("body", GOOD_CASES)
def test_checker_passes_good_hooks(tmp_path, body):
    rc, output = _run(tmp_path, body)
    assert rc == 0, f"checker incorrectly flagged good hook:\n{output}"


# Bad hook patterns that must FAIL the checker — one for every known
# regression vector.
BAD_CASES = [
    pytest.param("set -e", "set -e",
                 id="short-form-set-e"),
    pytest.param("set -euo pipefail (original v1.0.57 bug)",
                 "set -euo pipefail", id="set-euo-pipefail-the-original-bug"),
    pytest.param("set -o errexit (long-form bypass)",
                 "set -o errexit", id="long-form-errexit"),
    pytest.param("set -o nounset (long-form bypass)",
                 "set -o nounset", id="long-form-nounset"),
    pytest.param("set -o pipefail (long-form bypass)",
                 "set -o pipefail", id="long-form-pipefail"),
    pytest.param("set -o errtrace (long-form bypass)",
                 "set -o errtrace", id="long-form-errtrace"),
    pytest.param("source ./helpers.sh", "source ./helpers.sh",
                 id="source-external-file"),
    pytest.param(". ./helpers.sh", ". ./helpers.sh",
                 id="dot-source-external-file"),
    pytest.param("unbounded git rev-parse (line-94 bug class)",
                 "REPO_ROOT=$(git rev-parse --show-toplevel)",
                 id="unbounded-git-rev-parse"),
    pytest.param("unbounded git diff",
                 "git diff HEAD~1 HEAD",
                 id="unbounded-git-diff"),
]


@pytest.mark.parametrize("description,injection", BAD_CASES)
def test_checker_flags_bad_hooks(tmp_path, description, injection):
    body = f"""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
{injection}
echo "fail"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0, (
        f"checker FAILED to flag known regression '{description}'.\n"
        f"This injection should have been detected:\n  {injection}\n"
        f"Checker output:\n{output}"
    )


def test_checker_rejects_missing_exit_trap(tmp_path):
    body = """#!/usr/bin/env bash
trap 'exit 0' TERM HUP INT
echo "missing EXIT"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0
    assert "EXIT" in output


def test_checker_rejects_missing_term_trap(tmp_path):
    body = """#!/usr/bin/env bash
trap 'exit 0' EXIT
echo "missing TERM - SIGTERM returns 143"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0
    assert "TERM" in output


def test_checker_rejects_comment_masquerading_as_trap(tmp_path):
    """A comment containing 'trap exit 0 EXIT' must not satisfy the requirement."""
    body = """#!/usr/bin/env bash
# This script does have: trap 'exit 0' EXIT TERM HUP INT
echo "comment masquerade"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0


def test_checker_runs_against_actual_hook_scripts():
    """Sanity: the current production hook scripts must satisfy the contract."""
    hooks = sorted((REPO_ROOT / "hook-templates" / "scripts").glob("*.sh"))
    assert hooks, "no hook scripts found"
    r = subprocess.run(
        ["python3", str(CHECKER), *map(str, hooks)],
        capture_output=True, text=True, timeout=10,
    )
    assert r.returncode == 0, (
        f"Production hook scripts FAIL the fail-open checker — this PR broke "
        f"the contract.\nCheckpoint:\n{r.stdout}\n{r.stderr}"
    )


# ---------------------------------------------------------------------------
# CI guard evasion + raw-python3 detection
# Each known bypass pattern gets a dedicated regression test. If a future
# regex tweak breaks one of these, the failure message says exactly which
# bypass slipped through.
# ---------------------------------------------------------------------------

# Evasion patterns that previously bypassed `FORBIDDEN_SET_RE`. Each must
# FAIL the checker (strict mode flag detected anywhere in the body).
SET_E_EVASION_CASES = [
    pytest.param("[[ True ]] && set -e",
                 id="evasion-combinator-and"),
    pytest.param("true || set -e",
                 id="evasion-combinator-or"),
    pytest.param("true; set -e",
                 id="evasion-semicolon"),
    pytest.param("set \\\n    -e",
                 id="evasion-line-continuation"),
    pytest.param("eval 'set -e'",
                 id="evasion-eval-literal"),
    pytest.param("eval \"set -o pipefail\"",
                 id="evasion-eval-long-form"),
    pytest.param("if true; then set -euo pipefail; fi",
                 id="evasion-conditional-block"),
    pytest.param("{ set -e; true; }",
                 id="evasion-group-command"),
]


@pytest.mark.parametrize("injection", SET_E_EVASION_CASES)
def test_checker_flags_set_e_evasions(tmp_path, injection):
    """These bypass the original `^\\s*set\\s+` anchor. Substring scanning +
    line-continuation joining must catch each one."""
    body = f"""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
{injection}
echo "should be flagged"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0, (
        f"checker MISSED set -e evasion:\n{injection!r}\nOutput:\n{output}"
    )


# Signal-alias cases — these must PASS the checker because `SIGTERM` and
# numeric `15` are bash-equivalent to `TERM`.
SIGNAL_ALIAS_GOOD_CASES = [
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' SIGTERM SIGHUP SIGINT
echo "SIGTERM alias for TERM"
""", id="sigterm-spelled-out"),
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' 15 1 2
echo "numeric signal codes (15=TERM, 1=HUP, 2=INT)"
""", id="numeric-signal-codes"),
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT SIGTERM
echo "combined EXIT and SIGTERM in one trap"
""", id="combined-exit-sigterm"),
]


@pytest.mark.parametrize("body", SIGNAL_ALIAS_GOOD_CASES)
def test_checker_accepts_signal_aliases(tmp_path, body):
    """`SIGTERM` and numeric `15` must be recognized as TERM-equivalent."""
    rc, output = _run(tmp_path, body)
    assert rc == 0, (
        f"checker false-rejected valid signal alias:\n{body}\nOutput:\n{output}"
    )


# Raw python3 invocation cases — these must FAIL the checker.
RAW_PYTHON_BAD_CASES = [
    pytest.param("python3 my_script.py",
                 id="raw-python3-script-file"),
    pytest.param("python3 -m mymodule",
                 id="raw-python3-module"),
    pytest.param("python3 -u long_running.py",
                 id="raw-python3-unbuffered-script"),
    pytest.param("OUTPUT=$(python3 my_script.py)",
                 id="raw-python3-script-captured"),
]


@pytest.mark.parametrize("injection", RAW_PYTHON_BAD_CASES)
def test_checker_flags_raw_python3_scripts(tmp_path, injection):
    """Bash-level `python3 path.py` is unbounded — same bug class as
    `git rev-parse` hang. Must be detected."""
    body = f"""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
{injection}
echo "should be flagged"
"""
    rc, output = _run(tmp_path, body)
    assert rc != 0, (
        f"checker MISSED raw python3 script invocation:\n{injection!r}\n"
        f"Output:\n{output}"
    )


# Raw python3 SAFE forms — these must PASS (no false-positives).
RAW_PYTHON_OK_CASES = [
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
TOOL=$(python3 -c "import json,sys; print('ok')" 2>/dev/null || echo "")
echo "short -c literal is allowed"
""", id="python3-dash-c-short-literal"),
    pytest.param("""#!/usr/bin/env bash
trap 'exit 0' EXIT
trap 'exit 0' TERM HUP INT
OUT=$(python3 - <<'PYEOF' 2>/dev/null || echo ""
import subprocess
subprocess.run(["echo","hello"], timeout=1.0)
PYEOF
)
echo "heredoc with bounded internal logic is allowed"
""", id="python3-stdin-heredoc"),
]


@pytest.mark.parametrize("body", RAW_PYTHON_OK_CASES)
def test_checker_accepts_safe_python3_forms(tmp_path, body):
    """`python3 -c` short literals and `python3 - <<HEREDOC` patterns must
    NOT be flagged."""
    rc, output = _run(tmp_path, body)
    assert rc == 0, (
        f"checker false-positive on safe python3 form:\n{body}\nOutput:\n{output}"
    )
