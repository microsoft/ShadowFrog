"""C/C++ project handoff, declaration normalization, and header-dependent updates."""

import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


VERSION = {"astred_version": "2.14.34", "astred_release": "2.14.34-test"}
HEADER = """#pragma once
namespace Os {
class Device {
public:
    Device();
    ~Device();
    int Run(int count);
    int operator()(int value) const;
};
template<typename T> struct Holder { T value; };
using Alias = Device;
enum class State { Ready, Busy };
union Data { int number; char byte; };
}
"""
CPP = """#include "sample.h"
Os::Device::Device() {}
Os::Device::~Device() {}
int Os::Device::Run(int count) { return count; }
int Os::Device::operator()(int value) const { return value; }
#if SHADOWFROG_NATIVE
OS_API int NativeEntry(_In_ int value) { return FORCED_VALUE + value; }
#else
int WrongConfiguration() { return 0; }
#endif
"""
C = """#define OS_LIMIT 8
typedef struct _OS_RECORD { int value; } OS_RECORD;
int OsRead(OS_RECORD *record) { return record->value; }
"""


@pytest.fixture
def native_repo(tmp_git_repo):
    root = tmp_git_repo
    for path, text in {"sample.c": C, "sample.cpp": CPP, "sample.h": HEADER,
                       "config.h": "#define OS_API\n#define _In_\n#define FORCED_VALUE 1\n"}.items():
        (root / path).write_text(text, encoding="utf-8")
    (root / ".astred.project.json").write_text(json.dumps({"units": [
        {"language": "C", "sources": ["%REPO%/sample.c"]},
        {"language": "C++", "sources": ["%REPO%/sample.cpp"],
         "includepaths": ["%REPO%"], "forced": ["%REPO%/config.h"],
         "defines": {"SHADOWFROG_NATIVE": "1"}},
    ]}), encoding="utf-8")
    return root


def _export(root):
    blocks = {}

    def add(kind, name="", parent=None, file=None, row=1, **extra):
        number = len(blocks) + 1
        blocks[str(number)] = {
            "id": number, "blocktype": kind, "name": name,
            "begrc": {"Row": row, "Column": 1}, **extra,
        }
        if parent is not None:
            blocks[str(number)]["parentId"] = parent
        if file is not None:
            blocks[str(number)]["file"] = str(file)
        return number

    root_id = add("Root")
    header = root / "sample.h"
    header_id = add("File", str(header), root_id, header)
    namespace = add("Namespace", "Os", header_id, header)
    device = add("Class", "Device", namespace, header, row=3)
    declarations = {}
    for row, name in enumerate(("Device", "~Device", "Run", "operator()"), 5):
        declarations[name] = add("FunctionDeclaration", name, device, header, row=row)
    for row, kind, name in ((10, "Struct", "Holder"), (11, "Typedef", "Alias"),
                            (12, "Enum", "State"), (13, "Union", "Data")):
        add(kind, name, namespace, header, row=row)
    for path in ("sample.c", "sample.cpp", "config.h"):
        file = root / path
        file_id = add("File", str(file), root_id, file)
        if path == "sample.c":
            add("Macro", "OS_LIMIT", file_id, file)
            add("Struct", "_OS_RECORD", file_id, file, row=2)
            add("Typedef", "OS_RECORD", file_id, file, row=2)
            function = add("Function", "OsRead", file_id, file, row=3)
            params = add("Parameters", parent=function, file=file)
            add("Variable", "ARG", params, file)
            add("Invocation", "not_a_declaration", function, file)
        if path == "sample.cpp":
            for row, name in enumerate(declarations, 2):
                relation = ("MemberOf", device) if name in {"Device", "Run"} else (
                    "DeclaredBy", declarations[name],
                )
                add("Function", name, file_id, file, row=row,
                    relatedBlockIdsByRelationType={relation[0]: [relation[1]]})
            add("Function", "NativeEntry", file_id, file, row=7)
    dependency = root.parent / "sdk.h"
    dependency_id = add("File", str(dependency), root_id, dependency)
    add("FunctionDeclaration", "DoNotShadowTheSDK", dependency_id, dependency)
    return {"bljson_version": "2.14.0", **VERSION, "blocks": blocks}


