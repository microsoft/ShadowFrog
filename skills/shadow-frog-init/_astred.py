"""Optional, process-isolated Astred Python and C/C++ declaration backend."""

import hashlib
import json
import os
import platform
import re
import subprocess
import tempfile
import warnings
from pathlib import Path

NORMALIZER_VERSION = 2
IDENTITY_KEYS = ("astred_version", "astred_release", "normalizer_version",
                 "project", "project_sha256")
NATIVE_EXTENSIONS = {".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx", ".inl", ".ipp"}
NATIVE_KINDS = {
    "Namespace": "namespace", "Class": "class", "ClassDeclaration": "class",
    "Struct": "struct", "StructDeclaration": "struct",
    "Union": "union", "UnionDeclaration": "union",
    "Enum": "enum", "EnumDeclaration": "enum", "Typedef": "typedef",
    "Function": "function", "FunctionDeclaration": "function", "Macro": "macro",
}
NATIVE_SCOPES = {"Namespace", "Class", "Struct", "Union", "Enum", "Function",
                 "ClassDeclaration", "StructDeclaration", "UnionDeclaration"}


class AstredError(RuntimeError):
    """An extraction failed; callers must not treat it as an empty inventory."""


def tool_config_path():
    if os.name == "nt":
        directory = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        directory = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return directory / "ShadowFrog" / "astred.json"


def tool_configuration():
    config = tool_config_path()
    if not config.exists():
        return {}
    try:
        data = json.loads(config.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("command"), str) or not data["command"]:
            raise ValueError("expected a nonempty command string")
        if not Path(data["command"]).is_absolute():
            raise ValueError("expected an absolute executable path")
        if "dotnet_root" in data and (
            not isinstance(data["dotnet_root"], str) or not data["dotnet_root"]
            or not Path(data["dotnet_root"]).is_absolute()
        ):
            raise ValueError("expected an absolute dotnet_root path")
        return data
    except (OSError, ValueError) as exc:
        raise AstredError(f"Cannot read Astred setup configuration {config}: {exc}. Rerun install-astred.py.") from exc


def resolve_command(command=None):
    return (str(command) if command else os.environ.get("SHADOWFROG_ASTRED_COMMAND")
            or tool_configuration().get("command", "astred"))


def runtime_environment(dotnet_root, env=None):
    result = dict(os.environ if env is None else env)
    result["DOTNET_ROOT"] = str(dotnet_root)
    architecture = {"x86_64": "X64", "amd64": "X64", "aarch64": "ARM64",
                    "arm64": "ARM64", "armv7l": "ARM", "i386": "X86",
                    "i686": "X86", "x86": "X86"}.get(platform.machine().lower())
    if architecture:
        result[f"DOTNET_ROOT_{architecture}"] = str(dotnet_root)
    return result


def source_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def is_native(path):
    return Path(path).suffix.lower() in NATIVE_EXTENSIONS


def project_configuration(root, project_file=None):
    root = Path(root).resolve()
    project = root / (project_file or ".astred.project.json")
    try:
        relative = project.resolve().relative_to(root).as_posix()
    except ValueError as exc:
        raise AstredError("--astred-project must be inside --root so its path is portable.") from exc
    if not project.is_file():
        raise AstredError(
            "C/C++ extraction requires a build-derived .astred.project.json. "
            "Capture a clean component build with Astred TraceBld, run astred astproj, "
            "then supply --astred-project (or put .astred.project.json in --root)."
        )
    return project.resolve(), {"project": relative, "project_sha256": source_hash(project)}


