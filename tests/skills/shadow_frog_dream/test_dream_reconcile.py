"""Tests for skills/shadow-frog-dream/dream-reconcile.py.

Covers the high-leverage internal helpers (heading scan, dedup, bidirectional
back-pointers, manifest/index parsers, verification, top-index rebuild) and a
couple of CLI smoke paths via subprocess. Heavy integration paths
(`merge_discoveries`, `mirror_reports`, `update_index`, `update_state`,
`cleanup_branches` actual delete, `main`) are exercised indirectly through the
unit-level helpers they delegate to plus the CLI smoke tests.

Regression coverage:
* B4 — `add_cross_reference_backpointer` idempotency + false-positive substring.
* B5 — `find_cross_references_heading` case-insensitive match.
* B6 — `rebuild_top_index` reproduces the committed coupon-demo header.
* PREFIX-FALSE-PASS — `verify_reconciliation` and `cleanup_branches` must not
  confuse a short dream_id with a longer indexed one.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "shadow-frog-dream" / "dream-reconcile.py"


# --- Git env / helpers ---------------------------------------------------

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


def _git(*args, cwd, env, check=True):
    return _run(["git", *args], cwd=cwd, env=env, check=check)


def _seed_repo(repo: Path):
    """Seed an empty initialized git repo with one commit and a bare origin."""
    env = _git_env(repo)
    (repo / "README.md").write_text("# test\n")
    _git("add", "-A", cwd=repo, env=env)
    _git("commit", "-q", "-m", "init", cwd=repo, env=env)
    return env


def _add_bare_remote(repo: Path, env: dict) -> Path:
    """Create a bare repo alongside `repo` and wire it up as origin/main."""
    bare = repo.parent / f"{repo.name}.git"
    _git("init", "--bare", "-q", str(bare), cwd=repo.parent, env=env)
    _git("remote", "add", "origin", f"file://{bare}", cwd=repo, env=env)
    _git("push", "-q", "-u", "origin", "main", cwd=repo, env=env)
    # Mark origin/HEAD so cleanup_branches default-branch detection works.
    _git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main",
         cwd=repo, env=env)
    return bare


def make_dream_branch(
    repo: Path,
    env: dict,
    dream_ns: str,
    dream_id: str,
    manifest: dict,
    report: str | None = None,
    patch: str | None = "diff --git a/x b/x\n",
    extra_files: dict[str, str] | None = None,
) -> str:
    """Create + push a dream branch from main with the given artifacts.

    Returns the short branch name. Leaves the repo checked out on its
    original branch.
    """
    branch = f"dream/{dream_ns}/{dream_id}"
    original = _git("rev-parse", "--abbrev-ref", "HEAD",
                    cwd=repo, env=env).stdout.strip()

    _git("checkout", "-q", "-b", branch, cwd=repo, env=env)

    dream_dir = repo / ".shadow" / "_dreams" / dream_id
    dream_dir.mkdir(parents=True, exist_ok=True)
    (dream_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    if report is not None:
        (dream_dir / "report.md").write_text(report)
    if patch is not None:
        (dream_dir / "patch.diff").write_text(patch)

    if extra_files:
        for rel, content in extra_files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    _git("add", "-A", cwd=repo, env=env)
    _git("commit", "-q", "-m", f"dream: {dream_id}", cwd=repo, env=env)
    _git("push", "-q", "origin", branch, cwd=repo, env=env)

    _git("checkout", "-q", original, cwd=repo, env=env)
    return branch


def _default_report(dream_id: str, base_commit: str = "abcdef1234567890") -> str:
    return (
        "---\n"
        f"dream_id: \"{dream_id}\"\n"
        "category: bug hunting\n"
        "verdict: useful\n"
        f"base_commit: {base_commit}\n"
        "---\n\n"
        f"# {dream_id}\n\nBody.\n"
    )


def _default_manifest(dream_id: str, dream_ns: str = "proj") -> dict:
    return {
        "dream_id": dream_id,
        "branch": f"dream/{dream_ns}/{dream_id}",
        "parent_branch": "main",
        "category": "bug hunting",
        "verdict": "useful",
        "title": f"Title for {dream_id}",
        "discoveries": [],
        "cross_cutting": [],
    }


# ===========================================================================
# find_cross_references_heading — B5 case-insensitive regression
# ===========================================================================

@pytest.mark.parametrize("heading", [
    "## Cross-References",
    "## cross-references",
    "## Cross-references",
    "## CROSS-REFERENCES",
])
def test_find_cross_references_heading_is_case_insensitive(dream_reconcile, heading):
    lines = ["# Shadow\n", "\n", "## `foo`\n", "\n", "- discovery\n", "\n", heading + "\n"]
    assert dream_reconcile.find_cross_references_heading(lines) == 6


def test_find_cross_references_heading_missing_returns_negative_one(dream_reconcile):
    lines = ["# Shadow\n", "## `foo`\n", "- d\n"]
    assert dream_reconcile.find_cross_references_heading(lines) == -1


def test_find_cross_references_heading_ignores_trailing_whitespace(dream_reconcile):
    # The heading may have trailing whitespace before the newline.
    lines = ["## Cross-References   \n"]
    assert dream_reconcile.find_cross_references_heading(lines) == 0


# ===========================================================================
# is_duplicate_discovery
# ===========================================================================

def test_is_duplicate_discovery_exact_match(dream_reconcile):
    existing = ["- The cache silently fails on expired tokens\n"]
    assert dream_reconcile.is_duplicate_discovery(
        existing, "The cache silently fails on expired tokens"
    ) is True


def test_is_duplicate_discovery_exact_match_ignores_whitespace_and_case(dream_reconcile):
    existing = ["- The   Cache  Silently  fails  on  Expired  Tokens\n"]
    assert dream_reconcile.is_duplicate_discovery(
        existing, "the cache silently fails on expired tokens"
    ) is True


def test_is_duplicate_discovery_short_distinct_keyword_not_merged(dream_reconcile):
    """Short discoveries differing by ONE keyword must NOT be deduped."""
    existing = ["- returns None on expired tokens\n"]
    new = "returns None on revoked tokens"
    assert dream_reconcile.is_duplicate_discovery(existing, new) is False


def test_is_duplicate_discovery_long_high_overlap_is_merged(dream_reconcile):
    """Fuzzy match: 20 words, 1 swapped → 19/20 = 0.95 → merge."""
    existing_words = (
        "alpha beta gamma delta epsilon zeta eta theta iota kappa "
        "lambda mu nu xi omicron pi rho sigma tau upsilon"
    ).split()
    new_words = existing_words[:-1] + ["DIFFERENT_WORD"]
    existing = ["- " + " ".join(existing_words) + "\n"]
    new = " ".join(new_words)
    # overlap = |intersection| / |new_set| = 19/20 = 0.95 ≥ DEDUP_THRESHOLD
    assert dream_reconcile.is_duplicate_discovery(existing, new) is True


def test_is_duplicate_discovery_completely_different_text(dream_reconcile):
    existing = ["- alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu\n"]
    new = "totally unrelated content that shares no words at all here entirely now"
    assert dream_reconcile.is_duplicate_discovery(existing, new) is False


def test_is_duplicate_discovery_skips_non_bullet_lines(dream_reconcile):
    existing = ["## `foo`\n", "  _(verified, source: exploration)_\n"]
    assert dream_reconcile.is_duplicate_discovery(existing, "something new") is False


def test_is_duplicate_discovery_empty_new_text(dream_reconcile):
    assert dream_reconcile.is_duplicate_discovery(["- whatever\n"], "") is False


# ===========================================================================
# _ensure_cross_references_section
# ===========================================================================

def test_ensure_cross_references_appends_when_missing(dream_reconcile):
    lines = ["# Shadow\n", "\n", "## `foo`\n", "- d\n"]
    out = dream_reconcile._ensure_cross_references_section(list(lines))
    joined = "".join(out)
    assert "## Cross-References" in joined
    assert "_No cross-cutting discoveries yet._" in joined


def test_ensure_cross_references_is_idempotent(dream_reconcile):
    lines = ["## `foo`\n", "## Cross-References\n", "\n", "_No cross-cutting discoveries yet._\n"]
    out = dream_reconcile._ensure_cross_references_section(list(lines))
    # Section count should remain 1.
    assert "".join(out).count("## Cross-References") == 1


def test_ensure_cross_references_finds_lowercase_heading(dream_reconcile):
    """A lowercase rewrite must NOT cause a duplicate section append."""
    lines = ["## `foo`\n", "## cross-references\n", "- [link](x.md)\n"]
    out = dream_reconcile._ensure_cross_references_section(list(lines))
    assert "".join(out).count("## Cross-References") == 0  # not added
    assert "".join(out).count("## cross-references") == 1


# ===========================================================================
# add_cross_reference_backpointer — B4 regression
# ===========================================================================

def test_add_cross_reference_backpointer_bootstraps_missing_shadow(dream_reconcile, tmp_path):
    repo = tmp_path
    (repo / ".shadow").mkdir()

    written = dream_reconcile.add_cross_reference_backpointer(
        str(repo), "src/foo.py", "my-slug", "My Slug Title", "20260101-000000Z-x"
    )
    assert written is True

    shadow = repo / ".shadow" / "src" / "foo.py.md"
    assert shadow.is_file()
    text = shadow.read_text()
    assert "## File-Level" in text
    assert "## Cross-References" in text
    # depth=1 for src/foo.py → prefix "../"
    assert "[My Slug Title](../_cross/my-slug.md)" in text
    assert "dream: 20260101-000000Z-x" in text


def test_add_cross_reference_backpointer_is_idempotent(dream_reconcile, tmp_path):
    """Calling twice must not duplicate the back-pointer line (B4)."""
    repo = tmp_path
    (repo / ".shadow").mkdir()

    dream_reconcile.add_cross_reference_backpointer(
        str(repo), "foo.py", "slug-x", "Title X", "20260101-000000Z-x"
    )
    written2 = dream_reconcile.add_cross_reference_backpointer(
        str(repo), "foo.py", "slug-x", "Title X", "20260101-000000Z-x"
    )
    assert written2 is False

    shadow = repo / ".shadow" / "foo.py.md"
    body = shadow.read_text()
    assert body.count("[Title X](_cross/slug-x.md)") == 1


def test_add_cross_reference_backpointer_false_positive_substring(dream_reconcile, tmp_path):
    """A discovery body that MENTIONS `_cross/foo.md` (no link) used to falsely
    inhibit the back-pointer. The function must add the proper link.
    """
    repo = tmp_path
    (repo / ".shadow").mkdir()
    shadow = repo / ".shadow" / "bar.py.md"
    shadow.write_text(
        "## `bar`\n"
        "\n"
        "- A discovery that talks about `_cross/foo.md` but is not a link.\n"
        "  _(verified, source: exploration)_\n"
        "\n"
        "## Cross-References\n"
        "\n"
        "_No cross-cutting discoveries yet._\n"
    )

    written = dream_reconcile.add_cross_reference_backpointer(
        str(repo), "bar.py", "foo", "Foo Title", "20260101-000000Z-y"
    )
    assert written is True

    body = shadow.read_text()
    assert "[Foo Title](_cross/foo.md)" in body
    # Discovery line preserved.
    assert "talks about `_cross/foo.md`" in body


@pytest.mark.parametrize("file_part,expected_prefix,expected_dir", [
    ("foo.py", "", "."),
    ("src/foo.py", "../", "src"),
    ("a/b/c/foo.py", "../../../", "a/b/c"),
])
def test_add_cross_reference_backpointer_depth_math(
    dream_reconcile, tmp_path, file_part, expected_prefix, expected_dir
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_reconcile.add_cross_reference_backpointer(
        str(repo), file_part, "slug", "T", "20260101-000000Z-z"
    )
    shadow = repo / ".shadow" / (file_part + ".md")
    assert shadow.is_file()
    expected = f"[T]({expected_prefix}_cross/slug.md)"
    assert expected in shadow.read_text()


def test_add_cross_reference_backpointer_replaces_placeholder(
    dream_reconcile, tmp_path
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    shadow = repo / ".shadow" / "foo.py.md"
    shadow.write_text(
        "## `foo`\n\n- d\n\n## Cross-References\n\n_No cross-cutting discoveries yet._\n"
    )
    dream_reconcile.add_cross_reference_backpointer(
        str(repo), "foo.py", "slug-y", "Y", "20260101-000000Z-q"
    )
    body = shadow.read_text()
    assert "_No cross-cutting discoveries yet._" not in body
    assert "[Y](_cross/slug-y.md)" in body


# ===========================================================================
# merge_discovery_into_file
# ===========================================================================

def test_merge_discovery_creates_new_shadow(dream_reconcile, tmp_path):
    shadow = tmp_path / ".shadow" / "src" / "foo.py.md"
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow),
        "do_thing",
        {"text": "Returns None on empty input.",
         "status": "verified", "source": "exploration"},
        "20260101-000000Z-x",
    )
    assert written is True
    assert shadow.is_file()
    body = shadow.read_text()
    assert "## `do_thing`" in body
    assert "- Returns None on empty input." in body
    assert "_(verified, source: exploration)_" in body
    assert "Dream report: `_dreams/20260101-000000Z-x/`" in body
    assert "## Cross-References" in body


def test_merge_discovery_replaces_no_discoveries_placeholder(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n_No discoveries yet._\n\n## Cross-References\n\n_No cross-cutting discoveries yet._\n"
    )
    dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "Actual discovery.", "status": "verified",
         "source": "exploration"},
        "20260101-000000Z-x",
    )
    body = shadow.read_text()
    assert "_No discoveries yet._" not in body
    assert "- Actual discovery." in body


def test_merge_discovery_appends_after_existing_bullets(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- existing discovery\n  _(verified, source: exploration)_\n\n"
        "## Cross-References\n\n_No cross-cutting discoveries yet._\n"
    )
    dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "Another discovery.", "status": "verified",
         "source": "exploration"},
        "20260101-000000Z-x",
    )
    body = shadow.read_text()
    assert "- existing discovery" in body
    assert "- Another discovery." in body
    # Discovery order preserved.
    assert body.index("existing discovery") < body.index("Another discovery")


def test_merge_discovery_skips_duplicate(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- already here\n  _(verified, source: exploration)_\n\n"
        "## Cross-References\n\n_No cross-cutting discoveries yet._\n"
    )
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "already here", "status": "verified", "source": "exploration"},
        "20260101-000000Z-x",
    )
    assert written is False


def test_merge_discovery_empty_text_returns_false(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo", {"text": "   "}, "20260101-000000Z-x"
    )
    assert written is False
    assert not shadow.exists()


# ===========================================================================
# B15 — exact-text duplicate metadata merge (union labels, upgrade source
# trust, upgrade uncertain->verified; never touch refuted)
# ===========================================================================

def _xref_footer():
    return "\n## Cross-References\n\n_No cross-cutting discoveries yet._\n"


def test_merge_discovery_unions_labels_on_exact_match(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- claim text here\n  _(verified, source: exploration, labels: [bug])_\n"
        + _xref_footer()
    )
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "claim text here", "status": "verified",
         "source": "exploration", "labels": ["security"]},
        "20260101-000000Z-x",
    )
    assert written is True
    body = shadow.read_text()
    # Single merged discovery line (no duplicate appended).
    assert body.count("- claim text here") == 1
    assert "labels: [bug, security]" in body


def test_merge_discovery_upgrades_source_trust(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- some claim\n  _(verified, source: exploration)_\n"
        + _xref_footer()
    )
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "some claim", "status": "verified", "source": "user"},
        "20260101-000000Z-x",
    )
    assert written is True
    body = shadow.read_text()
    assert "source: user" in body
    assert "source: exploration" not in body


def test_merge_discovery_upgrades_uncertain_to_verified(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- a claim\n  _(uncertain, source: exploration)_\n"
        + _xref_footer()
    )
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "a claim", "status": "verified", "source": "exploration"},
        "20260101-000000Z-x",
    )
    assert written is True
    body = shadow.read_text()
    assert "_(verified, source: exploration)_" in body


def test_merge_discovery_never_downgrades_verified(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- a claim\n  _(verified, source: exploration)_\n"
        + _xref_footer()
    )
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "a claim", "status": "uncertain", "source": "exploration"},
        "20260101-000000Z-x",
    )
    # Nothing to upgrade → no write.
    assert written is False
    assert "_(verified, source: exploration)_" in shadow.read_text()


def test_merge_discovery_never_touches_refuted(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- a claim\n  _(refuted, source: exploration)_\n"
        + _xref_footer()
    )
    # New says verified — but refuted is a deliberate signal; status must hold.
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": "a claim", "status": "verified", "source": "exploration",
         "labels": ["bug"]},
        "20260101-000000Z-x",
    )
    body = shadow.read_text()
    # Status stays refuted; labels may still union.
    assert "refuted" in body
    assert "verified" not in body


def test_merge_discovery_fuzzy_match_still_skips(dream_reconcile, tmp_path):
    """Fuzzy (non-exact) near-duplicates must NOT trigger a metadata merge —
    they may be genuinely different claims."""
    existing = ("- the function returns none when the input list is "
                "completely empty or missing entirely")
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        f"## `foo`\n\n{existing}\n  _(uncertain, source: exploration)_\n"
        + _xref_footer()
    )
    # Same words minus one — high overlap but not exact.
    new_text = ("the function returns none when the input list is "
                "completely empty or missing")
    written = dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {"text": new_text, "status": "verified", "source": "exploration"},
        "20260101-000000Z-x",
    )
    # Treated as fuzzy duplicate → skipped, metadata untouched.
    assert written is False
    assert "_(uncertain, source: exploration)_" in shadow.read_text()


class TestMetaMergeHelpers:
    def test_parse_meta_line_full(self, dream_reconcile):
        assert dream_reconcile._parse_meta_line(
            "  _(verified, source: user, labels: [bug, security])_\n"
        ) == ("verified", "user", ["bug", "security"])

    def test_parse_meta_line_no_labels(self, dream_reconcile):
        assert dream_reconcile._parse_meta_line(
            "  _(uncertain, source: exploration)_"
        ) == ("uncertain", "exploration", [])

    def test_parse_meta_line_non_meta_returns_none(self, dream_reconcile):
        assert dream_reconcile._parse_meta_line("- not a meta line") is None

    def test_merge_meta_unions_and_upgrades(self, dream_reconcile):
        status, source, labels, changed = dream_reconcile._merge_meta(
            ("uncertain", "exploration", ["bug"]), "verified", "user", ["security"]
        )
        assert (status, source, labels) == ("verified", "user", ["bug", "security"])
        assert changed is True

    def test_merge_meta_no_change(self, dream_reconcile):
        status, source, labels, changed = dream_reconcile._merge_meta(
            ("verified", "user", ["bug"]), "verified", "user", ["bug"]
        )
        assert changed is False

    def test_merge_meta_refuted_untouched(self, dream_reconcile):
        status, _, _, _ = dream_reconcile._merge_meta(
            ("refuted", "exploration", []), "verified", "exploration", []
        )
        assert status == "refuted"


def test_merge_discovery_with_also_involves_and_labels(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    dream_reconcile.merge_discovery_into_file(
        str(shadow), "foo",
        {
            "text": "Something with refs.", "status": "verified",
            "source": "exploration", "labels": ["bug", "security"],
            "also_involves": ["other.py::thing", "more.py::stuff"],
        },
        "20260101-000000Z-x",
    )
    body = shadow.read_text()
    assert "labels: [bug, security]" in body
    assert "Also involves: `other.py::thing`, `more.py::stuff`" in body


# ===========================================================================
# _read_indexed_dream_ids / _read_indexed_branches
# ===========================================================================

INDEX_FIXTURE = """# Dream Experiments

