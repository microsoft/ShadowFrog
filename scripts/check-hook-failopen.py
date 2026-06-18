#!/usr/bin/env python3
"""Guard the fail-open contract of advisory hook scripts.

Copilot CLI >= 1.0.57 treats any non-zero exit from a preToolUse command hook
as a tool-call DENY. Our hooks therefore MUST run fail-open under every
condition. This script enforces the invariants required for that guarantee.

Invariants (failing any of these blocks the PR):

  1. NO `set -e`, `set -u`, `set -o errexit|nounset|pipefail|errtrace` in any
     spelling. Original v1.0.57 bug was `set -euo pipefail`. Long-form
     spellings (`set -o errexit`) plus four bypasses must also be rejected:
     `[[ X ]] && set -e`, `set \\\n -e` (line continuation), `; set -e`
     (after semicolon), and `eval 'set -e'`. The matcher scans substrings,
     not just line starts, and pre-joins line-continuations.

  2. NO `source` / `.` of external files. A sourced file with `set -e` would
     bypass an inline-only check.

  3. `trap 'exit 0' EXIT` present, on its own non-comment line. The EXIT trap
     converts clean non-zero exits to 0.

  4. `trap 'exit 0' TERM HUP INT` (or equivalent covering at least TERM)
     present, on its own non-comment line. EXIT alone returns 143/-15 under
     SIGTERM — verified empirically on bash 3.2 (macOS) and bash 5+ (Linux).
     Without this, the runner's timeout-kill still denies the tool. Signal
     aliases (`SIGTERM`, numeric `15`) are normalized to canonical names.

  5. NO raw `git` or unbounded raw `python3` invocations at the bash level
     outside of bounded Python `subprocess.run(timeout=...)` wrappers. An
     unbounded foreground child queues signals (the trap pyramid cannot fire
     while bash is in `wait`) — confirmed by empirical reproduction of a 31s
     hang on `git rev-parse --show-toplevel`.

Usage:  python3 check-hook-failopen.py path/to/hook1.sh path/to/hook2.sh ...
"""
import re
import sys


# Block: any form of strict-mode flags that would propagate non-zero exits.
# Four bypasses of the naive `^\s*set\s+` anchor must be caught:
#   1. `[[ X ]] && set -e`        (combinator)
#   2. `set \\\n  -e`             (line continuation — joined before regex)
#   3. `; set -e`                 (semicolon)
#   4. `eval 'set -e'`            (literal inside string)
# Substring matching catches all four. The leading boundary requires that
# `set` is either at line start (post-strip), or after a non-word context:
# whitespace, shell control char, or quote.
FORBIDDEN_SET_RE = re.compile(
    r"(?:^|[\s;&|'\"`])set\s+("
    # Short form: any combination containing e, u, or E (errtrace).
    r"-[a-zA-Z]*[euE][a-zA-Z]*"
    # Long form: -o errexit / nounset / pipefail / errtrace
    r"|-o\s+(errexit|nounset|pipefail|errtrace)"
    r")\b"
)

# Block: source / . of external files.
FORBIDDEN_SOURCE_RE = re.compile(r"^\s*(source|\.)\s+[^\s#]+")

# Require: trap '...exit 0...' that includes EXIT, on a non-comment line.
# Signal list allows alphanumerics so `SIGTERM` and numeric `15` are
# captured (then normalized below).
TRAP_EXIT_RE = re.compile(
    r"^\s*trap\s+['\"][^'\"]*exit\s+0[^'\"]*['\"]\s+([A-Za-z0-9\s]+)\s*(#.*)?$"
)

# Require: same trap form covering at least TERM (signal-kill defense).
# The set of signals we require to be covered:
REQUIRED_SIGNALS = {"TERM"}

# Map common signal aliases to their canonical short name. Bash accepts
# `SIGTERM`, `TERM`, and `15` interchangeably; the checker must too,
# else it false-rejects perfectly valid hooks.
SIGNAL_ALIASES = {
    "SIGTERM": "TERM", "15": "TERM",
    "SIGINT": "INT",   "2": "INT",
    "SIGHUP": "HUP",   "1": "HUP",
    "SIGPIPE": "PIPE", "13": "PIPE",
    "SIGQUIT": "QUIT", "3": "QUIT",
    "SIGEXIT": "EXIT",  "0": "EXIT",
}

