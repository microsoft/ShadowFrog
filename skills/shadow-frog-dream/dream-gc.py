#!/usr/bin/env python3
"""Sweep orphan dream worktrees, or completed worktrees in one namespace.

Usage: dream-gc.py [--repo-root DIR] [--min-age-min N] [--dry-run] [--quiet]
       dream-gc.py --task-complete --namespace NS [--min-age-min N]

Root: DREAM_WORKTREE_BASE, or <system-temp>/shadowfrog-dreams.
Repository: --repo-root, then REPO_ROOT, then Git discovery from cwd.
Default mode sweeps orphans across the whole base; --namespace only scopes
--task-complete (also accepts DREAM_NAMESPACE). Completion is asserted by the
caller, not inferred from age. Registered Git refusals never use raw removal.
Age defaults to 10 minutes; 0 disables the age filter. Quiet suppresses progress,
not errors. Dry-run reports eligible candidates without proving Git can remove
them. No branch refs are deleted.

Exit codes: 0 completed (including reported candidate refusals); 1 preflight/
root scan failed; 2 usage; 4 missing safety module.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import re
import sys
import time


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


def _age(value):
    if re.fullmatch(r"[0-9]+", value) is None:
        raise argparse.ArgumentTypeError("--min-age-min must be a non-negative integer")
    try:
        return int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--min-age-min is too large") from exc


def _candidates(core, root, namespace, refuse):
    def scan_namespace(path):
        try:
            info = path.lstat()
            if not core.is_plain_directory(info):
                refuse(f"namespace is not a plain directory: {path}")
                return
            with os.scandir(path) as entries:
                for entry in entries:
                    if not entry.name.startswith("dream-"):
                        continue
                    try:
                        info = entry.stat(follow_symlinks=False)
                        if core.is_link(info):
                            refuse(f"linked candidate: {entry.path}")
                        elif core.is_plain_directory(info):
                            yield Path(entry.path), info
                    except OSError as exc:
                        refuse(f"cannot inspect candidate {entry.path}: {exc}")
        except FileNotFoundError:
            return
        except OSError as exc:
            refuse(f"cannot scan namespace {path}: {exc}")

    if namespace is not None:
        yield from scan_namespace(root / namespace)
        return
    with os.scandir(root) as entries:
        for entry in entries:
            try:
                info = entry.stat(follow_symlinks=False)
                if core.is_link(info):
                    refuse(f"linked namespace: {entry.path}")
                elif core.is_plain_directory(info):
                    yield from scan_namespace(Path(entry.path))
            except OSError as exc:
                refuse(f"cannot inspect namespace {entry.path}: {exc}")


def sweep(core, root, repo, args):
    counts = {"removed": 0, "kept": 0, "refused": 0}

    def say(message):
        if not args.quiet:
            print(message)

    def refuse(message):
        counts["refused"] += 1
        print(f"WARN: {message}", file=sys.stderr)

    say(f"Sweeping dream worktree base: {root}")
    now = time.time()
    for path, info in _candidates(core, root, args.namespace if args.task_complete else None, refuse):
        if args.min_age_min and now - info.st_mtime <= args.min_age_min * 60:
            continue
        inspection = core.inspect_candidate(str(path), root, repo)
        if inspection.state == "missing":
            counts["kept"] += 1
            continue
        if inspection.state == "indeterminate":
            refuse(inspection.reason)
            continue
        if inspection.state in ("owned", "live-other-or-unknown") and not args.task_complete:
            counts["kept"] += 1
            continue
        if inspection.state == "live-other-or-unknown":
            refuse(inspection.reason)
            continue
        label = "orphan" if inspection.state == "orphan" else "stale-registered"
        if args.dry_run:
            say(f"  WOULD REMOVE ({label}): {path}")
            counts["removed"] += 1
            continue
        result = core.remove_candidate(inspection, root, repo, args.task_complete)
        if result.exit_code:
            refuse(result.message)
        elif result.action == "missing":
            counts["kept"] += 1
        else:
            say(f"  removed ({label}): {path}")
            counts["removed"] += 1
    say("Summary: " + " ".join(f"{key}={value}" for key, value in counts.items()))
    if not args.dry_run:
        warning = core.prune_repo(repo)
        if warning:
            print(f"WARN: {warning}", file=sys.stderr)
    return 0


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description=__doc__, allow_abbrev=False,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--repo-root")
    parser.add_argument("--min-age-min", type=_age, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--task-complete", action="store_true")
    parser.add_argument("--namespace", "-n")
    args = parser.parse_args()
    args.namespace = args.namespace or os.environ.get("DREAM_NAMESPACE")
    if args.task_complete and (
        not args.namespace or re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9._-]*", args.namespace) is None
    ):
        parser.error("--task-complete requires --namespace matching [A-Za-z0-9_-][A-Za-z0-9._-]* (or DREAM_NAMESPACE)")
    try:
        core = _load_core()
    except (ImportError, OSError, ValueError, SyntaxError, AttributeError) as exc:
        print(f"ERROR: cannot load cleanup helper: {exc}", file=sys.stderr)
        return 1
    try:
        root = core.resolve_root()
        try:
            info = root.lstat()
        except FileNotFoundError:
            if not args.quiet:
                print(f"Base does not exist (nothing to sweep): {root}")
            return 0
        if not core.is_plain_directory(info):
            raise core.CleanupError(f"base is not a plain directory: {root}")
        repo = core.resolve_repo(args.repo_root or os.environ.get("REPO_ROOT"))
        return sweep(core, root, repo, args)
    except core.CleanupError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return exc.exit_code
    except (OSError, ValueError) as exc:
        print(f"ERROR: cannot sweep: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