| dream_id | category | verdict | title | branch | parent | tip_commit |
|----------|----------|---------|-------|--------|--------|------------|
| 20260101-000000Z-alpha | bug hunting | useful | First | dream/p/20260101-000000Z-alpha | main | 1234567 |
| 20260102-000000Z-beta | investigation | useful | Second | dream/p/20260102-000000Z-beta | main | abcdef0 |
| 20260103-000000Z-gamma | feature design | dead_end | Third | dream/p/20260103-000000Z-gamma | main | 9876543 |
"""


def _write_index(repo: Path, content: str = INDEX_FIXTURE):
    idx = repo / ".shadow" / "_dreams" / "_index.md"
    idx.parent.mkdir(parents=True, exist_ok=True)
    idx.write_text(content)


def test_read_indexed_dream_ids_parses_table(dream_reconcile, tmp_path):
    _write_index(tmp_path)
    ids = dream_reconcile._read_indexed_dream_ids(str(tmp_path))
    assert ids == {
        "20260101-000000Z-alpha",
        "20260102-000000Z-beta",
        "20260103-000000Z-gamma",
    }


def test_read_indexed_dream_ids_returns_empty_when_missing(dream_reconcile, tmp_path):
    assert dream_reconcile._read_indexed_dream_ids(str(tmp_path)) == set()


def test_read_indexed_branches_parses_table(dream_reconcile, tmp_path):
    _write_index(tmp_path)
    rows = dream_reconcile._read_indexed_branches(str(tmp_path))
    assert ("dream/p/20260101-000000Z-alpha", "20260101-000000Z-alpha") in rows
    assert ("dream/p/20260102-000000Z-beta", "20260102-000000Z-beta") in rows
    assert len(rows) == 3


# ===========================================================================
# verify_reconciliation — PREFIX FALSE-PASS regression
# ===========================================================================

def _seed_dream_artifacts(repo: Path, dream_id: str):
    d = repo / ".shadow" / "_dreams" / dream_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text(f"# {dream_id}\n")
    (d / "manifest.json").write_text("{}")
    (d / "patch.diff").write_text("diff\n")


def test_verify_reconciliation_passes_for_indexed_dream(dream_reconcile, tmp_path):
    dream_id = "20260101-000000Z-alpha"
    _write_index(tmp_path)
    _seed_dream_artifacts(tmp_path, dream_id)
    manifests = [(f"dream/p/{dream_id}", dream_id, {})]
    assert dream_reconcile.verify_reconciliation(str(tmp_path), manifests) == []


def test_verify_reconciliation_prefix_does_not_false_pass(dream_reconcile, tmp_path):
    """A SHORTER manifest dream_id whose longer relative IS indexed
    must still be reported as missing from the index. Naive substring
    matching would have falsely passed this case.
    """
    short_id = "20260420-1400-foo"
    longer_id = "20260420-1400-foo-extended"  # contains `short_id` as a substring
    _write_index(
        tmp_path,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {longer_id} | bug hunting | useful | Long | dream/p/{longer_id} | main | abc1234 |\n",
    )
    _seed_dream_artifacts(tmp_path, short_id)  # artifacts OK, but index lacks it
    manifests = [(f"dream/p/{short_id}", short_id, {})]
    failures = dream_reconcile.verify_reconciliation(str(tmp_path), manifests)
    assert any("missing from _index.md" in f for f in failures), failures


def test_verify_reconciliation_reports_missing_artifacts(dream_reconcile, tmp_path):
    dream_id = "20260101-000000Z-alpha"
    _write_index(tmp_path)
    # Only create report.md — manifest.json and patch.diff missing.
    d = tmp_path / ".shadow" / "_dreams" / dream_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "report.md").write_text("# r\n")
    manifests = [(f"dream/p/{dream_id}", dream_id, {})]
    failures = dream_reconcile.verify_reconciliation(str(tmp_path), manifests)
    assert any("manifest.json" in f for f in failures)
    assert any("patch.diff" in f for f in failures)


def test_verify_reconciliation_reports_missing_index(dream_reconcile, tmp_path):
    dream_id = "20260101-000000Z-alpha"
    _seed_dream_artifacts(tmp_path, dream_id)
    manifests = [(f"dream/p/{dream_id}", dream_id, {})]
    failures = dream_reconcile.verify_reconciliation(str(tmp_path), manifests)
    assert any("_index.md does not exist" in f for f in failures)


# ===========================================================================
# cleanup_branches — PREFIX FALSE-PASS regression (data loss class)
# ===========================================================================

@pytest.mark.slow
def test_cleanup_branches_keeps_prefix_only_branch_to_prevent_data_loss(
    dream_reconcile, tmp_git_repo
):
    """If the manifest dream_id is a PREFIX of an indexed ID (but not equal),
    the branch must NOT be deleted — pre-fix substring check would have
    falsely passed and destroyed the only copy of those discoveries.
    """
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    short_id = "20260420-1400-foo"
    longer_id = "20260420-1400-foo-extended"

    # Index contains only the LONGER id.
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {longer_id} | bug hunting | useful | Long | dream/proj/{longer_id} | main | abc1234 |\n",
    )

    # Mirror artifacts for SHORT id (so safety check 1 passes).
    _seed_dream_artifacts(tmp_git_repo, short_id)

    manifests = [(f"dream/proj/{short_id}", short_id, {})]

    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo), manifests, "proj", dry_run=True
    )
    assert deleted == 0
    assert kept == 1


@pytest.mark.slow
def test_cleanup_branches_keeps_when_artifacts_missing(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    dream_id = "20260420-141500Z-missing-artifacts"
    _write_index(tmp_git_repo)  # index doesn't matter — artifacts check fires first

    manifests = [(f"dream/proj/{dream_id}", dream_id, {})]
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo), manifests, "proj", dry_run=True
    )
    assert deleted == 0
    assert kept == 1


@pytest.mark.slow
def test_cleanup_branches_respects_keep_branches_env(
    dream_reconcile, tmp_git_repo, monkeypatch
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    monkeypatch.setenv("SHADOWFROG_KEEP_BRANCHES", "1")
    manifests = [("dream/proj/xx", "xx", {})]
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo), manifests, "proj", dry_run=False
    )
    assert deleted == 0
    assert kept == 1


@pytest.mark.slow
def test_cleanup_branches_refuses_with_uncommitted_shadow(
    dream_reconcile, tmp_git_repo
):
    """B16: the combined `reconcile --cleanup-branches` invocation merges
    discoveries into the working tree but does NOT commit them. Cleanup must
    refuse while .shadow/ is dirty — otherwise the ancestor check passes
    against the stale (pre-reconcile) HEAD and the only durable copy of the
    discoveries (the dream branch) gets deleted.
    """
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    dream_id = "20260420-150000Z-dirty"
    _write_index(tmp_git_repo)
    _seed_dream_artifacts(tmp_git_repo, dream_id)  # uncommitted .shadow/ changes

    manifests = [(f"dream/proj/{dream_id}", dream_id, {})]
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo), manifests, "proj", dry_run=False
    )
    assert deleted == 0
    assert kept == 1


@pytest.mark.slow
def test_cleanup_branches_proceeds_when_shadow_committed_and_pushed(
    dream_reconcile, tmp_git_repo
):
    """Positive case: once the reconciliation is committed and pushed (clean
    .shadow/, HEAD on origin/main), cleanup is allowed to delete the branch."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    dream_id = "20260420-150500Z-clean"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))

    # Mirror artifacts + index onto main, then commit and push.
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiment Archive\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    manifests = [(branch, dream_id, _default_manifest(dream_id))]
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo), manifests, "proj", dry_run=False
    )
    assert deleted == 1
    assert kept == 0


