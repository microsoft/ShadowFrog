"""Tests for skills/shadow-frog-dream/dream-validate.py.

dream-validate.py is the pre-commit hard gate: it inspects
`.shadow/_dreams/<dream_id>/` artifacts (report.md, manifest.json,
patch.diff) and exits 1 on any structural or semantic violation. It
also runs `git diff` against the report's `base_commit` to verify the
agent mirrored discoveries into per-file shadows.

Tests construct a real dream tree in a tmp git repo and invoke the
script via subprocess so the exit-code contract is exercised
end-to-end. Most cases are marked `slow` because they shell out.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-validate.py"


# --- Helpers ---------------------------------------------------------------

def _git_env(home: Path) -> dict:
    return {
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
    }


def _run(cmd, cwd, env=None, check=True):
    return subprocess.run(
        cmd, cwd=cwd, env=env, capture_output=True, text=True, check=check
    )


def _commit_base(repo: Path) -> str:
    """Seed initial commit. Returns the base SHA (full)."""
    env = _git_env(repo)
    (repo / "README.md").write_text("# base\n")
    _run(["git", "add", "-A"], cwd=repo, env=env)
    _run(["git", "commit", "-q", "-m", "base"], cwd=repo, env=env)
    return _run(["git", "rev-parse", "HEAD"], cwd=repo, env=env).stdout.strip()


def _run_validate(dream_id: str, worktree: Path) -> subprocess.CompletedProcess:
    """Invoke dream-validate.py as a subprocess; never raises on non-zero."""
    return subprocess.run(
        [sys.executable, str(SCRIPT), dream_id, str(worktree)],
        capture_output=True, text=True,
    )


def _default_manifest(dream_id: str, **overrides) -> dict:
    m = {
        "dream_id": dream_id,
        "branch": f"dream/proj/{dream_id}",
        "parent_branch": "main",
        "category": "bug hunting",
        "verdict": "useful",
        "title": "Test dream",
        "discoveries": [],
        "cross_cutting": [],
    }
    m.update(overrides)
    return m


def _default_report(dream_id: str, base_commit: str, **fm_overrides) -> str:
    fm = {
        "dream_id": f'"{dream_id}"',
        "category": "bug hunting",
        "verdict": "useful",
        "base_commit": base_commit,
        "branch": f'"dream/proj/{dream_id}"',
        "parent_branch": '"main"',
        "remote": '"origin"',
    }
    fm.update(fm_overrides)
    fm_lines = "\n".join(f"{k}: {v}" for k, v in fm.items())
    return f"---\n{fm_lines}\n---\n\n# {dream_id}\n\nBody.\n"


def _write_dream(
    worktree: Path,
    dream_id: str,
    *,
    manifest: dict | None = None,
    report: str | None = None,
    patch: str = "diff --git a/x b/x\n--- /dev/null\n+++ b/x\n@@\n+x\n",
):
    d = worktree / ".shadow" / "_dreams" / dream_id
    d.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (d / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if report is not None:
        (d / "report.md").write_text(report)
    (d / "patch.diff").write_text(patch)
    return d


# ===========================================================================
# Help / arg parsing
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_help_exits_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Validate dream artifacts" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_no_args_exits_nonzero_and_prints_usage():
    result = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Usage" in result.stdout or "Validate dream artifacts" in result.stdout


# ===========================================================================
# Missing directory / missing files
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_missing_dream_directory_fails(tmp_git_repo):
    result = _run_validate("20260101-000000Z-nope", tmp_git_repo)
    assert result.returncode == 1
    assert "Missing dream subdirectory" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_missing_manifest_and_report_fails(tmp_git_repo):
    dream_id = "20260101-000000Z-missing"
    d = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "patch.diff").write_text("diff\n")
    # No report.md, no manifest.json.

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "Missing" in result.stdout
    assert "report.md" in result.stdout
    assert "manifest.json" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_flat_file_fails(tmp_git_repo):
    """Old flat-file layout (.shadow/_dreams/<id>.md) is rejected."""
    dream_id = "20260101-000000Z-flat"
    dreams = tmp_git_repo / ".shadow" / "_dreams"
    dreams.mkdir(parents=True, exist_ok=True)
    (dreams / f"{dream_id}.md").write_text("# flat\n")  # forbidden

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "Flat file found" in result.stdout
    assert "MUST use subdirectory" in result.stdout


# ===========================================================================
# Empty patch.diff
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_empty_patch_diff_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-emptypatch"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id),
        report=_default_report(dream_id, base),
        patch="",  # empty
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "patch.diff is empty" in result.stdout
    assert "completion criterion #1" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_whitespace_only_patch_diff_fails(tmp_git_repo):
    """Non-empty but content-free patch.diff (no diff markers) is rejected."""
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-blankpatch"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id),
        report=_default_report(dream_id, base),
        patch="   \n\n  \n",  # whitespace only — no diff markers
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "no unified-diff markers" in result.stdout


# ===========================================================================
# Frontmatter / manifest dream_id mismatch
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_report_dream_id_mismatch_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-good"
    wrong_id = "20260101-000000Z-WRONG"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id),
        # report says a different dream_id
        report=_default_report(wrong_id, base).replace(
            f"# {wrong_id}", f"# {dream_id}"
        ),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "dream_id mismatch" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_manifest_dream_id_mismatch_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-good"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest("20260101-000000Z-DIFFERENT"),
        report=_default_report(dream_id, base),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "manifest.json dream_id mismatch" in result.stdout


# ===========================================================================
# Required fields / invalid category / invalid verdict
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_missing_required_field_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-missingfield"
    m = _default_manifest(dream_id)
    del m["title"]
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=m,
        report=_default_report(dream_id, base),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "missing required fields" in result.stdout
    assert "title" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_invalid_category_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-badcat"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id, category="navel-gazing"),
        report=_default_report(dream_id, base),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "Invalid category" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_invalid_verdict_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-badverdict"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id, verdict="maybe"),
        report=_default_report(dream_id, base),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "Invalid verdict" in result.stdout


# ===========================================================================
# Discovery op validation (update/refute not yet supported)
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
@pytest.mark.parametrize("bad_op", ["update", "refute"])
def test_unsupported_discovery_op_fails(tmp_git_repo, bad_op):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-badop"
    # We don't actually need shadow mirroring for the op check to fire (it
    # runs before the mirror check). Use an empty discoveries list shaped
    # in a way that the op error blocks first.
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": bad_op,
                "anchor": "x.py::y",
                "text": "Something here.",
            }],
        ),
        report=_default_report(dream_id, base),
    )
    # Add a mirror so the OTHER hard-error (mirror check) doesn't drown
    # out our op message — though both can co-occur.
    _mirror_shadow(tmp_git_repo, "x.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert f'op = "{bad_op}"' in result.stdout
    assert 'only "add" is supported' in result.stdout


def _mirror_shadow(repo: Path, source_rel: str, base_sha: str):
    """Add a .shadow/<source>.md file as an uncommitted change, so the
    `git diff base..HEAD` + `git status` mirror check sees it.
    """
    shadow = repo / ".shadow" / (source_rel + ".md")
    shadow.parent.mkdir(parents=True, exist_ok=True)
    shadow.write_text("## `y`\n\n- mirrored discovery\n")


# ===========================================================================
# Discoveries-not-mirrored check (the "LOST at merge time" message —
# discoveries ARE merged via the manifest, but PR reviewers can't see them
# in context without per-file shadow updates).
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_discoveries_not_mirrored_to_shadows_fails(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-nomirror"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add",
                "anchor": "src/auth.py::validate",
                "text": "Always returns True on empty input.",
            }],
        ),
        report=_default_report(dream_id, base),
    )
    # Note: no .shadow/src/auth.py.md created → mirror check should fail.
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    # The actionable wording mentions PR reviewers / mirror.
    assert "NO .shadow/*.md files outside _dreams/" in result.stdout
    assert "PR reviewers" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_missing_base_commit_in_report_fails_when_discoveries_present(tmp_git_repo):
    _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-nobase"
    # Build a report WITHOUT base_commit by leaving it out of frontmatter.
    report = (
        "---\n"
        f'dream_id: "{dream_id}"\n'
        "category: bug hunting\n"
        "verdict: useful\n"
        "---\n\n"
        f"# {dream_id}\n"
    )
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add", "anchor": "x.py::y", "text": "claim"
            }],
        ),
        report=report,
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 1
    assert "missing `base_commit`" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_unresolvable_base_commit_warns_not_errors(tmp_git_repo):
    """A base_commit that git can't resolve should downgrade the mirror
    check to a warning (return 0), not a misleading hard 'no shadows' error."""
    _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-badbase"
    bogus = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add", "anchor": "x.py::y", "text": "claim"
            }],
        ),
        report=_default_report(dream_id, bogus),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "could not resolve base_commit" in result.stdout
    # The misleading hard-error wording must NOT appear.
    assert "NO .shadow/*.md files outside _dreams/" not in result.stdout


# ===========================================================================
# Label-triage WARNINGS (non-blocking)
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_label_triage_emits_warning_but_does_not_fail(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-warn"
    # Trigger the 'bug' label signal: phrase "silently fails" matches.
    # Provide labels: [] so the warning fires.
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add",
                "anchor": "src/foo.py::bar",
                "text": "The cache silently fails on expired tokens.",
                "labels": [],
            }],
        ),
        report=_default_report(dream_id, base),
    )
    _mirror_shadow(tmp_git_repo, "src/foo.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    # Happy: succeeds (warning is non-blocking).
    assert result.returncode == 0, result.stdout + result.stderr
    assert "WARNING" in result.stdout
    assert "'bug' signal" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_label_triage_silent_when_label_already_set(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-labelled"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add",
                "anchor": "src/foo.py::bar",
                "text": "The cache silently fails on expired tokens.",
                "labels": ["bug"],
            }],
        ),
        report=_default_report(dream_id, base),
    )
    _mirror_shadow(tmp_git_repo, "src/foo.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "'bug' signal" not in result.stdout


# ===========================================================================
# Happy path
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_happy_path_passes(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-happy"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[{
                "op": "add",
                "anchor": "src/foo.py::bar",
                "text": "Returns 0 for empty list.",
                "labels": [],
            }],
        ),
        report=_default_report(dream_id, base),
    )
    _mirror_shadow(tmp_git_repo, "src/foo.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validation passed" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_happy_path_with_no_discoveries_skips_mirror_check(tmp_git_repo):
    """A manifest with zero discoveries doesn't need to mirror any shadow."""
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-empty"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(dream_id, discoveries=[]),
        report=_default_report(dream_id, base),
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr


# ===========================================================================
# B1 regression — bare-string discoveries must not crash validation.
# The reconciler normalizes `"some text"` to `{"text": "..."}`; validate
# must accept exactly what the reconciler does instead of raising on str.
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_bare_string_discovery_is_accepted(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-strdisc"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=["Silently returns None on expired tokens."],
        ),
        report=_default_report(dream_id, base),
    )
    _mirror_shadow(tmp_git_repo, "src/foo.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == 0, result.stdout + result.stderr
    # Must not blow up with an attribute/type error on the str entry.
    assert "Traceback" not in result.stderr
    assert "Validation passed" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_non_dict_non_str_discovery_is_reported_not_crashed(tmp_git_repo):
    base = _commit_base(tmp_git_repo)
    dream_id = "20260101-000000Z-baddisc"
    _write_dream(
        tmp_git_repo, dream_id,
        manifest=_default_manifest(
            dream_id,
            discoveries=[12345],
        ),
        report=_default_report(dream_id, base),
    )
    _mirror_shadow(tmp_git_repo, "src/foo.py", base)

    result = _run_validate(dream_id, tmp_git_repo)
    assert "Traceback" not in result.stderr
    assert result.returncode == 1
    assert "must be a string or object" in result.stdout