def _native_scopes(block, blocks, seen=None):
    """Prefer semantic ownership for out-of-line methods; never parse debug symbol IDs."""
    seen = set() if seen is None else set(seen)
    if block["id"] in seen:
        raise AstredError("Cycle in Astred native declaration ownership.")
    seen.add(block["id"])
    relations = block.get("relatedBlockIdsByRelationType", {})
    if not isinstance(relations, dict):
        raise AstredError("Invalid Astred native declaration relationships.")
    for relation in ("MemberOf", "DeclaredBy"):
        targets = relations.get(relation, [])
        if not isinstance(targets, list) or any(type(target) is not int for target in targets):
            raise AstredError(f"Invalid Astred native {relation} targets.")
        if targets:
            candidates = []
            for target in targets:
                owner = blocks[str(target)]
                scopes = _native_scopes(owner, blocks, seen)
                if relation == "MemberOf" and owner.get("name"):
                    scopes.append((owner["name"], owner["blocktype"]))
                candidates.append(scopes)
            if any(candidate != candidates[0] for candidate in candidates[1:]):
                raise AstredError(f"Ambiguous Astred native ownership for {block.get('name')}.")
            return candidates[0]
    parent = blocks.get(str(block.get("parentId")))
    if parent is None or parent["blocktype"] == "File":
        return []
    scopes = _native_scopes(parent, blocks, seen)
    if parent["blocktype"] in NATIVE_SCOPES and parent.get("name"):
        scopes.append((parent["name"], parent["blocktype"]))
    return scopes


def _native_record(block, blocks):
    kind = NATIVE_KINDS.get(block["blocktype"])
    if kind is None or not block.get("name"):
        return None
    scopes = [] if kind == "macro" else _native_scopes(block, blocks)
    names = [name for name, _ in scopes] + [block["name"]]
    if any(not isinstance(name, str) or not name.strip()
           or any(ord(char) < 32 or char == "`" for char in name) for name in names):
        raise AstredError("Invalid native declaration name in Astred export.")
    names = [name.replace("::", ".") for name in names]
    parent = ".".join(names[:-1]) or None
    if kind == "function" and scopes and scopes[-1][1] in {
        "Class", "Struct", "Union", "ClassDeclaration", "StructDeclaration", "UnionDeclaration",
    }:
        kind = "method"
    return {"name": names[-1], "kind": kind, "parent": parent,
            "qualified_name": ".".join(names)}