# ===========================================================================
# _count_discoveries — excludes Cross-References back-pointers
# ===========================================================================

def test_count_discoveries_excludes_cross_references_section(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## File-Level\n\n"
        "- file-level discovery one\n"
        "- file-level discovery two\n"
        "\n"
        "## `foo`\n\n"
        "- symbol discovery one\n"
        "- symbol discovery two\n"
        "- symbol discovery three\n"
        "\n"
        "## Cross-References\n\n"
        "- [back-pointer one](_cross/a.md)\n"
        "- [back-pointer two](_cross/b.md)\n"
    )
    assert dream_reconcile._count_discoveries(str(shadow)) == 5


def test_count_discoveries_case_insensitive_xref_heading(dream_reconcile, tmp_path):
    """A lowercase rewrite must still suppress the back-pointer bullets."""
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## `foo`\n\n- real discovery\n\n"
        "## cross-references\n\n- [bp](_cross/a.md)\n"
    )
    assert dream_reconcile._count_discoveries(str(shadow)) == 1


def test_count_discoveries_missing_file_returns_zero(dream_reconcile, tmp_path):
    assert dream_reconcile._count_discoveries(str(tmp_path / "nope.md")) == 0


def test_count_discoveries_matches_coupon_demo_index(dream_reconcile, coupon_demo):
    """The committed _index.md says: cart=14, inventory=10, test_cart=9 → 33."""
    cart = coupon_demo / ".shadow" / "cart.py.md"
    inv = coupon_demo / ".shadow" / "inventory.py.md"
    tst = coupon_demo / ".shadow" / "test_cart.py.md"
    assert dream_reconcile._count_discoveries(str(cart)) == 14
    assert dream_reconcile._count_discoveries(str(inv)) == 10
    assert dream_reconcile._count_discoveries(str(tst)) == 9


# ===========================================================================
# _shadow_symbol_names / _shadow_language
# ===========================================================================

def test_shadow_symbol_names_extracts_top_level_only(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "## File-Level\n\n- foo\n\n"
        "## `bar`\n\n- d\n\n"
        "### `bar.method`\n\n- nested d (not counted)\n\n"
        "## `class Baz`\n\n- d\n\n"
        "## `interface IQux`\n\n- d\n\n"
        "## Cross-References\n\n"
    )
    names = dream_reconcile._shadow_symbol_names(str(shadow))
    assert names == ["bar", "Baz", "IQux"]


def test_shadow_symbol_names_missing_file(dream_reconcile, tmp_path):
    assert dream_reconcile._shadow_symbol_names(str(tmp_path / "missing.md")) == []


