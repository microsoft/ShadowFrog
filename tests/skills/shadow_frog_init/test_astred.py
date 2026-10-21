"""Credential-free backend tests; real CLI checks opt in via an executable path."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


METADATA = {
    "name": "astred", "normalizer_version": 1,
    "astred_version": "2.14.30", "astred_release": "2.14.30-test+fixture",
}
SOURCE = '''"""Example, not declarations:
def imaginary(): pass
class Imaginary: pass
BUG: prose
"""
A = 1
B, C = (2, 3)
_ = 4
class Outer:
    LIMIT = 5
    def method(self, PARAM):
        LOCAL = 6
        def inner():
            return A
        return inner()
    class Inner:
        def run(self):
            pass
def factory():
    class Local:
        pass
    return Local
'''


def _export(root, paths=("sample.py",)):
    blocks = {}

    def add(kind, name="", parent=None, file=None, row=1, column=1):
        number = len(blocks) + 1
        block = {"id": number, "blocktype": kind, "name": name,
                 "begrc": {"Row": row, "Column": column}}
        if parent is not None:
            block["parentId"] = parent
        if file is not None:
            block["file"] = file
        blocks[str(number)] = block
        return number

    root_id = add("Root", "__root__")
    for path in paths:
        file = str(root / path)
        file_id = add("File", file, root_id, file)
        add("Statement", parent=file_id, file=file)
        statement = add("Statement", parent=file_id, file=file)
        assignment = add("Assignment", parent=statement, file=file)
        for name in ("A", "B", "C", "_", "lowercase"):
            add("Variable", name, assignment, file)
        outer = add("Class", "Outer", file_id, file, row=9)
        assignment = add("Assignment", parent=outer, file=file)
        add("Field", "LIMIT", assignment, file)
        method = add("Function", "method", outer, file, row=11, column=5)
        params = add("Parameters", parent=method, file=file)
        add("Variable", "PARAM", params, file)
        statement = add("Statement", parent=method, file=file)
        assignment = add("Assignment", parent=statement, file=file)
        add("Variable", "LOCAL", assignment, file)
        add("Function", "inner", statement, file, row=13, column=9)
        nested = add("Class", "Inner", outer, file, row=16, column=5)
        add("Function", "run", nested, file, row=17, column=9)
        factory = add("Function", "factory", file_id, file, row=19)
        statement = add("Statement", parent=factory, file=file)
        add("Class", "Local", statement, file, row=20, column=5)
        add("Function", "", file_id, file)
        add("Invocation", "imaginary", file_id, file)
    return {
        "bljson_version": "2.14.0", "astred_version": METADATA["astred_version"],
        "astred_release": METADATA["astred_release"], "blocks": blocks,
    }


def _snapshot(directory):
    return {str(path.relative_to(directory)): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def _cli(repo_root, root, *args):
    return subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog-init/shadow-init.py"),
         "--root", str(root), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )


@pytest.fixture
def source_repo(tmp_git_repo):
    (tmp_git_repo / "sample.py").write_text(SOURCE, encoding="utf-8")
    return tmp_git_repo


@pytest.fixture
def fake_backend(shadow_init, monkeypatch):
    calls = []

    def extract(root, paths, **kwargs):
        calls.append((list(paths), kwargs))
        return shadow_init._astred.normalize_export(_export(root, paths), root, paths)

    monkeypatch.setattr(shadow_init._astred, "extract", extract)
    return calls


def test_normalizes_lexical_scopes_and_only_module_constants(shadow_init, source_repo):
    actual, metadata = shadow_init._astred.normalize_export(
        _export(source_repo), source_repo, ["sample.py"],
    )
    symbols = {record["qualified_name"]: record for record in actual["sample.py"]}
    assert set(symbols) == {
        "A", "B", "C", "Outer", "Outer.method", "Outer.method.inner",
        "Outer.Inner", "Outer.Inner.run", "factory", "factory.Local",
    }
    assert symbols["Outer.method"]["kind"] == "method"
    assert symbols["Outer.method.inner"]["kind"] == "function"
    assert symbols["Outer.Inner.run"]["parent"] == "Outer.Inner"
    assert symbols["factory.Local"]["parent"] == "factory"
    assert symbols["Outer.method.inner"]["line"] == 13
    assert metadata == METADATA
    assert all("block_id" not in record and "symbol" not in record for record in symbols.values())


@pytest.mark.parametrize("failure", [
    "not-object", "schema", "version", "blocks", "block-id", "missing-file",
    "extra-file", "duplicate-file", "error", "missing-parent", "cycle", "bad-name",
    "missing-class-name", "position",
])
def test_rejects_malformed_or_incomplete_exports(shadow_init, source_repo, failure):
    data = _export(source_repo)
    blocks = data["blocks"]
    function = next(block for block in blocks.values() if block.get("name") == "inner")
    file_block = next(block for block in blocks.values() if block["blocktype"] == "File")
    if failure == "not-object":
        data = []
    elif failure == "schema":
        data["bljson_version"] = "3.0.0"
    elif failure == "version":
        del data["astred_release"]
    elif failure == "blocks":
        data["blocks"] = []
    elif failure == "block-id":
        function["id"] = "invalid"
    elif failure == "missing-file":
        del blocks[str(file_block["id"])]
    elif failure in {"extra-file", "duplicate-file"}:
        extra = copy.deepcopy(file_block)
        extra["id"] = len(blocks) + 1
        if failure == "extra-file":
            extra["file"] = str(source_repo / "unrequested.py")
        blocks[str(extra["id"])] = extra
    elif failure == "error":
        function["blocktype"] = "Error"
    elif failure == "missing-parent":
        function["parentId"] = 999999
    elif failure == "cycle":
        function["parentId"] = function["id"]
    elif failure == "bad-name":
        function["name"] = "not.a.python.identifier"
    elif failure == "missing-class-name":
        next(block for block in blocks.values() if block["blocktype"] == "Class")["name"] = ""
    elif failure == "position":
        function["begrc"]["Row"] = 0
    with pytest.raises(shadow_init._astred.AstredError):
        shadow_init._astred.normalize_export(data, source_repo, ["sample.py"])


def test_batch_process_contract_and_version_pin(shadow_init, source_repo, monkeypatch):
    module = shadow_init._astred
    (source_repo / "second.py").write_text("pass\n", encoding="utf-8")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        assert args[0] == "tool path with spaces"
        assert "-noerrorreport" in args and "-nc" in args and "-q" not in args
        assert args[args.index("treeerr") + 1:args.index("treeerr") + 3] == ["--", "bljson"]
        assert "-nonfatal" not in args
        assert kwargs["timeout"] == 17
        assert kwargs["cwd"] != source_repo
        project = Path(next(arg.removeprefix("-project:") for arg in args if arg.startswith("-project:")))
        output = Path(next(arg.removeprefix("-file:") for arg in args if arg.startswith("-file:")))
        config = json.loads(project.read_text(encoding="utf-8"))
        assert config == {"units": [{"language": "Python", "sources": [
            str(source_repo / "sample.py"), str(source_repo / "second.py"),
        ]}]}
        output.write_text(json.dumps(_export(source_repo, ["sample.py", "second.py"])), encoding="utf-8")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(module.subprocess, "run", run)
    symbols, metadata = module.extract(
        source_repo, ["sample.py", "second.py"], "tool path with spaces", 17, METADATA,
    )
    assert len(calls) == 1 and set(symbols) == {"sample.py", "second.py"}
    assert metadata == METADATA
    assert not Path(next(arg[9:] for arg in calls[0] if arg.startswith("-project:"))).exists()
    with pytest.raises(module.AstredError, match="backend changed"):
        module.extract(source_repo, ["sample.py", "second.py"],
                       "tool path with spaces", 17, {**METADATA, "astred_release": "another-release"})


@pytest.mark.parametrize("failure", ["exit", "timeout", "missing-tool", "missing-output",
                                    "json", "logged-exception", "source-change"])
def test_process_failures_never_become_empty_success(shadow_init, source_repo, monkeypatch, failure):
    module = shadow_init._astred

    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(args, 12)
        if failure == "missing-tool":
            raise FileNotFoundError("fixture tool not found")
        output = Path(next(arg[6:] for arg in args if arg.startswith("-file:")))
        if failure != "missing-output":
            output.write_text("invalid" if failure == "json" else json.dumps(_export(source_repo)),
                              encoding="utf-8")
        if failure == "source-change":
            (source_repo / "sample.py").write_text("# changed\n", encoding="utf-8")
        return SimpleNamespace(
            returncode=7 if failure == "exit" else 0, stdout="",
            stderr="Graph Creation Exceptions:\nfixture failure" if failure == "logged-exception" else "",
        )

    monkeypatch.setattr(module.subprocess, "run", run)
    with pytest.raises(module.AstredError):
        module.extract(source_repo, ["sample.py"])


def test_nested_headings_round_trip_through_viewer(shadow_init, shadow_viewer, source_repo):
    records, _ = shadow_init._astred.normalize_export(_export(source_repo), source_repo, ["sample.py"])
    symbols = [shadow_init._inventory_symbol(record) for record in records["sample.py"]]
    content = shadow_init.build_shadow_content("sample.py", "Python", 22, "unknown", symbols)
    assert "## `class Outer`" in content
    assert "### `class Outer.Inner`" in content
    assert "### `Outer.Inner.run`" in content
    assert "### `class factory.Local`" in content
    assert "### `Outer.method.inner`" in content
    path = source_repo / "shadow.md"
    path.write_text(content, encoding="utf-8")
    parsed = shadow_viewer.parse_shadow_file(path)
    assert parsed["parse_errors"] == []
    assert parsed["symbols"] == [symbol.heading_text for symbol in symbols]


def test_regex_default_does_not_invoke_astred(shadow_init, source_repo, monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Default regex extraction must not invoke Astred.")

    monkeypatch.setattr(shadow_init._astred, "extract", unexpected)
    inventory = shadow_init.extract_inventory(source_repo)
    assert inventory["symbol_backend"] == {"name": "regex"}
    expected = shadow_init.extract_symbols(SOURCE, "Python")
    assert [record["qualified_name"] for record in inventory["files"][0]["symbols"]] == [
        symbol.display_name for symbol in expected
    ]
    assert not (source_repo / ".shadow").exists()


def test_init_persists_provenance_and_update_reuses_unchanged_files(
    shadow_init, source_repo, fake_backend, capsys,
):
    (source_repo / "other.js").write_text("function other() {}\n", encoding="utf-8")
    assert shadow_init.init_shadow(source_repo, symbol_backend="astred", astred_command="custom-tool")
    shadow = source_repo / ".shadow"
    state = json.loads((shadow / "_meta/state.json").read_text(encoding="utf-8"))
    assert state["symbol_backend"] == {**METADATA, "fallback_languages": ["JavaScript"]}
    assert "custom-tool" not in json.dumps(state)
    assert state["total_symbols"] == 11
    assert "using regex for: JavaScript" in capsys.readouterr().err
    before = _snapshot(shadow)
    inventory = shadow_init.extract_inventory(source_repo)
    assert len(fake_backend) == 1
    assert inventory["symbol_backend"] == state["symbol_backend"]
    assert _snapshot(shadow) == before

    (source_repo / "sample.py").write_text(SOURCE + "# changed\n", encoding="utf-8")
    shadow_init.extract_inventory(source_repo, astred_command="custom-tool")
    assert len(fake_backend) == 2
    assert fake_backend[-1][0] == ["sample.py"]
    assert fake_backend[-1][1]["expected_metadata"] == state["symbol_backend"]
    assert _snapshot(shadow) == before


def test_dry_run_astred_does_not_create_shadow(shadow_init, source_repo, fake_backend):
    assert shadow_init.init_shadow(source_repo, dry_run=True, symbol_backend="astred")
    assert len(fake_backend) == 1
    assert not (source_repo / ".shadow").exists()


def test_cache_invalidation_for_normalizer_and_corrupt_cache(shadow_init, source_repo, fake_backend):
    assert shadow_init.init_shadow(source_repo, symbol_backend="astred")
    cache_path = source_repo / ".shadow/_meta/symbols.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8"))
    cache["symbol_backend"]["normalizer_version"] = 0
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    shadow_init.extract_inventory(source_repo)
    assert len(fake_backend) == 2
    cache_path.write_text("{bad", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot read"):
        shadow_init.extract_inventory(source_repo)

    cache["symbol_backend"] = None
    cache_path.write_text(json.dumps(cache), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid backend metadata"):
        shadow_init.extract_inventory(source_repo)


def test_cache_rejects_missing_source_positions(shadow_init, source_repo, fake_backend):
    assert shadow_init.init_shadow(source_repo, symbol_backend="astred")
    path = source_repo / ".shadow/_meta/symbols.json"
    cache = json.loads(path.read_text(encoding="utf-8"))
    del cache["files"][0]["symbols"][0]["line"]
    path.write_text(json.dumps(cache), encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid cached Astred declaration"):
        shadow_init.extract_inventory(source_repo)


def test_changing_fallback_languages_does_not_invalidate_python_cache(
    shadow_init, source_repo, fake_backend,
):
    assert shadow_init.init_shadow(source_repo, symbol_backend="astred")
    (source_repo / "other.js").write_text("function other() {}\n", encoding="utf-8")
    report = shadow_init.extract_inventory(source_repo)
    assert report["symbol_backend"]["fallback_languages"] == ["JavaScript"]
    (source_repo / ".shadow/_meta/symbols.json").write_text(json.dumps(report), encoding="utf-8")
    shadow_init.extract_inventory(source_repo)
    assert len(fake_backend) == 1


def _legacy_shadow(root):
    shadow = root / ".shadow"
    (shadow / "_meta").mkdir(parents=True)
    (shadow / "_cross").mkdir()
    (shadow / "_meta/state.json").write_text('{"version":1,"total_discoveries":3}\n', encoding="utf-8")
    (shadow / "_prefs.md").write_text("User preference\n", encoding="utf-8")
    (shadow / "_cross/finding.md").write_text("**Refs**: sample.py::inner\n", encoding="utf-8")
    (shadow / "sample.py.md").write_text(
        "# Shadow: sample.py\n\n## File-Level\n\n## `inner`\n\n"
        "- Keep this discovery.\n  _(verified, source: user, citation_score: 7)_\n\n"
        "## `class Inner`\n\n## `imaginary`\n\n"
        "## Cross-References\n\n- _cross/finding.md\n", encoding="utf-8",
    )
    return shadow


def test_migration_preview_preserves_every_byte_and_proposes_scope_changes(
    shadow_init, source_repo, fake_backend,
):
    shadow = _legacy_shadow(source_repo)
    before = _snapshot(shadow)
    with pytest.raises(ValueError, match="preview-migration"):
        shadow_init.extract_inventory(source_repo, "astred")
    inventory = shadow_init.extract_inventory(source_repo, "astred", preview=True)
    migration = shadow_init.preview_migration(source_repo, inventory)
    change = migration["files"][0]
    assert migration["applied"] is False and migration["review_required"] is True
    assert {"from": "inner", "to": "Outer.method.inner"} in change["scope_change_candidates"]
    assert {"from": "Inner", "to": "Outer.Inner"} in change["scope_change_candidates"]
    assert change["retained_unmatched"] == ["imaginary"]
    assert _snapshot(shadow) == before


def test_migration_never_guesses_ambiguous_names_and_includes_orphans(shadow_init, source_repo):
    shadow = _legacy_shadow(source_repo)
    with (shadow / "sample.py.md").open("a", encoding="utf-8") as handle:
        handle.write("\n## `old.work`\n")
    (shadow / "deleted.py.md").write_text("# Shadow: deleted.py\n\n## `lost`\n", encoding="utf-8")
    inventory = {"files": [{"path": "sample.py", "symbols": [
        {"qualified_name": name} for name in ("x.work", "y.work")
    ]}]}
    before = _snapshot(shadow)
    migration = shadow_init.preview_migration(source_repo, inventory)
    changes = {entry["path"]: entry for entry in migration["files"]}
    assert changes["sample.py"]["ambiguous"] == [
        {"existing": ["old.work"], "proposed": ["x.work", "y.work"]},
    ]
    assert changes["sample.py"]["scope_change_candidates"] == []
    assert changes["deleted.py"]["source_missing"] is True
    assert changes["deleted.py"]["retained_unmatched"] == ["lost"]
    assert _snapshot(shadow) == before


def test_preview_ignores_heading_examples_inside_discoveries(shadow_init, source_repo):
    shadow = _legacy_shadow(source_repo)
    with (shadow / "sample.py.md").open("a", encoding="utf-8") as handle:
        handle.write("\n~~~markdown\n## `not.a.symbol`\n~~~\n"
                     "\n````markdown\n### `also.not.a.symbol`\n````\n")
    report = shadow_init.preview_migration(source_repo, {"files": []})
    assert report["files"][0]["retained_unmatched"] == ["Inner", "imaginary", "inner"]


def test_cli_read_only_modes(repo_root, source_repo):
    result = _cli(repo_root, source_repo, "--symbols-only")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["symbol_backend"] == {"name": "regex"}
    assert not (source_repo / ".shadow").exists()
    shadow = _legacy_shadow(source_repo)
    before = _snapshot(shadow)
    result = _cli(repo_root, source_repo, "--preview-migration", "--symbol-backend", "regex")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["migration"]["applied"] is False
    assert _snapshot(shadow) == before


@pytest.mark.parametrize("reset", [False, True])
def test_cli_missing_astred_fails_before_writes_or_reset(repo_root, source_repo, reset):
    if reset:
        shadow = _legacy_shadow(source_repo)
        before = _snapshot(shadow)
    args = ["--symbol-backend", "astred", "--astred-command", str(source_repo / "missing-tool")]
    result = _cli(repo_root, source_repo, *args, *(["--reset"] if reset else []))
    assert result.returncode == 1
    assert "Cannot run/read Astred" in result.stderr
    assert "Shadow initialized" not in result.stdout
    if reset:
        assert _snapshot(shadow) == before
    else:
        assert not (source_repo / ".shadow").exists()


@pytest.mark.parametrize("args", [
    ["--astred-timeout", "0"], ["--reset", "--symbols-only"],
    ["--reset", "--preview-migration"],
])
def test_cli_rejects_unsafe_argument_combinations(repo_root, source_repo, args):
    result = _cli(repo_root, source_repo, *args)
    assert result.returncode == 2
    assert not (source_repo / ".shadow").exists()


def test_cli_preview_requires_shadow_before_invoking_astred(repo_root, source_repo):
    result = _cli(repo_root, source_repo, "--preview-migration", "--symbol-backend", "astred",
                  "--astred-command", str(source_repo / "missing-tool"))
    assert result.returncode == 1
    assert "requires an existing .shadow" in result.stderr
    assert "Cannot run/read Astred" not in result.stderr
    assert not (source_repo / ".shadow").exists()


REAL_ASTRED = os.environ.get("SHADOWFROG_ASTRED_TEST_COMMAND")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the authenticated real tool")
def test_real_astred_init_cache_and_shared_update_interface(repo_root, source_repo):
    (source_repo / "other.js").write_text("function other() {}\n", encoding="utf-8")
    result = _cli(repo_root, source_repo, "--symbol-backend", "astred", "--astred-command", REAL_ASTRED)
    assert result.returncode == 0, result.stderr
    shadow = source_repo / ".shadow"
    state = json.loads((shadow / "_meta/state.json").read_text(encoding="utf-8"))
    assert state["symbol_backend"]["name"] == "astred"
    assert state["symbol_backend"]["astred_release"]
    content = (shadow / "sample.py.md").read_text(encoding="utf-8")
    assert "### `class Outer.Inner`" in content and "### `Outer.method.inner`" in content
    assert "imaginary" not in content and "BUG" not in content
    assert "## `B`" in content and "## `C`" in content and "## `_`" not in content
    before = _snapshot(shadow)
    result = _cli(repo_root, source_repo, "--symbols-only", "--astred-command", REAL_ASTRED)
    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    assert inventory["symbol_backend"] == state["symbol_backend"]
    assert _snapshot(shadow) == before
    (source_repo / "sample.py").write_text(SOURCE + "\ndef added(): pass\n", encoding="utf-8")
    result = _cli(repo_root, source_repo, "--symbols-only", "--astred-command", REAL_ASTRED)
    assert result.returncode == 0, result.stderr
    inventory = json.loads(result.stdout)
    python = next(entry for entry in inventory["files"] if entry["path"] == "sample.py")
    assert "added" in {record["qualified_name"] for record in python["symbols"]}
    assert _snapshot(shadow) == before


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the authenticated real tool")
def test_real_astred_migration_preview_never_changes_knowledge(repo_root, source_repo):
    shadow = _legacy_shadow(source_repo)
    before = _snapshot(shadow)
    result = _cli(repo_root, source_repo, "--preview-migration", "--symbol-backend", "astred",
                  "--astred-command", REAL_ASTRED)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["migration"]["applied"] is False
    change = report["migration"]["files"][0]
    assert {"from": "inner", "to": "Outer.method.inner"} in change["scope_change_candidates"]
    assert change["retained_unmatched"] == ["imaginary"]
    assert _snapshot(shadow) == before


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the authenticated real tool")
def test_real_astred_non_python_repository_keeps_explicit_fallback(repo_root, tmp_git_repo):
    (tmp_git_repo / "other.js").write_text("function other() {}\n", encoding="utf-8")
    result = _cli(repo_root, tmp_git_repo, "--symbol-backend", "astred",
                  "--astred-command", REAL_ASTRED)
    assert result.returncode == 0, result.stderr
    state = json.loads((tmp_git_repo / ".shadow/_meta/state.json").read_text(encoding="utf-8"))
    assert state["symbol_backend"]["fallback_languages"] == ["JavaScript"]
    assert state["symbol_backend"]["astred_release"]
    assert state["total_symbols"] == 1


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the authenticated real tool")
@pytest.mark.parametrize("reset", [False, True])
def test_real_astred_reported_parse_error_fails_before_writes(repo_root, source_repo, reset):
    if reset:
        shadow = _legacy_shadow(source_repo)
        before = _snapshot(shadow)
    (source_repo / "sample.py").write_text("def broken(,\n", encoding="utf-8")
    result = _cli(repo_root, source_repo, "--symbol-backend", "astred",
                  "--astred-command", REAL_ASTRED, *(["--reset"] if reset else []))
    assert result.returncode == 1, (result.stdout, result.stderr)
    assert "Astred exited with" in result.stderr
    assert "Errors in" in result.stderr
    assert "Tree-Sitter parsing error" in result.stderr
    assert "Illegal argument" not in result.stderr
    assert "Shadow initialized" not in result.stdout
    if reset:
        assert _snapshot(shadow) == before
    else:
        assert not (source_repo / ".shadow").exists()
