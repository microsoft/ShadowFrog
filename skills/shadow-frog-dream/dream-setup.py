#!/usr/bin/env python3
"""Dream experiment setup — create the worktree and print its context as JSON.

Usage:
    python dream-setup.py --slug t01-csv-fuzzer
    python dream-setup.py --slug t03-extend --base-branch dream/ns/<prior-id>

Prints a JSON object to stdout with keys:
    repo_root, default_branch, dream_ns, dream_id, branch_name, parent_branch,
    worktree_dir, worktree_root, worktree_base, base_commit, run_prefix, slug

Exits non-zero (message on stderr) on any failure — always check the exit code.

This script:
  1. Validates inputs (slug/namespace) against [A-Za-z0-9_-][A-Za-z0-9._-]*
     (a non-'.' first char rejects a bare '.'/'..').
  2. Computes dream_id, branch_name, worktree_dir, base_commit.
  3. Creates the worktree (idempotent — cleans an existing one via a safety gate).
  4. Prints the context as JSON (no shell escaping needed).

Flags:
    --slug NAME        Task slug (required, e.g. "t01-csv-fuzzer")
    --base-branch REF  Branch to base from (default: the repo's default branch)
    --namespace NS     Override DREAM_NAMESPACE (default: env or repo basename)
    --repo-root DIR    Override repo root (default: git rev-parse)
    --print-json       Accepted for compatibility (JSON is the only output mode)
    --dry-run          Compute values without creating the worktree
    --help, -h         Show this help message

Design:
  - Idempotent: re-running with the same slug cleans and recreates.
  - External path: worktrees always in <DREAM_WORKTREE_BASE>/<ns>/dream-<slug>.
  - Refuses to create a worktree inside the project directory.
  - Detects the default branch (main/master) automatically.
  - Resolves the namespace from env > TASK_INFO.json > .env > repo basename.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

# Setup is stricter than the cleanup safety gate: it rejects leading ".".
SAFE_RE = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]*$")
SAFE_INT_RE = re.compile(r"^[0-9]+$")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))


def _err(msg):
    print(msg, file=sys.stderr)


def _git(args, cwd=None):
    """Run a git command, capturing output as UTF-8. Never raises on non-zero."""
    return subprocess.run(
        ["git", *args], cwd=cwd,
        capture_output=True, text=True, encoding="utf-8",
    )


def _touch(path):
    try:
        with open(path, "a", encoding="utf-8"):
            pass
        os.utime(path, None)
    except OSError:
        pass


def _resolve_repo_root(path):
    candidate = os.path.abspath(path) if path else None
    r = _git(["rev-parse", "--show-toplevel"], cwd=candidate)
    if r.returncode != 0:
        _err("ERROR: Not in a git repository")
        sys.exit(1)
    return os.path.abspath(r.stdout.strip())


def _import_safety():
    """Lazily import the shared rm-rf safety gate (sibling module). Raises
    ImportError if the module is missing — the caller decides how to react."""
    sys.path.insert(0, SCRIPT_DIR)
    try:
        from _worktree_safety import safe_worktree_path, UnsafePath
        return safe_worktree_path, UnsafePath
    finally:
        if sys.path and sys.path[0] == SCRIPT_DIR:
            sys.path.pop(0)


def _import_paths():
    """Lazily import the shared worktree-root resolver."""
    sys.path.insert(0, SCRIPT_DIR)
    try:
        from _worktree_paths import resolve_worktree_root
        return resolve_worktree_root
    finally:
        if sys.path and sys.path[0] == SCRIPT_DIR:
            sys.path.pop(0)


def _read_env_namespace(path):
    """Extract DREAM_NAMESPACE from a `.env` file (first match), stripping
    surrounding whitespace and one layer of matching quotes."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                if line.startswith("DREAM_NAMESPACE="):
                    val = line.split("=", 1)[1].strip()
                    if len(val) >= 2 and val[0] in "\"'" and val[-1] == val[0]:
                        val = val[1:-1]
                    return val.strip()
    except OSError:
        return ""
    return ""


