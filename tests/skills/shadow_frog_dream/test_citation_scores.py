"""Dream artifacts preserve visible citation hints without summing shared ancestry."""

import pytest


def test_merge_keeps_larger_score_while_upgrading_metadata(dream_reconcile, tmp_path):
    path = tmp_path / ".shadow/source.py.md"
    path.parent.mkdir()
    path.write_text(
        "# Shadow: source.py\n\n## `run`\n\n- Claim.\n"
        "  _(uncertain, source: exploration, citation_score: 7)_\n\n## Cross-References\n",
        encoding="utf-8",
    )
    discovery = {"text": "Claim.", "status": "verified", "source": "user", "citation_score": 3}
    assert dream_reconcile.merge_discovery_into_file(
        str(path), "run", discovery, "dream", repo_root=str(tmp_path),
    )
    assert "source: user, citation_score: 7" in path.read_text(encoding="utf-8")
    discovery["citation_score"] = 9
    assert dream_reconcile.merge_discovery_into_file(
        str(path), "run", discovery, "dream", repo_root=str(tmp_path),
    )
    assert "citation_score: 9" in path.read_text(encoding="utf-8")
    assert not dream_reconcile.merge_discovery_into_file(
        str(path), "run", discovery, "dream", repo_root=str(tmp_path),
    )


def test_existing_cross_refs_and_scores_merge_independently(dream_reconcile, tmp_path):
    path = tmp_path / ".shadow/_cross/contract.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "# Contract\n\n**Refs**:\n- `a.py::run`\n\n**Discovery**: Shared claim.\n\n"
        "_(verified, source: exploration, citation_score: 4)_\n",
        encoding="utf-8",
    )
    assert dream_reconcile._merge_refs_into_cross_file(
        str(path), ["b.py::call"], repo_root=str(tmp_path), citation_score=2,
    )
    assert "citation_score: 4" in path.read_text(encoding="utf-8")
    assert dream_reconcile._merge_refs_into_cross_file(
        str(path), ["a.py::run"], repo_root=str(tmp_path), citation_score=7,
    )
    assert "citation_score: 7" in path.read_text(encoding="utf-8")
    assert "`b.py::call`" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("score", [-1, 0.5, True, None, "4"])
@pytest.mark.parametrize("kind", ["discoveries", "cross_cutting"])
def test_invalid_manifest_score_fails_before_any_publication(dream_reconcile, tmp_path, score, kind):
    invalid = {"anchor": "a.py::run", "text": "Claim.", "slug": "contract", "refs": [], "citation_score": score}
    manifest = {"discoveries": [{"anchor": "valid.py::run", "text": "Valid claim."}]}
    if kind == "discoveries":
        manifest["discoveries"].append(invalid)
    else:
        manifest[kind] = [invalid]
    with pytest.raises(dream_reconcile.CitationError, match="nonnegative integer"):
        dream_reconcile.merge_discoveries(str(tmp_path), [("dream/p/id", "id", manifest)])
    assert list(tmp_path.iterdir()) == []


@pytest.mark.slow
@pytest.mark.parametrize("score,expected", [(0, 0), (7, 0), (-1, 1), (False, 1), (0.5, 1), ("3", 1)])
@pytest.mark.parametrize("kind", ["discoveries", "cross_cutting"])
def test_validator_checks_manifest_citation_fields(tmp_git_repo, score, expected, kind):
    from tests.skills.shadow_frog_dream.test_dream_validate import (
        _commit_base, _default_manifest, _default_report, _run_validate, _write_dream,
    )

    base = _commit_base(tmp_git_repo)
    dream_id = "20260924-010000Z-citations"
    entry = {
        "anchor": "a.py::run", "slug": "contract", "refs": ["a.py::run"],
        "text": "The function leaves its input unchanged.", "citation_score": score,
    }
    manifest = _default_manifest(dream_id, **{kind: [entry]})
    _write_dream(tmp_git_repo, dream_id, manifest=manifest, report=_default_report(dream_id, base))
    (tmp_git_repo / ".shadow/a.py.md").write_text(
        "# Shadow: a.py\n\n## `run`\n\n- Leaves its input unchanged.\n"
        "  _(verified, source: exploration, citation_score: 0)_\n",
        encoding="utf-8",
    )
    result = _run_validate(dream_id, tmp_git_repo)
    assert result.returncode == expected, result.stdout + result.stderr
    if expected:
        assert f"{kind}[0].citation_score" in result.stdout
        assert "nonnegative integer" in result.stdout