def test_shadow_language_reads_header(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text(
        "# Shadow: foo.py\n\n"
        "**Language**: Python | **Lines**: 28 | **Last modified**: 2026-04-20\n\n"
        "## `foo`\n"
    )
    assert dream_reconcile._shadow_language(str(shadow)) == "Python"


def test_shadow_language_missing_header_returns_unknown(dream_reconcile, tmp_path):
    shadow = tmp_path / "foo.md"
    shadow.write_text("## `foo`\n\n- d\n")
    assert dream_reconcile._shadow_language(str(shadow)) == "Unknown"


def test_shadow_language_reads_from_coupon_demo(dream_reconcile, coupon_demo):
    cart = coupon_demo / ".shadow" / "cart.py.md"
    assert dream_reconcile._shadow_language(str(cart)) == "Python"


# ===========================================================================
# rebuild_top_index — B6 regression
# ===========================================================================

def test_rebuild_top_index_dry_run_does_not_write(dream_reconcile, coupon_demo):
    index_path = coupon_demo / ".shadow" / "_index.md"
    original = index_path.read_text()
    dream_reconcile.rebuild_top_index(str(coupon_demo), dry_run=True)
    assert index_path.read_text() == original


def test_rebuild_top_index_regenerates_coupon_demo_counts(dream_reconcile, coupon_demo):
    """Regenerated header must report the same totals as the committed file."""
    index_path = coupon_demo / ".shadow" / "_index.md"
    original = index_path.read_text()

    # Sanity-check the committed file states the expected counts.
    assert "Total files: 3" in original
    assert "Symbols: 9" in original
    assert "Discoveries: 33" in original
    assert "Cross-cutting: 3" in original
    assert "Dream cycles: 3" in original

    dream_reconcile.rebuild_top_index(str(coupon_demo), dry_run=False)
    new = index_path.read_text()

    assert "Total files: 3" in new
    assert "Symbols: 9" in new
    assert "Discoveries: 33" in new
    assert "Cross-cutting: 3" in new
    assert "Dream cycles: 3" in new

    # Per-file rows preserved (any order — counts should match).
    assert re.search(r"\| cart\.py \| Python \| 4 .* \| 14 \|", new)
    assert re.search(r"\| inventory\.py \| Python \| 2 .* \| 10 \|", new)
    assert re.search(r"\| test_cart\.py \| Python \| 3 .* \| 9 \|", new)


def test_rebuild_top_index_preserves_init_provenance(dream_reconcile, coupon_demo):
    index_path = coupon_demo / ".shadow" / "_index.md"
    dream_reconcile.rebuild_top_index(str(coupon_demo), dry_run=False)
    new = index_path.read_text()
    assert "Initially generated by shadow-frog-init on 2026-04-20" in new
    assert "Last updated by shadow-frog-dream on" in new


def test_rebuild_top_index_handles_missing_shadow_dir(dream_reconcile, tmp_path, capsys):
    # No .shadow dir at all — should print and return without raising.
    dream_reconcile.rebuild_top_index(str(tmp_path), dry_run=False)
    out = capsys.readouterr().out
    assert "No .shadow" in out


def test_rebuild_top_index_includes_underscore_prefixed_source_dirs(
    dream_reconcile, tmp_path
):
    """B19: a shadow nested under a `_`-prefixed source dir must appear in the
    top index (only top-level _meta/_cross/_dreams are pruned)."""
    shadow = tmp_path / ".shadow"
    (shadow / "_meta").mkdir(parents=True)
    (shadow / "src" / "_internal").mkdir(parents=True)
    (shadow / "src" / "_internal" / "helper.py.md").write_text(
        "# Shadow\n## `helper`\n\n- d1\n\n## Cross-References\n\n_No discoveries yet._\n"
    )
    dream_reconcile.rebuild_top_index(str(tmp_path), dry_run=False)
    new = (shadow / "_index.md").read_text()
    assert "src/_internal/helper.py" in new
    assert "Total files: 1" in new


# ===========================================================================
# load_manifests + discover_branches — integration via real branches
# ===========================================================================

@pytest.mark.slow
def test_load_manifests_skips_invalid(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    good_id = "20260420-100000Z-good"
    bad_id = "20260420-100100Z-bad"

    make_dream_branch(
        tmp_git_repo, env, "proj", good_id,
        _default_manifest(good_id),
    )
    make_dream_branch(
        tmp_git_repo, env, "proj", bad_id,
        {"dream_id": "OOPS", "category": "x", "verdict": "useful"},
    )

    branches = [
        (f"dream/proj/{good_id}", good_id),
        (f"dream/proj/{bad_id}", bad_id),
    ]
    manifests, skipped = dream_reconcile.load_manifests(str(tmp_git_repo), branches)
    assert len(manifests) == 1
    assert manifests[0][1] == good_id
    assert len(skipped) == 1
    assert skipped[0][1] == bad_id


@pytest.mark.slow
def test_discover_branches_filters_namespace_and_existing(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    new_id = "20260420-110000Z-new"
    indexed_id = "20260420-111000Z-indexed"
    other_ns_id = "20260420-112000Z-other"

    make_dream_branch(tmp_git_repo, env, "proj", new_id, _default_manifest(new_id))
    make_dream_branch(tmp_git_repo, env, "proj", indexed_id,
                      _default_manifest(indexed_id))
    make_dream_branch(tmp_git_repo, env, "different", other_ns_id,
                      _default_manifest(other_ns_id, dream_ns="different"))

    # Mark indexed_id as already reconciled.
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {indexed_id} | bug hunting | useful | T | dream/proj/{indexed_id} | main | 1234567 |\n",
    )

    discovered = dream_reconcile.discover_branches(str(tmp_git_repo), "proj")
    discovered_ids = {did for _, did in discovered}
    assert new_id in discovered_ids
    assert indexed_id not in discovered_ids  # already in index
    assert other_ns_id not in discovered_ids  # wrong namespace


# ===========================================================================
# CLI smoke tests
# ===========================================================================

@pytest.mark.slow
@pytest.mark.integration
def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0
    assert "Dream reconciliation" in result.stdout
    assert "--dry-run" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_dry_run_with_no_dream_branches(tmp_git_repo):
    """Run the full CLI against a repo with no dream branches → exits 0
    with the 'no new branches' messaging."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)

    full_env = os.environ.copy()
    full_env["DREAM_NAMESPACE"] = "proj"

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new branches" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_unknown_argument_exits_nonzero():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--bogus-flag"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "Unknown argument" in result.stderr


# ===========================================================================
# find_heading — symbol heading lookup
# ===========================================================================

def test_find_heading_backtick_top_level(dream_reconcile):
    lines = ["# Shadow\n", "\n", "## `do_thing`\n", "- d\n"]
    assert dream_reconcile.find_heading(lines, "do_thing") == 2


def test_find_heading_backtick_nested(dream_reconcile):
    lines = ["## `class Foo`\n", "### `Foo.bar`\n", "- d\n"]
    assert dream_reconcile.find_heading(lines, "Foo.bar") == 1


def test_find_heading_bare_top_level(dream_reconcile):
    lines = ["## do_thing\n", "- d\n"]
    assert dream_reconcile.find_heading(lines, "do_thing") == 0


def test_find_heading_bare_nested(dream_reconcile):
    lines = ["### do_thing\n"]
    assert dream_reconcile.find_heading(lines, "do_thing") == 0


def test_find_heading_returns_negative_one_when_missing(dream_reconcile):
    lines = ["## `other`\n", "## `also_other`\n"]
    assert dream_reconcile.find_heading(lines, "missing") == -1


def test_find_heading_returns_first_match(dream_reconcile):
    """If the same heading appears twice (legacy malformed file), we want the
    earliest line so subsequent inserts go into the correct section."""
    lines = ["## `foo`\n", "- d1\n", "## `foo`\n", "- d2\n"]
    assert dream_reconcile.find_heading(lines, "foo") == 0


def test_find_heading_ignores_trailing_whitespace(dream_reconcile):
    lines = ["## `foo`   \n"]
    assert dream_reconcile.find_heading(lines, "foo") == 0


def test_find_heading_does_not_match_partial(dream_reconcile):
    """Substring-style false-positive guard."""
    lines = ["## `foobar`\n"]
    assert dream_reconcile.find_heading(lines, "foo") == -1


# ===========================================================================
# git / git_show helpers — subprocess wrappers
# ===========================================================================

def test_git_returns_stdout_stripped(dream_reconcile, tmp_git_repo):
    """git() should return stdout with surrounding whitespace stripped."""
    _seed_repo(tmp_git_repo)
    out = dream_reconcile.git("rev-parse", "--abbrev-ref", "HEAD",
                              cwd=str(tmp_git_repo))
    assert out == "main"


def test_git_raises_runtime_error_on_failure(dream_reconcile, tmp_git_repo):
    """check=True (default) raises RuntimeError on non-zero exit."""
    _seed_repo(tmp_git_repo)
    with pytest.raises(RuntimeError, match="git "):
        dream_reconcile.git("rev-parse", "no-such-ref-xyz",
                            cwd=str(tmp_git_repo))


def test_git_check_false_swallows_error(dream_reconcile, tmp_git_repo):
    """check=False returns whatever stdout came back (possibly empty)."""
    _seed_repo(tmp_git_repo)
    out = dream_reconcile.git("config", "--get", "nonexistent.shadowfrog.key",
                              cwd=str(tmp_git_repo), check=False)
    assert out == ""  # stdout empty when key missing


def test_git_show_returns_file_content(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    (tmp_git_repo / "hello.txt").write_text("hello world\n")
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "add hello", cwd=tmp_git_repo, env=env)
    out = dream_reconcile.git_show("HEAD", "hello.txt", cwd=str(tmp_git_repo))
    assert out == "hello world\n"


def test_git_show_returns_none_for_missing_path(dream_reconcile, tmp_git_repo):
    _seed_repo(tmp_git_repo)
    out = dream_reconcile.git_show("HEAD", "no-such-file.txt",
                                   cwd=str(tmp_git_repo))
    assert out is None


def test_git_show_returns_none_for_missing_ref(dream_reconcile, tmp_git_repo):
    _seed_repo(tmp_git_repo)
    out = dream_reconcile.git_show("no-such-ref", "README.md",
                                   cwd=str(tmp_git_repo))
    assert out is None


# ===========================================================================
# _resolve_tip_commit
# ===========================================================================

@pytest.mark.slow
def test_resolve_tip_commit_returns_short_sha(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-090000Z-tip"
    branch = make_dream_branch(
        tmp_git_repo, env, "proj", dream_id, _default_manifest(dream_id),
    )
    tip = dream_reconcile._resolve_tip_commit(str(tmp_git_repo), branch)
    assert re.fullmatch(r"[0-9a-f]{7}", tip), tip


@pytest.mark.slow
def test_resolve_tip_commit_returns_unknown_for_missing_branch(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    tip = dream_reconcile._resolve_tip_commit(
        str(tmp_git_repo), "dream/proj/never-existed"
    )
    assert tip == "unknown"


@pytest.mark.slow
def test_resolve_tip_commit_returns_unknown_with_no_remote(
    dream_reconcile, tmp_git_repo
):
    """No origin remote at all — must not raise."""
    _seed_repo(tmp_git_repo)
    tip = dream_reconcile._resolve_tip_commit(
        str(tmp_git_repo), "dream/proj/any"
    )
    assert tip == "unknown"


# ===========================================================================
# merge_discoveries
# ===========================================================================

def _discovery(anchor, text, **kwargs):
    out = {"anchor": anchor, "text": text}
    out.update(kwargs)
    return out


def _manifest_with(dream_id, discoveries=None, cross_cutting=None, dream_ns="proj"):
    m = _default_manifest(dream_id, dream_ns=dream_ns)
    m["discoveries"] = discoveries or []
    m["cross_cutting"] = cross_cutting or []
    return m


def test_merge_discoveries_creates_per_file_shadows(dream_reconcile, tmp_path):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000000Z-merge1"
    manifest = _manifest_with(dream_id, discoveries=[
        _discovery("src/cart.py::add_item", "Cart silently drops negative qty.",
                   status="verified", source="exploration"),
        _discovery("src/cart.py::checkout", "Checkout retries 3x on 5xx.",
                   status="verified", source="exploration"),
        _discovery("lib/util.py::norm", "Norm strips zero-width space chars.",
                   status="verified", source="user"),
    ])
    manifests = [(f"dream/proj/{dream_id}", dream_id, manifest)]

    merged, skipped = dream_reconcile.merge_discoveries(
        str(repo), manifests, dry_run=False,
    )
    assert merged == 3
    assert skipped == 0

    cart = (repo / ".shadow" / "src" / "cart.py.md").read_text()
    util = (repo / ".shadow" / "lib" / "util.py.md").read_text()

    assert "## `add_item`" in cart
    assert "## `checkout`" in cart
    assert "silently drops negative qty" in cart
    assert "Dream report: `_dreams/20260420-000000Z-merge1/`" in cart
    assert "## `norm`" in util
    assert "source: user" in util


def test_merge_discoveries_adds_dream_report_marker_to_each(
    dream_reconcile, tmp_path
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000100Z-marker"
    manifest = _manifest_with(dream_id, discoveries=[
        _discovery("a.py::x", "First discovery here for x."),
        _discovery("a.py::y", "Second discovery here for y."),
    ])
    dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    body = (repo / ".shadow" / "a.py.md").read_text()
    assert body.count(f"Dream report: `_dreams/{dream_id}/`") == 2


def test_merge_discoveries_dry_run_writes_no_files(dream_reconcile, tmp_path):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000200Z-dry"
    manifest = _manifest_with(dream_id, discoveries=[
        _discovery("src/foo.py::bar", "Bar does the thing."),
    ], cross_cutting=[
        {"slug": "abc", "title": "ABC", "text": "Crosses A,B,C.",
         "refs": ["src/foo.py::bar", "lib/baz.py::qux"]},
    ])
    before = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    merged, _ = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
        dry_run=True,
    )
    after = sorted(p.relative_to(repo) for p in repo.rglob("*"))
    # Dry-run claims to have done all the work but writes nothing new.
    assert merged == 2
    assert before == after


def test_merge_discoveries_skips_discoveries_without_anchor(
    dream_reconcile, tmp_path
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000300Z-noanc"
    # Missing `::` → skipped, not crashing.
    manifest = _manifest_with(dream_id, discoveries=[
        {"anchor": "no-double-colon-here", "text": "Skipped."},
        {"anchor": "", "text": "Also skipped."},
        _discovery("a.py::ok", "This one stays."),
    ])
    merged, skipped = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert merged == 1
    assert skipped == 2


def test_merge_discoveries_normalizes_string_discovery(dream_reconcile, tmp_path):
    """A bare string in `discoveries` becomes {'anchor': '', 'text': ...}
    and is then skipped (no anchor) — must not crash."""
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000400Z-strnorm"
    manifest = _manifest_with(dream_id, discoveries=[
        "raw string with no anchor",
    ])
    merged, skipped = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert merged == 0
    assert skipped == 1


def test_merge_discoveries_creates_cross_cutting_file_with_back_pointers(
    dream_reconcile, tmp_path
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000500Z-cross"
    manifest = _manifest_with(dream_id, cross_cutting=[
        {
            "slug": "auth-lifecycle",
            "title": "Auth Lifecycle",
            "category": "behavior",
            "refs": ["src/auth.py::login", "src/auth.py::logout",
                     "lib/session.py::Session"],
            "text": "Login/logout flow shares state with Session.",
            "status": "verified",
            "source": "exploration",
        },
    ])
    dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )

    cross = repo / ".shadow" / "_cross" / "auth-lifecycle.md"
    assert cross.is_file()
    body = cross.read_text()
    assert body.startswith("# Auth Lifecycle\n")
    assert "**Category**: behavior" in body
    assert "`src/auth.py::login`" in body
    assert "`lib/session.py::Session`" in body
    assert "_(verified, source: exploration)_" in body

    # Each referenced per-file shadow must have a back-pointer.
    auth = (repo / ".shadow" / "src" / "auth.py.md").read_text()
    sess = (repo / ".shadow" / "lib" / "session.py.md").read_text()
    assert "[Auth Lifecycle](../_cross/auth-lifecycle.md)" in auth
    assert "[Auth Lifecycle](../_cross/auth-lifecycle.md)" in sess
    assert f"dream: {dream_id}" in auth
    assert f"dream: {dream_id}" in sess


def test_merge_discoveries_normalizes_string_cross_cutting(
    dream_reconcile, tmp_path
):
    """A bare string in cross_cutting is normalized to a dict with a slug
    derived from the text. With no refs and no anchor, it just creates a
    cross-cutting file — must not crash."""
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000600Z-xcross"
    manifest = _manifest_with(dream_id, cross_cutting=[
        "Some Behavior That Spans Files",
    ])
    merged, _ = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    # The slug is derived from the lowered-and-kebabbed text prefix.
    cross_dir = repo / ".shadow" / "_cross"
    assert cross_dir.is_dir()
    files = list(cross_dir.glob("*.md"))
    assert len(files) == 1
    assert "some-behavior-that-spans-files" in files[0].name


def test_merge_discoveries_skips_cross_cutting_without_slug(
    dream_reconcile, tmp_path
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000700Z-noslug"
    manifest = _manifest_with(dream_id, cross_cutting=[
        {"slug": "", "title": "Empty", "refs": []},
    ])
    merged, skipped = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert merged == 0
    assert not (repo / ".shadow" / "_cross").exists()


def test_merge_discoveries_skips_existing_cross_cutting_file(
    dream_reconcile, tmp_path
):
    """If `.shadow/_cross/<slug>.md` already exists, merging counts it as
    skipped (no overwrite) but still heals back-pointers."""
    repo = tmp_path
    (repo / ".shadow" / "_cross").mkdir(parents=True)
    existing = repo / ".shadow" / "_cross" / "preexist.md"
    existing.write_text("# Old Title\n\n**Refs**:\n- `x.py::y`\n")

    dream_id = "20260420-000800Z-preexist"
    manifest = _manifest_with(dream_id, cross_cutting=[
        {
            "slug": "preexist", "title": "Old Title",
            "refs": ["x.py::y"], "text": "ignored",
        },
    ])
    merged, skipped = dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    # No new cross file created — but the back-pointer to x.py is still added.
    assert merged == 0
    assert skipped == 1
    assert "# Old Title" in existing.read_text()
    # And the back-pointer landed.
    assert "[Old Title](_cross/preexist.md)" in (
        repo / ".shadow" / "x.py.md"
    ).read_text()


def test_merge_discoveries_skips_cross_ref_without_double_colon(
    dream_reconcile, tmp_path
):
    """Refs lacking `::` don't get back-pointers (no file is implied)."""
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-000900Z-bareref"
    manifest = _manifest_with(dream_id, cross_cutting=[
        {
            "slug": "behave", "title": "Behaviour",
            "refs": ["just-a-symbol-name"],   # no `::`
            "text": "a thing", "status": "verified", "source": "exploration",
        },
    ])
    dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    cross = repo / ".shadow" / "_cross" / "behave.md"
    assert cross.is_file()
    # No per-file shadow was synthesized.
    other_md = [p for p in (repo / ".shadow").rglob("*.md")
                if p.relative_to(repo / ".shadow").parts[0] != "_cross"]
    assert other_md == []


def test_merge_discoveries_dry_run_prints_planned_actions(
    dream_reconcile, tmp_path, capsys
):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    dream_id = "20260420-001000Z-dryprint"
    manifest = _manifest_with(dream_id, discoveries=[
        _discovery("a.py::z", "Discovery."),
    ], cross_cutting=[
        {"slug": "xx", "title": "XX",
         "refs": ["a.py::z"], "text": "t"},
    ])
    dream_reconcile.merge_discoveries(
        str(repo), [(f"dream/proj/{dream_id}", dream_id, manifest)],
        dry_run=True,
    )
    out = capsys.readouterr().out
    assert "Would merge: a.py::z" in out
    assert "Would create cross-cutting: _cross/xx.md" in out
    assert "+ back-pointer in .shadow/a.py.md" in out


def test_merge_discoveries_multiple_manifests(dream_reconcile, tmp_path):
    repo = tmp_path
    (repo / ".shadow").mkdir()
    d1, d2 = "20260420-001100Z-d1", "20260420-001200Z-d2"
    m1 = _manifest_with(d1, discoveries=[_discovery("a.py::x", "First.")])
    m2 = _manifest_with(d2, discoveries=[_discovery("a.py::y", "Second.")])
    merged, _ = dream_reconcile.merge_discoveries(
        str(repo),
        [
            (f"dream/proj/{d1}", d1, m1),
            (f"dream/proj/{d2}", d2, m2),
        ],
    )
    assert merged == 2
    body = (repo / ".shadow" / "a.py.md").read_text()
    assert "## `x`" in body
    assert "## `y`" in body
    assert f"_dreams/{d1}/" in body
    assert f"_dreams/{d2}/" in body


# ===========================================================================
# mirror_reports
# ===========================================================================

@pytest.mark.slow
def test_mirror_reports_copies_report_and_patch(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020000Z-mirror"
    manifest = _default_manifest(dream_id)
    report = _default_report(dream_id)
    patch = "diff --git a/foo b/foo\n--- a/foo\n+++ b/foo\n@@\n+changed\n"
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      manifest, report=report, patch=patch)

    mirrored, corrupted = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert mirrored == 1
    assert corrupted == []
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    assert (dream_dir / "report.md").read_text() == report
    assert (dream_dir / "patch.diff").read_text() == patch
    # Manifest is rewritten from the in-memory dict.
    saved_manifest = json.loads((dream_dir / "manifest.json").read_text())
    assert saved_manifest["dream_id"] == dream_id


@pytest.mark.slow
def test_mirror_reports_handles_missing_report_gracefully(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020100Z-noreport"
    manifest = _default_manifest(dream_id)
    # No report.md, but manifest + patch present.
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest,
                      report=None, patch="diff --git a/x b/x\n")
    mirrored, corrupted = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert mirrored == 1
    assert corrupted == []
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    # Manifest + patch mirrored; report.md NOT created on main side.
    assert (dream_dir / "manifest.json").is_file()
    assert (dream_dir / "patch.diff").is_file()
    assert not (dream_dir / "report.md").is_file()


@pytest.mark.slow
def test_mirror_reports_handles_missing_patch(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020200Z-nopatch"
    manifest = _default_manifest(dream_id)
    report = _default_report(dream_id)
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest,
                      report=report, patch=None)
    mirrored, _ = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert mirrored == 1
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    assert (dream_dir / "report.md").is_file()
    assert not (dream_dir / "patch.diff").is_file()


@pytest.mark.slow
def test_mirror_reports_preserves_empty_patch(dream_reconcile, tmp_git_repo):
    """A 0-byte patch.diff on the dream branch must be mirrored as a
    0-byte patch.diff on main — not skipped.

    Regression: a truthy `if patch:` check used to conflate
    `git_show` returning None (file absent) with returning "" (file
    present, 0 bytes). The empty-but-present case got silently dropped,
    so verify_artifacts reported `missing patch.diff` even though the
    file existed on origin, and the dream branch couldn't be cleaned up.
    """
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020250Z-emptypatch"
    manifest = _default_manifest(dream_id)
    report = _default_report(dream_id)
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest,
                      report=report, patch="")
    mirrored, corrupted = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    assert mirrored == 1
    assert corrupted == []
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    patch_path = dream_dir / "patch.diff"
    assert patch_path.is_file(), (
        "Empty patch.diff on the dream branch must still be mirrored to "
        "main; otherwise verify_artifacts spuriously reports it missing."
    )
    assert patch_path.stat().st_size == 0


@pytest.mark.slow
def test_mirror_reports_detects_dream_id_mismatch(dream_reconcile, tmp_git_repo):
    """If the report.md frontmatter declares a DIFFERENT dream_id, mirror
    must write a 'Corrupted Report' placeholder and surface the mismatch —
    but the manifest and patch are still mirrored so valid artifacts (and
    the discoveries read from the manifest) are never lost to one bad line."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020300Z-corrupt"
    wrong_id = "20260420-020300Z-CORRUPTED-FROM-ELSEWHERE"
    manifest = _default_manifest(dream_id)
    # report references the WRONG id in frontmatter.
    bad_report = _default_report(wrong_id)
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest,
                      report=bad_report)
    mirrored, corrupted = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    # The mismatch is surfaced, but the dream's artifacts are still mirrored.
    assert mirrored == 1
    assert corrupted == [(dream_id, wrong_id)]
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / dream_id
    body = (dream_dir / "report.md").read_text()
    assert "Corrupted Report" in body
    assert wrong_id in body
    # The manifest must still be mirrored despite the corrupt report.
    assert (dream_dir / "manifest.json").is_file()


@pytest.mark.slow
def test_mirror_reports_dry_run_writes_nothing(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-020400Z-dry"
    manifest = _default_manifest(dream_id)
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      manifest, report=_default_report(dream_id))
    before = sorted(p.relative_to(tmp_git_repo)
                    for p in tmp_git_repo.rglob("*")
                    if ".git" not in p.parts)
    mirrored, corrupted = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
        dry_run=True,
    )
    after = sorted(p.relative_to(tmp_git_repo)
                   for p in tmp_git_repo.rglob("*")
                   if ".git" not in p.parts)
    assert mirrored == 1
    assert before == after


@pytest.mark.slow
def test_mirror_reports_multiple_manifests(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    d1 = "20260420-020500Z-m1"
    d2 = "20260420-020600Z-m2"
    make_dream_branch(tmp_git_repo, env, "proj", d1,
                      _default_manifest(d1), report=_default_report(d1))
    make_dream_branch(tmp_git_repo, env, "proj", d2,
                      _default_manifest(d2), report=_default_report(d2))
    mirrored, _ = dream_reconcile.mirror_reports(
        str(tmp_git_repo),
        [
            (f"dream/proj/{d1}", d1, _default_manifest(d1)),
            (f"dream/proj/{d2}", d2, _default_manifest(d2)),
        ],
    )
    assert mirrored == 2
    for d in (d1, d2):
        body = (tmp_git_repo / ".shadow" / "_dreams" / d /
                "report.md").read_text()
        assert f"# {d}" in body


# ===========================================================================
# update_index
# ===========================================================================

@pytest.mark.slow
def test_update_index_bootstraps_when_missing(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030000Z-boot"
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      _default_manifest(dream_id))
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
    )
    idx = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert "# Dream Experiment Archive" in idx
    assert "| dream_id | category | verdict |" in idx
    assert f"| {dream_id} | bug hunting | useful |" in idx
    assert f"dream/proj/{dream_id}" in idx


@pytest.mark.slow
def test_update_index_preserves_existing_rows(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    new_id = "20260420-030100Z-newer"
    # Create the dream branch FIRST so the pre-seeded _index.md doesn't get
    # carried into it (uncommitted files on main follow the checkout).
    make_dream_branch(tmp_git_repo, env, "proj", new_id,
                      _default_manifest(new_id))
    _write_index(tmp_git_repo)  # pre-seed with INDEX_FIXTURE (3 rows)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{new_id}", new_id, _default_manifest(new_id))],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert "20260101-000000Z-alpha" in body
    assert "20260102-000000Z-beta" in body
    assert "20260103-000000Z-gamma" in body
    assert new_id in body


@pytest.mark.slow
def test_update_index_records_real_tip_commit(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030200Z-tip"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    expected_tip = dream_reconcile._resolve_tip_commit(str(tmp_git_repo), branch)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    row = [l for l in body.splitlines() if dream_id in l][0]
    assert f"| {expected_tip} |" in row


@pytest.mark.slow
def test_update_index_dry_run_does_not_write(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030300Z-dry"
    # Create dream branch FIRST so the pre-seeded file isn't pulled into it.
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      _default_manifest(dream_id))
    idx_path = tmp_git_repo / ".shadow" / "_dreams" / "_index.md"
    # Pre-seed the file so the bootstrap path doesn't fire.
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    idx_path.write_text("# Dream Index\n\nold body\n")
    before = idx_path.read_text()
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
        dry_run=True,
    )
    assert idx_path.read_text() == before


@pytest.mark.slow
def test_update_index_uses_report_heading_when_manifest_title_missing(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030400Z-fromreport"
    manifest = _default_manifest(dream_id)
    del manifest["title"]
    # Report frontmatter then a markdown heading we want extracted.
    report = (
        "---\n"
        f"dream_id: \"{dream_id}\"\n"
        "category: bug hunting\n"
        "verdict: useful\n"
        "base_commit: abcdef1234567890\n"
        "---\n\n"
        "# Beautiful Title From Report\n\nBody.\n"
    )
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      manifest, report=report)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert "Beautiful Title From Report" in body


@pytest.mark.slow
def test_update_index_falls_back_to_dream_id_when_no_title(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030500Z-noref"
    manifest = _default_manifest(dream_id)
    del manifest["title"]
    # No report.md at all → fallback to "Dream <id>"
    make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                      manifest, report=None)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert f"| Dream {dream_id} |" in body


@pytest.mark.slow
def test_update_index_strips_pipe_chars_from_title(dream_reconcile, tmp_git_repo):
    """Title containing `|` would break markdown table parsing; must be neutralized."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030600Z-pipe"
    manifest = _default_manifest(dream_id)
    manifest["title"] = "Bad | title | with pipes"
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    row = [l for l in body.splitlines() if dream_id in l][0]
    # Sanitized: pipes replaced with hyphens; row still has exactly the
    # canonical 8 separators ('| ' + 7 columns + ' |').
    assert "Bad - title - with pipes" in row
    assert row.count("|") == 8


@pytest.mark.slow
def test_update_index_normalizes_category_with_parens(
    dream_reconcile, tmp_git_repo
):
    """Categories like `bug hunting (refresher)` must strip the trailing
    parenthetical so they match the canonical category set."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-030700Z-cat"
    manifest = _default_manifest(dream_id)
    manifest["category"] = "Bug Hunting (notes from the field)"
    make_dream_branch(tmp_git_repo, env, "proj", dream_id, manifest)
    dream_reconcile.update_index(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    body = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    row = [l for l in body.splitlines() if dream_id in l][0]
    assert "| bug hunting |" in row
    assert "(notes" not in row


# ===========================================================================
# update_state
# ===========================================================================

@pytest.mark.slow
def test_update_state_bootstraps_missing_state_json(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    # No .shadow/_meta yet — must be created from scratch.
    dream_id = "20260420-040000Z-init"
    manifest = _default_manifest(dream_id)
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, manifest)],
    )
    state_path = tmp_git_repo / ".shadow" / "_meta" / "state.json"
    assert state_path.is_file()
    state = json.loads(state_path.read_text())
    assert state["dream_cycles_completed"] == 1
    assert state["last_update_type"] == "dream"
    assert "last_update_at" in state
    assert "last_commit" in state and len(state["last_commit"]) == 40


@pytest.mark.slow
def test_update_state_increments_dream_cycles(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    state_dir = tmp_git_repo / ".shadow" / "_meta"
    state_dir.mkdir(parents=True)
    (state_dir / "state.json").write_text(json.dumps({
        "version": 1, "dream_cycles_completed": 5,
        "total_files": 0, "total_symbols": 0, "total_discoveries": 0,
    }))
    dream_id = "20260420-040100Z-inc"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
    )
    state = json.loads((state_dir / "state.json").read_text())
    assert state["dream_cycles_completed"] == 6
    assert state["last_update_type"] == "dream"


@pytest.mark.slow
def test_update_state_recomputes_totals_excludes_internal_dirs(
    dream_reconcile, tmp_git_repo
):
    """`_meta/`, `_cross/`, `_dreams/` files must NOT contribute to totals."""
    _seed_repo(tmp_git_repo)
    shadow = tmp_git_repo / ".shadow"
    # 1 real file with 2 symbols + 3 discoveries.
    (shadow / "src").mkdir(parents=True)
    (shadow / "src" / "real.py.md").write_text(
        "# Shadow\n"
        "## `foo`\n\n- d1\n- d2\n\n"
        "## `bar`\n\n- d3\n\n"
        "## Cross-References\n\n- [bp](../_cross/x.md)\n"
    )
    # Cross-cutting (should NOT contribute).
    (shadow / "_cross").mkdir()
    (shadow / "_cross" / "x.md").write_text(
        "# X\n## `should_not_count`\n- bogus\n"
    )
    # Dream report (should NOT contribute).
    (shadow / "_dreams" / "20260420-040200Z-x").mkdir(parents=True)
    (shadow / "_dreams" / "20260420-040200Z-x" / "report.md").write_text(
        "## `also_not_counted`\n- bogus\n"
    )
    dream_id = "20260420-040200Z-totals"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
    )
    state = json.loads(
        (shadow / "_meta" / "state.json").read_text()
    )
    assert state["total_files"] == 1
    assert state["total_symbols"] == 2     # foo + bar
    assert state["total_discoveries"] == 3  # d1, d2, d3 (back-pointer excluded)


@pytest.mark.slow
def test_update_state_counts_underscore_prefixed_source_dirs(
    dream_reconcile, tmp_git_repo
):
    """B19: `_`-prefixed *source* dirs (e.g. src/_internal/) are real shadows.

    Only top-level internal dirs (_meta/_cross/_dreams) are pruned; a mirrored
    shadow nested under a `_`-prefixed source dir must still be counted.
    """
    _seed_repo(tmp_git_repo)
    shadow = tmp_git_repo / ".shadow"
    (shadow / "src" / "_internal").mkdir(parents=True)
    (shadow / "src" / "_internal" / "helper.py.md").write_text(
        "# Shadow\n"
        "## `helper`\n\n- d1\n- d2\n\n"
        "## Cross-References\n\n_No discoveries yet._\n"
    )
    dream_id = "20260420-040250Z-underscore"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
    )
    state = json.loads(
        (shadow / "_meta" / "state.json").read_text()
    )
    assert state["total_files"] == 1
    assert state["total_symbols"] == 1
    assert state["total_discoveries"] == 2


@pytest.mark.slow
def test_update_state_dry_run_does_not_create_state(
    dream_reconcile, tmp_git_repo, capsys
):
    _seed_repo(tmp_git_repo)
    dream_id = "20260420-040300Z-dry"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
        dry_run=True,
    )
    assert not (tmp_git_repo / ".shadow" / "_meta" / "state.json").exists()
    assert "Would create state.json" in capsys.readouterr().out


@pytest.mark.slow
def test_update_state_dry_run_does_not_modify_existing(
    dream_reconcile, tmp_git_repo, capsys
):
    _seed_repo(tmp_git_repo)
    state_dir = tmp_git_repo / ".shadow" / "_meta"
    state_dir.mkdir(parents=True)
    payload = {
        "version": 1, "dream_cycles_completed": 7,
        "total_files": 9, "total_symbols": 99, "total_discoveries": 42,
    }
    (state_dir / "state.json").write_text(json.dumps(payload))
    original = (state_dir / "state.json").read_text()
    dream_id = "20260420-040400Z-drye"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
        dry_run=True,
    )
    assert (state_dir / "state.json").read_text() == original
    assert "Would update state.json" in capsys.readouterr().out


@pytest.mark.slow
def test_update_state_records_last_commit_sha(dream_reconcile, tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    head_sha = _git("rev-parse", "HEAD", cwd=tmp_git_repo, env=env).stdout.strip()
    dream_id = "20260420-040500Z-sha"
    dream_reconcile.update_state(
        str(tmp_git_repo),
        [(f"dream/proj/{dream_id}", dream_id, _default_manifest(dream_id))],
    )
    state = json.loads(
        (tmp_git_repo / ".shadow" / "_meta" / "state.json").read_text()
    )
    assert state["last_commit"] == head_sha


# ===========================================================================
# cleanup_branches — REAL delete path (non-dry-run)
# ===========================================================================

@pytest.mark.slow
def test_cleanup_branches_actually_deletes_when_all_checks_pass(
    dream_reconcile, tmp_git_repo
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-050000Z-realdel"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))

    # All safety conditions satisfied:
    # 1. HEAD == origin/main → ancestor check OK.
    # 2. All 3 artifacts on main.
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    # 3. dream_id indexed.
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )

    # Commit + push the reconciliation so .shadow/ is clean and HEAD is on
    # origin/main (the safe two-phase flow the dirty-tree guard enforces).
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Pre-check: branch exists on origin.
    pre = _git("ls-remote", "--heads", "origin", branch,
               cwd=tmp_git_repo, env=env).stdout
    assert branch in pre

    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 1
    assert kept == 0
    # Branch gone from origin.
    post = _git("ls-remote", "--heads", "origin", branch,
                cwd=tmp_git_repo, env=env).stdout
    assert branch not in post


@pytest.mark.slow
def test_cleanup_branches_refuses_when_head_not_pushed(
    dream_reconcile, tmp_git_repo
):
    """If HEAD is ahead of origin/main, refuse to delete anything — would
    risk losing the only copy of the discoveries."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-050100Z-notpushed"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    # Make a new local commit on main that is NOT pushed.
    (tmp_git_repo / "drift.txt").write_text("unpushed\n")
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "local-only", cwd=tmp_git_repo, env=env)

    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 0
    assert kept == 1
    # Branch still on origin (NOT deleted).
    post = _git("ls-remote", "--heads", "origin", branch,
                cwd=tmp_git_repo, env=env).stdout
    assert branch in post


