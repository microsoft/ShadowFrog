#!/usr/bin/env python3
"""Remove one completed dream worktree, preserving uncertain or locked targets.

Usage: dream-cleanup.py WORKTREE_DIR [--repo-root DIR] [--quiet]
Repository: --repo-root, then REPO_ROOT, then the worktree's Git metadata.
Root: DREAM_WORKTREE_BASE, or the system temp directory's shadowfrog-dreams.
Exit codes: 0 removed/missing; 1 refused; 2 usage; 3 rmtree failed;
4 missing safety module. No branch refs are deleted.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import sys


def _load_core():
    directory = Path(__file__).resolve().parent
    path = directory / "_worktree_cleanup.py"
    if path.resolve().parent != directory:
        raise ImportError(f"cleanup helper is outside the skill directory: {path}")
    spec = importlib.util.spec_from_file_location("_worktree_cleanup", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load cleanup helper: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("worktree_dir")
    parser.add_argument("--repo-root")
    parser.add_argument("--quiet", "-q", action="store_true")
    args = parser.parse_args()
    try:
        core = _load_core()
    except (ImportError, OSError, ValueError, SyntaxError, AttributeError) as exc:
        print(f"ERROR: cannot load cleanup helper: {exc}", file=sys.stderr)
        return 1
    try:
        root = core.resolve_root()
        initial = core.inspect_candidate(args.worktree_dir, root, None)
        if initial.state == "missing":
            if not args.quiet:
                print(f"Nothing to clean: {args.worktree_dir}")
            return 0
        if initial.state == "indeterminate":
            raise core.CleanupError(initial.reason)
        repo = core.resolve_repo(args.repo_root or os.environ.get("REPO_ROOT"), initial.path)
        inspection = core.inspect_candidate(args.worktree_dir, root, repo)
        if inspection.state not in ("orphan", "owned", "missing"):
            raise core.CleanupError(inspection.reason)
        result = core.remove_candidate(inspection, root, repo, allow_registered=True)
        if result.exit_code:
            print(f"ERROR: {result.message}", file=sys.stderr)
        elif not args.quiet:
            print(result.message)
        warning = core.prune_repo(repo)
        if warning:
            print(f"WARN: {warning}", file=sys.stderr)
        return result.exit_code
    except core.CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