def normalize_export(data, root, paths, allow_dependencies=False):
    """Normalize bljson v2 declarations, validating graph coverage and ancestry."""
    try:
        if not isinstance(data, dict):
            raise AstredError("Astred export must be a JSON object.")
        if not re.fullmatch(r"2\.\d+\.\d+", str(data.get("bljson_version", ""))):
            raise AstredError("Unsupported Astred bljson schema; expected version 2.x.")
        metadata = {"name": "astred", "normalizer_version": NORMALIZER_VERSION}
        for key in ("astred_version", "astred_release"):
            if not isinstance(data.get(key), str) or not data[key].strip():
                raise AstredError(f"Astred export is missing {key}.")
            metadata[key] = data[key]
        blocks = data["blocks"]
        if not isinstance(blocks, dict):
            raise AstredError("Astred export blocks must be an object.")
        expected = {_path_key(root / path): path for path in paths}
        if len(expected) != len(paths):
            raise AstredError("Multiple source paths resolve to the same source file.")
        covered = set()
        for key, block in blocks.items():
            if (
                not isinstance(block, dict) or type(block.get("id")) is not int
                or str(block["id"]) != key or not isinstance(block.get("blocktype"), str)
            ):
                raise AstredError(f"Invalid Astred block: {key}.")
            if block["blocktype"] == "Error":
                raise AstredError(
                    f"Astred parse error in {block.get('file', '<unknown>')} "
                    f"at {block.get('begrc')}. No symbols were accepted."
                )
            if block["blocktype"] == "File":
                file_key = _path_key(block["file"])
                if file_key in covered and not (allow_dependencies and is_native(block["file"])):
                    raise AstredError(f"Duplicate Astred file block: {block['file']}.")
                covered.add(file_key)
        if not set(expected) <= covered or (not allow_dependencies and covered != set(expected)):
            missing = sorted(expected[key] for key in set(expected) - covered)
            extra = sorted(covered - set(expected))
            raise AstredError(f"Astred file coverage mismatch: missing={missing}, extra={extra}.")

        visited = set()
        for key in blocks:
            chain = set()
            while key not in visited:
                if key in chain:
                    raise AstredError("Cycle in Astred block ancestry.")
                chain.add(key)
                parent_id = blocks[key].get("parentId")
                if parent_id is None:
                    break
                key = str(parent_id)
            visited.update(chain)

        result = {path: [] for path in paths}
        native_records = {path: set() for path in paths if is_native(path)}
        for block in blocks.values():
            block_type = block["blocktype"]
            file_key = _path_key(block["file"]) if "file" in block else None
            path = expected.get(file_key)
            if path is None:
                continue
            native = is_native(path)
            if block_type not in (NATIVE_KINDS if native else {"Class", "Function", "Variable", "Field"}):
                continue
            name = block.get("name")
            if not name:
                if block_type == "Class" and not native:
                    raise AstredError("Astred class declaration is missing its name.")
                continue  # Anonymous functions and unnamed graph nodes are not addresses.
            if not isinstance(name, str):
                raise AstredError("Astred declaration name must be a string.")
            parents = []
            seen = {str(block["id"])}
            parent_id = block.get("parentId")
            while parent_id is not None:
                key = str(parent_id)
                if key in seen:
                    raise AstredError(f"Cycle in Astred ancestry for {name}.")
                seen.add(key)
                parent = blocks[key]
                parents.append(parent)
                if parent["blocktype"] == "File":
                    break
                parent_id = parent.get("parentId")
            if not parents or parents[-1]["blocktype"] != "File":
                raise AstredError(f"Astred declaration {name} has no enclosing file.")
            if file_key != _path_key(parents[-1]["file"]):
                raise AstredError(f"Inconsistent Astred file ancestry for {name}.")
            if native:
                record = _native_record(block, blocks)
                if record is None:
                    continue
                row, column = block["begrc"]["Row"], block["begrc"]["Column"]
                if type(row) is not int or type(column) is not int or min(row, column) < 1:
                    raise AstredError(f"Invalid Astred source position for {name}.")
                record.update(line=row, column=column)
                identity = tuple(record[key] for key in
                                 ("qualified_name", "kind", "parent", "line", "column"))
                if identity not in native_records[path]:
                    native_records[path].add(identity)
                    result[path].append(record)
                continue
            scopes = [
                parent for parent in reversed(parents)
                if parent["blocktype"] in {"Class", "Function"} and parent.get("name")
            ]
            if block_type in {"Variable", "Field"}:
                parent_types = [parent["blocktype"] for parent in parents[:-1]]
                if parent_types and parent_types[-1] == "Statement":
                    parent_types.pop()
                if (
                    not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name)
                    or not re.search(r"[A-Z]", name)
                    or not parent_types
                    or any(kind != "Assignment" for kind in parent_types)
                ):
                    continue
                kind = "constant"
            elif block_type == "Class":
                kind = "class"
            else:
                kind = "method" if scopes and scopes[-1]["blocktype"] == "Class" else "function"
            if not name.isidentifier() or any(not scope["name"].isidentifier() for scope in scopes):
                raise AstredError(f"Invalid Python declaration name in Astred export: {name}.")
            parent_name = ".".join(scope["name"] for scope in scopes) or None
            row, column = block["begrc"]["Row"], block["begrc"]["Column"]
            if type(row) is not int or type(column) is not int or min(row, column) < 1:
                raise AstredError(f"Invalid Astred source position for {name}.")
            result[path].append({
                "name": name, "kind": kind, "parent": parent_name,
                "qualified_name": f"{parent_name}.{name}" if parent_name else name,
                "line": row, "column": column,
            })
        for symbols in result.values():
            symbols.sort(key=lambda symbol: (symbol["line"], symbol["column"]))
        return result, metadata
    except (KeyError, TypeError, ValueError) as exc:
        raise AstredError(f"Malformed Astred export: {exc}") from exc