@pytest.mark.slow
def test_cleanup_branches_keeps_when_descendant_branch_exists(
    dream_reconcile, tmp_git_repo
):
    """An un-reconciled child branch that lists `branch` as its parent must
    inhibit deletion (would orphan the descendant's lineage)."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    parent_id = "20260420-050200Z-parent"
    child_id = "20260420-050300Z-child"

    parent_branch = make_dream_branch(
        tmp_git_repo, env, "proj", parent_id, _default_manifest(parent_id),
    )

    # Child manifest references `parent_branch` as its parent.
    child_manifest = _default_manifest(child_id)
    child_manifest["parent_branch"] = parent_branch
    make_dream_branch(tmp_git_repo, env, "proj", child_id, child_manifest)

    # Parent fully reconciled; child NOT in index (still unreconciled).
    _seed_dream_artifacts(tmp_git_repo, parent_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {parent_id} | bug hunting | useful | T | {parent_branch} | main | abc1234 |\n",
    )

    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(parent_branch, parent_id, _default_manifest(parent_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 0
    assert kept == 1
    # Parent branch is preserved on origin so the child still has its lineage.
    post = _git("ls-remote", "--heads", "origin", parent_branch,
                cwd=tmp_git_repo, env=env).stdout
    assert parent_branch in post


@pytest.mark.slow
def test_cleanup_branches_keep_branches_env_overrides_real_delete(
    dream_reconcile, tmp_git_repo, monkeypatch
):
    """Even when ALL safety conditions pass, SHADOWFROG_KEEP_BRANCHES=1
    must inhibit deletion in the real-delete code path."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-050400Z-keepenv"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    monkeypatch.setenv("SHADOWFROG_KEEP_BRANCHES", "1")
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 0
    assert kept == 1
    # Branch still on origin.
    post = _git("ls-remote", "--heads", "origin", branch,
                cwd=tmp_git_repo, env=env).stdout
    assert branch in post


