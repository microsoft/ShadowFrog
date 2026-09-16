"""Shared worktree-root resolution for the dream lifecycle."""
import os
import tempfile


def resolve_worktree_root(override=None):
    """Return the configured root or the platform's default dream root."""
    return override or os.environ.get("DREAM_WORKTREE_BASE") or os.path.join(
        tempfile.gettempdir(), "shadowfrog-dreams"
    )
