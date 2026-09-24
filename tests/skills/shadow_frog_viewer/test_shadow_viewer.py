"""User-facing Viewer CLI shares knowledge identities with the optional agent helper."""

import os
import re
import shutil
import subprocess
import sys

import pytest


@pytest.mark.parametrize("layout", [".github", ".claude"])
def test_user_and_agent_interfaces_share_citation_identity(repo_root, tmp_path, layout):
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    content = "# Shadow: source.py\n\n## `run`\n\n- Shared knowledge.\n  _(verified, source: exploration)_\n"
    (shadow / "source.py.md").write_text(content, encoding="utf-8")
    for skill in ("shadow-frog", "shadow-frog-viewer"):
        shutil.copytree(
            repo_root / "skills" / skill, tmp_path / layout / "skills" / skill,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
    viewer = tmp_path / layout / "skills/shadow-frog-viewer/shadow-viewer.py"
    reader = tmp_path / layout / "skills/shadow-frog/shadow-read.py"
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)

    def call(script, *args):
        return subprocess.run(
            [sys.executable, str(script), "--shadow-dir", str(shadow), *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", check=True,
        ).stdout

    overview = call(viewer)
    assert "Shadow Knowledge Base Summary" in overview
    user_result = call(viewer, "--search", "Shared")
    assert "citation_score=0" in user_result
    agent_result = call(reader, "source.py::run")
    assert "citation_score=1" in agent_result
    assert re.findall(r"id=(d_[0-9a-f]{32})", user_result) == re.findall(
        r"id=(d_[0-9a-f]{32})", agent_result,
    )
    assert (shadow / "source.py.md").read_text(encoding="utf-8") == content
    assert not list((tmp_path / layout / "skills").rglob("*.pyc"))
    assert "for users" in call(viewer, "--help")
    assert "Optional bounded agent retrieval" in call(reader, "--help")
