"""Tests for `skills/shadow-frog-init/shadow-init.py`.

Philosophy: USE REAL FILES, REAL GIT, REAL SUBPROCESSES (per
`minimal-mocking-tests`). The script is loaded as a module via the
`shadow_init` fixture (importlib loader path) for in-process tests, and
exercised end-to-end via subprocess for the CLI argparse dispatcher.

Categories:
  * Pure-function tests (no I/O): Symbol formatting, language detection,
    path filters, language extractors. Parametrized aggressively.
  * Filesystem tests using `tmp_path` / `tmp_git_repo`: walk, shadowignore
    loading, end-to-end file discovery.
  * Scaffolding tests: build_state_json (B2 regression), build_shadow_content,
    build_index.
  * CLI integration tests (marked slow + integration): full subprocess
    invocations covering --dry-run, --reset, and the empty-repo B2 case.

B2 regression: `last_commit` must NEVER be `""`. It should always be either
a 40-char SHA or the sentinel string `"none"` (downstream hooks rely on
that distinction).
"""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "skills/shadow-frog-init/shadow-init.py"


def _to_tuples(symbols):
    """Flatten a list of Symbol objects to (name, kind, parent) tuples."""
    return [(s.name, s.kind, s.parent) for s in symbols]


def _run_shadow_init(repo_root, *args, timeout=60):
    """Invoke shadow-init.py as a subprocess, returning the CompletedProcess."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(repo_root), *args],
        capture_output=True, text=True, timeout=timeout, cwd=str(repo_root),
    )


# ---------------------------------------------------------------------------
# Symbol class
# ---------------------------------------------------------------------------

def test_symbol_display_name_top_level(shadow_init):
    s = shadow_init.Symbol("foo", "function")
    assert s.display_name == "foo"
    assert s.heading_text == "foo"
    assert s.is_container is False


def test_symbol_display_name_method_has_parent_dot(shadow_init):
    s = shadow_init.Symbol("validate", "method", parent="UserAuth")
    assert s.display_name == "UserAuth.validate"
    assert s.heading_text == "UserAuth.validate"


@pytest.mark.parametrize("kind,name,expected_heading,container", [
    ("class", "Foo", "class Foo", True),
    ("interface", "IBar", "interface IBar", True),
    ("enum", "Color", "enum Color", True),
    ("trait", "Greet", "trait Greet", True),
    ("struct", "Point", "struct Point", True),
    ("protocol", "Encodable", "protocol Encodable", True),
    ("module", "Utils", "module Utils", True),
    ("function", "main", "main", False),
])
def test_symbol_heading_and_container_per_kind(shadow_init, kind, name, expected_heading, container):
    s = shadow_init.Symbol(name, kind)
    assert s.heading_text == expected_heading
    assert s.is_container is container


# ---------------------------------------------------------------------------
# detect_language
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rel_path,expected", [
    ("foo.py", "Python"),
    ("src/main.py", "Python"),
    ("foo.js", "JavaScript"),
    ("foo.jsx", "JavaScript"),
    ("foo.mjs", "JavaScript"),
    ("foo.cjs", "JavaScript"),
    ("foo.ts", "TypeScript"),
    ("foo.tsx", "TypeScript"),
    ("Foo.java", "Java"),
    ("Foo.kt", "Kotlin"),
    ("Foo.kts", "Kotlin"),
    ("Foo.scala", "Scala"),
    ("foo.go", "Go"),
    ("foo.rs", "Rust"),
    ("foo.rb", "Ruby"),
    ("foo.c", "C"),
    ("foo.h", "C"),
    ("foo.cpp", "C++"),
    ("foo.hpp", "C++"),
    ("foo.cs", "C#"),
    ("foo.php", "PHP"),
    ("foo.sh", "Shell"),
    ("foo.bash", "Shell"),
    ("foo.zsh", "Shell"),
    ("foo.swift", "Swift"),
    ("foo.yaml", "YAML"),
    ("foo.yml", "YAML"),
    ("foo.toml", "TOML"),
    ("foo.json", "JSON"),
])
def test_detect_language_by_extension(shadow_init, rel_path, expected):
    assert shadow_init.detect_language(rel_path) == expected


@pytest.mark.parametrize("basename,expected", [
    ("Makefile", "Makefile"),
    ("Dockerfile", "Dockerfile"),
    ("Containerfile", "Dockerfile"),
    ("Rakefile", "Ruby"),
    ("Gemfile", "Ruby"),
])
def test_detect_language_by_basename(shadow_init, basename, expected):
    # Basename works both bare and in a subdirectory
    assert shadow_init.detect_language(basename) == expected
    assert shadow_init.detect_language(f"path/to/{basename}") == expected


@pytest.mark.parametrize("rel_path", [
    "foo.xyz",
    "README",
    "weird.exe",
    "no_extension_file",
])
def test_detect_language_unknown_returns_unknown(shadow_init, rel_path):
    assert shadow_init.detect_language(rel_path) == "Unknown"


# ---------------------------------------------------------------------------
# _is_excluded_path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,is_excluded", [
    # Excluded directory components
    ("node_modules/foo/bar.js", True),
    ("foo/node_modules/bar.js", True),
    ("vendor/lib.js", True),
    ("__pycache__/foo.pyc", True),
    ("dist/bundle.js", True),
    ("build/output.txt", True),
    ("target/release/app", True),
    ("out/main.js", True),
    (".venv/lib/site-packages/foo.py", True),
    ("venv/bin/python", True),
    (".shadow/foo.md", True),
    # Excluded suffixes
    ("foo.min.js", True),
    ("path/foo.min.css", True),
    ("foo.map", True),
    ("Pipfile.lock", True),
    # ShadowFrog's own install artifacts (project install copies these in)
    (".github/skills/shadow-frog/SKILL.md", True),
    (".github/skills/shadow-frog-init/shadow-init.py", True),
    (".github/hooks/scripts/shadow-frog-pre-tool.sh", True),
    (".claude/skills/shadow-frog/SKILL.md", True),
    (".claude/skills/shadow-frog-viewer/shadow-viewer.py", True),
    (".claude/hooks/scripts/shadow-frog-check-init.sh", True),
    # NOT excluded
    ("src/main.py", False),
    ("foo/bar.js", False),
    ("Makefile", False),
    ("package.json", False),  # ".lock" suffix, not "lock" — package.json should pass
    (".github/workflows/ci.yml", False),  # user's own .github content is shadowed
    (".github/skills/my-other-skill/SKILL.md", False),  # only shadow-frog* is excluded
    (".claude/skills/my-other-skill/SKILL.md", False),  # only shadow-frog* is excluded
])
def test_is_excluded_path(shadow_init, path, is_excluded):
    assert shadow_init._is_excluded_path(path) is is_excluded


# ---------------------------------------------------------------------------
# _is_source_file
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("foo.py", True),
    ("foo.PY", True),  # case insensitive
    ("foo.js", True),
    ("foo.ts", True),
    ("Makefile", True),
    ("path/to/Dockerfile", True),
    ("path/to/Rakefile", True),
    # Not source
    ("foo.txt", False),
    ("foo.png", False),
    ("foo.exe", False),
    ("README", False),
    ("notes.md", False),
])
def test_is_source_file(shadow_init, path, expected):
    assert shadow_init._is_source_file(path) is expected


# ---------------------------------------------------------------------------
# _walk_files
# ---------------------------------------------------------------------------

def test_walk_files_empty_repo(shadow_init, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert shadow_init._walk_files(str(empty)) == []


def test_walk_files_one_file(shadow_init, tmp_path):
    repo = tmp_path / "one_file"
    repo.mkdir()
    (repo / "foo.py").write_text("x = 1\n")
    assert shadow_init._walk_files(str(repo)) == ["foo.py"]


def test_walk_files_skips_excluded_and_dotfile_dirs(shadow_init, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "foo.py").write_text("x = 1\n")
    (repo / "src").mkdir()
    (repo / "src" / "main.py").write_text("y = 2\n")
    # Excluded dir
    (repo / "node_modules").mkdir()
    (repo / "node_modules" / "bundle.js").write_text("z = 3\n")
    # Hidden dir (skipped by _walk_files's leading-dot filter)
    (repo / ".git").mkdir()
    (repo / ".git" / "config").write_text("[core]\n")

    result = sorted(shadow_init._walk_files(str(repo)))
    assert "foo.py" in result
    assert "src/main.py" in result
    assert not any("node_modules" in p for p in result)
    assert not any(".git" in p for p in result)


# ---------------------------------------------------------------------------
# _load_shadowignore
# ---------------------------------------------------------------------------

def test_load_shadowignore_no_file(shadow_init, tmp_path):
    matcher = shadow_init._load_shadowignore(tmp_path)
    assert matcher("anything.py") is False


def test_load_shadowignore_empty_file(shadow_init, tmp_path):
    (tmp_path / ".shadowignore").write_text("")
    matcher = shadow_init._load_shadowignore(tmp_path)
    assert matcher("anything.py") is False


def test_load_shadowignore_comments_only(shadow_init, tmp_path):
    (tmp_path / ".shadowignore").write_text("# header comment\n\n# another\n")
    matcher = shadow_init._load_shadowignore(tmp_path)
    assert matcher("anything.py") is False


def test_load_shadowignore_pattern_matches(shadow_init, tmp_path):
    (tmp_path / ".shadowignore").write_text("*.log\nsecret/\n")
    matcher = shadow_init._load_shadowignore(tmp_path)
    assert matcher("foo.log") is True
    assert matcher("nested/path/error.log") is True
    assert matcher("foo.py") is False


def test_load_shadowignore_negation_does_not_crash(shadow_init, tmp_path):
    # Negation is valid gitwildmatch syntax — the matcher must not raise.
    (tmp_path / ".shadowignore").write_text("*.log\n!keep.log\n")
    matcher = shadow_init._load_shadowignore(tmp_path)
    # Both calls must succeed (we don't assert specific values because
    # pathspec respects negation but the fnmatch fallback does not).
    matcher("foo.log")
    matcher("keep.log")


# ---------------------------------------------------------------------------
# discover_files (end-to-end against tmp_git_repo)
# ---------------------------------------------------------------------------

@pytest.mark.slow
def test_discover_files_end_to_end(shadow_init, tmp_git_repo):
    (tmp_git_repo / "foo.py").write_text("def hello(): pass\n")
    (tmp_git_repo / "bar.js").write_text("function world() {}\n")
    (tmp_git_repo / "ignore.txt").write_text("not source\n")
    sub = tmp_git_repo / "src"
    sub.mkdir()
    (sub / "baz.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add files"],
                   cwd=tmp_git_repo, check=True)

    shadow_dir = tmp_git_repo / ".shadow"  # may or may not exist; fine either way
    result = shadow_init.discover_files(str(tmp_git_repo), shadow_dir)

    assert "foo.py" in result
    assert "bar.js" in result
    assert "src/baz.py" in result
    assert "ignore.txt" not in result  # not a recognized source ext
    # discover_files returns a sorted, deduped list
    assert result == sorted(set(result))


@pytest.mark.slow
def test_discover_files_honors_shadowignore(shadow_init, tmp_git_repo):
    (tmp_git_repo / "foo.py").write_text("def a(): pass\n")
    (tmp_git_repo / "bar.py").write_text("def b(): pass\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                   cwd=tmp_git_repo, check=True)

    shadow_dir = tmp_git_repo / ".shadow"
    shadow_dir.mkdir()
    (shadow_dir / ".shadowignore").write_text("bar.py\n")

    result = shadow_init.discover_files(str(tmp_git_repo), shadow_dir)
    assert "foo.py" in result
    assert "bar.py" not in result


# ---------------------------------------------------------------------------
# extract_symbols dispatcher
# ---------------------------------------------------------------------------

def test_extract_symbols_dispatcher_python(shadow_init):
    syms = shadow_init.extract_symbols("def foo(): pass\n", "Python", "foo.py")
    assert len(syms) == 1
    assert syms[0].name == "foo"
    assert syms[0].kind == "function"


def test_extract_symbols_dispatcher_javascript(shadow_init):
    syms = shadow_init.extract_symbols("function foo() {}\n", "JavaScript", "foo.js")
    assert len(syms) == 1
    assert syms[0].name == "foo"


def test_extract_symbols_dispatcher_typescript_uses_js_extractor(shadow_init):
    # TypeScript shares the JS extractor via the EXTRACTORS dispatch table.
    syms = shadow_init.extract_symbols(
        "export function bar(): number { return 1; }\n",
        "TypeScript", "bar.ts",
    )
    assert any(s.name == "bar" for s in syms)


@pytest.mark.parametrize("unknown_lang", ["Unknown", "YAML", "JSON", "TOML", "Makefile", "Dockerfile"])
def test_extract_symbols_unknown_language_returns_empty(shadow_init, unknown_lang):
    """Languages without a registered extractor return [] (not None, not error)."""
    assert shadow_init.extract_symbols("anything", unknown_lang, "x") == []


def test_extract_symbols_never_raises_on_garbage(shadow_init):
    """Per docstring: never raises — returns [] on any failure."""
    result = shadow_init.extract_symbols(
        "\x00\x01\x02 ~`!@#$%^&*()", "Python", "weird.py",
    )
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# Per-language extractors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("source,expected", [
    # simple top-level function
    ("def foo():\n    pass\n",
     [("foo", "function", None)]),
    # async function
    ("async def fetch():\n    pass\n",
     [("fetch", "function", None)]),
    # class with method
    ("class Foo:\n    def bar(self):\n        pass\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    # function with default args
    ("def add(x=1, y=2):\n    return x + y\n",
     [("add", "function", None)]),
    # decorator does not interfere
    ("@staticmethod\ndef foo():\n    pass\n",
     [("foo", "function", None)]),
    # comment-only / empty
    ("# just comments\n# nothing here\n", []),
    ("", []),
    # module-level ALL_CAPS constant
    ("MAX_RETRIES = 3\n",
     [("MAX_RETRIES", "constant", None)]),
    # annotated module-level constant
    ("TIMEOUT: int = 30\n",
     [("TIMEOUT", "constant", None)]),
    # lowercase module var is NOT captured
    ("config = {}\n", []),
    # comparison / augmented assignment are NOT captured
    ("COUNT += 1\n", []),
    # constant alongside a function
    ("CACHE = {}\ndef get(code):\n    return CACHE.get(code)\n",
     [("CACHE", "constant", None), ("get", "function", None)]),
])
def test_extract_python(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_python(source)) == expected


def test_extract_python_class_scoped_constant_not_captured(shadow_init):
    """ALL_CAPS names inside a class body are class attributes, not
    module-level constants, so they are NOT emitted as constant symbols."""
    source = (
        "class Foo:\n"
        "    CLASS_CONST = 5\n"
        "    def bar(self):\n"
        "        LOCAL = 2\n"
        "        return LOCAL\n"
        "GLOBAL_AFTER = {}\n"
    )
    syms = _to_tuples(shadow_init._extract_python(source))
    assert ("CLASS_CONST", "constant", None) not in syms
    assert ("Foo", "class", None) in syms
    assert ("bar", "method", "Foo") in syms
    # A module-level constant after the class IS captured (scope reset).
    assert ("GLOBAL_AFTER", "constant", None) in syms


@pytest.mark.parametrize("source,expected", [
    ("function foo() {}\n",
     [("foo", "function", None)]),
    ("export function bar() {}\n",
     [("bar", "function", None)]),
    ("async function fetchData() {}\n",
     [("fetchData", "function", None)]),
    # const arrow function
    ("const baz = () => { return 1; };\n",
     [("baz", "function", None)]),
    # class with method
    ("class Foo {\n  bar() { return 1; }\n}\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    # comment-only
    ("// just a comment\n", []),
])
def test_extract_javascript(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_javascript(source)) == expected


@pytest.mark.parametrize("source,expected", [
    ("public class Foo {\n    public void bar() {}\n}\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    ("public interface IBar {\n}\n",
     [("IBar", "interface", None)]),
    ("enum Color {\n    RED, BLUE\n}\n",
     [("Color", "enum", None)]),
    # Kotlin `fun` inside class
    ("class Greeter {\n    fun hello() {}\n}\n",
     [("Greeter", "class", None), ("hello", "method", "Greeter")]),
    # comment-only
    ("// nothing\n", []),
])
def test_extract_java_like(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_java_like(source)) == expected


@pytest.mark.parametrize("source,expected", [
    ("func Foo() {}\n",
     [("Foo", "function", None)]),
    ("type Server struct {\n    addr string\n}\n",
     [("Server", "struct", None)]),
    ("type Handler interface {\n    Handle()\n}\n",
     [("Handler", "interface", None)]),
    # method on pointer receiver
    ("func (s *Server) Start() {}\n",
     [("Start", "method", "Server")]),
    # method on value receiver
    ("func (s Server) Stop() {}\n",
     [("Stop", "method", "Server")]),
    # package + comment only
    ("package main\n// nothing\n", []),
])
def test_extract_go(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_go(source)) == expected


@pytest.mark.parametrize("source,expected", [
    ("fn main() {}\n",
     [("main", "function", None)]),
    ("pub fn add(x: i32, y: i32) -> i32 { x + y }\n",
     [("add", "function", None)]),
    ("struct Point {\n    x: f64,\n    y: f64,\n}\n",
     [("Point", "struct", None)]),
    ("enum Color { Red, Blue }\n",
     [("Color", "enum", None)]),
    # impl block: fn inside an impl becomes a method
    ("struct Foo;\nimpl Foo {\n    fn new() -> Self { Foo }\n}\n",
     [("Foo", "struct", None), ("new", "method", "Foo")]),
    # comment-only
    ("// just a comment\n", []),
])
def test_extract_rust(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_rust(source)) == expected


@pytest.mark.parametrize("source,expected", [
    # top-level def
    ("def hello\n  puts 'hi'\nend\n",
     [("hello", "function", None)]),
    # class with def -> method
    ("class Foo\n  def bar\n    1\n  end\nend\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    # module: methods inside are NOT scoped to a class (Ruby extractor only
    # sets current_class for `class`, not `module`)
    ("module M\n  def fn\n    1\n  end\nend\n",
     [("M", "module", None), ("fn", "function", None)]),
    # comment-only
    ("# nothing\n", []),
])
def test_extract_ruby(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_ruby(source)) == expected


@pytest.mark.parametrize("source,expected", [
    # plain function
    ("int add(int a, int b) {\n    return a + b;\n}\n",
     [("add", "function", None)]),
    # class with declared method body
    ("class Foo {\npublic:\n    void bar();\n};\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    # preprocessor lines (start with #) are skipped
    ("#include <stdio.h>\nint main() { return 0; }\n",
     [("main", "function", None)]),
    # commented-out class is skipped; real fn extracted
    ("// fake class Foo {};\nint real() { return 0; }\n",
     [("real", "function", None)]),
])
def test_extract_c_cpp(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_c_cpp(source)) == expected


@pytest.mark.parametrize("source,expected", [
    ("<?php\nfunction foo() {}\n",
     [("foo", "function", None)]),
    ("<?php\nclass Foo {\n  public function bar() {}\n}\n",
     [("Foo", "class", None), ("bar", "method", "Foo")]),
    ("<?php\nabstract class Base {\n  abstract function init();\n}\n",
     [("Base", "class", None), ("init", "method", "Base")]),
    ("<?php\n// just a comment\n", []),
])
def test_extract_php(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_php(source)) == expected


@pytest.mark.parametrize("source,expected", [
    # `function NAME {` form
    ("function foo {\n  echo hi\n}\n",
     [("foo", "function", None)]),
    # `NAME() {` form
    ("bar() {\n  echo bar\n}\n",
     [("bar", "function", None)]),
    # both forms in one file, shebang ignored
    ("#!/bin/bash\nfunction a {\n  :\n}\nb() {\n  :\n}\n",
     [("a", "function", None), ("b", "function", None)]),
    # comment-only
    ("# comment only\n", []),
])
def test_extract_shell(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_shell(source)) == expected


@pytest.mark.parametrize("source,expected", [
    # top-level func
    ("func hello() -> String {\n    return \"hi\"\n}\n",
     [("hello", "function", None)]),
    # class with method
    ("class Greeter {\n    func hi() {}\n}\n",
     [("Greeter", "class", None), ("hi", "method", "Greeter")]),
    # protocol with method
    ("protocol Greeter {\n    func hi()\n}\n",
     [("Greeter", "protocol", None), ("hi", "method", "Greeter")]),
    # struct (no methods)
    ("struct Point {\n    var x: Double\n}\n",
     [("Point", "struct", None)]),
    # comment-only
    ("// nothing\n", []),
])
def test_extract_swift(shadow_init, source, expected):
    assert _to_tuples(shadow_init._extract_swift(source)) == expected


# ---------------------------------------------------------------------------
# build_state_json — B2 REGRESSION
# ---------------------------------------------------------------------------

def test_build_state_json_empty_string_becomes_none_sentinel(shadow_init):
    """B2 regression: empty string last_commit MUST become 'none', not ''."""
    state = shadow_init.build_state_json(0, 0, "")
    assert state["last_commit"] == "none", (
        f"B2 regression: expected sentinel 'none', got {state['last_commit']!r}. "
        "Downstream hooks rely on 'none' to distinguish missing-commit from empty."
    )


def test_build_state_json_none_becomes_none_sentinel(shadow_init):
    """B2 regression: None last_commit MUST become 'none'."""
    state = shadow_init.build_state_json(0, 0, None)
    assert state["last_commit"] == "none"


def test_build_state_json_valid_sha_passes_through(shadow_init):
    """B2 regression: a real 40-char SHA must NOT be replaced by the sentinel."""
    sha = "abcd" * 10  # exactly 40 hex chars
    assert len(sha) == 40, "test setup: SHA must be 40 chars"
    state = shadow_init.build_state_json(0, 0, sha)
    assert state["last_commit"] == sha


def test_build_state_json_required_fields(shadow_init):
    state = shadow_init.build_state_json(5, 17, "deadbeef" * 5)
    assert state["version"] == 1
    assert state["last_update_type"] == "init"
    assert state["total_files"] == 5
    assert state["total_symbols"] == 17
    assert state["total_discoveries"] == 0
    assert state["dream_cycles_completed"] == 0
    # ISO-ish timestamp (or "unknown" if the clock call failed — unlikely)
    assert "T" in state["initialized_at"] or state["initialized_at"] == "unknown"
    assert state["initialized_at"] == state["last_update_at"]


# ---------------------------------------------------------------------------
# build_shadow_content
# ---------------------------------------------------------------------------

def test_build_shadow_content_no_symbols(shadow_init):
    content = shadow_init.build_shadow_content(
        "src/foo.py", "Python", 42, "2024-01-15", [],
    )
    assert content.startswith("# Shadow: src/foo.py")
    assert "**Language**: Python" in content
    assert "**Lines**: 42" in content
    assert "**Last modified**: 2024-01-15" in content
    assert "## File-Level" in content
    assert "## Cross-References" in content
    assert "_No discoveries yet._" in content
    assert "_No cross-cutting discoveries yet._" in content


def test_build_shadow_content_with_class_and_method_and_function(shadow_init):
    Symbol = shadow_init.Symbol
    syms = [
        Symbol("Foo", "class"),
        Symbol("bar", "method", parent="Foo"),
        Symbol("standalone", "function"),
    ]
    content = shadow_init.build_shadow_content(
        "foo.py", "Python", 10, "2024-01-15", syms,
    )
    # Class container heading with prefix and child as ###
    assert "## `class Foo`" in content
    assert "### `Foo.bar`" in content
    # Standalone function as ##
    assert "## `standalone`" in content
    # Cross-References is the last section
    assert content.rfind("## Cross-References") > content.rfind("## `standalone`")


def test_build_shadow_content_empty_container_gets_placeholder(shadow_init):
    Symbol = shadow_init.Symbol
    content = shadow_init.build_shadow_content(
        "foo.java", "Java", 5, "2024-01-15",
        [Symbol("Empty", "class")],
    )
    assert "## `class Empty`" in content
    # The class with no children still gets a placeholder
    placeholder_after_heading = content.split("## `class Empty`", 1)[1]
    assert "_No discoveries yet._" in placeholder_after_heading


# ---------------------------------------------------------------------------
# build_index
# ---------------------------------------------------------------------------

def test_build_index_empty(shadow_init):
    content = shadow_init.build_index([], 0)
    assert content.startswith("# Shadow Index")
    assert "Total files: 0" in content
    assert "Symbols: 0" in content
    assert "| File | Language | Symbols | Discoveries |" in content
    assert "|------|----------|---------|-------------|" in content


def test_build_index_with_records(shadow_init):
    Symbol = shadow_init.Symbol
    records = [
        ("src/foo.py", "Python", [Symbol("hello", "function"), Symbol("Foo", "class")]),
        ("src/bar.js", "JavaScript", []),
    ]
    content = shadow_init.build_index(records, 2)
    assert "Total files: 2" in content
    assert "src/foo.py" in content
    assert "src/bar.js" in content
    assert "Python" in content
    assert "JavaScript" in content
    # Top-level symbol names should appear in the symbols column
    assert "hello" in content
    assert "Foo" in content
    # Empty record renders symbol count of 0
    assert "| src/bar.js | JavaScript | 0 |" in content


def test_build_index_truncates_long_symbol_lists(shadow_init):
    Symbol = shadow_init.Symbol
    syms = [Symbol(f"f{i}", "function") for i in range(5)]
    records = [("foo.py", "Python", syms)]
    content = shadow_init.build_index(records, 5)
    assert "..." in content   # truncated tail
    assert "5 (" in content   # explicit count


# ---------------------------------------------------------------------------
# CLI integration (subprocess path)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
def test_cli_dry_run_does_not_write_shadow(tmp_git_repo):
    """--dry-run must report what *would* happen but never touch the FS."""
    result = _run_shadow_init(tmp_git_repo, "--reset", "--dry-run")
    assert result.returncode == 0, (
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )
    assert not (tmp_git_repo / ".shadow").exists()
    combined = (result.stdout + result.stderr).lower()
    assert "dry-run" in combined


@pytest.mark.slow
@pytest.mark.integration
def test_cli_creates_shadow_for_committed_file(tmp_git_repo):
    """End-to-end: a committed `foo.py` should yield a complete .shadow/ tree."""
    (tmp_git_repo / "foo.py").write_text(
        "def hello():\n    return 'world'\n\nclass Greeter:\n    def hi(self):\n        return 'hi'\n"
    )
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add foo"],
                   cwd=tmp_git_repo, check=True)

    result = _run_shadow_init(tmp_git_repo, "--reset")
    assert result.returncode == 0, (
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )

    shadow = tmp_git_repo / ".shadow"
    assert shadow.is_dir()
    assert (shadow / "foo.py.md").is_file()
    assert (shadow / "_index.md").is_file()
    assert (shadow / "_prefs.md").is_file()
    assert (shadow / ".shadowignore").is_file()
    assert (shadow / "_meta" / "state.json").is_file()
    assert (shadow / "_cross").is_dir()
    assert (shadow / "_dreams").is_dir()

    state = json.loads((shadow / "_meta" / "state.json").read_text())
    assert isinstance(state["last_commit"], str)
    assert re.fullmatch(r"[0-9a-f]{40}", state["last_commit"]), (
        f"Expected 40-char hex SHA, got: {state['last_commit']!r}"
    )
    assert state["total_files"] == 1
    assert state["total_symbols"] >= 2  # hello + Greeter (at least)
    assert state["last_update_type"] == "init"
    assert state["version"] == 1

    shadow_content = (shadow / "foo.py.md").read_text()
    assert "hello" in shadow_content
    assert "Greeter" in shadow_content
    assert "Python" in shadow_content
    assert "## Cross-References" in shadow_content


@pytest.mark.slow
@pytest.mark.integration
def test_cli_b2_no_commits_uses_none_sentinel(tmp_git_repo):
    """B2 regression: an empty repo (no HEAD) must write last_commit='none'.

    git rev-parse HEAD fails in a fresh repo with no commits — the script
    must fall back to the 'none' sentinel rather than writing an empty string.
    """
    result = _run_shadow_init(tmp_git_repo, "--reset")
    assert result.returncode == 0, (
        f"stderr: {result.stderr}\nstdout: {result.stdout}"
    )

    state_path = tmp_git_repo / ".shadow" / "_meta" / "state.json"
    state = json.loads(state_path.read_text())
    assert state["last_commit"] == "none", (
        f"B2 regression: expected sentinel 'none', got {state['last_commit']!r}. "
        "An empty string here breaks downstream hooks that distinguish "
        "missing-commit from empty-commit."
    )


@pytest.mark.slow
@pytest.mark.integration
def test_cli_refuses_to_overwrite_without_reset(tmp_git_repo):
    """If .shadow/ already exists, the script must refuse without --reset."""
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "marker").write_text("preserve me\n")

    result = _run_shadow_init(tmp_git_repo)  # no --reset
    assert result.returncode == 1
    # The pre-existing marker must NOT have been deleted
    assert (tmp_git_repo / ".shadow" / "marker").is_file()
    assert "already exists" in result.stderr.lower()


# ===========================================================================
# APPENDED TESTS — main() CLI dispatcher, init_shadow integration,
# remaining extractor edge branches, and helper-function direct coverage.
#
# These tests target the uncovered-line ranges in the shadow-init coverage
# report so the script reaches ~75% line coverage. They follow the same
# zero-mock philosophy as the tests above: real subprocess, real git,
# real filesystems. monkeypatch is used only for sys.argv / cwd plumbing
# required to exercise main() in-process (so coverage is recorded).
# ===========================================================================

import os


@pytest.fixture
def reset_diagnostics(shadow_init):
    """Snapshot/restore the module-level warning/error counters.

    `shadow_init` is session-scoped so counters accumulate across tests.
    Tests that assert on counter contents should request this fixture
    so they get a clean slate and don't perturb later tests.
    """
    saved_w = shadow_init._warning_count
    saved_e = shadow_init._error_count
    saved_d = list(shadow_init._diagnostics)
    shadow_init._warning_count = 0
    shadow_init._error_count = 0
    shadow_init._diagnostics.clear()
    try:
        yield shadow_init
    finally:
        shadow_init._warning_count = saved_w
        shadow_init._error_count = saved_e
        shadow_init._diagnostics[:] = saved_d


# ---------------------------------------------------------------------------
# warn() / error() — direct invocation (lines 41-43, 49-51)
# ---------------------------------------------------------------------------

def test_warn_increments_counter_and_logs(reset_diagnostics, capsys):
    si = reset_diagnostics
    si.warn("synthetic warning")
    assert si._warning_count == 1
    assert ("warning", "synthetic warning") in si._diagnostics
    err = capsys.readouterr().err
    assert "[shadow-init warning] synthetic warning" in err


def test_error_increments_counter_and_logs(reset_diagnostics, capsys):
    si = reset_diagnostics
    si.error("synthetic error")
    assert si._error_count == 1
    assert ("error", "synthetic error") in si._diagnostics
    err = capsys.readouterr().err
    assert "[shadow-init error] synthetic error" in err


# ---------------------------------------------------------------------------
# run_git() failure branches (lines 69-81)
# ---------------------------------------------------------------------------

def test_run_git_returns_none_outside_repo(shadow_init, tmp_path):
    """A failing git command (non-zero exit) yields None + warn."""
    out = shadow_init.run_git(["rev-parse", "HEAD"], cwd=str(tmp_path))
    assert out is None


def test_run_git_returns_stdout_on_success(shadow_init, tmp_git_repo):
    out = shadow_init.run_git(["rev-parse", "--is-inside-work-tree"],
                              cwd=str(tmp_git_repo))
    assert out is not None
    assert out.strip() == "true"


def test_run_git_unknown_subcommand_returns_none(shadow_init, tmp_git_repo):
    """`git this-is-not-real` returns non-zero → run_git returns None."""
    out = shadow_init.run_git(["this-is-not-a-real-git-command"],
                              cwd=str(tmp_git_repo))
    assert out is None


# ---------------------------------------------------------------------------
# find_repo_root() (lines 88-128)
# ---------------------------------------------------------------------------

def test_find_repo_root_returns_root_inside_git(shadow_init, tmp_git_repo,
                                                monkeypatch, capsys):
    monkeypatch.chdir(tmp_git_repo)
    root = shadow_init.find_repo_root()
    assert root is not None
    assert Path(root).resolve() == tmp_git_repo.resolve()
    # Diagnostic line printed to stderr
    assert "Detected" in capsys.readouterr().err


def test_find_repo_root_outside_repo_returns_none(shadow_init, tmp_path,
                                                  monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert shadow_init.find_repo_root() is None


def test_find_repo_root_inside_subdirectory(shadow_init, tmp_git_repo,
                                            monkeypatch):
    """find_repo_root should walk up to repo root from a subdir."""
    sub = tmp_git_repo / "deep" / "nested"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)
    root = shadow_init.find_repo_root()
    assert root is not None
    assert Path(root).resolve() == tmp_git_repo.resolve()


# ---------------------------------------------------------------------------
# _walk_files: additional coverage
# ---------------------------------------------------------------------------

def test_walk_files_lists_all_non_excluded(shadow_init, tmp_path):
    (tmp_path / "a.py").write_text("x")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("y")
    result = shadow_init._walk_files(tmp_path)
    # Note: walk does NOT filter by extension — that's discover_files's job
    assert "a.py" in result
    assert os.path.join("sub", "b.txt") in result


# ---------------------------------------------------------------------------
# _load_shadowignore: unreadable file (lines 225-227)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0,
                    reason="root can read 0o000 files")
def test_load_shadowignore_unreadable_file_warns(shadow_init, tmp_path,
                                                 reset_diagnostics):
    ignore = tmp_path / ".shadowignore"
    ignore.write_text("*.log\n")
    ignore.chmod(0o000)
    try:
        matcher = reset_diagnostics._load_shadowignore(tmp_path)
        # Falls back to a permissive matcher
        assert matcher("anything.log") is False
        msgs = [m for lvl, m in reset_diagnostics._diagnostics
                if lvl == "warning"]
        assert any("Could not read .shadowignore" in m for m in msgs)
    finally:
        ignore.chmod(0o600)


# ---------------------------------------------------------------------------
# discover_files: walk fallback in non-git dir (lines 282-284)
# ---------------------------------------------------------------------------

def test_discover_files_falls_back_to_walk_in_non_git_dir(shadow_init,
                                                          tmp_path):
    """ls-files fails outside git → walk fallback finds files anyway."""
    (tmp_path / "foo.py").write_text("def x(): pass\n")
    (tmp_path / "bar.js").write_text("function y(){}\n")
    (tmp_path / "ignored.txt").write_text("not source\n")
    result = shadow_init.discover_files(str(tmp_path), tmp_path / ".shadow")
    assert "foo.py" in result
    assert "bar.js" in result
    assert "ignored.txt" not in result  # filtered by _is_source_file


# ---------------------------------------------------------------------------
# Python extractor edge: top-level def after a class exits the class scope
# (lines 414-415)
# ---------------------------------------------------------------------------

def test_extract_python_top_level_def_after_class_exits_scope(shadow_init):
    source = (
        "class Foo:\n"
        "    def a(self):\n"
        "        pass\n"
        "\n"
        "def b():\n"
        "    pass\n"
    )
    syms = _to_tuples(shadow_init._extract_python(source))
    assert ("Foo", "class", None) in syms
    assert ("a", "method", "Foo") in syms
    assert ("b", "function", None) in syms


def test_extract_python_def_at_class_indent_becomes_function(shadow_init):
    """A `def` at the *same* indent as the class is a top-level function
    (triggers the secondary scope-exit branch in the def-handler)."""
    # Using \t-equivalent: two classes at column 0, with a def at column 0
    # immediately after (no blank line) so the line-after-method handler
    # reaches the indent==class_indent comparison on the def path.
    source = "class Foo:\n  def m(self):\n    pass\ndef top():\n  pass\n"
    syms = _to_tuples(shadow_init._extract_python(source))
    assert ("top", "function", None) in syms


# ---------------------------------------------------------------------------
# JavaScript extractor edges: function inside class (470), brace clamp (493)
# ---------------------------------------------------------------------------

def test_extract_javascript_function_keyword_inside_class_is_method(shadow_init):
    """A `function name()` line while we're inside a class body should
    attribute to the class (line 470)."""
    source = (
        "class C {\n"
        "function inner() {}\n"
        "}\n"
    )
    syms = _to_tuples(shadow_init._extract_javascript(source))
    assert ("C", "class", None) in syms
    assert ("inner", "method", "C") in syms


def test_extract_javascript_clamps_negative_brace_depth(shadow_init):
    """Stray close-braces must not crash; depth should clamp to 0 (line 493)."""
    source = "}}\n}}\nfunction good() {}\n"
    syms = _to_tuples(shadow_init._extract_javascript(source))
    assert ("good", "function", None) in syms


# ---------------------------------------------------------------------------
# Java-like extractor: Kotlin-specific `fun <T>` (lines 553-555) + brace
# clamp (lines 558-559)
# ---------------------------------------------------------------------------

def test_extract_java_like_kotlin_generic_fun_inside_class(shadow_init):
    """`fun <T> name(...)` doesn't match the primary regex (because of
    the generic between `fun` and the name) — falls through to the
    Kotlin-specific fallback regex."""
    source = (
        "class Box {\n"
        "    fun <T> tag(item: T): T { return item }\n"
        "}\n"
    )
    syms = _to_tuples(shadow_init._extract_java_like(source))
    assert ("Box", "class", None) in syms
    assert ("tag", "method", "Box") in syms


def test_extract_java_like_clamps_negative_brace_depth(shadow_init):
    source = "}}}\nclass Z {}\n"
    syms = _to_tuples(shadow_init._extract_java_like(source))
    assert ("Z", "class", None) in syms


# ---------------------------------------------------------------------------
# Rust / Ruby / C++ / PHP / Swift — brace/depth clamp branches
# ---------------------------------------------------------------------------

def test_extract_rust_clamps_negative_brace_depth(shadow_init):
    source = "}}}\npub fn ok() {}\n"
    syms = _to_tuples(shadow_init._extract_rust(source))
    assert ("ok", "function", None) in syms


def test_extract_ruby_clamps_negative_depth(shadow_init):
    source = "end\nend\ndef ok\nend\n"
    syms = _to_tuples(shadow_init._extract_ruby(source))
    assert ("ok", "function", None) in syms


def test_extract_c_cpp_scoped_function_attributed_to_scope(shadow_init):
    """`Foo::bar()` syntax → method of `Foo` via the scope capture group
    (line 740)."""
    source = "int Foo::bar() { return 1; }\n"
    syms = _to_tuples(shadow_init._extract_c_cpp(source))
    assert ("bar", "method", "Foo") in syms


def test_extract_c_cpp_skips_blacklisted_control_keywords(shadow_init):
    """Lines like `int if(x)` would otherwise be reported as a function
    named `if` — the blacklist branch (lines 736-738) prevents this."""
    source = (
        "int if() { return 1; }\n"
        "int real_one() { return 2; }\n"
    )
    syms = _to_tuples(shadow_init._extract_c_cpp(source))
    assert not any(name == "if" for name, *_ in syms)
    assert ("real_one", "function", None) in syms


def test_extract_c_cpp_clamps_negative_brace_depth(shadow_init):
    source = "}}}\nint ok() { return 0; }\n"
    syms = _to_tuples(shadow_init._extract_c_cpp(source))
    assert ("ok", "function", None) in syms


def test_extract_php_clamps_negative_brace_depth(shadow_init):
    source = "}}}\n<?php\nfunction ok() {}\n"
    syms = _to_tuples(shadow_init._extract_php(source))
    assert any(name == "ok" for name, *_ in syms)


def test_extract_swift_clamps_negative_brace_depth(shadow_init):
    source = "}}}\nfunc ok() {}\n"
    syms = _to_tuples(shadow_init._extract_swift(source))
    assert ("ok", "function", None) in syms


# ---------------------------------------------------------------------------
# get_last_modified / count_lines (lines 945-956, 960)
# ---------------------------------------------------------------------------

def test_get_last_modified_returns_iso_date_for_committed_file(shadow_init,
                                                               tmp_git_repo):
    (tmp_git_repo / "foo.py").write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)
    date = shadow_init.get_last_modified("foo.py", str(tmp_git_repo))
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", date), \
        f"expected ISO date, got: {date!r}"


def test_get_last_modified_returns_unknown_in_non_git_dir(shadow_init,
                                                          tmp_path):
    (tmp_path / "foo.py").write_text("x\n")
    assert shadow_init.get_last_modified("foo.py", str(tmp_path)) == "unknown"


def test_get_last_modified_returns_unknown_for_uncommitted(shadow_init,
                                                           tmp_git_repo):
    (tmp_git_repo / "fresh.py").write_text("x\n")  # not committed
    assert shadow_init.get_last_modified("fresh.py",
                                         str(tmp_git_repo)) == "unknown"


@pytest.mark.parametrize("source,expected", [
    ("", 0),
    ("a", 1),
    ("a\nb\n", 2),
    ("a\nb\nc", 3),
    ("\n\n\n", 3),
])
def test_count_lines(shadow_init, source, expected):
    assert shadow_init.count_lines(source) == expected


# ---------------------------------------------------------------------------
# build_shadow_content: orphan nested symbol (lines 1009-1012)
# ---------------------------------------------------------------------------

def test_build_shadow_content_orphan_method_renders_as_h3(shadow_init):
    """A symbol with parent set but no preceding container of that name
    falls into the orphan branch — rendered as a ### heading using the
    `Parent.name` display name."""
    Symbol = shadow_init.Symbol
    content = shadow_init.build_shadow_content(
        "foo.py", "Python", 1, "2024-01-01",
        [Symbol("lost", "method", parent="Ghost")],
    )
    assert "### `Ghost.lost`" in content


def test_build_shadow_content_orphan_after_real_class(shadow_init):
    """Orphan and proper-class scenarios coexist in one file without crashing."""
    Symbol = shadow_init.Symbol
    syms = [
        Symbol("Real", "class"),
        Symbol("inner", "method", parent="Real"),
        Symbol("escaped", "method", parent="Vanished"),  # orphan
    ]
    content = shadow_init.build_shadow_content(
        "x.py", "Python", 10, "2024-01-01", syms,
    )
    assert "## `class Real`" in content
    assert "### `Real.inner`" in content
    assert "### `Vanished.escaped`" in content


# ---------------------------------------------------------------------------
# build_index: empty-top-level names fallback (1143), malformed row (1150-1152)
# ---------------------------------------------------------------------------

def test_build_index_no_top_level_falls_back_to_display_names(shadow_init):
    """When a file has only nested symbols (no top-level), the index row
    shows `Parent.name` display names instead of an empty list."""
    Symbol = shadow_init.Symbol
    records = [("foo.py", "Python",
                [Symbol("m1", "method", parent="X"),
                 Symbol("m2", "method", parent="X")])]
    content = shadow_init.build_index(records, 2)
    assert "X.m1" in content
    assert "X.m2" in content


def test_build_index_malformed_record_renders_fallback_row(shadow_init,
                                                           reset_diagnostics):
    """A row where `symbols` is None blows up len()/iteration → caught,
    fallback row with `?` in the symbols column is emitted (line 1152)."""
    bad_records = [("foo.py", "Python", None)]
    content = reset_diagnostics.build_index(bad_records, 0)
    assert "| foo.py | Python | ? | 0 |" in content
    msgs = [m for lvl, m in reset_diagnostics._diagnostics if lvl == "warning"]
    assert any("Failed to build index row" in m for m in msgs)


# ---------------------------------------------------------------------------
# init_shadow direct invocation (lines 1168-1400)
# ---------------------------------------------------------------------------

def test_init_shadow_empty_git_repo_writes_none_sentinel(shadow_init,
                                                        tmp_git_repo):
    """Empty repo (no commits): last_commit must be the 'none' sentinel
    (B2 regression) and all scaffold artifacts must exist."""
    ok = shadow_init.init_shadow(str(tmp_git_repo))
    assert ok is True
    shadow = tmp_git_repo / ".shadow"
    assert (shadow / "_meta" / "state.json").is_file()
    assert (shadow / "_cross").is_dir()
    assert (shadow / "_dreams").is_dir()
    assert (shadow / "_dreams" / "_index.md").is_file()
    assert (shadow / ".shadowignore").is_file()
    assert (shadow / "_index.md").is_file()
    assert (shadow / "_prefs.md").is_file()

    state = json.loads((shadow / "_meta" / "state.json").read_text())
    assert state["last_commit"] == "none"
    assert state["total_files"] == 0
    assert state["total_symbols"] == 0
    assert state["version"] == 1
    assert state["last_update_type"] == "init"
    assert state["total_discoveries"] == 0
    assert state["dream_cycles_completed"] == 0


def test_init_shadow_dreams_index_has_seven_column_header(shadow_init,
                                                         tmp_git_repo):
    """The _dreams/_index.md scaffold must use the 7-column schema that
    dream-reconcile.py / dream-lineage.py validate against."""
    shadow_init.init_shadow(str(tmp_git_repo))
    idx = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert "dream_id" in idx
    assert "category" in idx
    assert "verdict" in idx
    assert "title" in idx
    assert "branch" in idx
    assert "parent" in idx
    assert "tip_commit" in idx


def test_init_shadow_default_shadowignore_has_expected_content(shadow_init,
                                                               tmp_git_repo):
    shadow_init.init_shadow(str(tmp_git_repo))
    sig = (tmp_git_repo / ".shadow" / ".shadowignore").read_text()
    assert "node_modules/" in sig
    assert "*.min.js" in sig
    assert ".shadow/" in sig
    assert "*.png" in sig


def test_init_shadow_prefs_default_content(shadow_init, tmp_git_repo):
    shadow_init.init_shadow(str(tmp_git_repo))
    prefs = (tmp_git_repo / ".shadow" / "_prefs.md").read_text()
    assert "# Preferences" in prefs
    assert "_No preferences recorded yet._" in prefs


def test_init_shadow_mixed_language_project(shadow_init, tmp_git_repo):
    """Multi-language repo: every recognized source file gets a shadow."""
    (tmp_git_repo / "main.py").write_text("def main(): pass\n")
    (tmp_git_repo / "app.js").write_text("function start() {}\n")
    (tmp_git_repo / "lib.go").write_text("package x\nfunc Hello() {}\n")
    (tmp_git_repo / "mod.rb").write_text("class Foo\n  def bar\n  end\nend\n")
    (tmp_git_repo / "thing.rs").write_text("pub fn run() {}\n")
    (tmp_git_repo / "shell.sh").write_text("greet() { echo hi; }\n")
    sub = tmp_git_repo / "src"
    sub.mkdir()
    (sub / "deep.ts").write_text("export function compute() {}\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"],
                   cwd=tmp_git_repo, check=True)

    ok = shadow_init.init_shadow(str(tmp_git_repo))
    assert ok is True
    shadow = tmp_git_repo / ".shadow"
    for path in ("main.py.md", "app.js.md", "lib.go.md", "mod.rb.md",
                 "thing.rs.md", "shell.sh.md", "src/deep.ts.md"):
        assert (shadow / path).is_file(), f"missing shadow: {path}"

    state = json.loads((shadow / "_meta" / "state.json").read_text())
    assert state["total_files"] == 7
    assert state["total_symbols"] >= 7  # each file contributes >=1 symbol
    assert re.fullmatch(r"[0-9a-f]{40}", state["last_commit"])

    index = (shadow / "_index.md").read_text()
    for p in ("main.py", "app.js", "lib.go", "mod.rb",
              "thing.rs", "shell.sh", "src/deep.ts"):
        assert p in index


def test_init_shadow_state_counts_match_disk(shadow_init, tmp_git_repo):
    """state.json totals must match actual files/symbols on disk."""
    (tmp_git_repo / "a.py").write_text(
        "def f1(): pass\ndef f2(): pass\nclass C:\n    def m(self): pass\n"
    )
    (tmp_git_repo / "b.py").write_text("def only(): pass\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    shadow_init.init_shadow(str(tmp_git_repo))
    state = json.loads(
        (tmp_git_repo / ".shadow" / "_meta" / "state.json").read_text()
    )
    # Two source files
    assert state["total_files"] == 2
    # 4 syms in a.py (f1, f2, C, C.m) + 1 in b.py
    assert state["total_symbols"] == 5


def test_init_shadow_refuses_when_shadow_exists_without_reset(shadow_init,
                                                              tmp_git_repo,
                                                              reset_diagnostics):
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "marker").write_text("preserve\n")
    ok = reset_diagnostics.init_shadow(str(tmp_git_repo), reset=False)
    assert ok is False
    assert (tmp_git_repo / ".shadow" / "marker").read_text() == "preserve\n"
    msgs = [m for lvl, m in reset_diagnostics._diagnostics if lvl == "error"]
    assert any("already exists" in m for m in msgs)


def test_init_shadow_reset_replaces_existing(shadow_init, tmp_git_repo):
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "stale.md").write_text("old\n")
    (tmp_git_repo / "foo.py").write_text("def x(): pass\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    ok = shadow_init.init_shadow(str(tmp_git_repo), reset=True)
    assert ok is True
    assert not (tmp_git_repo / ".shadow" / "stale.md").exists()
    assert (tmp_git_repo / ".shadow" / "foo.py.md").is_file()


def test_init_shadow_dry_run_creates_no_files(shadow_init, tmp_git_repo,
                                              capsys):
    (tmp_git_repo / "foo.py").write_text("def x(): pass\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    ok = shadow_init.init_shadow(str(tmp_git_repo), dry_run=True)
    assert ok is True
    assert not (tmp_git_repo / ".shadow").exists()
    out = capsys.readouterr().out.lower()
    assert "dry-run" in out


def test_init_shadow_dry_run_with_existing_shadow_and_reset(shadow_init,
                                                            tmp_git_repo,
                                                            capsys):
    """dry_run + reset must report 'Would delete' without actually deleting."""
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "keep.txt").write_text("preserved\n")
    ok = shadow_init.init_shadow(str(tmp_git_repo), reset=True, dry_run=True)
    assert ok is True
    # Original .shadow contents untouched
    assert (tmp_git_repo / ".shadow" / "keep.txt").read_text() == "preserved\n"
    out = capsys.readouterr().out.lower()
    assert "would delete" in out


def test_init_shadow_skips_excluded_paths_and_basenames(shadow_init,
                                                       tmp_git_repo):
    """node_modules/, *.lock, *.min.js are filtered by built-in rules."""
    (tmp_git_repo / "real.py").write_text("def x(): pass\n")
    nm = tmp_git_repo / "node_modules"
    nm.mkdir()
    (nm / "junk.js").write_text("function junk(){}\n")
    (tmp_git_repo / "huge.lock").write_text("{}\n")
    (tmp_git_repo / "min.min.js").write_text("var a=1;\n")
    # Non-source file (no recognized extension)
    (tmp_git_repo / "README.txt").write_text("docs\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    ok = shadow_init.init_shadow(str(tmp_git_repo))
    assert ok is True
    shadow = tmp_git_repo / ".shadow"
    assert (shadow / "real.py.md").is_file()
    assert not (shadow / "node_modules").exists()
    assert not (shadow / "huge.lock.md").exists()
    assert not (shadow / "min.min.js.md").exists()
    assert not (shadow / "README.txt.md").exists()

    state = json.loads((shadow / "_meta" / "state.json").read_text())
    assert state["total_files"] == 1


def test_init_shadow_special_basenames_detected(shadow_init, tmp_git_repo):
    """Dockerfile and Makefile (basename-keyed languages) get shadows too."""
    (tmp_git_repo / "Dockerfile").write_text("FROM scratch\n")
    (tmp_git_repo / "Makefile").write_text("all:\n\techo hi\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    shadow_init.init_shadow(str(tmp_git_repo))
    shadow = tmp_git_repo / ".shadow"
    assert (shadow / "Dockerfile.md").is_file()
    assert (shadow / "Makefile.md").is_file()
    docker_md = (shadow / "Dockerfile.md").read_text()
    assert "Dockerfile" in docker_md


def test_init_shadow_handles_unknown_language_file(shadow_init, tmp_git_repo):
    """A YAML file is recognized as a source file but has no extractor —
    a shadow is still created (with File-Level only, no symbols)."""
    (tmp_git_repo / "config.yaml").write_text("key: value\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)

    shadow_init.init_shadow(str(tmp_git_repo))
    content = (tmp_git_repo / ".shadow" / "config.yaml.md").read_text()
    assert "YAML" in content
    assert "## File-Level" in content
    assert "## Cross-References" in content


def test_init_shadow_returns_true_with_no_source_files(shadow_init,
                                                      tmp_git_repo,
                                                      reset_diagnostics):
    """Empty repo (no files) still succeeds; warns about empty shadow."""
    ok = reset_diagnostics.init_shadow(str(tmp_git_repo))
    assert ok is True
    msgs = [m for lvl, m in reset_diagnostics._diagnostics if lvl == "warning"]
    assert any("No source files" in m for m in msgs)


def test_init_shadow_summary_printed_to_stdout(shadow_init, tmp_git_repo,
                                               capsys):
    (tmp_git_repo / "a.py").write_text("def x(): pass\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_git_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "m"],
                   cwd=tmp_git_repo, check=True)
    shadow_init.init_shadow(str(tmp_git_repo))
    out = capsys.readouterr().out
    assert "Shadow initialized." in out
    assert "Files:" in out
    assert "Symbols:" in out
    assert "Languages:" in out


# ---------------------------------------------------------------------------
# main() CLI dispatcher (lines 1408-1465)
# ---------------------------------------------------------------------------

def test_main_help_exits_zero(shadow_init, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["shadow-init.py", "--help"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    out = capsys.readouterr().out.lower()
    assert "usage" in out or "initialize" in out


def test_main_unknown_flag_exits_nonzero(shadow_init, monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--definitely-not-a-flag"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code != 0


def test_main_with_explicit_root_initializes_shadow(shadow_init, tmp_git_repo,
                                                    monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(tmp_git_repo)])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    assert (tmp_git_repo / ".shadow" / "_meta" / "state.json").is_file()


def test_main_nonexistent_root_exits_one(shadow_init, tmp_path, monkeypatch,
                                         capsys):
    bogus = tmp_path / "does" / "not" / "exist"
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(bogus)])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "does not exist" in err.lower()


def test_main_root_pointing_to_file_exits_one(shadow_init, tmp_path,
                                              monkeypatch):
    f = tmp_path / "regular.txt"
    f.write_text("x")
    monkeypatch.setattr(sys, "argv", ["shadow-init.py", "--root", str(f)])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 1


def test_main_auto_detect_from_inside_repo(shadow_init, tmp_git_repo,
                                           monkeypatch):
    monkeypatch.chdir(tmp_git_repo)
    monkeypatch.setattr(sys, "argv", ["shadow-init.py"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    assert (tmp_git_repo / ".shadow" / "_meta" / "state.json").is_file()


def test_main_auto_detect_outside_git_exits_one(shadow_init, tmp_path,
                                                monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["shadow-init.py"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 1
    err = capsys.readouterr().err.lower()
    assert "could not detect" in err or "not inside" in err


def test_main_dry_run_does_not_write_shadow(shadow_init, tmp_git_repo,
                                            monkeypatch):
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(tmp_git_repo),
                         "--dry-run"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    assert not (tmp_git_repo / ".shadow").exists()


def test_main_refuses_when_shadow_exists_without_reset(shadow_init,
                                                      tmp_git_repo,
                                                      monkeypatch):
    (tmp_git_repo / ".shadow").mkdir()
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(tmp_git_repo)])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 1


def test_main_reset_replaces_existing_shadow(shadow_init, tmp_git_repo,
                                             monkeypatch):
    (tmp_git_repo / ".shadow").mkdir()
    (tmp_git_repo / ".shadow" / "stale").write_text("old\n")
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(tmp_git_repo),
                         "--reset"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    assert not (tmp_git_repo / ".shadow" / "stale").exists()


def test_main_with_coupon_demo_reset(shadow_init, coupon_demo, monkeypatch):
    """Re-initializing the example coupon-demo via --reset yields a fresh
    .shadow/ that round-trips through state.json correctly."""
    monkeypatch.setattr(sys, "argv",
                        ["shadow-init.py", "--root", str(coupon_demo),
                         "--reset"])
    with pytest.raises(SystemExit) as exc:
        shadow_init.main()
    assert exc.value.code == 0
    state = json.loads(
        (coupon_demo / ".shadow" / "_meta" / "state.json").read_text()
    )
    assert state["version"] == 1
    assert state["last_update_type"] == "init"
    # coupon-demo's tracked files are the .py sources
    assert state["total_files"] >= 1


# ---------------------------------------------------------------------------
# Subprocess smoke test for __main__ block (line 1469)
# ---------------------------------------------------------------------------

@pytest.mark.slow
@pytest.mark.integration
def test_subprocess_main_block_via_cli_help():
    """Exercises the `if __name__ == '__main__'` invocation path."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0
    assert "usage" in result.stdout.lower()