def _canonicalize_signal(s: str) -> str:
    return SIGNAL_ALIASES.get(s, s)

# Detect raw `git` in bash code (outside Python heredocs).
RAW_GIT_RE = re.compile(r"^\s*(?:[A-Z_0-9]+=)?\$?\(?\s*git\s+\S")

# Detect raw `python3` script invocations at bash level (outside heredocs)
# that aren't `python3 -c "<short literal>"` AND aren't `python3 - <<HEREDOC`.
# The first form has bounded blast radius (a short literal); the second is
# the standard pattern for bounded internal logic with subprocess.run(timeout=).
# What we want to FLAG: `python3 path/to/script.py`, `python3 -m module`, or
# any unbounded long-running variant. These cannot be wrapped from inside
# (their work happens outside our control), and bash queues signals while
# waiting for them — same bug class as the original `git` hang.
RAW_PYTHON_BAD_RE = re.compile(
    # Forms to flag: `python3 path.py`, `python3 -m module`, `python3 -u path.py`.
    r"^\s*(?:[A-Z_0-9]+=)?\$?\(?\s*python3?\s+"
    # Followed by something that's NOT one of:
    #   `-c "..."` short inline
    #   `- <<HEREDOC` stdin heredoc
    #   `-` alone (stdin)
    r"(?!-c\s|--?\s*<<|--?\s*$|--version|--help)"
    r"\S"
)


def _strip_comments(line: str) -> str:
    """Drop trailing comment, respecting single-quoted strings."""
    in_squote = False
    out = []
    for ch in line:
        if ch == "'" and not in_squote:
            in_squote = True
        elif ch == "'" and in_squote:
            in_squote = False
        elif ch == "#" and not in_squote:
            break
        out.append(ch)
    return "".join(out)


def _join_continuations(text: str) -> str:
    """Join lines ending in `\\` so a continuation cannot smuggle `set -e`
    past a per-line regex."""
    # Replace backslash-newline with a single space, preserving overall
    # structure. We do NOT touch backslashes that aren't followed by
    # newline (those are escape sequences inside strings or paths).
    return re.sub(r"\\\n", " ", text)


def _is_in_heredoc(lines: list[str], idx: int) -> bool:
    """Return True if line `idx` is inside a heredoc (e.g., python3 -<<PYEOF)."""
    in_heredoc = False
    heredoc_term = None
    for i, line in enumerate(lines):
        if i > idx:
            break
        if in_heredoc:
            stripped = line.strip()
            if stripped == heredoc_term:
                in_heredoc = False
                heredoc_term = None
            continue
        # Look for heredoc start: <<'TERM' or <<TERM or <<-TERM
        m = re.search(r"<<-?\s*['\"]?([A-Za-z_][A-Za-z_0-9]*)['\"]?", line)
        if m:
            in_heredoc = True
            heredoc_term = m.group(1)
    return in_heredoc


