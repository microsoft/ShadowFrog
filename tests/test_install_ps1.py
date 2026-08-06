"""Tests for install.ps1 — Windows/PowerShell installer."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SCRIPT = REPO_ROOT / "install.ps1"
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")

pytestmark = [
    pytest.mark.slow,
    pytest.mark.integration,
    pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed"),
]


def test_utf8_reads_ignore_narrow_default_encoding(tmp_path):
    """Template, context, and existing target text is always decoded as UTF-8."""
    installer = tmp_path / "installer"
    installer.mkdir()
    shutil.copy2(INSTALL_SCRIPT, installer / "install.ps1")
    (installer / "skills").mkdir()
    (installer / "hook-templates" / "scripts").mkdir(parents=True)

    template_matcher = "PréTool—Use"
    template = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": template_matcher,
                    "hooks": [{"type": "command", "command": "echo test"}],
                }
            ]
        }
    }
    (installer / "hook-templates" / "claude-settings.json").write_text(
        json.dumps(template, ensure_ascii=False), encoding="utf-8"
    )

    context_text = "UTF-8 context: café — résumé"
    (installer / "agent-context.md").write_text(context_text, encoding="utf-8")

    project = tmp_path / "project"
    (project / ".claude").mkdir(parents=True)
    existing_model = "modèle—préservé"
    (project / ".claude" / "settings.json").write_text(
        json.dumps({"model": existing_model}, ensure_ascii=False), encoding="utf-8"
    )
    existing_context = "Existing café — preserved"
    (project / "CLAUDE.md").write_text(existing_context, encoding="utf-8")

    env = dict(os.environ)
    env["SHADOWFROG_INSTALLER"] = str(installer / "install.ps1")
    env["SHADOWFROG_TARGET"] = str(project)
    command = (
        "if ($PSVersionTable.PSVersion.Major -ge 6) { "
        "$PSDefaultParameterValues['Get-Content:Encoding'] = 'Latin1' }; "
        "& $env:SHADOWFROG_INSTALLER -Project $env:SHADOWFROG_TARGET -Agent claude"
    )
    result = subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr

    settings = json.loads(
        (project / ".claude" / "settings.json").read_text(encoding="utf-8")
    )
    assert settings["model"] == existing_model
    assert template_matcher in [
        group.get("matcher") for group in settings["hooks"]["PreToolUse"]
    ]

    installed_context = (project / "CLAUDE.md").read_text(encoding="utf-8")
    assert existing_context in installed_context
    assert context_text in installed_context