@pytest.fixture
def native_backend(shadow_init, native_repo, monkeypatch):
    calls = []

    def run(args, **kwargs):
        project = Path(next(arg[9:] for arg in args if arg.startswith("-project:")))
        assert "-noerrorreport" in args
        assert kwargs["cwd"] != native_repo
        output = Path(next(arg[6:] for arg in args if arg.startswith("-file:")))
        config = json.loads(project.read_text(encoding="utf-8"))
        if config["units"][0]["language"] == "Python":
            file = config["units"][0]["sources"][0]
            blocks = {
                "1": {"id": 1, "blocktype": "File", "file": file},
                "2": {"id": 2, "blocktype": "Function", "file": file, "parentId": 1,
                      "name": "PythonEntry", "begrc": {"Row": 1, "Column": 1}},
            }
            data = {"bljson_version": "2.14.0", **VERSION, "blocks": blocks}
        else:
            assert project in {native_repo / ".astred.project.json", native_repo / "configs/compile.json"}
            data = _export(native_repo)
        output.write_text(json.dumps(data), encoding="utf-8")
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(shadow_init._astred, "subprocess", SimpleNamespace(
        run=run, TimeoutExpired=subprocess.TimeoutExpired,
    ))
    return calls


def _snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_native_normalization_uses_semantic_ownership_and_keeps_header_declarations(
    shadow_init, native_repo,
):
    paths = ["sample.c", "sample.cpp", "sample.h", "config.h"]
    symbols, _ = shadow_init._astred.normalize_export(
        _export(native_repo), native_repo, paths, allow_dependencies=True,
    )
    assert {record["qualified_name"] for record in symbols["sample.cpp"]} == {
        "Os.Device.Device", "Os.Device.~Device", "Os.Device.Run",
        "Os.Device.operator()", "NativeEntry",
    }
    assert all(record["kind"] == "method" for record in symbols["sample.cpp"][:4])
    assert {record["kind"] for record in symbols["sample.h"]} == {
        "namespace", "class", "method", "struct", "typedef", "enum", "union",
    }
    assert {record["qualified_name"] for record in symbols["sample.c"]} == {
        "OS_LIMIT", "_OS_RECORD", "OS_RECORD", "OsRead",
    }
    assert symbols["config.h"] == []


def test_native_header_interpretations_deduplicate_but_overloads_remain(shadow_init, native_repo):
    data = _export(native_repo)
    blocks = data["blocks"]
    header = next(block for block in blocks.values() if block.get("name") == str(native_repo / "sample.h"))
    duplicate = copy.deepcopy(header)
    duplicate["id"] = len(blocks) + 1
    blocks[str(duplicate["id"])] = duplicate
    run = next(block for block in blocks.values() if block.get("name") == "Run")
    for row in (run["begrc"]["Row"], 30):
        duplicate = copy.deepcopy(run)
        duplicate["id"] = len(blocks) + 1
        duplicate["begrc"]["Row"] = row
        blocks[str(duplicate["id"])] = duplicate
    symbols, _ = shadow_init._astred.normalize_export(
        data, native_repo, ["sample.h"], allow_dependencies=True,
    )
    assert len([record for record in symbols["sample.h"]
                if record["qualified_name"] == "Os.Device.Run"]) == 2


@pytest.mark.parametrize("failure", ["missing-target", "cycle", "bad-relations", "bad-targets", "bad-name"])
def test_native_malformed_ownership_fails_visibly(shadow_init, native_repo, failure):
    data = _export(native_repo)
    run = next(block for block in data["blocks"].values()
               if block.get("name") == "Run" and block["blocktype"] == "Function")
    if failure == "missing-target":
        run["relatedBlockIdsByRelationType"] = {"MemberOf": [99999]}
    elif failure == "cycle":
        run["relatedBlockIdsByRelationType"] = {"DeclaredBy": [run["id"]]}
    elif failure == "bad-relations":
        run["relatedBlockIdsByRelationType"] = None
    elif failure == "bad-targets":
        run["relatedBlockIdsByRelationType"] = {"MemberOf": "invalid"}
    else:
        run["name"] = "bad`\nheading"
    with pytest.raises(shadow_init._astred.AstredError):
        shadow_init._astred.normalize_export(data, native_repo, ["sample.cpp"], allow_dependencies=True)