def _val(argv, i, flag):
    if i + 1 >= len(argv):
        _err(f"ERROR: {flag} requires a value")
        sys.exit(1)
    return argv[i + 1]


def _parse_args(argv):
    opts = {
        "slug": "", "base_branch": "", "namespace": "",
        "repo_root": "", "dry_run": False,
    }
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--slug":
            opts["slug"] = _val(argv, i, a); i += 2
        elif a == "--base-branch":
            opts["base_branch"] = _val(argv, i, a); i += 2
        elif a == "--namespace":
            opts["namespace"] = _val(argv, i, a); i += 2
        elif a == "--repo-root":
            opts["repo_root"] = _val(argv, i, a); i += 2
        elif a == "--print-json":
            i += 1  # accepted no-op — JSON is the only output mode
        elif a == "--dry-run":
            opts["dry_run"] = True; i += 1
        elif a in ("--help", "-h"):
            print(__doc__)
            sys.exit(0)
        else:
            _err(f"ERROR: Unknown argument: {a}")
            sys.exit(1)
    return opts


def _maybe_auto_gc(repo_root, worktree_base, worktree_root=None):
    """Periodic, throttled, best-effort orphan sweep. NEVER breaks setup.

    Prefers a `dream-gc.py` sibling if present, else falls back to the bash
    `dream-gc.sh`. Runs wherever a usable interpreter/shell exists, else a clean
    no-op. All GC output is routed to stderr so stdout stays pure JSON.
    """
    if os.environ.get("DREAM_GC_AUTO", "1") == "0":
        return

    interval_raw = os.environ.get("DREAM_GC_INTERVAL_MIN", "60")
    age_raw = os.environ.get("DREAM_GC_AGE_MIN", "60")
    if not SAFE_INT_RE.match(interval_raw) or not SAFE_INT_RE.match(age_raw):
        _err("WARN: DREAM_GC_INTERVAL_MIN / DREAM_GC_AGE_MIN must be "
             "non-negative integers — skipping auto-GC")
        return
    interval_min = int(interval_raw)

    gc_py = os.path.join(SCRIPT_DIR, "dream-gc.py")
    gc_sh = os.path.join(SCRIPT_DIR, "dream-gc.sh")
    if os.path.isfile(gc_py):
        gc_cmd = [sys.executable, gc_py, "--repo-root", repo_root,
                  "--quiet", "--min-age-min", age_raw]
    elif os.path.isfile(gc_sh):
        gc_cmd = ["bash", gc_sh, "--repo-root", repo_root,
                  "--quiet", "--min-age-min", age_raw]
    else:
        return

    tombstone = os.path.join(worktree_base, ".last-gc")
    should_run = False
    if not os.path.isfile(tombstone):
        should_run = True
    elif interval_min == 0:
        # Interval 0 ⇒ "always run" (a fresh tombstone would otherwise
        # throttle the very first sweep after touch).
        should_run = True
    else:
        try:
            if (time.time() - os.path.getmtime(tombstone)) > interval_min * 60:
                should_run = True
        except OSError:
            should_run = True
    if not should_run:
        return

    try:
        env = os.environ.copy()
        if worktree_root:
            env["DREAM_WORKTREE_BASE"] = worktree_root
        r = subprocess.run(
            gc_cmd, cwd=repo_root,
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            env=env,
        )
        if r.stdout:
            sys.stderr.write(r.stdout)
        if r.stderr:
            sys.stderr.write(r.stderr)
        if r.returncode == 0:
            _touch(tombstone)
        else:
            _err(f"WARN: auto-GC exited with code {r.returncode}; will retry later")
    except Exception:  # noqa: BLE001 — auto-GC must never break setup
        pass