def extract(root, paths, command=None, timeout=180, expected_metadata=None,
            on_warning=None, env=None, project_file=None):
    """Run Python and build-configured native batches without modifying the source tree."""
    root = Path(root).resolve()
    try:
        if command is None and not os.environ.get("SHADOWFROG_ASTRED_COMMAND") and env is None:
            config = tool_configuration()
            command = config.get("command")
            if config.get("dotnet_root"):
                env = runtime_environment(config["dotnet_root"])
        hashes = {path: source_hash(root / path) for path in paths}
        native_paths = [path for path in paths if is_native(path)]
        python_paths = [path for path in paths if Path(path).suffix.lower() == ".py"]
        if len(native_paths) + len(python_paths) != len(paths):
            raise AstredError("Astred extraction supports only Python and C/C++ source paths.")
        native_project, project_metadata = (project_configuration(root, project_file)
                                            if native_paths else (None, {}))
        if expected_metadata and any(project_metadata.get(key) != expected_metadata.get(key)
                                     for key in ("project", "project_sha256")):
            raise AstredError("Astred project changed. Run --preview-migration and review backend metadata.")
        symbols = {}
        metadata = None
        with tempfile.TemporaryDirectory(prefix="shadowfrog-astred-") as directory:
            temp = Path(directory)
            project, output = temp / "project.json", temp / "blocks.json"
            project.write_text(json.dumps({
                "units": [{"language": "Python", "sources": [
                    str(root / path) for path in python_paths
                ]}],
            }), encoding="utf-8")
            batches = []
            if python_paths or not native_paths:
                batches.append((project, python_paths, False))
            if native_paths:
                batches.append((native_project, native_paths, True))
            for batch_project, batch_paths, native in batches:
                output.unlink(missing_ok=True)
                process = subprocess.run(
                    [resolve_command(command), "-nc", "-noerrorreport", f"-repo:{root}",
                     f"-project:{batch_project}", "treeerr", "--", "bljson", f"-file:{output}"],
                    cwd=temp, capture_output=True, text=True, encoding="utf-8",
                    errors="replace", timeout=timeout, env=env,
                )
                diagnostics = "\n".join((process.stdout, process.stderr)).strip()
                if process.returncode:
                    raise AstredError(f"Astred exited with {process.returncode}: {diagnostics}")
                if re.search(r"(?im)^\s*Graph Creation Exceptions\b", diagnostics):
                    raise AstredError(f"Astred reported graph creation exceptions: {diagnostics}")
                if process.stderr.strip():
                    (on_warning or warnings.warn)(f"Astred diagnostics: {process.stderr.strip()}")
                extracted, batch_metadata = normalize_export(
                    json.loads(output.read_text(encoding="utf-8-sig")), root, batch_paths,
                    allow_dependencies=native,
                )
                if metadata is not None and batch_metadata != metadata:
                    raise AstredError("Astred backend changed between extraction batches.")
                metadata = batch_metadata
                symbols.update(extracted)
        metadata.update(project_metadata)
        if native_project and source_hash(native_project) != project_metadata["project_sha256"]:
            raise AstredError("Astred project changed during extraction. Retry.")
        if expected_metadata:
            for key in IDENTITY_KEYS:
                if metadata.get(key) != expected_metadata.get(key):
                    raise AstredError(
                        f"Astred backend changed ({key}). Run --preview-migration and "
                        "review symbol/reference changes before updating backend metadata."
                    )
        for path in paths:
            if source_hash(root / path) != hashes[path]:
                raise AstredError(f"Source changed during Astred extraction: {path}. Retry.")
        return symbols, metadata
    except subprocess.TimeoutExpired as exc:
        raise AstredError(f"Astred timed out after {timeout}s. No symbols were accepted.") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise AstredError(
            f"Cannot run/read Astred: {exc}. Run install-astred.py "
            "from the ShadowFrog checkout, or set --astred-command to an installed executable."
        ) from exc