def test_native_missing_coverage_is_not_an_empty_success(shadow_init, native_repo):
    with pytest.raises(shadow_init._astred.AstredError, match="coverage mismatch"):
        shadow_init._astred.normalize_export(
            _export(native_repo), native_repo, ["unused.h"], allow_dependencies=True,
        )


def test_project_is_passed_unchanged_and_recorded_portably(
    shadow_init, native_repo, native_backend,
):
    project = native_repo / ".astred.project.json"
    before = project.read_bytes()
    assert shadow_init.init_shadow(native_repo, symbol_backend="astred")
    metadata = json.loads((native_repo / ".shadow/_meta/state.json").read_text())["symbol_backend"]
    assert metadata["project"] == ".astred.project.json"
    assert metadata["project_sha256"] == shadow_init._astred.source_hash(project)
    assert str(native_repo) not in json.dumps(metadata)
    assert project.read_bytes() == before
    inventory = shadow_init.extract_inventory(native_repo)
    assert len(native_backend) == 2  # Native input is deliberately not source-only cached.
    assert all(entry["backend"] == "astred" for entry in inventory["files"]
               if entry["path"].endswith((".c", ".cpp", ".h")))
    (native_repo / "sample.h").write_text(HEADER + "// header changed\n", encoding="utf-8")
    shadow_init.extract_inventory(native_repo)
    assert len(native_backend) == 3


def test_compile_settings_change_requires_read_only_migration(
    shadow_init, native_repo, native_backend,
):
    assert shadow_init.init_shadow(native_repo, symbol_backend="astred")
    shadow = native_repo / ".shadow"
    before = _snapshot(shadow)
    project = native_repo / ".astred.project.json"
    project.write_text(project.read_text() + "\n", encoding="utf-8")
    with pytest.raises(shadow_init._astred.AstredError, match="project changed.*preview-migration"):
        shadow_init.extract_inventory(native_repo)
    assert len(native_backend) == 1
    inventory = shadow_init.extract_inventory(native_repo, preview=True)
    assert shadow_init.preview_migration(native_repo, inventory)["applied"] is False
    assert _snapshot(shadow) == before


def test_native_refresh_preserves_python_cache_in_mixed_project(
    shadow_init, native_repo, native_backend,
):
    (native_repo / "sample.py").write_text("def PythonEntry(): pass\n", encoding="utf-8")
    assert shadow_init.init_shadow(native_repo, symbol_backend="astred")
    assert len(native_backend) == 2
    metadata = json.loads((native_repo / ".shadow/_meta/state.json").read_text())["symbol_backend"]
    inventory = shadow_init.extract_inventory(native_repo)
    assert inventory["symbol_backend"] == metadata
    assert len(native_backend) == 3
    assert f"-project:{native_repo / '.astred.project.json'}" in native_backend[-1]
    python = next(entry for entry in inventory["files"] if entry["path"] == "sample.py")
    assert python["symbols"][0]["qualified_name"] == "PythonEntry"
    (native_repo / "sample.py").write_text("def PythonEntry(): return 1\n", encoding="utf-8")
    shadow_init.extract_inventory(native_repo)
    assert len(native_backend) == 5


def test_custom_project_is_inherited_on_updates(shadow_init, native_repo, native_backend):
    project = native_repo / "configs/compile.json"
    project.parent.mkdir()
    (native_repo / ".astred.project.json").rename(project)
    assert shadow_init.init_shadow(
        native_repo, symbol_backend="astred", astred_project="configs/compile.json",
    )
    metadata = json.loads((native_repo / ".shadow/_meta/state.json").read_text())["symbol_backend"]
    assert metadata["project"] == "configs/compile.json"
    assert shadow_init.extract_inventory(native_repo)["symbol_backend"] == metadata
    assert len(native_backend) == 2


def test_native_headings_round_trip_through_viewer(shadow_init, shadow_viewer, native_repo):
    symbols, _ = shadow_init._astred.normalize_export(
        _export(native_repo), native_repo, ["sample.h"], allow_dependencies=True,
    )
    records = [shadow_init._inventory_symbol(record) for record in symbols["sample.h"]]
    content = shadow_init.build_shadow_content("sample.h", "C++", 15, "unknown", records)
    path = native_repo / "shadow.md"
    path.write_text(content, encoding="utf-8")
    parsed = shadow_viewer.parse_shadow_file(path)
    assert parsed["parse_errors"] == []
    assert parsed["symbols"] == [record.heading_text for record in records]
    assert "## `namespace Os`" in content
    assert "### `union Os.Data`" in content


