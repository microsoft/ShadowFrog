"""Shared worktree-root resolution for the dream lifecycle."""
import os
import tempfile


def resolve_worktree_root(override=None):
    """Return the absolute, symlink-resolved configured dream worktree root."""
    root = override or os.environ.get("DREAM_WORKTREE_BASE") or os.path.join(
        tempfile.gettempdir(), "shadowfrog-dreams"
    )
    return os.path.realpath(os.path.abspath(root))


def canonical_worktree_path(path):
    """Return a path suitable for identity comparison on every supported OS."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))