def _preclean_worktree(repo_root, worktree_dir, gate_base):
    """Idempotent pre-clean of an existing worktree dir. Tries git first, then
    a safety-gated rmtree for stale dirs git can't see. Refuses (exit 1) if the
    safety module is missing — never an un-gated remove."""
    if not os.path.isdir(worktree_dir):
        return
    if _git(["worktree", "remove", worktree_dir, "--force"],
            cwd=repo_root).returncode != 0:
        try:
            safe_worktree_path, UnsafePath = _import_safety()
        except ImportError:
            _err("ERROR: safety module not found, refusing pre-clean rm: "
                 f"{os.path.join(SCRIPT_DIR, '_worktree_safety.py')}")
            sys.exit(1)
        try:
            resolved = safe_worktree_path(worktree_dir, gate_base)
        except UnsafePath:
            resolved = None
        if resolved is not None:
            shutil.rmtree(resolved, ignore_errors=True)
    _git(["worktree", "prune"], cwd=repo_root)


def main():
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")

    opts = _parse_args(sys.argv[1:])

    slug = opts["slug"]
    if not slug:
        _err("ERROR: --slug is required")
        _err("Usage: dream-setup.py --slug t01-name [--base-branch BRANCH]")
        sys.exit(1)
    if not SAFE_RE.match(slug):
        _err(f"ERROR: --slug must match {SAFE_RE.pattern} (got: {slug})")
        _err("  Use kebab-case alphanumerics like 't01-csv-fuzzer'.")
        sys.exit(1)
    if opts["namespace"] and not SAFE_RE.match(opts["namespace"]):
        _err(f"ERROR: --namespace must match {SAFE_RE.pattern} "
             f"(got: {opts['namespace']})")
        sys.exit(1)

    # --- Resolve repo root ---
    if opts["repo_root"]:
        repo_root = _resolve_repo_root(opts["repo_root"])
    else:
        repo_root = _resolve_repo_root("")
    os.chdir(repo_root)

    # --- Guard: .shadow/ must be tracked by git (not gitignored) ---
    # Probe a NEW child path (not `.shadow` itself): when `.shadow/` is
    # gitignored but already tracked, `git check-ignore .shadow` reports
    # "not ignored", yet `git add -A` still drops NEW files under it.
    probe = ".shadow/_dreams/__shadowfrog_probe__/manifest.json"
    if _git(["check-ignore", "-q", probe]).returncode == 0:
        _err("ERROR: .shadow/ is gitignored — shadow-frog-dream requires it "
             "to be tracked by git.")
        _err("  Dream experiments commit .shadow/ artifacts onto a branch, "
             "push them, and")
        _err("  reconcile reads them back from the remote. A gitignored "
             ".shadow/ would be")
        _err("  silently dropped at commit time, losing every discovery.")
        _err("  Fix: remove the '.shadow/' entry from .gitignore and commit "
             ".shadow/,")
        _err("  or run shadow-frog-update (which works in local-only mode) "
             "instead of dream.")
        sys.exit(1)

    # --- Detect default branch ---
    default_branch = ""
    r = _git(["symbolic-ref", "refs/remotes/origin/HEAD"])
    if r.returncode == 0:
        default_branch = r.stdout.strip().replace("refs/remotes/origin/", "")
    if not default_branch:
        if _git(["show-ref", "--verify",
                 "refs/remotes/origin/main"]).returncode == 0:
            default_branch = "main"
        elif _git(["show-ref", "--verify",
                   "refs/remotes/origin/master"]).returncode == 0:
            default_branch = "master"
        else:
            _err("ERROR: Cannot detect default branch. "
                 "Fix: git remote set-head origin <branch>")
            sys.exit(1)

    # --- Resolve namespace ---
    dream_ns = ""
    if opts["namespace"]:
        dream_ns = opts["namespace"]
    elif os.environ.get("DREAM_NAMESPACE"):
        dream_ns = os.environ["DREAM_NAMESPACE"]
    elif os.path.isfile("TASK_INFO.json"):
        try:
            with open("TASK_INFO.json", encoding="utf-8") as f:
                task_info = json.load(f)
            if not isinstance(task_info, dict):
                _err("ERROR: TASK_INFO.json must contain a JSON object")
                sys.exit(1)
            task_ns = task_info.get("dream_namespace", "")
            if task_ns:
                if not isinstance(task_ns, str):
                    _err("ERROR: TASK_INFO.json dream_namespace must be a string")
                    sys.exit(1)
                dream_ns = task_ns
        except (OSError, ValueError):
            dream_ns = ""
    elif os.path.isfile(".env"):
        dream_ns = _read_env_namespace(".env")
    if not dream_ns:
        dream_ns = os.path.basename(repo_root)

    if not SAFE_RE.match(dream_ns):
        _err(f"ERROR: Resolved DREAM_NS contains unsafe characters: {dream_ns}")
        _err(f"  Allowed: {SAFE_RE.pattern}")
        _err("  Override with --namespace or set DREAM_NAMESPACE.")
        sys.exit(1)

    # --- Compute identifiers ---
    dream_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%SZ") + "-" + slug
    branch_name = f"dream/{dream_ns}/{dream_id}"

    # --- Compute worktree path (ALWAYS external, NEVER in project) ---
    resolve_worktree_root = _import_paths()
    worktree_root = resolve_worktree_root()
    worktree_base = os.path.join(worktree_root, dream_ns)
    worktree_dir = os.path.join(worktree_base, f"dream-{slug}")

    rr = os.path.normcase(os.path.realpath(repo_root))
    wd = os.path.normcase(os.path.realpath(worktree_dir))
    try:
        inside_repo = os.path.commonpath([rr, wd]) == rr
    except ValueError:
        inside_repo = False
    if inside_repo:
        _err(f"ERROR: Worktree would be inside project: {worktree_dir}")
        _err("MUST use external path (default: <tempdir>/shadowfrog-dreams/)")
        sys.exit(1)

    # --- Resolve base reference ---
    if not opts["base_branch"]:
        base_ref = f"origin/{default_branch}"
        parent_branch = default_branch
    else:
        base_ref = f"origin/{opts['base_branch']}"
        parent_branch = opts["base_branch"]
        if _git(["show-ref", "--verify",
                 f"refs/remotes/{base_ref}"]).returncode != 0:
            _err(f"ERROR: Base branch not found: {base_ref}")
            sys.exit(1)

    # --- Create worktree (unless dry-run) ---
    base_commit = ""
    if not opts["dry_run"]:
        os.makedirs(worktree_base, exist_ok=True)
        _maybe_auto_gc(repo_root, worktree_base, worktree_root)
        _preclean_worktree(repo_root, worktree_dir, worktree_root)

        def _add():
            return _git(["worktree", "add", worktree_dir, "-b", branch_name,
                         base_ref], cwd=repo_root).returncode

        if _add() != 0:
            _git(["branch", "-D", branch_name], cwd=repo_root)
            _git(["worktree", "prune"], cwd=repo_root)
            if _add() != 0:
                _err(f"ERROR: git worktree add failed for {worktree_dir} "
                     f"(branch {branch_name}, base {base_ref})")
                sys.exit(1)

        base_commit = _git(["rev-parse", "HEAD"], cwd=worktree_dir).stdout.strip()
    else:
        r = _git(["rev-parse", base_ref])
        base_commit = r.stdout.strip() if r.returncode == 0 else "DRY_RUN"

    # --- Detect RUN_PREFIX ---
    run_prefix = ""
    if os.path.isfile("uv.lock"):
        run_prefix = "uv run"
    elif os.path.isfile("package-lock.json"):
        run_prefix = "npx"
    elif os.path.isfile("yarn.lock"):
        run_prefix = "npx"

    print(json.dumps({
        "repo_root": repo_root,
        "default_branch": default_branch,
        "dream_ns": dream_ns,
        "dream_id": dream_id,
        "branch_name": branch_name,
        "parent_branch": parent_branch,
        "worktree_dir": worktree_dir,
        "worktree_root": worktree_root,
        "worktree_base": worktree_base,
        "base_commit": base_commit,
        "run_prefix": run_prefix,
        "slug": slug,
    }, indent=2))


if __name__ == "__main__":
    main()
