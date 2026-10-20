"""Optional, process-isolated Astred Python declaration backend."""

import hashlib
import json
import os
import re
import subprocess
import tempfile
import warnings
from pathlib import Path

NORMALIZER_VERSION = 1
IDENTITY_KEYS = ("astred_version", "astred_release", "normalizer_version")


class AstredError(RuntimeError):
    """An extraction failed; callers must not treat it as an empty inventory."""


def source_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def normalize_export(data, root, paths):
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
            raise AstredError("Multiple source paths resolve to the same Python file.")
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
                if file_key in covered:
                    raise AstredError(f"Duplicate Astred file block: {block['file']}.")
                covered.add(file_key)
        if covered != set(expected):
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
        for block in blocks.values():
            block_type = block["blocktype"]
            if block_type not in {"Class", "Function", "Variable", "Field"}:
                continue
            name = block.get("name")
            if not name:
                if block_type == "Class":
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
            file_key = _path_key(block["file"])
            if file_key != _path_key(parents[-1]["file"]):
                raise AstredError(f"Inconsistent Astred file ancestry for {name}.")
            path = expected[file_key]
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


def extract(root, paths, command="astred", timeout=180, expected_metadata=None,
            on_warning=None):
    """Run one Python batch. Never write to the source tree or report errors remotely."""
    root = Path(root).resolve()
    try:
        hashes = {path: source_hash(root / path) for path in paths}
        with tempfile.TemporaryDirectory(prefix="shadowfrog-astred-") as directory:
            temp = Path(directory)
            project, output = temp / "project.json", temp / "blocks.json"
            project.write_text(json.dumps({
                "units": [{"language": "Python", "sources": [
                    str(root / path) for path in paths
                ]}],
            }), encoding="utf-8")
            process = subprocess.run(
                [str(command), "-nc", "-noerrorreport", f"-repo:{root}",
                 f"-project:{project}", "treeerr", "--", "bljson", f"-file:{output}"],
                cwd=temp, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout,
            )
            diagnostics = "\n".join((process.stdout, process.stderr)).strip()
            if process.returncode:
                raise AstredError(f"Astred exited with {process.returncode}: {diagnostics}")
            if re.search(r"(?im)^\s*Graph Creation Exceptions\b", diagnostics):
                raise AstredError(f"Astred reported graph creation exceptions: {diagnostics}")
            if process.stderr.strip():
                (on_warning or warnings.warn)(f"Astred diagnostics: {process.stderr.strip()}")
            symbols, metadata = normalize_export(
                json.loads(output.read_text(encoding="utf-8-sig")), root, paths,
            )
        if expected_metadata:
            for key in IDENTITY_KEYS:
                if metadata[key] != expected_metadata.get(key):
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
            f"Cannot run/read Astred: {exc}. Install the authenticated Astred.Tools "
            "package and set --astred-command to its executable."
        ) from exc
