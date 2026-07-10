"""Single source of truth for the "is it safe to rm -rf this dream worktree?" gate.

Imported by `dream-reconcile.py` and invoked as a subprocess by
`dream-cleanup.sh` + `dream-gc.sh`. Both bash callers pass the candidate
path + the configured base via argv (NOT via string interpolation), so the
caller can never inject Python source through this module.

A path is considered safe to remove ONLY when ALL of these hold:

1. Path and base are both non-empty strings.
2. Path and base are both absolute (start with "/").
3. Neither contains a ".." component in the literal input.
4. The resolved (symlink-followed) base is not a sensitive filesystem root
   (e.g. /, /tmp, /home, $HOME, /Users, /etc, /usr).
5. After resolving symlinks in the path's parent directory, the resolved
   path is STRICTLY INSIDE the resolved base — not equal to it, and not
   above it.
6. The path matches the exact dream-worktree shape `<base>/<ns>/dream-<slug>`
   where `<ns>` and `<slug>` are each `[A-Za-z0-9._-]+` (the same `SAFE_RE`
   that `dream-setup.sh` already enforces on the inputs).

These rules are deliberately strict: they reject anything that doesn't look
like a dream worktree created by `dream-setup.sh`. That means we will NEVER
`rm -rf` a path the user happens to point us at — only paths that match the
namespace's own creation contract.

CLI invocation (used by the bash scripts):
    python3 _worktree_safety.py <worktree-dir> <base> [<ns>]
    exit codes:
      0 → safe AND the path currently exists (caller should rm)
      2 → safe AND the path does NOT exist  (caller should treat as no-op)
      1 → UNSAFE; do not rm                  (error printed to stderr)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path, PurePath

# Same regex `dream-setup.sh` validates --slug and --namespace against.
# Keep these in lockstep — if one widens, the other must follow.
_SAFE_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# Defense-in-depth: even if rule 5 (strictly under base) holds, refuse
# outright if the BASE itself lands on one of these. Checked against BOTH
# the literal input AND the symlink-resolved value, with macOS's `/private`
# prefix stripped — otherwise `/tmp` → `/private/tmp` would silently bypass
# the check on macOS (verified empirically: macOS `realpath /tmp` returns
# `/private/tmp` which is not in the list, so the literal `/tmp` check is
# what catches it).
_UNIX_FORBIDDEN_BASES = frozenset({
    "/",
    "/bin", "/boot", "/dev", "/etc", "/home", "/lib", "/lib32", "/lib64",
    "/Library", "/mnt", "/media", "/opt", "/private", "/proc", "/root",
    "/run", "/sbin", "/srv", "/sys", "/System", "/tmp", "/Users", "/usr",
    "/Users/Shared", "/var", "/var/folders", "/var/tmp",
})

# macOS-specific: `/private` is the real location for `/tmp`, `/etc`,
# `/var`. After `realpath` these all gain the `/private` prefix.
_MACOS_PRIVATE_PREFIX = "/private"


def _strip_macos_private(p: str) -> str:
    """Strip the macOS `/private` prefix so resolved paths can be compared
    against the bare forbidden roots. `/private` itself stays `/private`."""
    if p.startswith(_MACOS_PRIVATE_PREFIX + "/"):
        return p[len(_MACOS_PRIVATE_PREFIX):]
    return p


def _get_windows_forbidden_bases() -> set[str]:
    """Sensitive Windows base dirs, read from the environment at call time
    (%SystemRoot%, %ProgramFiles%, %USERPROFILE%, ...); empty on POSIX.
    A function (not a constant) because these are runtime/env-derived, and so
    they stay monkeypatch-able in tests.
    """
    if os.name != "nt":
        return set()
    env_vars = ("SystemRoot", "windir", "ProgramFiles", "ProgramFiles(x86)",
                "ProgramData", "USERPROFILE", "PUBLIC")
    return {os.path.normpath(v) for v in map(os.environ.get, env_vars) if v}


def _normalize_path(p: str) -> str:
    """Normalize a path into a comparison key. `os.path.normcase` lowercases and
    unifies `/`->`\\` on Windows (no-op on POSIX); stripping macOS's `/private`
    prefix lets `/tmp` and its resolved `/private/tmp` form compare equal.
    """
    return os.path.normcase(_strip_macos_private(p))


def _is_filesystem_root(p: str) -> bool:
    """True if `p` is a filesystem root with no parent to sweep under: POSIX
    `/`, a Windows drive root (`C:\\`), or a UNC share root
    (`\\\\server\\share`). A root is its own parent: `dirname(p) == p`.
    """
    p = os.path.normpath(p)
    return os.path.dirname(p) == p


# The two sources of "sensitive base" are ASYMMETRIC ON PURPOSE:
#   * POSIX roots are fixed, known-at-author-time paths -> a CONSTANT
#     (`_UNIX_FORBIDDEN_BASES`).
#   * Windows roots come from the environment (%SystemRoot% etc.), vary by
#     machine/user, and aren't known until runtime -> a FUNCTION.
# `_get_forbidden_bases()` hides the split so callers just ask "what's forbidden here?".
def _get_forbidden_bases() -> set[str]:
    """Sensitive-base comparison keys for the CURRENT OS, plus the user's home.
    Selects the platform-appropriate source: the static `_UNIX_FORBIDDEN_BASES`
    on POSIX, the env-derived `_get_windows_forbidden_bases()` on Windows.
    """
    raw = _get_windows_forbidden_bases() if os.name == "nt" else _UNIX_FORBIDDEN_BASES
    keys = {_normalize_path(p) for p in raw}
    home = os.path.expanduser("~")
    if home and home != "~":
        keys.add(_normalize_path(home))
        keys.add(_normalize_path(os.path.realpath(home)))
    return keys


class UnsafePath(ValueError):
    """Raised when the candidate path fails any of the safety rules."""


def safe_worktree_path(path: str, base: str) -> Path:
    """Validate `path` is a safe-to-remove dream worktree under `base`.

    Returns the symlink-resolved `Path` on success.
    Raises `UnsafePath` on any rule violation.
    Does NOT touch the filesystem beyond `os.path.realpath` resolution.
    """
    # Rule 1: non-empty.
    if not isinstance(path, str) or not path.strip():
        raise UnsafePath(f"empty or non-string worktree path: {path!r}")
    if not isinstance(base, str) or not base.strip():
        raise UnsafePath(f"empty or non-string base: {base!r}")

    # Rule 2: absolute.
    if not os.path.isabs(path):
        raise UnsafePath(f"worktree path is not absolute: {path!r}")
    if not os.path.isabs(base):
        raise UnsafePath(f"base is not absolute: {base!r}")

    # Rule 3: no ".." traversal in the literal input. Catches things that
    # would otherwise normalize past the base.
    if ".." in PurePath(path).parts:
        raise UnsafePath(f"worktree path contains '..': {path!r}")
    if ".." in PurePath(base).parts:
        raise UnsafePath(f"base contains '..': {base!r}")

    # Resolve the base: this is what we compare against.
    base_res = os.path.realpath(base)

    # Rule 4: refuse sensitive bases. Build ONE normalized candidate set from the
    # literal input AND the symlink-resolved form (so a symlink like
    # `$HOME/dreams -> /` can't smuggle a root past us). Normalizing is required
    # for the (b) set-match and harmless for the (a) root test, so we share it.
    base_norm = os.path.normpath(base)
    candidates = {_normalize_path(base_norm), _normalize_path(base_res)}

    # (a) Refuse a filesystem root (`/`, `C:\`, `\\server\share`): it has no
    #     parent to sweep under.
    for candidate in candidates:
        if _is_filesystem_root(candidate):
            raise UnsafePath(
                f"refusing sweep: base is a sensitive root "
                f"(filesystem root): {base!r}"
            )

    # (b) Refuse a base that matches a known-sensitive dir.
    if candidates & _get_forbidden_bases():
        raise UnsafePath(
            f"refusing sweep: base resolves to a sensitive root: {base!r}"
        )

    # Resolve the path's PARENT (not the path itself — the leaf may not
    # exist, which is fine for the rm-is-a-no-op case). os.path.realpath
    # walks all symlinks; combining with the literal leaf prevents a
    # symlink AT the leaf from escaping the base after a successful check.
    stripped = path.rstrip("/")
    parent_lit = os.path.dirname(stripped) or "/"
    leaf = os.path.basename(stripped)
    parent_res = os.path.realpath(parent_lit)
    resolved = os.path.join(parent_res, leaf)

    # If the leaf is itself a symlink, follow it AFTER constructing
    # `resolved` so the strict-inside-base check uses the real target.
    if os.path.islink(resolved):
        resolved = os.path.realpath(resolved)

    # Rule 5: strictly under base.
    try:
        rel = os.path.relpath(resolved, base_res)
    except ValueError as exc:
        raise UnsafePath(
            f"worktree path {resolved!r} not relatable to base "
            f"{base_res!r}: {exc}"
        ) from None
    if rel == "." or rel.startswith(".."):
        raise UnsafePath(
            f"worktree path {resolved!r} is not strictly under base "
            f"{base_res!r} (relpath={rel!r})"
        )

    # Rule 6: exact dream-worktree shape: <base>/<ns>/dream-<slug>.
    parts = rel.split(os.sep)
    if len(parts) != 2:
        raise UnsafePath(
            f"worktree path {resolved!r} is not exactly 2 levels under "
            f"base {base_res!r} (got parts={parts!r})"
        )
    ns_part, leaf_part = parts
    if not _SAFE_RE.match(ns_part):
        raise UnsafePath(
            f"namespace component {ns_part!r} does not match {_SAFE_RE.pattern}"
        )
    if not leaf_part.startswith("dream-"):
        raise UnsafePath(
            f"leaf {leaf_part!r} does not start with 'dream-'"
        )
    slug_part = leaf_part[len("dream-"):]
    if not slug_part or not _SAFE_RE.match(slug_part):
        raise UnsafePath(
            f"slug component {slug_part!r} does not match {_SAFE_RE.pattern}"
        )

    return Path(resolved)


def _cli() -> int:
    if len(sys.argv) < 3:
        print(
            "usage: _worktree_safety.py <worktree-dir> <base>",
            file=sys.stderr,
        )
        return 1
    path, base = sys.argv[1], sys.argv[2]
    try:
        resolved = safe_worktree_path(path, base)
    except UnsafePath as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        # Defensive: e.g. embedded NUL would raise ValueError from
        # os.path.realpath. Treat any non-UnsafePath gate failure as
        # "refused" rather than crashing with a traceback that the bash
        # caller would interpret as exit-code-1 anyway, but noisily.
        print(f"ERROR: gate failure: {exc}", file=sys.stderr)
        return 1
    # Check existence on the RESOLVED path. islink() catches dangling
    # symlinks (which `exists()` reports as False but which we should
    # still remove).
    if resolved.exists() or resolved.is_symlink():
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_cli())