def check_file(path: str) -> list[str]:
    """Return list of error messages (empty if clean)."""
    errors: list[str] = []
    try:
        text = open(path).read()
    except OSError as e:
        return [f"{path}: cannot read: {e}"]

    # Join line continuations so a `set \\\n -e` cannot evade per-line regex.
    text = _join_continuations(text)
    lines = text.splitlines()

    has_exit_trap = False
    trap_signals_covered: set[str] = set()

    for lineno, raw_line in enumerate(lines, 1):
        # Skip lines inside heredocs — those are Python/awk/etc., not bash.
        if _is_in_heredoc(lines, lineno - 1):
            continue

        # Strip trailing comment to avoid false positives on comment text.
        line = _strip_comments(raw_line)
        if not line.strip():
            continue

        # Invariant 1: strict-mode flags (substring scan; catches
        # `&& set -e`, `; set -e`, `eval 'set -e'`, and joined
        # `set \\\n -e`).
        if FORBIDDEN_SET_RE.search(line):
            errors.append(
                f"{path}:{lineno}: FORBIDDEN strict-mode flag in advisory hook: {raw_line.strip()!r}\n"
                f"  Hooks MUST NOT use 'set -e', 'set -u', 'set -o errexit|nounset|pipefail|errtrace'.\n"
                f"  Copilot CLI >= 1.0.57 treats non-zero preToolUse exit as DENY."
            )

        # Invariant 2: source / dot of external files
        if FORBIDDEN_SOURCE_RE.match(line):
            errors.append(
                f"{path}:{lineno}: FORBIDDEN source/dot of external file: {raw_line.strip()!r}\n"
                f"  A sourced file could re-introduce 'set -e' and bypass this guard.\n"
                f"  Inline the needed code in the hook script directly."
            )

        # Capture trap declarations (normalize signal aliases).
        m = TRAP_EXIT_RE.match(line)
        if m:
            signals = [_canonicalize_signal(s) for s in m.group(1).split()]
            if "EXIT" in signals:
                has_exit_trap = True
            for sig in signals:
                trap_signals_covered.add(sig)

        # Invariant 5: raw git outside of bounded Python wrapper.
        # We allow `git` only inside heredoc'd Python (handled above by skip).
        if RAW_GIT_RE.match(line):
            errors.append(
                f"{path}:{lineno}: UNBOUNDED 'git' call at bash level: {raw_line.strip()!r}\n"
                f"  Hooks MUST wrap every git call in Python subprocess.run(timeout=...)\n"
                f"  to prevent runner timeout-kill on slow/locked repos.\n"
                f"  (bash's signal trap is queued while waiting for a foreground child.)"
            )

        # Invariant 5b: raw python3 script invocation (unbounded blast radius).
        # We allow `python3 -c "<short>"` (bash literal) and `python3 - <<HEREDOC`
        # (stdin heredoc, bounded inside). What we flag: `python3 path.py`,
        # `python3 -m module`, or any other script form that runs unbounded
        # external code at bash level. Same bug class as raw git.
        if RAW_PYTHON_BAD_RE.match(line):
            errors.append(
                f"{path}:{lineno}: UNBOUNDED 'python3' script at bash level: {raw_line.strip()!r}\n"
                f"  Hooks MUST only invoke python3 as either `python3 -c \"<short>\"`\n"
                f"  (small inline literal) or `python3 - <<HEREDOC` (stdin heredoc,\n"
                f"  bounded internally with subprocess.run(timeout=...)).\n"
                f"  Bash queues signals waiting for foreground python3, so an\n"
                f"  unbounded script blocks the trap pyramid from firing on SIGTERM."
            )

    # Invariant 3: trap on EXIT
    if not has_exit_trap:
        errors.append(
            f"{path}: MISSING 'trap \"exit 0\" EXIT' — required to convert clean\n"
            f"  non-zero exits to 0. Without it, any failed sub-step denies the tool."
        )

    # Invariant 4: trap covers at least TERM
    missing_sigs = REQUIRED_SIGNALS - trap_signals_covered
    if missing_sigs:
        errors.append(
            f"{path}: MISSING signal trap covering {sorted(missing_sigs)!r}.\n"
            f"  Required: trap 'exit 0' EXIT  AND  trap 'exit 0' TERM HUP INT\n"
            f"  Empirically verified: EXIT alone returns 143/-15 under SIGTERM\n"
            f"  (bash 3.2 macOS, bash 5+ Linux). The runner's timeout-kill would\n"
            f"  still deny the tool without an explicit TERM trap.\n"
            f"  Signal aliases SIGTERM and numeric 15 are accepted equivalents."
        )

    return errors


def main() -> int:
    if len(sys.argv) < 2:
        print("usage: check-hook-failopen.py <hook.sh> [<hook.sh> ...]", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for path in sys.argv[1:]:
        all_errors.extend(check_file(path))

    if all_errors:
        print("\n".join(all_errors))
        print()
        print(f"FAIL: {len(all_errors)} fail-open contract violation(s) found.")
        print("See CHANGELOG.md (2026-06-02) and claude.md (Hook Format).")
        return 1

    print(f"OK: fail-open contract intact across {len(sys.argv) - 1} hook script(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
