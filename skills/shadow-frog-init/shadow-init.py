#!/usr/bin/env python3
"""
shadow-init.py — Initialize a .shadow/ knowledge base for any codebase.

Part of the ShadowFrog skills suite. Discovers source files via git,
extracts symbols with regex or an optional Astred backend, and creates per-file shadow
markdown stubs plus scaffolding (_index.md, _prefs.md, state.json, etc.).

Usage:
    shadow-init.py [--root DIR] [--reset] [--dry-run]

Exit codes:
    0  Success (possibly with warnings on stderr)
    1  Fatal error (not a git repo, permissions, etc.)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import traceback
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _astred

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_warning_count = 0
_error_count = 0
_diagnostics = []  # collect (level, msg) for end-of-run summary


def warn(msg):
    """Print a warning to stderr. Agents read these to adjust strategy."""
    global _warning_count
    _warning_count += 1
    _diagnostics.append(("warning", msg))
    print(f"[shadow-init warning] {msg}", file=sys.stderr)


def error(msg):
    """Print an error to stderr."""
    global _error_count
    _error_count += 1
    _diagnostics.append(("error", msg))
    print(f"[shadow-init error] {msg}", file=sys.stderr)


def run_git(args, cwd=None):
    """Run a git command and return stdout. Returns None on failure.

    Strips GIT_DIR / GIT_WORK_TREE from the environment so git auto-detects
    from .git (file or directory) in the cwd. This is critical in worktrees
    where inherited env vars would point git to the wrong repository.
    """
    try:
        env = os.environ.copy()
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        result = subprocess.run(
            ["git"] + args,
            capture_output=True, text=True, timeout=30, cwd=cwd, env=env,
            encoding="utf-8",
        )
        if result.returncode != 0:
            warn(f"git {' '.join(args)} failed: {result.stderr.strip()}")
            return None
        return result.stdout
    except FileNotFoundError:
        error("git is not installed or not on PATH")
        return None
    except subprocess.TimeoutExpired:
        warn(f"git {' '.join(args)} timed out after 30s")
        return None
    except Exception as e:
        warn(f"git {' '.join(args)} error: {e}")
        return None


def find_repo_root():
    """Auto-detect the git repository root. Works in regular repos, worktrees,
    and sub-worktrees. Returns None with diagnostics on failure."""
    # First, check if we're inside a git repo at all
    check = run_git(["rev-parse", "--is-inside-work-tree"])
    if check is None or check.strip() != "true":
        # Not a git repo — maybe the agent's CWD is wrong
        warn("Not inside a git work tree. "
             "If in a worktree, make sure the .git file exists and points to a valid gitdir.")
        return None

    out = run_git(["rev-parse", "--show-toplevel"])
    if out is None:
        # show-toplevel failed — common in worktrees where .git is a file
        # pointing to a non-existent or inaccessible gitdir path
        git_path = Path(".git")
        if git_path.is_file():
            gitdir_line = git_path.read_text(encoding="utf-8").strip()
            warn(f"git rev-parse --show-toplevel failed. "
                 f".git is a file (worktree): {gitdir_line}. "
                 "The gitdir path may be inaccessible (e.g., running inside Docker "
                 "with a host-side .git reference). Use --root DIR to specify the root.")
        else:
            warn("git rev-parse --show-toplevel failed. Use --root DIR to specify the root.")
        return None

    root = out.strip()
    if not root:
        warn("git rev-parse --show-toplevel returned empty. Use --root DIR to specify the root.")
        return None

    # Validate the detected root actually exists and has git
    root_path = Path(root)
    if not root_path.is_dir():
        warn(f"Detected root {root} is not a directory. Use --root DIR to override.")
        return None

    # Log whether this is a worktree
    git_obj = root_path / ".git"
    if git_obj.is_file():
        print(f"[shadow-init] Detected worktree root: {root}", file=sys.stderr)
    else:
        print(f"[shadow-init] Detected repo root: {root}", file=sys.stderr)

    return root


# ---------------------------------------------------------------------------
# File discovery
# ---------------------------------------------------------------------------

SOURCE_EXTENSIONS = {
    # Python
    ".py",
    # JavaScript / TypeScript
    ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs",
    # Java / Kotlin / Scala
    ".java", ".kt", ".kts", ".scala",
    # Go
    ".go",
    # Rust
    ".rs",
    # Ruby
    ".rb",
    # C / C++
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx",
    # C#
    ".cs",
    # PHP
    ".php",
    # Shell
    ".sh", ".bash", ".zsh",
    # Swift
    ".swift",
    # Config / data
    ".yaml", ".yml", ".toml", ".json",
}

# Basenames (no extension) that are source files
SOURCE_BASENAMES = {"Makefile", "Dockerfile", "Containerfile", "Rakefile", "Gemfile"}

EXCLUDE_DIRS = {
    "node_modules", "vendor", "venv", ".venv", "__pycache__",
    "dist", "build", "target", "out", ".shadow",
}

EXCLUDE_PATTERNS_SUFFIX = [".min.js", ".min.css", ".map", ".lock"]

# ShadowFrog's own install artifacts. A project install copies the skills and
# hook scripts into the target repo's .github/, where they are untracked source
# files that `git ls-files --others` surfaces — so init would otherwise shadow
# ShadowFrog's own internals on a user's first run. These prefixes are matched
# against the POSIX-normalized relative path.
SHADOWFROG_ARTIFACT_PREFIXES = (
    ".github/skills/shadow-frog",
    ".github/hooks/scripts/shadow-frog",
    ".claude/skills/shadow-frog",
    ".claude/hooks/scripts/shadow-frog",
)


def _is_excluded_path(rel_path):
    """Check if a path should be excluded based on directory or suffix rules."""
    norm = Path(rel_path).as_posix()
    for prefix in SHADOWFROG_ARTIFACT_PREFIXES:
        if norm.startswith(prefix):
            return True
    parts = Path(rel_path).parts
    for part in parts:
        if part in EXCLUDE_DIRS:
            return True
    for suffix in EXCLUDE_PATTERNS_SUFFIX:
        if rel_path.endswith(suffix):
            return True
    return False


def _is_source_file(rel_path):
    """Check if a file matches known source extensions or basenames."""
    p = Path(rel_path)
    if p.name in SOURCE_BASENAMES:
        return True
    return p.suffix.lower() in SOURCE_EXTENSIONS


def _walk_files(repo_root):
    """Fallback file discovery via os.walk when git is unavailable."""
    root = Path(repo_root)
    result = []
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune excluded directories and `.git` only (not all dot-dirs):
        # the git path (discover_files) includes hidden source dirs like
        # `.github/`, so the fallback must too, or shadows differ by path.
        # The later _is_source_file / _is_excluded_path filters still apply.
        dirnames[:] = [
            d for d in dirnames
            if d not in EXCLUDE_DIRS and d != ".git"
        ]
        for fname in filenames:
            full = Path(dirpath) / fname
            try:
                rel = full.relative_to(root).as_posix()
            except ValueError:
                continue
            result.append(rel)
    return result


def _load_shadowignore(shadow_dir, content=None):
    """Load .shadowignore and return a matcher function.

    Uses pathspec if available, falls back to fnmatch.
    Returns a callable(rel_path) -> bool (True = ignored).
    """
    ignore_file = shadow_dir / ".shadowignore"
    if content is None and not ignore_file.exists():
        return lambda _: False

    try:
        lines = (content if content is not None else
                 ignore_file.read_text(encoding="utf-8")).splitlines()
    except Exception as e:
        warn(f"Could not read .shadowignore: {e}")
        return lambda _: False

    patterns = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        patterns.append(stripped)

    if not patterns:
        return lambda _: False

    # Try pathspec first (proper gitignore semantics)
    try:
        import pathspec

        # Newer pathspec deprecates the "gitwildmatch" factory in favor of
        # "gitignore"; fall back to "gitwildmatch" for older versions.
        try:
            spec = pathspec.PathSpec.from_lines("gitignore", patterns)
        except (ValueError, KeyError, LookupError):
            spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        return lambda path: spec.match_file(path)
    except ImportError:
        pass
    except Exception as e:
        warn(f"pathspec library loaded but failed to parse .shadowignore: {e}. Falling back to fnmatch.")

    # Fallback: fnmatch-based matching
    import fnmatch

    def _match(rel_path):
        for pat in patterns:
            # Directory pattern: match any path component
            if pat.endswith("/"):
                dir_pat = pat.rstrip("/")
                for part in Path(rel_path).parts:
                    if fnmatch.fnmatch(part, dir_pat):
                        return True
            else:
                if fnmatch.fnmatch(rel_path, pat):
                    return True
                if fnmatch.fnmatch(Path(rel_path).name, pat):
                    return True
        return False

    return _match


def discover_files(repo_root, shadow_dir, ignore_content=None):
    """Return sorted list of relative paths to source files.

    Tries git ls-files first. Falls back to filesystem walk if git is
    unavailable (e.g., inside a Docker container where .git references
    are broken).
    """
    out = run_git(
        ["ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=repo_root,
    )
    if out is None:
        warn("git ls-files failed. Falling back to filesystem walk. "
             "This may include files that would normally be gitignored.")
        all_files = _walk_files(repo_root)
    else:
        all_files = [f for f in out.splitlines() if f.strip()]

    # Apply built-in filters
    filtered = []
    for f in all_files:
        if _is_excluded_path(f):
            continue
        if not _is_source_file(f):
            continue
        filtered.append(f)

    # Apply .shadowignore
    is_ignored = _load_shadowignore(shadow_dir, ignore_content)
    result = []
    for f in filtered:
        try:
            if is_ignored(f):
                continue
        except Exception as e:
            warn(f"Shadowignore matcher failed on '{f}': {e}. Including file anyway.")
        result.append(f)

    return sorted(set(result))


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

EXTENSION_TO_LANG = {
    ".py": "Python",
    ".js": "JavaScript", ".jsx": "JavaScript",
    ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java",
    ".kt": "Kotlin", ".kts": "Kotlin",
    ".scala": "Scala",
    ".go": "Go",
    ".rs": "Rust",
    ".rb": "Ruby",
    ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cc": "C++",
    ".hh": "C++", ".cxx": "C++", ".hxx": "C++",
    ".cs": "C#",
    ".php": "PHP",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell",
    ".swift": "Swift",
    ".yaml": "YAML", ".yml": "YAML",
    ".toml": "TOML",
    ".json": "JSON",
}

BASENAME_TO_LANG = {
    "Makefile": "Makefile",
    "Dockerfile": "Dockerfile",
    "Containerfile": "Dockerfile",
    "Rakefile": "Ruby",
    "Gemfile": "Ruby",
}


def detect_language(rel_path):
    p = Path(rel_path)
    if p.name in BASENAME_TO_LANG:
        return BASENAME_TO_LANG[p.name]
    return EXTENSION_TO_LANG.get(p.suffix.lower(), "Unknown")


# ---------------------------------------------------------------------------
# Symbol extraction
# ---------------------------------------------------------------------------

class Symbol:
    """Represents an extracted symbol."""
    __slots__ = ("name", "kind", "parent")

    def __init__(self, name, kind, parent=None):
        self.name = name          # e.g. "UserAuth" or "validate"
        self.kind = kind          # "class", "function", "method", "interface", etc.
        self.parent = parent      # fully qualified lexical parent or None

    @property
    def display_name(self):
        if self.parent:
            return f"{self.parent}.{self.name}"
        return self.name

    @property
    def heading_text(self):
        """Text for the markdown heading (backtick-wrapped)."""
        if self.is_container:
            return f"{self.kind} {self.display_name}"
        return self.display_name

    @property
    def is_container(self):
        return self.kind in ("class", "interface", "enum", "trait",
                             "struct", "protocol", "module")


# --- Per-language regex extractors ---

def _extract_python(source):
    symbols = []
    current_class = None
    class_indent = -1

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.lstrip()
            indent = len(line) - len(stripped)

            # Track class scope by indentation
            if current_class and indent <= class_indent and stripped:
                current_class = None
                class_indent = -1

            m = re.match(r'^class\s+([A-Za-z_]\w*)', stripped)
            if m:
                current_class = m.group(1)
                class_indent = indent
                symbols.append(Symbol(current_class, "class"))
                continue

            m = re.match(r'^(?:async\s+)?def\s+([A-Za-z_]\w*)', stripped)
            if m:
                name = m.group(1)
                if current_class and indent > class_indent:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                    if current_class and indent <= class_indent:
                        current_class = None
                        class_indent = -1
                continue

            # Module-level ALL_CAPS constants/globals (indent 0, not inside a
            # class). These often carry real behavioral weight (shared mutable
            # caches, sentinels, tunables). ALL_CAPS keeps this low-noise vs.
            # capturing every lowercase temp assignment.
            if current_class is None and indent == 0:
                m = re.match(
                    r'^([A-Z_][A-Z0-9_]*)\s*(?::\s*[^=]+?)?\s*=(?!=)',
                    stripped,
                )
                if m:
                    symbols.append(Symbol(m.group(1), "constant"))
                    continue
        except Exception as e:
            warn(f"Python extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_javascript(source):
    symbols = []
    current_class = None
    class_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()

            # Track braces for class scope
            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            # Class declaration
            m = re.match(r'^(?:export\s+(?:default\s+)?)?class\s+([A-Za-z_$]\w*)', stripped)
            if m:
                current_class = m.group(1)
                class_brace_depth = brace_depth
                symbols.append(Symbol(current_class, "class"))
                brace_depth += open_braces - close_braces
                continue

            # Standalone / export function
            m = re.match(r'^(?:export\s+(?:default\s+)?)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$]\w*)', stripped)
            if m:
                name = m.group(1)
                if current_class and brace_depth > class_brace_depth:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                brace_depth += open_braces - close_braces
                continue

            # Method inside class (name(...) { or async name(...) {)
            if current_class and brace_depth > class_brace_depth:
                m = re.match(r'^(?:async\s+)?(?:static\s+)?(?:get\s+|set\s+)?([A-Za-z_$]\w*)\s*\(', stripped)
                if m and m.group(1) not in ("if", "for", "while", "switch", "catch", "return", "new"):
                    symbols.append(Symbol(m.group(1), "method", parent=current_class))
                    brace_depth += open_braces - close_braces
                    continue

            # const/let/var name = (arrow function or value)
            m = re.match(r'^(?:export\s+(?:default\s+)?)?(?:const|let|var)\s+([A-Za-z_$]\w*)\s*=', stripped)
            if m:
                symbols.append(Symbol(m.group(1), "function"))
                brace_depth += open_braces - close_braces
                continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_class and brace_depth <= class_brace_depth:
                current_class = None
                class_brace_depth = 0
        except Exception as e:
            warn(f"JS/TS extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_java_like(source):
    """Java, Kotlin, C#, Scala."""
    symbols = []
    current_class = None
    class_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()
            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            # Class / interface / enum
            m = re.match(
                r'^(?:(?:public|private|protected|internal|abstract|sealed|static|final|open|data)\s+)*'
                r'(class|interface|enum)\s+([A-Za-z_]\w*)',
                stripped,
            )
            if m:
                kind = m.group(1)
                name = m.group(2)
                current_class = name
                class_brace_depth = brace_depth
                symbols.append(Symbol(name, kind))
                brace_depth += open_braces - close_braces
                continue

            # Method signatures (access modifier + return type + name)
            if current_class and brace_depth > class_brace_depth:
                m = re.match(
                    r'^(?:(?:public|private|protected|internal|abstract|static|final|override|open|suspend|virtual|async)\s+)*'
                    r'(?:(?:fun|void|int|long|float|double|boolean|char|byte|short|string|String|var|val|Task|IActionResult|'
                    r'[A-Z]\w*(?:<[^>]*>)?)\s+)'
                    r'([A-Za-z_]\w*)\s*(?:<[^>]*>)?\s*\(',
                    stripped,
                )
                if m and m.group(1) not in ("if", "for", "while", "switch", "catch", "return", "new"):
                    symbols.append(Symbol(m.group(1), "method", parent=current_class))
                    brace_depth += open_braces - close_braces
                    continue

                # Kotlin fun keyword
                m = re.match(
                    r'^(?:(?:public|private|protected|internal|abstract|override|open|suspend)\s+)*'
                    r'fun\s+(?:<[^>]*>\s*)?([A-Za-z_]\w*)',
                    stripped,
                )
                if m:
                    symbols.append(Symbol(m.group(1), "method", parent=current_class))
                    brace_depth += open_braces - close_braces
                    continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_class and brace_depth <= class_brace_depth:
                current_class = None
                class_brace_depth = 0
        except Exception as e:
            warn(f"Java/Kotlin/C# extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_go(source):
    symbols = []
    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()

            # type Foo struct/interface
            m = re.match(r'^type\s+([A-Za-z_]\w*)\s+(struct|interface)\b', stripped)
            if m:
                kind = "struct" if m.group(2) == "struct" else "interface"
                symbols.append(Symbol(m.group(1), kind))
                continue

            # func (receiver) Method(...)  — method
            m = re.match(r'^func\s+\(\s*\w+\s+\*?([A-Za-z_]\w*)\s*\)\s*([A-Za-z_]\w*)', stripped)
            if m:
                symbols.append(Symbol(m.group(2), "method", parent=m.group(1)))
                continue

            # func Foo(...)  — top-level function
            m = re.match(r'^func\s+([A-Za-z_]\w*)', stripped)
            if m:
                symbols.append(Symbol(m.group(1), "function"))
                continue
        except Exception as e:
            warn(f"Go extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_rust(source):
    symbols = []
    current_impl = None
    impl_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()
            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            # struct / enum / trait
            m = re.match(r'^(?:pub(?:\(crate\))?\s+)?(struct|enum|trait)\s+([A-Za-z_]\w*)', stripped)
            if m:
                symbols.append(Symbol(m.group(2), m.group(1)))
                brace_depth += open_braces - close_braces
                continue

            # impl Block
            m = re.match(r'^impl(?:<[^>]*>)?\s+(?:[A-Za-z_]\w*\s+for\s+)?([A-Za-z_]\w*)', stripped)
            if m:
                current_impl = m.group(1)
                impl_brace_depth = brace_depth
                brace_depth += open_braces - close_braces
                continue

            # fn
            m = re.match(r'^(?:pub(?:\(crate\))?\s+)?(?:async\s+)?(?:unsafe\s+)?(?:const\s+)?fn\s+([A-Za-z_]\w*)', stripped)
            if m:
                name = m.group(1)
                if current_impl and brace_depth > impl_brace_depth:
                    symbols.append(Symbol(name, "method", parent=current_impl))
                else:
                    symbols.append(Symbol(name, "function"))
                brace_depth += open_braces - close_braces
                continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_impl and brace_depth <= impl_brace_depth:
                current_impl = None
                impl_brace_depth = 0
        except Exception as e:
            warn(f"Rust extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_ruby(source):
    symbols = []
    current_class = None
    class_depth = 0
    depth = 0  # keyword-level nesting (class/module/def/do ... end)

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()

            # Approximate depth tracking via keywords
            opens = len(re.findall(r'\b(class|module|def|do|begin|if|unless|case|while|until|for)\b', stripped))
            closes = len(re.findall(r'\bend\b', stripped))

            m = re.match(r'^(class|module)\s+([A-Za-z_]\w*)', stripped)
            if m:
                kind = m.group(1)
                name = m.group(2)
                if kind == "class":
                    current_class = name
                    class_depth = depth
                    symbols.append(Symbol(name, "class"))
                else:
                    symbols.append(Symbol(name, "module"))
                depth += opens - closes
                continue

            m = re.match(r'^def\s+(self\.)?([A-Za-z_]\w*[!?=]?)', stripped)
            if m:
                name = m.group(2)
                if current_class and depth > class_depth:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                depth += opens - closes
                continue

            depth += opens - closes
            if depth < 0:
                depth = 0
            if current_class and depth <= class_depth:
                current_class = None
                class_depth = 0
        except Exception as e:
            warn(f"Ruby extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_c_cpp(source):
    symbols = []
    current_class = None
    class_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()
            if stripped.startswith("//") or stripped.startswith("#"):
                continue

            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            # class / struct
            m = re.match(r'^(?:template\s*<[^>]*>\s*)?(?:class|struct)\s+([A-Za-z_]\w*)', stripped)
            if m:
                current_class = m.group(1)
                class_brace_depth = brace_depth
                symbols.append(Symbol(current_class, "class"))
                brace_depth += open_braces - close_braces
                continue

            # Function/method: return_type name(...)
            m = re.match(
                r'^(?:(?:static|virtual|inline|extern|const|unsigned|signed|volatile)\s+)*'
                r'(?:[A-Za-z_]\w*(?:::[A-Za-z_]\w*)*[\s*&]*\s+)'
                r'(?:([A-Za-z_]\w*)::)?([A-Za-z_]\w*)\s*\(',
                stripped,
            )
            if m:
                scope = m.group(1)
                name = m.group(2)
                if name in ("if", "for", "while", "switch", "catch", "return", "sizeof", "typeof"):
                    brace_depth += open_braces - close_braces
                    continue
                if scope:
                    symbols.append(Symbol(name, "method", parent=scope))
                elif current_class and brace_depth > class_brace_depth:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                brace_depth += open_braces - close_braces
                continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_class and brace_depth <= class_brace_depth:
                current_class = None
                class_brace_depth = 0
        except Exception as e:
            warn(f"C/C++ extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_php(source):
    symbols = []
    current_class = None
    class_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()
            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            m = re.match(
                r'^(?:(?:abstract|final)\s+)?(?:class|interface|trait)\s+([A-Za-z_]\w*)',
                stripped,
            )
            if m:
                current_class = m.group(1)
                class_brace_depth = brace_depth
                symbols.append(Symbol(current_class, "class"))
                brace_depth += open_braces - close_braces
                continue

            m = re.match(
                r'^(?:(?:public|private|protected|static|abstract|final)\s+)*function\s+([A-Za-z_]\w*)',
                stripped,
            )
            if m:
                name = m.group(1)
                if current_class and brace_depth > class_brace_depth:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                brace_depth += open_braces - close_braces
                continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_class and brace_depth <= class_brace_depth:
                current_class = None
                class_brace_depth = 0
        except Exception as e:
            warn(f"PHP extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_shell(source):
    symbols = []
    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()

            # function foo { ... }
            m = re.match(r'^function\s+([A-Za-z_]\w*)', stripped)
            if m:
                symbols.append(Symbol(m.group(1), "function"))
                continue

            # foo() { ... }
            m = re.match(r'^([A-Za-z_]\w*)\s*\(\s*\)', stripped)
            if m:
                symbols.append(Symbol(m.group(1), "function"))
                continue
        except Exception as e:
            warn(f"Shell extractor: error at line {line_num}: {e}")
            continue

    return symbols


def _extract_swift(source):
    symbols = []
    current_class = None
    class_brace_depth = 0
    brace_depth = 0

    for line_num, line in enumerate(source.splitlines(), 1):
        try:
            stripped = line.strip()
            open_braces = stripped.count("{")
            close_braces = stripped.count("}")

            # class / struct / enum / protocol
            m = re.match(
                r'^(?:(?:public|private|fileprivate|internal|open|final)\s+)*'
                r'(class|struct|enum|protocol)\s+([A-Za-z_]\w*)',
                stripped,
            )
            if m:
                kind = m.group(1)
                name = m.group(2)
                current_class = name
                class_brace_depth = brace_depth
                symbols.append(Symbol(name, kind))
                brace_depth += open_braces - close_braces
                continue

            # func
            m = re.match(
                r'^(?:(?:public|private|fileprivate|internal|open|static|override|class|mutating)\s+)*'
                r'func\s+([A-Za-z_]\w*)',
                stripped,
            )
            if m:
                name = m.group(1)
                if current_class and brace_depth > class_brace_depth:
                    symbols.append(Symbol(name, "method", parent=current_class))
                else:
                    symbols.append(Symbol(name, "function"))
                brace_depth += open_braces - close_braces
                continue

            brace_depth += open_braces - close_braces
            if brace_depth < 0:
                brace_depth = 0
            if current_class and brace_depth <= class_brace_depth:
                current_class = None
                class_brace_depth = 0
        except Exception as e:
            warn(f"Swift extractor: error at line {line_num}: {e}")
            continue

    return symbols


# Dispatcher
EXTRACTORS = {
    "Python": _extract_python,
    "JavaScript": _extract_javascript,
    "TypeScript": _extract_javascript,
    "Java": _extract_java_like,
    "Kotlin": _extract_java_like,
    "C#": _extract_java_like,
    "Scala": _extract_java_like,
    "Go": _extract_go,
    "Rust": _extract_rust,
    "Ruby": _extract_ruby,
    "C": _extract_c_cpp,
    "C++": _extract_c_cpp,
    "PHP": _extract_php,
    "Shell": _extract_shell,
    "Swift": _extract_swift,
}


def extract_symbols(source, language, rel_path="<unknown>"):
    """Extract symbols from source code. Returns list of Symbol objects.

    Never raises — returns empty list on any failure, with diagnostic warnings.
    """
    extractor = EXTRACTORS.get(language)
    if extractor is None:
        return []
    try:
        symbols = extractor(source)
        # Validate symbols — catch malformed Symbol objects
        for s in symbols:
            if not isinstance(s.name, str) or not s.name:
                warn(f"Symbol extractor for {language} produced empty name in {rel_path}. Skipping symbol.")
                symbols = [s for s in symbols if isinstance(s.name, str) and s.name]
                break
        return symbols
    except re.error as e:
        warn(f"Regex error during {language} symbol extraction for {rel_path}: {e}. "
             "This may indicate unusual syntax in the source file. Returning no symbols.")
        return []
    except Exception as e:
        warn(f"Symbol extraction failed for {rel_path} ({language}): {type(e).__name__}: {e}. "
             "The shadow file will still be created with a File-Level section only.")
        return []


def _read_json_object(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Cannot read {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return value


def configured_backend(root):
    state_path = root / ".shadow" / "_meta" / "state.json"
    metadata = (_read_json_object(state_path).get("symbol_backend", {"name": "regex"})
                if state_path.exists() else {"name": "regex"})
    if not isinstance(metadata, dict) or metadata.get("name") not in {"regex", "astred"}:
        raise ValueError(f"Invalid symbol_backend in {state_path}.")
    return metadata


def _inventory_symbol(record):
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("name"), str) or not record["name"]
        or not isinstance(record.get("kind"), str)
        or (record.get("parent") is not None and not isinstance(record["parent"], str))
    ):
        raise ValueError("Invalid symbol record in extraction inventory.")
    symbol = Symbol(record["name"], record["kind"], record.get("parent"))
    if record.get("qualified_name") != symbol.display_name:
        raise ValueError("Inconsistent qualified name in extraction inventory.")
    return symbol


def extract_inventory(repo_root, symbol_backend=None, astred_command="astred",
                      astred_timeout=180, preview=False, ignore_content=None):
    """Shared, read-only inventory for init, incremental updates and migration review."""
    root = Path(repo_root).resolve()
    shadow_dir = root / ".shadow"
    configured = configured_backend(root)
    backend = symbol_backend or configured["name"]
    if backend not in {"regex", "astred"}:
        raise ValueError(f"Unknown symbol backend: {backend}.")
    if backend != configured["name"] and shadow_dir.exists() and not preview:
        raise ValueError("Changing symbol backends requires --preview-migration first.")
    paths = discover_files(str(root), shadow_dir, ignore_content)
    hashes = {path: _astred.source_hash(root / path) for path in paths}
    python_paths = [path for path in paths if detect_language(path) == "Python"]
    python_symbols = {}
    metadata = {"name": backend}
    if backend == "astred":
        expected = configured if configured["name"] == "astred" and not preview else None
        cache_path = shadow_dir / "_meta" / "symbols.json"
        if expected and cache_path.exists():
            cache = _read_json_object(cache_path)
            if cache.get("schema_version") != 1 or not isinstance(cache.get("files"), list):
                raise ValueError(f"Invalid symbol cache {cache_path}; remove only this cache and retry.")
            cache_metadata = cache.get("symbol_backend", {})
            if not isinstance(cache_metadata, dict) or cache_metadata.get("name") not in {"regex", "astred"}:
                raise ValueError(f"Invalid backend metadata in symbol cache {cache_path}.")
            if (
                cache_metadata.get("name") == configured["name"]
                and all(cache_metadata.get(key) == configured.get(key) for key in _astred.IDENTITY_KEYS)
                and cache_metadata.get("normalizer_version") == _astred.NORMALIZER_VERSION
            ):
                for entry in cache["files"]:
                    if not isinstance(entry, dict):
                        raise ValueError(f"Invalid file entry in {cache_path}.")
                    path = entry.get("path")
                    if (
                        path in python_paths and entry.get("backend") == "astred"
                        and entry.get("source_sha256") == hashes[path]
                    ):
                        if path in python_symbols or not isinstance(entry.get("symbols"), list):
                            raise ValueError(f"Invalid or duplicate cached Python file: {path}.")
                        for record in entry["symbols"]:
                            _inventory_symbol(record)
                            if (
                                record["kind"] not in {"class", "function", "method", "constant"}
                                or any(type(record.get(key)) is not int or record[key] < 1
                                       for key in ("line", "column"))
                            ):
                                raise ValueError(f"Invalid cached Astred declaration in {path}.")
                        python_symbols[path] = entry["symbols"]
                metadata = dict(cache_metadata)
            else:
                warn("Symbol cache backend/version differs from configuration; re-extracting Python.")
        pending = [path for path in python_paths if path not in python_symbols]
        if pending or metadata == {"name": "astred"}:
            extracted, metadata = _astred.extract(
                root, pending, command=astred_command, timeout=astred_timeout,
                expected_metadata=expected, on_warning=warn,
            )
            python_symbols.update(extracted)
        fallback_languages = sorted({detect_language(path) for path in paths if path not in python_paths})
        metadata["fallback_languages"] = fallback_languages
        if fallback_languages:
            warn("Astred supports Python in this integration; using regex for: "
                 + ", ".join(fallback_languages))

    records = []
    for path in paths:
        language = detect_language(path)
        if backend == "astred" and language == "Python":
            symbols = python_symbols[path]
        else:
            source = (root / path).read_text(encoding="utf-8", errors="replace")
            symbols = [
                {"name": symbol.name, "kind": symbol.kind, "parent": symbol.parent,
                 "qualified_name": symbol.display_name}
                for symbol in extract_symbols(source, language, path)
            ]
        for record in symbols:
            record["heading"] = _inventory_symbol(record).heading_text
        if _astred.source_hash(root / path) != hashes[path]:
            raise ValueError(f"Source changed during symbol extraction: {path}. Retry.")
        records.append({
            "path": path, "language": language,
            "backend": "astred" if backend == "astred" and language == "Python" else "regex",
            "source_sha256": hashes[path], "symbols": symbols,
        })
    return {"schema_version": 1, "symbol_backend": metadata, "files": records}


def preview_migration(repo_root, inventory):
    """Propose address changes without moving/deleting any existing knowledge."""
    shadow_dir = Path(repo_root).resolve() / ".shadow"
    if not shadow_dir.is_dir():
        raise ValueError("--preview-migration requires an existing .shadow/.")
    previous = {}
    for path in sorted(shadow_dir.rglob("*.md")):
        relative = path.relative_to(shadow_dir)
        if relative.parts[0] in {"_meta", "_cross", "_dreams", "_index.md", "_prefs.md"}:
            continue
        content = path.read_text(encoding="utf-8")
        source_path = relative.as_posix()[:-3]
        if not content.startswith(f"# Shadow: {source_path}\n"):
            raise ValueError(f"Unexpected shadow header in {path}; review it before migration.")
        headings = []
        fence = None
        for line in content.splitlines():
            stripped = line.strip()
            if fence is not None:
                if re.fullmatch(re.escape(fence[0]) + "{" + str(len(fence)) + ",}", stripped):
                    fence = None
                continue
            opening = re.match(r"`{3,}|~{3,}", stripped)
            if opening:
                fence = opening.group(0)
                continue
            heading = re.fullmatch(r"#{2,3}\s+`([^`]+)`\s*", line)
            if heading:
                headings.append(heading.group(1))
        previous[source_path] = Counter(
            re.sub(r"^(?:class|interface|enum|trait|struct|protocol|module)\s+", "", heading)
            for heading in headings
        )
    proposed = {entry["path"]: Counter(record["qualified_name"] for record in entry["symbols"])
                for entry in inventory["files"]}
    changes = []
    for path in sorted(previous.keys() | proposed.keys()):
        old, new = previous.get(path, Counter()), proposed.get(path, Counter())
        removed, added = old - new, new - old
        if not removed and not added and path in previous and path in proposed:
            continue
        scope_changes, ambiguous = [], []
        leaves = {name.rsplit(".", 1)[-1] for name in removed}
        for leaf in sorted(leaves):
            old_names = [name for name in removed if name.rsplit(".", 1)[-1] == leaf]
            new_names = [name for name in added if name.rsplit(".", 1)[-1] == leaf]
            if not new_names:
                continue
            if len(old_names) == len(new_names) == 1 and removed[old_names[0]] == added[new_names[0]] == 1:
                scope_changes.append({"from": old_names[0], "to": new_names[0]})
                del removed[old_names[0]]
                del added[new_names[0]]
            else:
                ambiguous.append({"existing": list(Counter({name: removed[name] for name in old_names}).elements()),
                                  "proposed": list(Counter({name: added[name] for name in new_names}).elements())})
        changes.append({
            "path": path, "scope_change_candidates": scope_changes,
            "retained_unmatched": sorted(removed.elements()), "added": sorted(added.elements()),
            "ambiguous": ambiguous, "source_missing": path not in proposed,
        })
    return {"applied": False, "review_required": True, "files": changes}


# ---------------------------------------------------------------------------
# Shadow file generation
# ---------------------------------------------------------------------------

def get_last_modified(rel_path, repo_root):
    """Get the last commit date for a file via git log.

    Returns a date string like '2025-01-15', or 'unknown' on any failure.
    """
    try:
        out = run_git(["log", "-1", "--format=%ci", "--", rel_path], cwd=repo_root)
        if out and out.strip():
            # Format: "2025-01-15 10:00:00 -0500" → take date part
            parts = out.strip().split(" ")
            if parts and len(parts[0]) >= 8:
                return parts[0]
            warn(f"Unexpected git log date format for {rel_path}: '{out.strip()}'")
        return "unknown"
    except Exception as e:
        warn(f"Could not get last modified date for {rel_path}: {e}")
        return "unknown"


def count_lines(source):
    return len(source.splitlines())


def build_shadow_content(rel_path, language, line_count, last_modified, symbols):
    """Build the markdown content for a per-file shadow.

    Never raises — returns a minimal valid shadow on any failure.
    """
    try:
        lines = []
        lines.append(f"# Shadow: {rel_path}")
        lines.append("")
        lines.append(f"**Language**: {language} | **Lines**: {line_count} | **Last modified**: {last_modified}")
        lines.append("")
        lines.append("## File-Level")
        lines.append("")
        lines.append("_No discoveries yet._")

        # Group symbols: containers with their children
        i = 0
        while i < len(symbols):
            try:
                sym = symbols[i]
                if sym.parent is None:
                    lines.append("")
                    lines.append(f"## `{sym.heading_text}`")
                    if sym.is_container:
                        # Collect child symbols belonging to this container
                        children = []
                        j = i + 1
                        while j < len(symbols) and symbols[j].parent == sym.display_name:
                            children.append(symbols[j])
                            j += 1
                        if not children:
                            lines.append("")
                            lines.append("_No discoveries yet._")
                        else:
                            for child in children:
                                lines.append("")
                                lines.append(f"### `{child.heading_text}`")
                                lines.append("")
                                lines.append("_No discoveries yet._")
                        i = j
                        continue
                    else:
                        lines.append("")
                        lines.append("_No discoveries yet._")
                else:
                    # Orphan nested symbol (parent wasn't found as container)
                    lines.append("")
                    lines.append(f"### `{sym.heading_text}`")
                    lines.append("")
                    lines.append("_No discoveries yet._")
            except Exception as e:
                warn(f"Error formatting symbol #{i} in {rel_path}: {e}. Skipping this symbol.")
            i += 1

        lines.append("")
        lines.append("## Cross-References")
        lines.append("")
        lines.append("_No cross-cutting discoveries yet._")
        lines.append("")

        return "\n".join(lines)

    except Exception as e:
        # Fallback: return a minimal valid shadow with no symbols
        warn(f"Failed to build shadow content for {rel_path}: {type(e).__name__}: {e}. "
             "Creating minimal shadow with File-Level section only.")
        return (
            f"# Shadow: {rel_path}\n\n"
            f"**Language**: {language} | **Lines**: {line_count} | **Last modified**: {last_modified}\n\n"
            f"## File-Level\n\n"
            f"_No discoveries yet._\n\n"
            f"## Cross-References\n\n"
            f"_No cross-cutting discoveries yet._\n"
        )


# ---------------------------------------------------------------------------
# Scaffolding
# ---------------------------------------------------------------------------

SHADOWIGNORE_CONTENT = """\
# Directories
node_modules/
vendor/
venv/
.venv/
__pycache__/
dist/
build/
target/
out/

# Generated / minified
*.min.js
*.min.css
*.map
*.lock

# Binary
*.png
*.jpg
*.gif
*.ico
*.woff
*.woff2
*.ttf
*.eot
*.pdf
*.zip
*.tar.gz

# The shadow itself
.shadow/

# ShadowFrog's own install artifacts (project install copies these here)
.github/skills/shadow-frog*/
.github/hooks/scripts/shadow-frog-*
.claude/skills/shadow-frog*/
.claude/hooks/scripts/shadow-frog-*
"""

PREFS_CONTENT = """\
# Preferences

_No preferences recorded yet._
"""


def build_state_json(total_files, total_symbols, last_commit):
    """Build state.json dict. last_commit should be a 40-char SHA or "none"
    (sentinel for non-git or empty repos). Downstream hooks rely on "none"
    to distinguish missing-commit from empty-string."""
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        now = "unknown"
    return {
        "version": 1,
        "initialized_at": now,
        "last_update_at": now,
        "last_commit": last_commit or "none",
        "last_update_type": "init",
        "total_files": total_files,
        "total_symbols": total_symbols,
        "total_discoveries": 0,
        "dream_cycles_completed": 0,
    }


def build_index(file_records, total_symbols, total_discoveries=0, cross_cutting=0):
    """Build _index.md content.

    file_records: list of (rel_path, language, symbols_list)
    Never raises — returns a valid index even if some rows fail.
    """
    try:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        now = "unknown"
    total_files = len(file_records)

    lines = []
    lines.append("# Shadow Index")
    lines.append("")
    lines.append(
        f"> Generated by shadow-frog-init on {now}"
    )
    lines.append(
        f"> Total files: {total_files} | Symbols: {total_symbols} "
        f"| Discoveries: {total_discoveries} | Cross-cutting: {cross_cutting}"
    )
    lines.append("")
    lines.append("| File | Language | Symbols | Discoveries |")
    lines.append("|------|----------|---------|-------------|")

    for rel_path, language, symbols in file_records:
        try:
            sym_count = len(symbols)
            if sym_count == 0:
                sym_display = "0"
            else:
                names = []
                for s in symbols:
                    if s.parent is None:
                        names.append(s.name)
                if not names:
                    names = [s.display_name for s in symbols]
                if len(names) <= 3:
                    sym_display = f"{sym_count} ({', '.join(names)})"
                else:
                    sym_display = f"{sym_count} ({', '.join(names[:3])}, ...)"

            lines.append(f"| {rel_path} | {language} | {sym_display} | 0 |")
        except Exception as e:
            warn(f"Failed to build index row for {rel_path}: {e}. Using fallback row.")
            lines.append(f"| {rel_path} | {language} | ? | 0 |")

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main logic
# ---------------------------------------------------------------------------

def init_shadow(repo_root, reset=False, dry_run=False, symbol_backend="regex",
                astred_command="astred", astred_timeout=180):
    """Initialize the .shadow/ knowledge base.

    Returns True on success (even with non-fatal warnings), False on fatal errors.
    Designed to never crash — all sections are independently protected.
    """
    root = Path(repo_root).resolve()
    shadow_dir = root / ".shadow"
    inventory = None

    if shadow_dir.exists() and not reset:
        error(f".shadow/ already exists at {shadow_dir}. "
              "Use --preview-migration to review a backend change without deleting knowledge, "
              "or --reset to delete and recreate.")
        return False
    if symbol_backend == "astred":
        try:
            inventory = extract_inventory(
                root, symbol_backend, astred_command, astred_timeout,
                preview=True, ignore_content=SHADOWIGNORE_CONTENT,
            )
        except (_astred.AstredError, ValueError, OSError) as exc:
            error(str(exc))
            return False

    # Handle existing shadow
    if shadow_dir.exists():
        if dry_run:
            print(f"[dry-run] Would delete {shadow_dir}")
        else:
            try:
                shutil.rmtree(shadow_dir)
            except PermissionError as e:
                error(f"Permission denied removing .shadow/: {e}. "
                      "Check file ownership and permissions.")
                return False
            except Exception as e:
                error(f"Failed to remove existing .shadow/: {type(e).__name__}: {e}")
                return False

    # Create base directories
    dirs_to_create = [
        shadow_dir,
        shadow_dir / "_meta",
        shadow_dir / "_cross",
        shadow_dir / "_dreams",
    ]
    if dry_run:
        for d in dirs_to_create:
            print(f"[dry-run] Would create directory {d.relative_to(root)}")
    else:
        for d in dirs_to_create:
            try:
                d.mkdir(parents=True, exist_ok=True)
            except PermissionError as e:
                error(f"Permission denied creating {d}: {e}. "
                      "Check write permissions on the repository root.")
                return False
            except Exception as e:
                error(f"Failed to create directory {d}: {type(e).__name__}: {e}")
                return False

    # Write .shadowignore first (needed for file discovery filtering)
    shadowignore_path = shadow_dir / ".shadowignore"
    if dry_run:
        print(f"[dry-run] Would create {shadowignore_path.relative_to(root)}")
    else:
        try:
            shadowignore_path.write_text(SHADOWIGNORE_CONTENT, encoding="utf-8")
        except Exception as e:
            error(f"Failed to write .shadowignore: {e}. "
                  "Cannot proceed without this file.")
            return False

    # Create empty _dreams/_index.md scaffold with the 7-column schema
    # the reconciler/lineage parser expect. The extra columns (branch,
    # parent, tip_commit) stay blank for the no-dream initial state but
    # the header alignment is what dream-reconcile.py / dream-lineage.py
    # validate against.
    dreams_index = shadow_dir / "_dreams" / "_index.md"
    if dry_run:
        print(f"[dry-run] Would create {dreams_index.relative_to(root)}")
    else:
        try:
            dreams_index.write_text(
                "# Dream Experiment Archive\n\n"
                "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
                "|----------|----------|---------|-------|--------|--------|------------|\n",
                encoding="utf-8"
            )
        except Exception as e:
            warn(f"Failed to write _dreams/_index.md: {e}. "
                 "Dream archive will be created on first dream run.")

    # Discover files
    try:
        source_files = ([entry["path"] for entry in inventory["files"]] if inventory is not None
                        else discover_files(str(root), shadow_dir))
    except Exception as e:
        error(f"File discovery crashed: {type(e).__name__}: {e}. "
              "Is this a valid git repository?")
        source_files = []
    if not source_files:
        warn("No source files found. The shadow will be empty. "
             "Check that the repository has committed or tracked files, "
             "and that .shadowignore isn't excluding everything.")

    # Get latest commit
    last_commit_out = run_git(["rev-parse", "HEAD"], cwd=str(root))
    last_commit = last_commit_out.strip() if last_commit_out else "none"

    # Process each file
    file_records = []     # (rel_path, language, symbols)
    total_symbols = 0
    language_counts = defaultdict(int)
    skipped_files = []
    inventory_files = {entry["path"]: entry for entry in inventory["files"]} if inventory else {}

    for rel_path in source_files:
        try:
            abs_path = root / rel_path
            language = detect_language(rel_path)
            language_counts[language] += 1

            # Read file content
            try:
                source = abs_path.read_text(encoding="utf-8", errors="replace")
            except PermissionError:
                warn(f"Permission denied reading {rel_path}. Skipping.")
                skipped_files.append((rel_path, "permission denied"))
                continue
            except OSError as e:
                warn(f"OS error reading {rel_path}: {e}. "
                     "File may have been deleted since discovery. Skipping.")
                skipped_files.append((rel_path, str(e)))
                continue

            line_count = count_lines(source)
            if inventory is not None:
                if _astred.source_hash(abs_path) != inventory_files[rel_path]["source_sha256"]:
                    error(f"Source changed since extraction: {rel_path}. Retry initialization.")
                    return False
                symbols = [_inventory_symbol(record) for record in inventory_files[rel_path]["symbols"]]
            else:
                symbols = extract_symbols(source, language, rel_path)
            total_symbols += len(symbols)

            last_modified = get_last_modified(rel_path, str(root))

            shadow_content = build_shadow_content(
                rel_path, language, line_count, last_modified, symbols,
            )

            # Determine shadow file path
            shadow_file = shadow_dir / (rel_path + ".md")

            if dry_run:
                print(f"[dry-run] Would create {shadow_file.relative_to(root)}  "
                      f"({language}, {len(symbols)} symbols)")
            else:
                try:
                    shadow_file.parent.mkdir(parents=True, exist_ok=True)
                    shadow_file.write_text(shadow_content, encoding="utf-8")
                except PermissionError:
                    warn(f"Permission denied writing shadow for {rel_path}. Skipping.")
                    skipped_files.append((rel_path, "write permission denied"))
                    continue
                except OSError as e:
                    warn(f"OS error writing shadow for {rel_path}: {e}. "
                         "Path may be too long or contain invalid characters. Skipping.")
                    skipped_files.append((rel_path, str(e)))
                    continue

            file_records.append((rel_path, language, symbols))

        except Exception as e:
            warn(f"Unexpected error processing {rel_path}: {type(e).__name__}: {e}. "
                 "Skipping this file. Other files will still be processed.")
            skipped_files.append((rel_path, f"unexpected: {e}"))
            continue

    # Write _prefs.md
    prefs_path = shadow_dir / "_prefs.md"
    if dry_run:
        print(f"[dry-run] Would create {prefs_path.relative_to(root)}")
    else:
        try:
            prefs_path.write_text(PREFS_CONTENT, encoding="utf-8")
        except Exception as e:
            warn(f"Failed to write _prefs.md: {e}. "
                 "You can create this file manually: '# Preferences\\n\\n_No preferences recorded yet._'")

    # Write _index.md
    try:
        index_content = build_index(file_records, total_symbols)
    except Exception as e:
        warn(f"Failed to build index content: {type(e).__name__}: {e}. Writing minimal index.")
        index_content = "# Shadow Index\n\n> Index generation failed. Run shadow-frog-update to rebuild.\n"
    index_path = shadow_dir / "_index.md"
    if dry_run:
        print(f"[dry-run] Would create {index_path.relative_to(root)}")
    else:
        try:
            index_path.write_text(index_content, encoding="utf-8")
        except Exception as e:
            warn(f"Failed to write _index.md: {e}")

    # Write state.json
    try:
        state = build_state_json(len(file_records), total_symbols, last_commit)
    except Exception as e:
        warn(f"Failed to build state.json content: {e}. Writing minimal state.")
        state = {"version": 1, "last_update_type": "init",
                 "total_files": len(file_records), "total_symbols": total_symbols,
                 "total_discoveries": 0, "dream_cycles_completed": 0}
    state_path = shadow_dir / "_meta" / "state.json"
    if inventory is not None:
        state["symbol_backend"] = inventory["symbol_backend"]
    if dry_run:
        print(f"[dry-run] Would create {state_path.relative_to(root)}")
        print(f"[dry-run] state.json: {json.dumps(state, indent=2)}")
    else:
        try:
            state_path.write_text(
                json.dumps(state, indent=2) + "\n", encoding="utf-8",
            )
        except Exception as e:
            if inventory is not None:
                error(f"Failed to persist Astred backend metadata: {e}")
                return False
            warn(f"Failed to write state.json: {e}. "
                 "The shadow is usable but state tracking will be incomplete.")
        if inventory is not None:
            try:
                (shadow_dir / "_meta" / "symbols.json").write_text(
                    json.dumps(inventory, indent=2) + "\n", encoding="utf-8",
                )
            except OSError as exc:
                warn(f"Could not write symbol cache; future updates will re-extract: {exc}")

    # --- Summary ---
    total_files = len(file_records)
    try:
        lang_summary = ", ".join(
            f"{lang} ({count})"
            for lang, count in sorted(language_counts.items(), key=lambda x: -x[1])
        )
    except Exception:
        lang_summary = f"{len(language_counts)} languages"

    print("")
    if dry_run:
        print("[dry-run] Shadow would be initialized.")
    else:
        print("Shadow initialized.")
    print(f"  Files:     {total_files}")
    print(f"  Symbols:   {total_symbols}")
    print(f"  Languages: {lang_summary or 'none'}")
    if inventory is not None:
        print(f"  Backend:   Astred {inventory['symbol_backend']['astred_version']} "
              "(Python; regex for other languages)")

    if skipped_files:
        print(f"  Skipped:   {len(skipped_files)} files (see warnings above)")
        # List skipped files for agent diagnosis
        for path, reason in skipped_files:
            print(f"    - {path}: {reason}", file=sys.stderr)

    if _warning_count or _error_count:
        print(f"  Diagnostics: {_warning_count} warnings, {_error_count} errors")

    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Initialize a .shadow/ knowledge base for any codebase.",
        prog="shadow-init.py",
    )
    parser.add_argument(
        "--root",
        metavar="DIR",
        default=None,
        help="Repository root (default: auto-detect via git)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete existing .shadow/ and recreate",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be created without writing",
    )
    parser.add_argument("--symbol-backend", choices=("regex", "astred"), default=None,
                        help="Symbol backend (default: state.json setting, otherwise regex)")
    parser.add_argument("--astred-command", default=os.environ.get("SHADOWFROG_ASTRED_COMMAND", "astred"),
                        help="Astred executable path (or SHADOWFROG_ASTRED_COMMAND)")
    parser.add_argument("--astred-timeout", type=int, default=180,
                        help="Astred batch timeout in seconds (default: 180)")
    parser.add_argument("--symbols-only", action="store_true",
                        help="Print the shared symbol inventory as JSON; do not write .shadow/")
    parser.add_argument("--preview-migration", action="store_true",
                        help="Print JSON inventory and address-change candidates; never rewrite knowledge")

    args = parser.parse_args()
    if args.astred_timeout <= 0:
        parser.error("--astred-timeout must be positive")
    if args.reset and (args.symbols_only or args.preview_migration):
        parser.error("--reset cannot be combined with --symbols-only or --preview-migration")

    try:
        # Determine repo root
        repo_root = args.root
        if repo_root is None:
            repo_root = find_repo_root()
            if repo_root is None:
                error("Could not detect git repository root. "
                      "Make sure you are inside a git repository, or use --root DIR to specify.")
                sys.exit(1)

        repo_root = str(Path(repo_root).resolve())
        if not Path(repo_root).is_dir():
            error(f"Root directory does not exist: {repo_root}")
            sys.exit(1)

        if args.symbols_only or args.preview_migration:
            if args.preview_migration and not (Path(repo_root) / ".shadow").is_dir():
                raise ValueError("--preview-migration requires an existing .shadow/.")
            inventory = extract_inventory(
                repo_root, args.symbol_backend, args.astred_command, args.astred_timeout,
                preview=args.preview_migration,
                ignore_content=SHADOWIGNORE_CONTENT if not (Path(repo_root) / ".shadow").exists() else None,
            )
            if args.preview_migration:
                inventory["migration"] = preview_migration(repo_root, inventory)
            print(json.dumps(inventory, indent=2))
            sys.exit(0)
        backend = args.symbol_backend or configured_backend(Path(repo_root))["name"]
        success = init_shadow(
            repo_root, reset=args.reset, dry_run=args.dry_run, symbol_backend=backend,
            astred_command=args.astred_command, astred_timeout=args.astred_timeout,
        )
        sys.exit(0 if success else 1)

    except (_astred.AstredError, ValueError, OSError) as exc:
        error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        error("Interrupted by user.")
        sys.exit(130)
    except Exception as e:
        # Top-level catch-all — should never fire, but if it does,
        # give the agent maximum diagnostic context
        error(f"Unexpected top-level crash: {type(e).__name__}: {e}")
        print("\n--- Full traceback (for agent diagnosis) ---", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        print("--- End traceback ---\n", file=sys.stderr)
        print(
            "[shadow-init guidance] This is a bug in shadow-init.py. "
            "As a workaround, you can initialize .shadow/ manually by following "
            "the instructions in shadow-frog-init/SKILL.md under 'Fallback: Manual Init'.",
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