def test_project_outside_root_is_not_saved_in_shared_metadata(shadow_init, native_repo, tmp_path):
    project = tmp_path / "outside.json"
    project.write_text('{"units":[]}', encoding="utf-8")
    with pytest.raises(shadow_init._astred.AstredError, match="inside --root"):
        shadow_init._astred.project_configuration(native_repo, project)


@pytest.mark.parametrize("mode", [[], ["--symbols-only"], ["--reset"]])
def test_cli_native_preflight_never_writes_without_build_project(repo_root, native_repo, mode):
    (native_repo / ".astred.project.json").unlink()
    result = subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog-init/shadow-init.py"),
         "--root", str(native_repo), "--symbol-backend", "astred",
         "--astred-command", str(native_repo / "missing-tool"), *mode],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 1
    assert "build-derived .astred.project.json" in result.stderr
    assert "Cannot run/read Astred" not in result.stderr
    assert not (native_repo / ".shadow").exists()


@pytest.mark.parametrize("mode", [[], ["--symbols-only"]])
def test_cli_rejects_native_project_with_regex_backend(repo_root, native_repo, mode):
    result = subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog-init/shadow-init.py"),
         "--root", str(native_repo), "--astred-project", ".astred.project.json", *mode],
        capture_output=True, text=True, timeout=90,
    )
    assert result.returncode == 1
    assert "--astred-project requires the Astred symbol backend" in result.stderr
    assert not (native_repo / ".shadow").exists()


REAL_ASTRED = os.environ.get("SHADOWFROG_ASTRED_TEST_COMMAND")


@pytest.mark.integration
@pytest.mark.slow
@pytest.mark.skipif(not REAL_ASTRED, reason="Set SHADOWFROG_ASTRED_TEST_COMMAND for the real tool")
def test_real_native_init_and_header_dependent_update(shadow_init, native_repo, repo_root):
    script = str(repo_root / "skills/shadow-frog-init/shadow-init.py")
    command = [sys.executable, script, "--root", str(native_repo),
               "--astred-command", REAL_ASTRED, "--astred-project", ".astred.project.json"]
    initialized = subprocess.run(
        command + ["--symbol-backend", "astred"], capture_output=True, text=True, timeout=180,
    )
    assert initialized.returncode == 0, (initialized.stdout, initialized.stderr)
    shadow = native_repo / ".shadow"
    before = _snapshot(shadow)
    header = (shadow / "sample.h.md").read_text(encoding="utf-8")
    assert "### `Os.Device.Run`" in header and "### `Os.Device.~Device`" in header
    assert "### `Os.Device.operator()`" in header and "### `union Os.Data`" in header
    native = (shadow / "sample.cpp.md").read_text(encoding="utf-8")
    assert "### `Os.Device.Run`" in native and "## `NativeEntry`" in native
    assert "WrongConfiguration" not in native
    assert "## `OsRead`" in (shadow / "sample.c.md").read_text(encoding="utf-8")
    (native_repo / "config.h").write_text(
        "#define OS_API\n#define _In_\n#define FORCED_VALUE 1\n#undef SHADOWFROG_NATIVE\n",
        encoding="utf-8",
    )
    updated = subprocess.run(command + ["--symbols-only"], capture_output=True, text=True, timeout=180)
    assert updated.returncode == 0, (updated.stdout, updated.stderr)
    report = json.loads(updated.stdout)
    symbols = next(entry["symbols"] for entry in report["files"] if entry["path"] == "sample.cpp")
    names = {record["qualified_name"] for record in symbols}
    assert "WrongConfiguration" in names and "NativeEntry" not in names
    assert _snapshot(shadow) == before
    (native_repo / "sample.c").write_text("int broken(,\n", encoding="utf-8")
    failed = subprocess.run(
        command + ["--reset"], capture_output=True, text=True, timeout=180,
    )
    assert failed.returncode == 1, (failed.stdout, failed.stderr)
    assert "Astred exited with" in failed.stderr and "Tree-Sitter parsing error" in failed.stderr
    assert _snapshot(shadow) == before