@pytest.mark.parametrize("env_value", ["1", "true", "yes"])
@pytest.mark.slow
def test_cleanup_branches_keep_branches_env_truthy_values(
    dream_reconcile, tmp_git_repo, monkeypatch, env_value
):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    monkeypatch.setenv("SHADOWFROG_KEEP_BRANCHES", env_value)
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [("dream/proj/xx", "xx", {})], "proj", dry_run=False,
    )
    assert deleted == 0
    assert kept == 1


@pytest.mark.slow
def test_cleanup_branches_keep_branches_env_falsy_does_not_block(
    dream_reconcile, tmp_git_repo, monkeypatch
):
    """SHADOWFROG_KEEP_BRANCHES=0 (or empty) must NOT inhibit cleanup;
    safety checks proceed normally."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-050500Z-zero"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    monkeypatch.setenv("SHADOWFROG_KEEP_BRANCHES", "0")
    # Commit + push so .shadow/ is clean (dirty-tree guard would otherwise fire).
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)
    deleted, kept = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj", dry_run=False,
    )
    # "0" is not a truthy value → cleanup proceeds → branch deleted.
    assert deleted == 1
    assert kept == 0


# ===========================================================================
# add_cross_reference_backpointer — coverage for append-after-existing path
# ===========================================================================

def test_add_cross_reference_backpointer_appends_when_other_links_exist(
    dream_reconcile, tmp_path
):
    """If Cross-References already has a NON-matching back-pointer (no
    placeholder), the new pointer must be appended after it."""
    repo = tmp_path
    (repo / ".shadow").mkdir()
    shadow = repo / ".shadow" / "foo.py.md"
    shadow.write_text(
        "## `foo`\n\n- discovery\n\n"
        "## Cross-References\n\n"
        "- [Existing](_cross/existing.md) (dream: 20260101-prev)\n"
    )
    written = dream_reconcile.add_cross_reference_backpointer(
        str(repo), "foo.py", "fresh", "Fresh Title", "20260420-091000Z-new",
    )
    assert written is True
    body = shadow.read_text()
    assert "[Existing](_cross/existing.md)" in body
    assert "[Fresh Title](_cross/fresh.md)" in body
    # The pre-existing entry is before the new one.
    assert body.index("Existing") < body.index("Fresh Title")


def test_add_cross_reference_backpointer_inserts_before_next_heading(
    dream_reconcile, tmp_path
):
    """File has another `## ...` heading after Cross-References → the
    back-pointer must be inserted BEFORE that next heading."""
    repo = tmp_path
    (repo / ".shadow").mkdir()
    shadow = repo / ".shadow" / "z.py.md"
    shadow.write_text(
        "## `z`\n\n- d\n\n"
        "## Cross-References\n\n"
        "- [Pre](_cross/pre.md)\n\n"
        "## Other Section\n\nstuff\n"
    )
    dream_reconcile.add_cross_reference_backpointer(
        str(repo), "z.py", "new", "New", "20260420-091500Z-y",
    )
    body = shadow.read_text()
    assert "[New](_cross/new.md)" in body
    # Inserted before `## Other Section`.
    assert body.index("[New]") < body.index("## Other Section")


# ===========================================================================
# main() — full CLI orchestrator integration tests
# ===========================================================================

def _cli_env(extra=None):
    """Realistic CLI env with isolated git config and optional overrides."""
    env = os.environ.copy()
    env["GIT_CONFIG_GLOBAL"] = "/dev/null"
    env["GIT_CONFIG_SYSTEM"] = "/dev/null"
    if extra:
        env.update(extra)
    return env


@pytest.mark.slow
@pytest.mark.integration
def test_cli_namespace_requires_value():
    """`--namespace` with no following arg must exit 1 with a clear message."""
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--namespace"],
        capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "--namespace requires a value" in result.stderr


@pytest.mark.slow
@pytest.mark.integration
def test_cli_full_happy_path_reconciles_two_dreams(tmp_git_repo):
    """Two dream branches → reconcile → state.json, _index.md, _dreams/
    mirrors all updated; verify pass."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    d1 = "20260420-060000Z-cli1"
    d2 = "20260420-060100Z-cli2"
    m1 = _manifest_with(d1, discoveries=[
        _discovery("src/cart.py::add_item", "Cart drops negative qty silently."),
    ], cross_cutting=[
        {"slug": "cart-flow", "title": "Cart Flow",
         "refs": ["src/cart.py::add_item"], "text": "cross"},
    ])
    m2 = _manifest_with(d2, discoveries=[
        _discovery("src/cart.py::checkout", "Checkout retries 3x."),
    ])
    make_dream_branch(tmp_git_repo, env, "proj", d1, m1,
                      report=_default_report(d1))
    make_dream_branch(tmp_git_repo, env, "proj", d2, m2,
                      report=_default_report(d2))

    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo)],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "All 2 dreams verified" in result.stdout
    # state.json updated.
    state = json.loads(
        (tmp_git_repo / ".shadow" / "_meta" / "state.json").read_text()
    )
    assert state["dream_cycles_completed"] == 1
    assert state["last_update_type"] == "dream"
    assert state["total_discoveries"] >= 2
    # _index.md has both dreams.
    idx = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert d1 in idx
    assert d2 in idx
    # Per-dream mirror dirs.
    for d in (d1, d2):
        d_dir = tmp_git_repo / ".shadow" / "_dreams" / d
        assert (d_dir / "manifest.json").is_file()
        assert (d_dir / "report.md").is_file()
    # Per-file shadow exists.
    cart = (tmp_git_repo / ".shadow" / "src" / "cart.py.md").read_text()
    assert "## `add_item`" in cart
    assert "## `checkout`" in cart
    # Cross-cutting file exists.
    assert (tmp_git_repo / ".shadow" / "_cross" / "cart-flow.md").is_file()
    # Top-level _index.md regenerated with dream cycles.
    top = (tmp_git_repo / ".shadow" / "_index.md").read_text()
    assert "Dream cycles: 1" in top


