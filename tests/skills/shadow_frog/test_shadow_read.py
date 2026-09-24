"""Optional agent retrieval targets known files without depending on the Viewer."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest


def sample_shadow(tmp_path):
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    (shadow / "source.py.md").write_text(
        "# Shadow: source.py\n\n## File-Level\n\n"
        "- Importing starts no workers.\n  _(verified, source: user)_\n\n"
        "## `class Worker`\n\n"
        "- Construction opens no files.\n  _(verified, source: exploration)_\n\n"
        "### `Worker.run`\n\n"
        "- Empty input returns immediately.\n  _(verified, source: exploration)_\n\n"
        "## Cross-References\n\n- [Worker contract](_cross/worker-contract.md)\n",
        encoding="utf-8",
    )
    cross = shadow / "_cross"
    cross.mkdir()
    (cross / "worker-contract.md").write_text(
        "# Worker contract\n\n**Category**: contract\n**Refs**:\n"
        "- `source.py::Worker.run`\n- `other.py::dispatch`\n\n"
        "**Discovery**: Dispatch must retain worker ownership.\n\n"
        "_(verified, source: exploration)_\n",
        encoding="utf-8",
    )
    return shadow


def invoke(script, shadow, *args):
    return subprocess.run(
        [sys.executable, str(script), "--shadow-dir", str(shadow), *args],
        cwd=shadow.parent, capture_output=True, text=True, encoding="utf-8", timeout=15,
    )


def identities(text):
    return re.findall(r"\bid=(d_[0-9a-f]{32})", text)


@pytest.mark.parametrize("layout", [".github", ".claude"])
def test_core_only_install_supports_file_and_symbol_reads(repo_root, tmp_path, layout):
    shadow = sample_shadow(tmp_path)
    installed = tmp_path / layout / "skills/shadow-frog"
    shutil.copytree(
        repo_root / "skills/shadow-frog", installed,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    script = installed / "shadow-read.py"
    assert not (installed.parent / "shadow-frog-viewer").exists()
    file_view = invoke(script, shadow, "source.py")
    assert file_view.returncode == 0, file_view.stderr
    assert "Importing starts no workers." in file_view.stdout
    assert "Construction opens no files." in file_view.stdout
    assert "Empty input returns immediately." in file_view.stdout
    assert "Dispatch must retain worker ownership." in file_view.stdout
    symbol_view = invoke(script, shadow, "source.py::Worker.run")
    assert symbol_view.returncode == 0, symbol_view.stderr
    assert "Empty input returns immediately." in symbol_view.stdout
    assert "Dispatch must retain worker ownership." in symbol_view.stdout
    assert "Construction opens no files." not in symbol_view.stdout
    assert "Importing starts no workers." not in symbol_view.stdout
    assert set(identities(symbol_view.stdout)) <= set(identities(file_view.stdout))
    expanded = invoke(script, shadow, "--get", identities(symbol_view.stdout)[0])
    assert expanded.returncode == 0, expanded.stderr


def test_file_read_does_not_parse_unrelated_per_file_shadows(repo_root, tmp_path):
    shadow = sample_shadow(tmp_path)
    (shadow / "unrelated.py.md").write_bytes(b"\xff")
    result = invoke(repo_root / "skills/shadow-frog/shadow-read.py", shadow, "--file", "source.py")
    assert result.returncode == 0
    assert "Empty input" in result.stdout
    assert result.stderr == ""
    assert "unrelated" not in result.stdout


def test_file_and_symbol_positional_targets_conflict_with_other_views(repo_root, tmp_path):
    shadow = sample_shadow(tmp_path)
    result = invoke(
        repo_root / "skills/shadow-frog/shadow-read.py", shadow,
        "source.py", "--search", "workers",
    )
    assert result.returncode == 2
    assert "positional target or an explicit view" in result.stderr


def test_reader_requires_an_explicit_target_or_operation(repo_root, tmp_path):
    shadow = sample_shadow(tmp_path)
    result = invoke(repo_root / "skills/shadow-frog/shadow-read.py", shadow)
    assert result.returncode == 2
    assert "known FILE[::SYMBOL]" in result.stderr


def test_file_first_role_instructions_and_bundled_reference(repo_root):
    core = repo_root / "skills/shadow-frog"
    instructions = (core / "SKILL.md").read_text(encoding="utf-8")
    context = (repo_root / "agent-context.md").read_text(encoding="utf-8")
    user_skill = (repo_root / "skills/shadow-frog-viewer/SKILL.md").read_text(encoding="utf-8")
    assert "**File/symbol navigation is primary.**" in instructions
    assert "shadow-read.py" in instructions and "(retrieval.md)" in instructions
    assert "Native file reads remain normal and uncounted." in instructions
    assert "**Navigate directly**" in context
    assert "user-facing" in context and "mandatory" in context
    assert "**User-facing inspection and visualization**" in user_skill
    assert (core / "retrieval.md").is_file()


def test_reader_without_modules_reports_error_but_raw_knowledge_stays_readable(repo_root, tmp_path):
    shadow = sample_shadow(tmp_path)
    script = tmp_path / "incomplete/shadow-read.py"
    script.parent.mkdir()
    shutil.copyfile(repo_root / "skills/shadow-frog/shadow-read.py", script)
    before = (shadow / "source.py.md").read_bytes()
    result = invoke(script, shadow, "source.py")
    assert result.returncode != 0 and "reinstall" in result.stderr
    assert (shadow / "source.py.md").read_bytes() == before


def test_known_file_read_is_bounded_and_pageable(repo_root, tmp_path):
    shadow = sample_shadow(tmp_path)
    script = repo_root / "skills/shadow-frog/shadow-read.py"
    first = invoke(script, shadow, "source.py", "--limit", "1", "--max-chars", "600")
    assert first.returncode == 0 and len(first.stdout) <= 600
    continuation = re.search(r"--cursor ([0-9a-f]{32}:\d+)", first.stdout).group(1)
    second = invoke(
        script, shadow, "source.py", "--limit", "1", "--max-chars", "600",
        "--cursor", continuation,
    )
    assert second.returncode == 0 and len(second.stdout) <= 600
    assert not set(identities(first.stdout)) & set(identities(second.stdout))


@pytest.mark.parametrize("layout", [".github", ".claude"])
def test_agent_hook_uses_core_reader_without_user_viewer(repo_root, coupon_demo, tmp_path, layout):
    from tests._shell import BASH, HAVE_BASH, shell_path

    if not HAVE_BASH:
        pytest.skip("No POSIX shell available for the existing hook")
    installed = coupon_demo / layout / "skills/shadow-frog"
    shutil.copytree(
        repo_root / "skills/shadow-frog", installed,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    hook = coupon_demo / layout / "hooks/scripts/shadow-frog-pre-tool.sh"
    hook.parent.mkdir(parents=True)
    shutil.copyfile(repo_root / "hook-templates/scripts/shadow-frog-pre-tool.sh", hook)
    assert not (installed.parent / "shadow-frog-viewer").exists()
    env = os.environ.copy()
    env.update(
        PATH=shell_path(), HOME=str(coupon_demo),
        GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull,
        SHADOWFROG_TMP_DIR=str(tmp_path / "hook-state"),
    )
    result = subprocess.run(
        [BASH, str(hook)],
        input=json.dumps({"tool_name": "edit", "tool_input": {"file_path": "cart.py"}}),
        cwd=coupon_demo, env=env, capture_output=True, text=True, encoding="utf-8", timeout=10,
    )
    assert result.returncode == 0, result.stderr
    context = json.loads(result.stdout)["additionalContext"]
    assert "Actionable discoveries" in context and "id=d_" in context
