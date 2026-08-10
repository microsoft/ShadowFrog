"""Cross-platform helpers for invoking POSIX shell scripts in tests."""

from __future__ import annotations

import atexit
import os
import shlex
import shutil
import sys
import tempfile
from pathlib import Path


def _is_wsl_stub(path: str | None) -> bool:
    if not path:
        return False
    return path.lower().endswith(r"\windows\system32\bash.exe")


def _find_bash() -> str | None:
    which_bash = shutil.which("bash")
    if os.name != "nt":
        return which_bash
    if which_bash and not _is_wsl_stub(which_bash):
        return which_bash

    candidates: list[Path] = []
    git = shutil.which("git")
    if git:
        git_path = Path(git)
        candidates.extend([
            git_path.parent / "bash.exe",
            git_path.parent.parent / "bin" / "bash.exe",
            git_path.parent.parent / "usr" / "bin" / "bash.exe",
        ])
    for env_var in ("ProgramFiles", "ProgramFiles(x86)"):
        base = os.environ.get(env_var)
        if base:
            candidates.extend([
                Path(base) / "Git" / "bin" / "bash.exe",
                Path(base) / "Git" / "usr" / "bin" / "bash.exe",
            ])

    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


def shell_path(path: str | os.PathLike[str]) -> str:
    value = os.fspath(path)
    if os.name != "nt":
        return value
    value = value.replace("\\", "/")
    if len(value) >= 3 and value[1] == ":" and value[2] == "/":
        return f"/{value[0].lower()}{value[2:]}"
    return value


_FOUND_BASH = _find_bash()
BASH = _FOUND_BASH or "bash"
HAVE_BASH = _FOUND_BASH is not None

_WINDOWS_SHIM_DIR: str | None = None


def _windows_shims() -> list[str]:
    global _WINDOWS_SHIM_DIR
    if os.name != "nt":
        return []
    if _WINDOWS_SHIM_DIR is not None:
        return [_WINDOWS_SHIM_DIR]

    shim_dir = tempfile.mkdtemp(prefix="shadowfrog-shell-")
    atexit.register(lambda: shutil.rmtree(shim_dir, ignore_errors=True))
    python3 = Path(shim_dir) / "python3"
    python3.write_text(
        "#!/usr/bin/env sh\n"
        f"exec {shlex.quote(shell_path(sys.executable))} \"$@\"\n",
        encoding="utf-8",
    )
    python3.chmod(0o755)
    _WINDOWS_SHIM_DIR = shim_dir
    return [shim_dir]


def prepend_path(env: dict, *entries: str | os.PathLike[str]) -> dict:
    updated = dict(env)
    paths = [os.fspath(item) for item in entries if item]
    paths.extend(_windows_shims())
    current = updated.get("PATH", "")
    if current:
        paths.append(current)
    updated["PATH"] = os.pathsep.join(paths)
    return updated