@pytest.mark.slow
@pytest.mark.integration
def test_cli_dry_run_makes_no_persistent_changes(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    d1 = "20260420-061000Z-dry1"
    m1 = _manifest_with(d1, discoveries=[
        _discovery("a.py::f", "Discovery for f."),
    ])
    make_dream_branch(tmp_git_repo, env, "proj", d1, m1,
                      report=_default_report(d1))

    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY RUN" in result.stdout
    assert "would complete" in result.stdout
    # No substantive artifacts: no per-file shadow, no state.json,
    # no coverage.json, no mirrored dream dir.
    assert not (tmp_git_repo / ".shadow" / "a.py.md").exists()
    assert not (tmp_git_repo / ".shadow" / "_meta" / "state.json").exists()
    assert not (tmp_git_repo / ".shadow" / "_meta" / "coverage.json").exists()
    assert not (tmp_git_repo / ".shadow" / "_dreams" / d1).exists()


@pytest.mark.slow
@pytest.mark.integration
def test_cli_namespace_filters_branches(tmp_git_repo):
    """`--namespace foo` ignores `dream/bar/...` branches entirely."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    in_id = "20260420-062000Z-inside"
    out_id = "20260420-062100Z-outside"
    make_dream_branch(tmp_git_repo, env, "inside", in_id,
                      _default_manifest(in_id, dream_ns="inside"))
    make_dream_branch(tmp_git_repo, env, "outside", out_id,
                      _default_manifest(out_id, dream_ns="outside"))
    full_env = _cli_env()
    full_env.pop("DREAM_NAMESPACE", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo),
         "--namespace", "inside", "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert in_id in result.stdout
    assert out_id not in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_skips_invalid_manifest_and_continues(tmp_git_repo):
    """A dream with manifest-validation failures must NOT crash the run.
    Other valid dreams continue to reconcile."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    good = "20260420-063000Z-good"
    bad = "20260420-063100Z-bad"
    make_dream_branch(tmp_git_repo, env, "proj", good,
                      _default_manifest(good),
                      report=_default_report(good))
    # Invalid manifest: dream_id mismatch.
    bad_manifest = {"dream_id": "wrong-id", "category": "x",
                    "verdict": "useful", "discoveries": []}
    make_dream_branch(tmp_git_repo, env, "proj", bad, bad_manifest,
                      report=_default_report(bad))
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo)],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert good in result.stdout
    # The bad one is mentioned as SKIP, not crashed.
    assert "SKIP" in result.stdout and bad in result.stdout
    # And only the good one made it into _index.md.
    idx = (tmp_git_repo / ".shadow" / "_dreams" / "_index.md").read_text()
    assert good in idx
    assert bad not in idx


@pytest.mark.slow
@pytest.mark.integration
def test_cli_no_dreams_exits_zero_with_message(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo)],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0
    assert "No new branches" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_not_in_git_repo_exits_nonzero(tmp_path):
    """Pointing at a non-git directory must error out."""
    not_a_repo = tmp_path / "not_a_repo"
    not_a_repo.mkdir()
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(not_a_repo / "nope")],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 1
    assert "Not in a git repository" in result.stderr


@pytest.mark.slow
@pytest.mark.integration
def test_cli_no_positional_uses_git_toplevel(tmp_git_repo):
    """No positional → git rev-parse --show-toplevel runs in CWD."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--dry-run"],
        capture_output=True, text=True, env=full_env, cwd=str(tmp_git_repo),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "No new branches" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_namespace_from_dotenv_file(tmp_git_repo):
    """If DREAM_NAMESPACE is unset and a `.env` provides one, use it."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    (tmp_git_repo / ".env").write_text("DREAM_NAMESPACE=fromdotenv\n")
    full_env = _cli_env()
    full_env.pop("DREAM_NAMESPACE", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Namespace: fromdotenv" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_namespace_from_task_info_json(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    (tmp_git_repo / "TASK_INFO.json").write_text(
        json.dumps({"dream_namespace": "fromtaskinfo"})
    )
    full_env = _cli_env()
    full_env.pop("DREAM_NAMESPACE", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Namespace: fromtaskinfo" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_namespace_falls_back_to_repo_basename(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    full_env = _cli_env()
    full_env.pop("DREAM_NAMESPACE", None)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--dry-run"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The fixture's repo dir is named "repo".
    assert f"Namespace: {tmp_git_repo.name}" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_verify_only_empty_index_exits_zero(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--verify-only"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0
    assert "No reconciled dreams" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_verify_only_passes_for_fully_reconciled_dream(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-064000Z-vok"
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | dream/proj/{dream_id} | main | abc1234 |\n",
    )
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--verify-only"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0
    assert "All 1 indexed dreams verified" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_verify_only_reports_missing_artifacts(tmp_git_repo):
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-064100Z-broken"
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | dream/proj/{dream_id} | main | abc1234 |\n",
    )
    # ONLY index, no dream artifacts.
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo), "--verify-only"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 1
    assert "Verification FAILED" in result.stdout
    assert "missing" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_cleanup_branches_after_reconcile(tmp_git_repo):
    """Full reconcile + push + cleanup pipeline."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    d1 = "20260420-065000Z-cleanup"
    m1 = _manifest_with(d1, discoveries=[
        _discovery("a.py::f", "Discovery for cleanup test."),
    ])
    branch = make_dream_branch(tmp_git_repo, env, "proj", d1, m1,
                               report=_default_report(d1))
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})

    # Reconcile + commit + push so the cleanup ancestor check passes.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo)],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Re-run with --cleanup-branches; no new dreams → falls through.
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo),
         "--cleanup-branches"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "Deleted: 1" in result.stdout
    # Branch gone from origin.
    post = _git("ls-remote", "--heads", "origin", branch,
                cwd=tmp_git_repo, env=env).stdout
    assert branch not in post


@pytest.mark.slow
@pytest.mark.integration
def test_cli_cleanup_no_new_no_indexed_exits_zero(tmp_git_repo):
    """`--cleanup-branches` with no new branches AND empty index exits 0."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo),
         "--cleanup-branches"],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0
    assert "Nothing in _index.md to clean up" in result.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_corrupted_manifest_json_skipped(tmp_git_repo):
    """A branch with malformed JSON in manifest.json must be reported as
    SKIP without crashing the run."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    bad_id = "20260420-066000Z-badjson"
    branch = f"dream/proj/{bad_id}"
    original = _git("rev-parse", "--abbrev-ref", "HEAD",
                    cwd=tmp_git_repo, env=env).stdout.strip()
    _git("checkout", "-q", "-b", branch, cwd=tmp_git_repo, env=env)
    dream_dir = tmp_git_repo / ".shadow" / "_dreams" / bad_id
    dream_dir.mkdir(parents=True)
    (dream_dir / "manifest.json").write_text("{this is not json")
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "bad json", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", branch, cwd=tmp_git_repo, env=env)
    _git("checkout", "-q", original, cwd=tmp_git_repo, env=env)

    full_env = _cli_env({"DREAM_NAMESPACE": "proj"})
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(tmp_git_repo)],
        capture_output=True, text=True, env=full_env,
    )
    assert result.returncode == 0
    assert "SKIP" in result.stdout
    assert "invalid JSON" in result.stdout


# --- Regression: update_index dry-run must not write to disk ---
# Bug surfaced by Phase-3 audit: prior update_index bootstrapped
# _dreams/_index.md (creating directory + file) BEFORE the dry_run check,
# so --dry-run silently materialized an empty index file. Dry-run must
# be fully read-only.

class TestUpdateIndexDryRunIsReadOnly:
    def test_dry_run_does_not_create_index_when_missing(
        self, dream_reconcile, tmp_git_repo
    ):
        shadow = tmp_git_repo / ".shadow"
        manifests = [
            ("dream/proj/xx", "20260420-1500Z-feat",
             {"category": "feature", "verdict": "useful"}),
        ]
        dream_reconcile.update_index(str(tmp_git_repo), manifests, dry_run=True)

        assert not (shadow / "_dreams" / "_index.md").exists()
        assert not (shadow / "_dreams").exists()

    def test_dry_run_does_not_create_dreams_dir_when_shadow_exists(
        self, dream_reconcile, tmp_git_repo
    ):
        shadow = tmp_git_repo / ".shadow"
        shadow.mkdir()
        manifests = [
            ("dream/proj/xx", "20260420-1500Z-feat",
             {"category": "feature", "verdict": "useful"}),
        ]
        dream_reconcile.update_index(str(tmp_git_repo), manifests, dry_run=True)

        assert not (shadow / "_dreams").exists()
        assert not (shadow / "_dreams" / "_index.md").exists()

    def test_dry_run_does_not_append_when_index_exists(
        self, dream_reconcile, tmp_git_repo
    ):
        shadow_dreams = tmp_git_repo / ".shadow" / "_dreams"
        shadow_dreams.mkdir(parents=True)
        index_path = shadow_dreams / "_index.md"
        original = (
            "# Dream Index\n\n"
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|----------|----------|---------|-------|--------|--------|------------|\n"
        )
        index_path.write_text(original)

        manifests = [
            ("dream/proj/xx", "20260420-1500Z-feat",
             {"category": "feature", "verdict": "useful"}),
        ]
        dream_reconcile.update_index(str(tmp_git_repo), manifests, dry_run=True)

        assert index_path.read_text() == original

    def test_dry_run_still_prints_preview(
        self, dream_reconcile, tmp_git_repo, capsys
    ):
        manifests = [
            ("dream/proj/xx", "20260420-1500Z-foo",
             {"category": "feature", "verdict": "useful"}),
            ("dream/proj/yy", "20260420-1600Z-bar",
             {"category": "bug", "verdict": "dead_end"}),
        ]
        dream_reconcile.update_index(str(tmp_git_repo), manifests, dry_run=True)
        out = capsys.readouterr().out
        assert "Would index: 20260420-1500Z-foo" in out
        assert "Would index: 20260420-1600Z-bar" in out


# ===========================================================================
# Canonical header helpers — B4 regression
# (reconciler-created shadows must match init-created ones)
# ===========================================================================

class TestCanonicalHeaderHelpers:
    @pytest.mark.parametrize("rel,lang", [
        ("src/auth.py", "Python"),
        ("lib/http.go", "Go"),
        ("app/main.ts", "TypeScript"),
        ("weird.unknownext", "Unknown"),
    ])
    def test_detect_language(self, dream_reconcile, rel, lang):
        assert dream_reconcile._detect_language(rel) == lang

    def test_detect_language_basename(self, dream_reconcile):
        # Basename map (e.g. Dockerfile/Makefile) must win over extension.
        assert dream_reconcile._detect_language("ops/Dockerfile") != "Unknown"

    @pytest.mark.parametrize("shadow,expected", [
        ("/repo/.shadow/src/auth.py.md", "src/auth.py"),
        ("/repo/.shadow/main.go.md", "main.go"),
        ("/repo/nested/.shadow/a/b/c.ts.md", "a/b/c.ts"),
    ])
    def test_source_rel_from_shadow(self, dream_reconcile, shadow, expected):
        assert dream_reconcile._source_rel_from_shadow(shadow) == expected

    def test_canonical_header_lines_match_init_template(self, dream_reconcile):
        lines = dream_reconcile._canonical_header_lines(
            "/repo/.shadow/src/auth.py.md"
        )
        text = "".join(lines)
        assert text.startswith("# Shadow: src/auth.py\n")
        assert "**Language**: Python\n" in text
        assert "## File-Level\n" in text
        assert "_No discoveries yet._\n" in text

    def test_canonical_header_unknown_language(self, dream_reconcile):
        text = "".join(
            dream_reconcile._canonical_header_lines("/r/.shadow/x.qqq.md")
        )
        assert "**Language**: Unknown\n" in text


# ===========================================================================
# _merge_refs_into_cross_file — B14 regression (ref union, not drop)
# ===========================================================================

class TestMergeRefsIntoCrossFile:
    def _write_cross(self, tmp_path, refs):
        p = tmp_path / "slug.md"
        body = ["# Title", "", "**Category**: pattern", "**Refs**:"]
        body += [f"- `{r}`" for r in refs]
        body += ["", "**Discovery**: something", ""]
        p.write_text("\n".join(body))
        return p

    def test_unions_new_refs(self, dream_reconcile, tmp_path):
        p = self._write_cross(tmp_path, ["src/a.py::f"])
        changed = dream_reconcile._merge_refs_into_cross_file(
            str(p), ["src/b.py::g", "src/a.py::f"]
        )
        assert changed is True
        text = p.read_text()
        assert "- `src/a.py::f`" in text
        assert "- `src/b.py::g`" in text
        # No duplication of the already-present ref.
        assert text.count("- `src/a.py::f`") == 1

    def test_no_change_when_all_present(self, dream_reconcile, tmp_path):
        p = self._write_cross(tmp_path, ["src/a.py::f"])
        before = p.read_text()
        changed = dream_reconcile._merge_refs_into_cross_file(
            str(p), ["src/a.py::f"]
        )
        assert changed is False
        assert p.read_text() == before

    def test_missing_file_returns_false(self, dream_reconcile, tmp_path):
        assert dream_reconcile._merge_refs_into_cross_file(
            str(tmp_path / "nope.md"), ["x::y"]
        ) is False

    def test_no_refs_block_returns_false(self, dream_reconcile, tmp_path):
        p = tmp_path / "norefs.md"
        p.write_text("# Title\n\nNo refs section here.\n")
        assert dream_reconcile._merge_refs_into_cross_file(
            str(p), ["x::y"]
        ) is False
        # File untouched.
        assert "No refs section here." in p.read_text()


# ===========================================================================
# _resolve_tip_commit — B10 regression (no 7-char truncation, hex-validated)
# ===========================================================================

class TestResolveTipCommit:
    def test_unknown_when_branch_absent(self, dream_reconcile, tmp_git_repo):
        env = _seed_repo(tmp_git_repo)
        _add_bare_remote(tmp_git_repo, env)
        assert dream_reconcile._resolve_tip_commit(
            str(tmp_git_repo), "does-not-exist"
        ) == "unknown"

    def test_resolves_real_branch_to_hex_sha(self, dream_reconcile, tmp_git_repo):
        env = _seed_repo(tmp_git_repo)
        _add_bare_remote(tmp_git_repo, env)
        dream_id = "20260420-030000Z-boot"
        branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                                   _default_manifest(dream_id))
        tip = dream_reconcile._resolve_tip_commit(str(tmp_git_repo), branch)
        assert tip != "unknown"
        assert re.fullmatch(r"[0-9a-fA-F]{7,40}", tip)
        # Matches the branch's actual full SHA prefix (not truncated to 7).
        full = _git("rev-parse", f"origin/{branch}",
                    cwd=tmp_git_repo, env=env).stdout.strip()
        assert full.startswith(tip)


# ===========================================================================
# Worktree GC — Fix 3 from bug-worktree-leak.md
#
# After cleanup_branches successfully deletes a dream branch, the matching
# worktree directory at $DREAM_WORKTREE_BASE/<ns>/dream-<slug>/ must also
# be removed. Pre-fix: directories accumulated forever (538/540 leaked on
# matplotlib; 691/693 on sphinx per the bug report).
# ===========================================================================

class TestSlugFromDreamId:
    """Direct tests for the slug-derivation helper. Critical: dream_ids
    contain '-' (the date itself has one), so a naive partition('-') yields
    the timestamp tail, NOT the slug. Worktree GC would target the wrong
    directory."""

    def test_extracts_simple_slug(self, dream_reconcile):
        assert dream_reconcile._slug_from_dream_id(
            "20260420-050000Z-foo"
        ) == "foo"

    def test_extracts_multipart_slug_with_dashes(self, dream_reconcile):
        """Slug like 't01-csv-fuzzer' — must NOT lose internal dashes."""
        assert dream_reconcile._slug_from_dream_id(
            "20260420-050000Z-t01-csv-fuzzer"
        ) == "t01-csv-fuzzer"

    def test_extracts_slug_with_dots_and_underscores(self, dream_reconcile):
        assert dream_reconcile._slug_from_dream_id(
            "20260420-050000Z-v1.2.3_beta"
        ) == "v1.2.3_beta"

    @pytest.mark.parametrize("bad", [
        "",                  # empty
        None,                # not a string
        "garbage",           # no timestamp
        "2026-04-20-foo",    # wrong date shape
        "20260420050000Z-foo",  # missing dash between date and time
        "20260420-050000-foo",  # missing Z
    ])
    def test_returns_none_for_malformed_id(self, dream_reconcile, bad):
        assert dream_reconcile._slug_from_dream_id(bad) is None


@pytest.mark.slow
def test_cleanup_branches_also_removes_worktree(
    dream_reconcile, tmp_git_repo, tmp_path, monkeypatch
):
    """Full integration: after cleanup_branches deletes the branch, the
    worktree directory at ${DREAM_WORKTREE_BASE}/<ns>/dream-<slug>/ must
    also be gone. Pre-fix this directory leaked forever."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-070000Z-gctest"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Build the worktree dir the reconciler will look for.
    base = tmp_path / "wt-base"
    worktree_dir = base / "proj" / "dream-gctest"
    worktree_dir.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(worktree_dir), branch,
         cwd=tmp_git_repo, env=env)
    assert worktree_dir.is_dir()

    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(base))

    deleted, _ = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 1
    assert not worktree_dir.exists(), (
        "worktree GC didn't fire — bug-worktree-leak.md regression"
    )


