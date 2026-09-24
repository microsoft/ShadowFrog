"""Untrusted manifest destinations must stay inside the shadow output tree."""

from copy import deepcopy
import subprocess
import sys

import pytest

from tests.skills.shadow_frog_dream.test_dream_reconcile import (
    SCRIPT,
    _add_bare_remote,
    _default_manifest,
    _default_report,
    _seed_repo,
    make_dream_branch,
)


DREAM_ID = "20260923-040000Z-paths"


def batch(anchor="src/caf\u00e9 tools.py::run", refs=None, slug="shared-behavior"):
    manifest = _default_manifest(DREAM_ID)
    manifest["discoveries"] = [
        {"anchor": anchor, "text": "Empty input is rejected before dispatch."},
    ]
    if refs is not None:
        manifest["cross_cutting"] = [
            {"slug": slug, "title": "Shared behavior", "text": "Callers share state.", "refs": refs},
        ]
    return [(manifest["branch"], DREAM_ID, manifest)]


def tree_bytes(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


@pytest.mark.parametrize("cross_ref", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.slow
def test_cli_rejects_unsafe_manifest_before_any_shadow_writes(
    tmp_git_repo, cross_ref, dry_run,
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    outside = tmp_git_repo.parent / "outside.md"
    outside.write_text("keep this file\n", encoding="utf-8")
    malicious = f"{outside.with_suffix('').as_posix()}::outside"
    entries = batch()
    manifest = entries[0][2]
    if cross_ref:
        manifest["cross_cutting"] = [{
            "slug": "cross", "text": "Invalid reference.",
            "refs": ["src/valid.py::run", malicious],
        }]
    else:
        manifest["discoveries"].append({"anchor": malicious, "text": "Do not write here."})
    make_dream_branch(
        tmp_git_repo, env, "proj", DREAM_ID, manifest,
        report=_default_report(DREAM_ID),
    )
    command = [
        sys.executable, str(SCRIPT), str(tmp_git_repo), "--namespace", "proj",
    ]
    if dry_run:
        command.append("--dry-run")
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ERROR:" in result.stderr and DREAM_ID in result.stderr
    assert "Traceback" not in result.stderr
    assert "Reconciliation complete" not in result.stdout
    assert outside.read_text(encoding="utf-8") == "keep this file\n"
    assert not (tmp_git_repo / ".shadow").exists()


@pytest.mark.parametrize("file_part", [
    "../outside", "src/../../outside", "/absolute/file",
    "C:/Users/example/file", "C:relative", "//server/share/file",
    r"\\server\share\file", r"src\..\outside", "src/file:stream",
    "", ".", "..", "src/./file", "src//file", "src/file\0name",
    "src/.. /outside", "src/.../outside", "src/\nfile", "src/\rfile",
    "NUL", "src/con.py", "aux.txt", "COM1", "lib/lpt9.log",
    "COM¹", "LPT³.txt", "CONIN$", "CONOUT$.log",
])
@pytest.mark.parametrize("cross_ref", [False, True])
@pytest.mark.parametrize("dry_run", [False, True])
def test_manifest_paths_are_validated_before_writing_any_entry(
    dream_reconcile, tmp_path, file_part, cross_ref, dry_run,
):
    entries = batch()
    manifest = entries[0][2]
    invalid = f"{file_part}::run"
    if cross_ref:
        manifest["cross_cutting"] = [
            {"slug": "good", "refs": ["good.py::run"], "text": "Safe first entry."},
            {"slug": "bad", "refs": [invalid], "text": "Unsafe second entry."},
        ]
    else:
        manifest["discoveries"].append({"anchor": invalid, "text": "Unsafe second entry."})
    with pytest.raises(dream_reconcile.UnsafeShadowPath, match=DREAM_ID):
        dream_reconcile.merge_discoveries(str(tmp_path), entries, dry_run=dry_run)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("field", ["slug", "dream_id"])
@pytest.mark.parametrize("value", [
    "../outside", "/outside", "C:/outside", r"..\outside", ".", "..",
    "NUL", "con.txt", "COM1", "lpt³.md", "CONIN$",
])
def test_artifact_names_cannot_escape_their_subdirectory(dream_reconcile, tmp_path, field, value):
    entries = batch(refs=["a.py::run"])
    if field == "slug":
        entries[0][2]["cross_cutting"][0]["slug"] = value
    else:
        entries = [(entries[0][0], value, entries[0][2])]
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discoveries(str(tmp_path), entries)
    assert list(tmp_path.iterdir()) == []


def test_later_invalid_manifest_cannot_partially_publish_the_batch(dream_reconcile, tmp_path):
    entries = batch()
    invalid = batch("../outside::run")
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discoveries(str(tmp_path), entries + invalid)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("value", [None, 1, [], {}])
def test_nontext_anchor_is_rejected_before_outputs(dream_reconcile, tmp_path, value):
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discoveries(str(tmp_path), batch(value))
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("target", [
    "src", "src/target.py.md", "_cross", "_cross/shared-behavior.md",
    "_dreams", f"_dreams/{DREAM_ID}", f"_dreams/{DREAM_ID}/report.md",
    f"_dreams/{DREAM_ID}/manifest.json", f"_dreams/{DREAM_ID}/patch.diff",
    "_dreams/_index.md", "_meta", "_meta/state.json", "_index.md",
])
@pytest.mark.parametrize("dangling", [False, True])
def test_symlink_escapes_are_rejected_before_other_outputs(
    dream_reconcile, tmp_path, make_symlink, target, dangling,
):
    repo = tmp_path / "repo"
    shadow = repo / ".shadow"
    shadow.mkdir(parents=True)
    destination = tmp_path / "outside"
    is_file = target.endswith((".md", ".json", ".diff"))
    if not dangling:
        if is_file:
            destination.write_text("keep outside\n", encoding="utf-8")
        else:
            destination.mkdir()
            (destination / "sentinel").write_text("keep outside\n", encoding="utf-8")
    link = shadow / target
    link.parent.mkdir(parents=True, exist_ok=True)
    make_symlink(link, destination, target_is_directory=not is_file)
    before = tree_bytes(tmp_path)
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discoveries(
            str(repo), batch("src/target.py::run", refs=["src/target.py::run"]),
        )
    assert tree_bytes(tmp_path) == before
    assert link.is_symlink()
    assert destination.exists() is not dangling


def test_outward_shadow_root_symlink_is_not_a_trusted_root(
    dream_reconcile, tmp_path, make_symlink,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    make_symlink(repo / ".shadow", outside, target_is_directory=True)
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discoveries(str(repo), batch())
    assert list(outside.iterdir()) == []


def test_direct_writers_require_contained_destinations(dream_reconcile, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("# Original\n\n**Refs**:\n", encoding="utf-8")
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discovery_into_file(
            str(outside), "run", {"text": "Must not write."}, DREAM_ID, repo_root=str(repo),
        )
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile._merge_refs_into_cross_file(
            str(outside), ["a.py::run"], repo_root=str(repo),
        )
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.add_cross_reference_backpointer(
            str(repo), "../outside", "slug", "Title", DREAM_ID,
        )
    assert outside.read_text(encoding="utf-8") == "# Original\n\n**Refs**:\n"
    assert list(repo.iterdir()) == []


def test_common_path_prefix_is_not_containment(dream_reconcile, tmp_path):
    outside = tmp_path / ".shadow-other/file.md"
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile.merge_discovery_into_file(
            str(outside), "run", {"text": "Must not write."}, DREAM_ID,
            repo_root=str(tmp_path),
        )
    assert not outside.parent.exists()


def test_direct_cross_merge_validates_new_ref_before_mutation(dream_reconcile, tmp_path):
    cross = tmp_path / ".shadow/_cross/shared.md"
    cross.parent.mkdir(parents=True)
    cross.write_text("# Shared\n\n**Refs**:\n- `a.py::run`\n", encoding="utf-8")
    before = cross.read_bytes()
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        dream_reconcile._merge_refs_into_cross_file(
            str(cross), ["b.py::run", "../outside::run"], repo_root=str(tmp_path),
        )
    assert cross.read_bytes() == before


def test_valid_unicode_spaces_and_internal_directory_symlink(
    dream_reconcile, tmp_path, make_symlink,
):
    shadow = tmp_path / ".shadow"
    (shadow / "real").mkdir(parents=True)
    make_symlink(shadow / "alias", shadow / "real", target_is_directory=True)
    anchor = "alias/caf\u00e9 tools..py::run"
    entries = batch(anchor, refs=[anchor, "lib/other.py::call"])
    original = deepcopy(entries)
    merged, skipped = dream_reconcile.merge_discoveries(str(tmp_path), entries)
    assert (merged, skipped) == (2, 0)
    assert entries == original
    path = shadow / "real/caf\u00e9 tools..py.md"
    assert "## `run`" in path.read_text(encoding="utf-8")
    assert "../_cross/shared-behavior.md" in path.read_text(encoding="utf-8")
    assert anchor in (shadow / "_cross/shared-behavior.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("writer", ["mirror_reports", "update_index", "update_state", "rebuild_top_index"])
def test_other_writers_refuse_symlinked_outputs(dream_reconcile, tmp_path, make_symlink, writer):
    repo = tmp_path / "repo"
    shadow = repo / ".shadow"
    shadow.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    target = {
        "mirror_reports": "_dreams", "update_index": "_dreams",
        "update_state": "_meta", "rebuild_top_index": "_index.md",
    }[writer]
    if writer == "rebuild_top_index":
        outside = outside / "index.md"
        outside.write_text("keep\n", encoding="utf-8")
    make_symlink(shadow / target, outside, target_is_directory=writer != "rebuild_top_index")
    before = tree_bytes(tmp_path)
    args = [str(repo)] if writer == "rebuild_top_index" else [str(repo), batch()]
    with pytest.raises(dream_reconcile.UnsafeShadowPath):
        getattr(dream_reconcile, writer)(*args)
    assert tree_bytes(tmp_path) == before
