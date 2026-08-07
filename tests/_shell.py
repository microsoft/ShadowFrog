"""Cross-platform helpers for the POSIX-shell integration tests.

The shell scripts under ``hook-templates/`` and ``skills/`` are POSIX ``bash``
scripts that internally call ``python3``, ``git`` and coreutils (``sed``,
``grep``, ``tr`` ...).  Running them from the test suite on Windows has two
pitfalls that this module papers over:

1. **``bash`` resolution.**  On a GitHub ``windows-latest`` runner a bare
   ``bash`` on ``PATH`` resolves to ``C:\\Windows\\System32\\bash.exe`` — the
   WSL launcher stub, which has no distro installed and fails immediately.
   :data:`BASH` instead points at the *Git Bash* interpreter that ships with
   Git for Windows (preinstalled on every ``windows-latest`` runner).

2. **Tool discovery inside Git Bash.**  Git Bash does not ship ``python3`` and
   keeps ``git`` under ``mingw64/bin`` (not ``/usr/bin``).  :func:`shell_path`
   returns a ``PATH`` that makes ``python3`` (via a shim that execs the test
   interpreter) and ``git`` discoverable, so the scripts behave exactly as on
   Linux.

On non-Windows platforms both helpers are thin pass-throughs, so the Linux CI
behaviour is unchanged.
"""
import functools
import os
import shutil
import sys
import tempfile
from pathlib import Path

_DEFAULT_POSIX_PATH = "/usr/bin:/bin:/usr/local/bin"


def _find_bash() -> str:
    """Locate a real POSIX ``bash``.

    Returns an empty string when none is available (e.g. a Windows dev box
    without Git for Windows) so callers can skip gracefully instead of failing.
    """
    if os.name != "nt":
        return "bash"

    override = os.environ.get("SHADOWFROG_BASH")
    if override and Path(override).is_file():
        return override

    candidates = [
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
        r"C:\Program Files (x86)\Git\bin\bash.exe",
    ]
    git = shutil.which("git")
    if git:
        # git.exe usually lives at <Git>\cmd\git.exe or <Git>\bin\git.exe.
        candidates.append(str(Path(git).parent.parent / "bin" / "bash.exe"))

    for candidate in candidates:
        # Never accept System32\bash.exe — that is the WSL launcher stub.
        if "System32" in candidate or "system32" in candidate:
            continue
        if Path(candidate).is_file():
            return candidate
    return ""


#: Path to a real POSIX ``bash`` ("bash" on POSIX, Git Bash on Windows, or ""
#: when unavailable).
BASH = _find_bash()

#: True when a usable POSIX shell was found. Use as a skip guard.
HAVE_BASH = bool(BASH)


@functools.lru_cache(maxsize=1)
def _python3_shim_dir() -> str:
    """Create a directory containing a ``python3`` launcher for Git Bash.

    Git Bash has no ``python3``; the scripts hard-code that name.  The shim
    execs the *current* test interpreter, so the scripts run under exactly the
    Python the suite uses.
    """
    shim_dir = Path(tempfile.mkdtemp(prefix="sf-py3-shim-"))
    shim = shim_dir / "python3"
    shim.write_text(
        '#!/bin/sh\nexec "%s" "$@"\n' % Path(sys.executable).as_posix(),
        encoding="ascii",
    )
    os.chmod(shim, 0o755)
    return str(shim_dir)


@functools.lru_cache(maxsize=1)
def _git_tool_dirs() -> tuple:
    """Git Bash bin directories that hold ``git`` and the coreutils."""
    if not BASH:
        return ()
    git_root = Path(BASH).parent.parent  # ...\Git\bin\bash.exe -> ...\Git
    dirs = []
    for sub in ("mingw64/bin", "usr/bin", "bin"):
        candidate = git_root / sub
        if candidate.is_dir():
            dirs.append(str(candidate))
    return tuple(dirs)


def shell_path(base: str | None = None) -> str:
    """Return a ``PATH`` for running the POSIX shell scripts via :data:`BASH`.

    On POSIX this preserves the historical behaviour (inherit the ambient
    ``PATH``, or ``base`` when given).  On Windows it returns a controlled
    ``PATH`` giving Git Bash access to ``python3`` (shim) and ``git`` +
    coreutils, expressed as a Windows ``;``-separated string that Git Bash
    converts to POSIX form at launch.
    """
    if os.name != "nt":
        if base is not None:
            return base
        return os.environ.get("PATH", _DEFAULT_POSIX_PATH)
    return os.pathsep.join([_python3_shim_dir(), *_git_tool_dirs()])


def prepend_path(extra_dir) -> str:
    """Return :func:`shell_path` with ``extra_dir`` prepended.

    Use instead of ``f"{stub}:{os.environ['PATH']}"`` so the correct path
    separator (``:`` on POSIX, ``;`` on Windows) is used and the Windows tool
    directories are still present.
    """
    return os.pathsep.join([str(extra_dir), shell_path()])
