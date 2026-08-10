"""Tests for install.sh — Skills+hooks installer.

Exercises: --help, --project requirement, --project with target, idempotency,
expected layout.
"""
import os
import subprocess
from pathlib import Path

import json
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "install.sh"

EXPECTED_SKILLS = [
    "shadow-frog",
    "shadow-frog-init",
    "shadow-frog-update",
    "shadow-frog-dream",
    "shadow-frog-meditate",
    "shadow-frog-viewer",
]


def _base_env(extras: dict | None = None) -> dict:
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin:/usr/local/bin"),
        "HOME": "/nonexistent",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "LANG": "en_US.UTF-8",
    }
    if extras:
        env.update(extras)
    return env


def run_install(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run install.sh with given arguments."""
    env = _base_env(env_extra)
    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.slow
@pytest.mark.integration
class TestInstallHelp:
    def test_help_exits_zero(self):
        result = run_install("--help")
        assert result.returncode == 0
        assert "Usage" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
class TestInstallProject:
    """--project installs skills + hooks into .github/ of target."""

    def test_project_install_creates_skills(self, tmp_path):
        target = tmp_path / "myproject"
        target.mkdir()
        result = run_install("--project", str(target))
        assert result.returncode == 0

        # Check skills copied
        for skill in EXPECTED_SKILLS:
            skill_dir = target / ".github" / "skills" / skill
            assert skill_dir.is_dir(), f"Missing skill dir: {skill}"
            assert (skill_dir / "SKILL.md").is_file(), f"Missing SKILL.md in {skill}"

    def test_project_install_creates_hooks(self, tmp_path):
        target = tmp_path / "myproject"
        target.mkdir()
        result = run_install("--project", str(target))
        assert result.returncode == 0

        hooks_dir = target / ".github" / "hooks"
        assert (hooks_dir / "hooks.json").is_file()
        assert (hooks_dir / "scripts" / "shadow-frog-pre-tool.sh").is_file()
        assert (hooks_dir / "scripts" / "shadow-frog-check-init.sh").is_file()

    def test_project_install_creates_copilot_instructions(self, tmp_path):
        target = tmp_path / "myproject"
        target.mkdir()
        result = run_install("--project", str(target))
        assert result.returncode == 0

        instructions = target / ".github" / "copilot-instructions.md"
        assert instructions.is_file()
        content = instructions.read_text()
        assert "shadowfrog:agent-context" in content

    def test_project_install_idempotent(self, tmp_path):
        """Running twice doesn't fail and re-applies cleanly."""
        target = tmp_path / "myproject"
        target.mkdir()
        r1 = run_install("--project", str(target))
        r2 = run_install("--project", str(target))
        assert r1.returncode == 0
        assert r2.returncode == 0

        # Instructions file should have only ONE shadowfrog block
        instructions = target / ".github" / "copilot-instructions.md"
        content = instructions.read_text()
        assert content.count("<!-- shadowfrog:agent-context -->") == 1

    def test_project_install_no_hooks_flag(self, tmp_path):
        """--no-hooks skips hook installation."""
        target = tmp_path / "myproject"
        target.mkdir()
        result = run_install("--project", str(target), "--no-hooks")
        assert result.returncode == 0

        hooks_dir = target / ".github" / "hooks"
        assert not hooks_dir.exists()

    def test_project_install_no_context_flag(self, tmp_path):
        """--no-context skips copilot-instructions injection."""
        target = tmp_path / "myproject"
        target.mkdir()
        result = run_install("--project", str(target), "--no-context")
        assert result.returncode == 0

        instructions = target / ".github" / "copilot-instructions.md"
        assert not instructions.exists()


@pytest.mark.slow
@pytest.mark.integration
class TestProjectRequired:
    """--project is mandatory; ShadowFrog is never installed globally."""

    def test_no_project_fails(self, tmp_path):
        """Running with no --project must fail loudly (no global install)."""
        fake_home = tmp_path / "home"
        (fake_home / ".copilot").mkdir(parents=True)
        result = run_install(env_extra={"HOME": str(fake_home)})
        assert result.returncode != 0
        assert "--project" in (result.stdout + result.stderr)

    def test_no_project_claude_fails(self, tmp_path):
        """--agent claude with no --project must also fail."""
        fake_home = tmp_path / "home"
        (fake_home / ".claude").mkdir(parents=True)
        result = run_install("--agent", "claude", env_extra={"HOME": str(fake_home)})
        assert result.returncode != 0
        assert "--project" in (result.stdout + result.stderr)


@pytest.mark.slow
@pytest.mark.integration
class TestInvalidAgent:
    def test_invalid_agent_value_fails(self, tmp_path):
        target = tmp_path / "p"
        target.mkdir()
        result = run_install("--agent", "vscode", "--project", str(target))
        assert result.returncode != 0
        assert "must be 'copilot' or 'claude'" in (result.stdout + result.stderr)


@pytest.mark.slow
@pytest.mark.integration
class TestClaudeProjectInstall:
    """--agent claude --project installs into .claude/ + CLAUDE.md."""

    def test_skills_go_to_claude_skills(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        result = run_install("--agent", "claude", "--project", str(target))
        assert result.returncode == 0, result.stderr
        for skill in EXPECTED_SKILLS:
            skill_dir = target / ".claude" / "skills" / skill
            assert skill_dir.is_dir(), f"Missing claude skill dir: {skill}"
            assert (skill_dir / "SKILL.md").is_file()
        # Copilot tree must NOT be created for a claude install
        assert not (target / ".github").exists()

    def test_hooks_create_settings_and_scripts(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        result = run_install("--agent", "claude", "--project", str(target))
        assert result.returncode == 0, result.stderr

        settings = target / ".claude" / "settings.json"
        assert settings.is_file()
        data = json.loads(settings.read_text())
        assert "SessionStart" in data["hooks"]
        assert "PreToolUse" in data["hooks"]

        scripts = target / ".claude" / "hooks" / "scripts"
        assert (scripts / "shadow-frog-check-init.sh").is_file()
        assert (scripts / "shadow-frog-pre-tool.sh").is_file()

    def test_context_injected_into_claude_md(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        result = run_install("--agent", "claude", "--project", str(target))
        assert result.returncode == 0, result.stderr
        claude_md = target / "CLAUDE.md"
        assert claude_md.is_file()
        assert "shadowfrog:agent-context" in claude_md.read_text()

    def test_settings_merge_preserves_existing_hooks(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        claude_dir = target / ".claude"
        claude_dir.mkdir()
        existing = {
            "hooks": {
                "PreToolUse": [
                    {"matcher": "Bash", "hooks": [
                        {"type": "command", "command": "/usr/local/bin/my-guard.sh"}
                    ]}
                ]
            },
            "model": "claude-opus",
        }
        (claude_dir / "settings.json").write_text(json.dumps(existing))

        result = run_install("--agent", "claude", "--project", str(target))
        assert result.returncode == 0, result.stderr

        data = json.loads((claude_dir / "settings.json").read_text())
        # Pre-existing unrelated key preserved
        assert data["model"] == "claude-opus"
        # User's own hook preserved
        cmds = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        assert "/usr/local/bin/my-guard.sh" in cmds
        # Ours added
        assert any("shadow-frog-pre-tool.sh" in c for c in cmds)

    def test_idempotent(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        run_install("--agent", "claude", "--project", str(target))
        r2 = run_install("--agent", "claude", "--project", str(target))
        assert r2.returncode == 0, r2.stderr

        # No duplicate context block
        claude_md = target / "CLAUDE.md"
        assert claude_md.read_text().count("<!-- shadowfrog:agent-context -->") == 1

        # No duplicate hook handler
        data = json.loads((target / ".claude" / "settings.json").read_text())
        cmds = [h["command"] for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
        assert sum("shadow-frog-pre-tool.sh" in c for c in cmds) == 1