@pytest.mark.slow
def test_cleanup_branches_worktree_gc_falls_back_on_dead_gitdir(
    dream_reconcile, tmp_git_repo, tmp_path, monkeypatch
):
    """The whole reason this fix exists: git worktree remove fails silently
    when the gitdir pointer is broken. The reconciler's GC must fall back
    to rm -rf (safety-gated) so the directory does NOT leak."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-080000Z-dead"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Manually build a DEAD worktree directory at the expected location
    # (broken .git pointer — git worktree remove will refuse to clean it).
    base = tmp_path / "wt-base"
    worktree_dir = base / "proj" / "dream-dead"
    worktree_dir.mkdir(parents=True)
    (worktree_dir / ".git").write_text("gitdir: /nonexistent/wt-dir\n")
    (worktree_dir / "leaked.pyc").write_text("# leak me\n")

    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(base))

    deleted, _ = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 1
    assert not worktree_dir.exists(), (
        "fallback rm -rf must clean dead worktrees"
    )


@pytest.mark.slow
def test_cleanup_branches_worktree_gc_refuses_unsafe_base(
    dream_reconcile, tmp_git_repo, tmp_path, monkeypatch, capsys
):
    """If $DREAM_WORKTREE_BASE is set to something sensitive (/, /tmp, $HOME),
    the safety gate must refuse the rm even though the branch delete itself
    succeeded. The branch should still be deleted (it's already been
    successfully pushed and verified)."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    dream_id = "20260420-090000Z-unsafe"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Decoy at /tmp/proj/dream-unsafe — must NOT be touched.
    decoy = tmp_path / "should-not-be-deleted"
    decoy.mkdir()
    (decoy / "important.txt").write_text("keep me\n")

    # Point DREAM_WORKTREE_BASE at /tmp — gate must refuse.
    monkeypatch.setenv("DREAM_WORKTREE_BASE", "/tmp")

    deleted, _ = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    # Branch delete itself MUST still succeed — GC failure is non-fatal.
    assert deleted == 1
    captured = capsys.readouterr()
    assert "Skipping worktree GC" in captured.out or "sensitive root" in captured.out
    # Decoy is untouched.
    assert decoy.is_dir()
    assert (decoy / "important.txt").read_text() == "keep me\n"


@pytest.mark.slow
def test_cleanup_branches_worktree_gc_skips_unparseable_dream_id(
    dream_reconcile, tmp_git_repo, tmp_path, monkeypatch, capsys
):
    """A malformed dream_id (manifest schema drift, future migration, …)
    must NOT crash cleanup_branches. The GC bails silently and the branch
    is still deleted."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    # Branch uses a malformed dream_id (no Z, wrong shape).
    dream_id = "not-a-real-dream-id"
    branch = make_dream_branch(tmp_git_repo, env, "proj", dream_id,
                               _default_manifest(dream_id))
    _seed_dream_artifacts(tmp_git_repo, dream_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_id} | bug hunting | useful | T | {branch} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(tmp_path / "wt-base"))

    deleted, _ = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch, dream_id, _default_manifest(dream_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 1  # branch still deleted


# ===========================================================================
# Cross-deletion guard for slug-collision (S2 from 5-model review panel)
# ===========================================================================
# Worktree paths are slug-only (see dream-setup.sh:165), but dream_ids
# include a timestamp. So two dreams with the same slug at different
# times share a worktree path. If dream A is reconciled AFTER dream B has
# reclaimed the shared path, A's GC must NOT delete B's live worktree.


class TestRegisteredWorktreeBranch:
    """Unit-level coverage of _registered_worktree_branch — the helper
    that lets the GC distinguish 'our worktree' from 'someone else's
    worktree at the same shared path'."""

    @pytest.mark.slow
    def test_returns_branch_for_registered_path(
        self, dream_reconcile, tmp_git_repo, tmp_path
    ):
        env = _seed_repo(tmp_git_repo)
        wt = tmp_path / "wt"
        _git("worktree", "add", "-q", "-b", "feature-x", str(wt),
             cwd=tmp_git_repo, env=env)
        branch = dream_reconcile._registered_worktree_branch(
            str(tmp_git_repo), str(wt)
        )
        assert branch == "feature-x"

    @pytest.mark.slow
    def test_returns_none_for_unregistered_path(
        self, dream_reconcile, tmp_git_repo, tmp_path
    ):
        _seed_repo(tmp_git_repo)
        # A path that's not registered as a worktree at all.
        branch = dream_reconcile._registered_worktree_branch(
            str(tmp_git_repo), str(tmp_path / "nope")
        )
        assert branch is None

    @pytest.mark.slow
    def test_matches_through_symlink(
        self, dream_reconcile, tmp_git_repo, tmp_path
    ):
        """macOS /tmp ↔ /private/tmp scenario: the path we query may
        differ from the path git recorded, but realpath unifies them."""
        env = _seed_repo(tmp_git_repo)
        real_wt = tmp_path / "real-wt"
        link_wt = tmp_path / "link-wt"
        _git("worktree", "add", "-q", "-b", "feature-y", str(real_wt),
             cwd=tmp_git_repo, env=env)
        link_wt.symlink_to(real_wt)
        branch_via_link = dream_reconcile._registered_worktree_branch(
            str(tmp_git_repo), str(link_wt)
        )
        assert branch_via_link == "feature-y"


@pytest.mark.slow
def test_cleanup_branches_does_not_clobber_concurrent_slug_collision(
    dream_reconcile, tmp_git_repo, tmp_path, monkeypatch, capsys
):
    """S2 from review panel — REPRODUCES the cross-deletion bug.

    Setup: two dreams share slug='same' at different timestamps.
        Dream A: 20260420-100000Z-same
        Dream B: 20260420-110000Z-same   (created after A)
    Dream B has reclaimed the shared worktree path; A's branch exists
    but has no worktree of its own. Reconciling A must NOT touch B's
    live worktree (where uncommitted work would otherwise be lost)."""
    env = _seed_repo(tmp_git_repo)
    _add_bare_remote(tmp_git_repo, env)
    base = tmp_path / "wt-base"
    base.mkdir()
    monkeypatch.setenv("DREAM_WORKTREE_BASE", str(base))

    # Build dream A's branch (no worktree — simulating the leak case
    # where the path was reclaimed by a later dream).
    dream_a_id = "20260420-100000Z-same"
    dream_b_id = "20260420-110000Z-same"
    branch_a = make_dream_branch(tmp_git_repo, env, "proj", dream_a_id,
                                 _default_manifest(dream_a_id))
    branch_b = make_dream_branch(tmp_git_repo, env, "proj", dream_b_id,
                                 _default_manifest(dream_b_id))
    _seed_dream_artifacts(tmp_git_repo, dream_a_id)
    _write_index(
        tmp_git_repo,
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
        f"| {dream_a_id} | bug hunting | useful | A | {branch_a} | main | abc1234 |\n",
    )
    _git("add", "-A", cwd=tmp_git_repo, env=env)
    _git("commit", "-q", "-m", "reconcile A", cwd=tmp_git_repo, env=env)
    _git("push", "-q", "origin", "main", cwd=tmp_git_repo, env=env)

    # Dream B claims the shared path. (In production, dream-setup.sh's
    # idempotent pre-clean would have already wiped any stale A worktree.)
    shared_path = base / "proj" / "dream-same"
    shared_path.parent.mkdir(parents=True)
    _git("worktree", "add", "-q", str(shared_path), branch_b,
         cwd=tmp_git_repo, env=env)
    assert shared_path.is_dir()
    # Add a "user file" — proxy for uncommitted work that must survive.
    (shared_path / "uncommitted-work.txt").write_text("PRECIOUS WORK\n")

    # Reconcile A — its GC must NOT take down B's worktree.
    deleted, _ = dream_reconcile.cleanup_branches(
        str(tmp_git_repo),
        [(branch_a, dream_a_id, _default_manifest(dream_a_id))],
        "proj",
        dry_run=False,
    )
    assert deleted == 1, "A's branch should have been cleaned up"

    # The critical assertion: B's worktree must survive intact.
    assert shared_path.is_dir(), (
        "B's live worktree was DESTROYED by A's reconciler GC — "
        "cross-deletion regression (S2)"
    )
    assert (shared_path / "uncommitted-work.txt").read_text() == "PRECIOUS WORK\n", (
        "user's uncommitted work in B's worktree was lost"
    )
    # B's branch must still exist too.
    branches_out = _git("branch", cwd=tmp_git_repo, env=env).stdout
    assert branch_b in branches_out, f"B's branch is gone: {branches_out}"

    # The skip should have been LOGGED so operators can audit.
    captured = capsys.readouterr()
    assert "now belongs to" in captured.out, (
        f"cross-deletion skip was silent — operators won't know:\n{captured.out}"
    )
