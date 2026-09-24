"""Viewer reads visible scores without recording implicit views or creating state."""

import os
import shutil
import subprocess
import sys

import pytest


def test_metadata_score_is_not_discovery_text(shadow_viewer):
    discovery = shadow_viewer.parse_discovery(
        "- Rejects empty input.",
        ["  _(verified, source: user, labels: [bug], citation_score: 7)_"],
    )
    assert discovery["text"] == "Rejects empty input."
    assert discovery["citation_score"] == 7
    assert discovery["status"] == "verified" and discovery["labels"] == ["bug"]
    assert shadow_viewer.parse_discovery("- Older claim.", ["  _(verified, source: exploration)_"])["citation_score"] == 0
    assert shadow_viewer.parse_discovery("- Preference.", ["  _(source: user, citation_score: 3)_"])["citation_score"] == 3


def test_reading_and_ranking_scores_never_rewrites_shadow(shadow_viewer, tmp_path, capsys):
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    path = shadow / "source.py.md"
    path.write_text(
        "# Shadow: source.py\n\n## `run`\n\n"
        "- Rare finding.\n  _(verified, source: exploration, labels: [bug], citation_score: 1)_\n\n"
        "- Popular finding.\n  _(verified, source: exploration, labels: [bug], citation_score: 9)_\n\n"
        "- User constraint.\n  _(verified, source: user, labels: [bug], citation_score: 0)_\n\n"
        "## Cross-References\n",
        encoding="utf-8",
    )
    before = path.read_bytes()
    shadow_viewer.view_top(shadow, "source.py", "bug", 3, 0)
    output = capsys.readouterr().out
    assert output.index("User constraint") < output.index("Popular finding") < output.index("Rare finding")
    assert "citation_score: 9" in output
    shadow_viewer.view_search(shadow, "finding")
    output = capsys.readouterr().out
    assert output.index("Popular finding") < output.index("Rare finding")
    assert path.read_bytes() == before
    assert sorted(p.name for p in shadow.iterdir()) == ["source.py.md"]


@pytest.mark.parametrize("location", ["source.py.md", "_prefs.md", "_cross/contract.md"])
@pytest.mark.parametrize("score", ["-1", "1.5", "true"])
def test_structural_audit_reports_invalid_visible_score(shadow_viewer, tmp_path, capsys, location, score):
    shadow = tmp_path / ".shadow"
    path = shadow / location
    path.parent.mkdir(parents=True)
    content = "# Shadow: source.py\n\n## `run`\n\n- Claim.\n"
    metadata = f"  _(verified, source: exploration, citation_score: {score})_\n"
    if location == "_prefs.md":
        content = "# Preferences\n\n- Claim.\n"
        metadata = f"  _(source: user, citation_score: {score})_\n"
    elif location.startswith("_cross"):
        content = "# Contract\n\n**Category**: contract\n**Refs**:\n- `source.py::run`\n\n**Discovery**: Claim.\n\n"
    path.write_text(content + metadata, encoding="utf-8")
    assert shadow_viewer.view_check_invariants(shadow) == 1
    assert "citation_score" in capsys.readouterr().out


@pytest.mark.slow
def test_direct_read_then_cite_then_user_viewer(repo_root, tmp_path):
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    path = shadow / "source.py.md"
    path.write_text(
        "# Shadow: source.py\n\n## `run`\n\n- Rejects empty input.\n"
        "  _(verified, source: exploration, citation_score: 0)_\n",
        encoding="utf-8",
    )
    assert "citation_score: 0" in path.read_text(encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog/shadow-cite.py"), str(path),
         "--symbol", "run", "--text", "Rejects empty input."],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert "0 -> 1" in result.stdout
    before_view = path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"),
         "--shadow-dir", str(shadow), "--search", "empty"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert "citation_score: 1" in result.stdout and path.read_bytes() == before_view


@pytest.mark.parametrize("layout", [".github", ".claude"])
def test_installed_counter_helper_has_no_hidden_store_or_bytecode(repo_root, tmp_path, layout):
    installed = tmp_path / layout / "skills"
    for name in ("shadow-frog", "shadow-frog-viewer"):
        shutil.copytree(
            repo_root / "skills" / name, installed / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    target = shadow / "sample.py.md"
    target.write_text(
        "# Shadow: sample.py\n\n## `run`\n\n- Keep the input unchanged.\n"
        "  _(verified, source: user, citation_score: 0)_\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    result = subprocess.run(
        [sys.executable, str(installed / "shadow-frog/shadow-cite.py"), str(target),
         "--symbol", "run", "--text", "Keep the input unchanged."],
        env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert "citation_score: 1" in target.read_text(encoding="utf-8")
    assert not list(tmp_path.rglob("*.sqlite3"))
    assert not list(installed.rglob("*.pyc"))
    assert not list(shadow.rglob("*.citation.lock"))
