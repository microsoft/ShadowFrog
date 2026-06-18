"""Tests for meditate-repair.py — dream index repair pipeline."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "shadow-frog-meditate" / "meditate-repair.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_dream(dreams_dir: Path, did: str, *, report: str = "", manifest: dict | None = None):
    """Create a synthetic dream folder with optional report and manifest."""
    d = dreams_dir / did
    d.mkdir(parents=True, exist_ok=True)
    if report:
        (d / "report.md").write_text(report)
    if manifest is not None:
        (d / "manifest.json").write_text(json.dumps(manifest))
    return d


def make_index(dreams_dir: Path, rows: list[str]) -> Path:
    """Write a synthetic _index.md with header + rows."""
    header = (
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
    )
    content = header + "\n".join(rows) + "\n"
    index_path = dreams_dir / "_index.md"
    index_path.write_text(content)
    return index_path


# ---------------------------------------------------------------------------
# detect_corrupted tests
# ---------------------------------------------------------------------------

class TestDetectCorrupted:
    def test_valid_not_flagged(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-valid"
        report = f'---\ndream_id: "{did}"\n---\n# Valid\n'
        make_dream(dreams, did, report=report)

        result = meditate_repair.detect_corrupted(str(dreams))
        assert did not in result

    def test_mismatched_id_flagged(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-mismatch"
        # Report claims a different dream_id
        report = '---\ndream_id: "20250101-120000Z-OTHER"\n---\n# Mismatch\n'
        make_dream(dreams, did, report=report)

        result = meditate_repair.detect_corrupted(str(dreams))
        assert did in result

    def test_no_report_not_flagged(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-noreport"
        (dreams / did).mkdir(parents=True)

        result = meditate_repair.detect_corrupted(str(dreams))
        assert did not in result


# ---------------------------------------------------------------------------
# lookup_verdict tests
# ---------------------------------------------------------------------------

class TestLookupVerdict:
    def test_manifest_verdict(self, meditate_repair, tmp_path):
        did = "20250101-120000Z-v"
        d = tmp_path / did
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"verdict": "useful"}))

        result = meditate_repair.lookup_verdict(str(d), "")
        assert result == "useful"

    def test_dead_end_signal_in_content(self, meditate_repair, tmp_path):
        did = "20250101-120000Z-dead"
        d = tmp_path / did
        d.mkdir()
        content = "## Verdict\nThis was a dead end, no improvement observed."
        result = meditate_repair.lookup_verdict(str(d), content)
        assert result == "dead_end"

    def test_useful_signal_in_content(self, meditate_repair, tmp_path):
        did = "20250101-120000Z-useful"
        d = tmp_path / did
        d.mkdir()
        content = "## Verdict\nAll tests pass and the fix is confirmed."
        result = meditate_repair.lookup_verdict(str(d), content)
        assert result == "useful"

    def test_no_signal_returns_empty(self, meditate_repair, tmp_path):
        did = "20250101-120000Z-nothing"
        d = tmp_path / did
        d.mkdir()
        result = meditate_repair.lookup_verdict(str(d), "Nothing relevant here.")
        assert result == ""

    def test_malformed_manifest_falls_through(self, meditate_repair, tmp_path):
        did = "20250101-120000Z-bad"
        d = tmp_path / did
        d.mkdir()
        (d / "manifest.json").write_text("not json")
        content = "## Verdict\nThis is useful and verified."
        result = meditate_repair.lookup_verdict(str(d), content)
        assert result == "useful"


# ---------------------------------------------------------------------------
# parse_dream_id tests
# ---------------------------------------------------------------------------

class TestParseDreamId:
    def test_valid(self, meditate_repair):
        result = meditate_repair.parse_dream_id("20250101-120000Z-my-slug")
        assert result == ("20250101-120000Z", "my-slug")

    def test_complex_slug(self, meditate_repair):
        result = meditate_repair.parse_dream_id("20250420-140000Z-cache-poison-sequence")
        assert result == ("20250420-140000Z", "cache-poison-sequence")

    def test_invalid_returns_none(self, meditate_repair):
        assert meditate_repair.parse_dream_id("not-a-dream-id") is None
        assert meditate_repair.parse_dream_id("") is None
        assert meditate_repair.parse_dream_id("2025-01-01-slug") is None


# ---------------------------------------------------------------------------
# repair_row tests
# ---------------------------------------------------------------------------

class TestRepairRow:
    def test_repairs_unknown_category(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-repair"
        report = (
            '---\ndream_id: "20250101-120000Z-repair"\n---\n'
            "# Good Title\n\n**Category**: investigation\n\n## Verdict\nAll tests pass.\n"
        )
        make_dream(dreams, did, report=report)

        parts = ["", did, "unknown", "unknown", did, "branch", "main", "abc"]
        result = meditate_repair.repair_row(parts, str(dreams))
        assert result is not None
        new_parts, changed = result
        assert changed
        assert new_parts[2] == "investigation"

    def test_repairs_unknown_verdict(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-vrepair"
        report = '---\ndream_id: "20250101-120000Z-vrepair"\n---\n# Title\n## Verdict\nDead end.\n'
        make_dream(dreams, did, report=report, manifest={"verdict": "dead_end"})

        parts = ["", did, "investigation", "unknown", did, "branch", "main", "abc"]
        result = meditate_repair.repair_row(parts, str(dreams))
        assert result is not None
        new_parts, changed = result
        assert changed
        assert new_parts[3] == "dead_end"

    def test_repairs_generic_title(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-slug"
        report = f'---\ndream_id: "{did}"\n---\n# A Proper Title\nBody.\n'
        make_dream(dreams, did, report=report)

        # Generic title = just the slug
        parts = ["", did, "investigation", "useful", "20250101-120000Z-slug", "branch", "main", "abc"]
        result = meditate_repair.repair_row(parts, str(dreams))
        assert result is not None
        new_parts, changed = result
        assert changed
        assert new_parts[4] == "A Proper Title"

    def test_repairs_unknown_category_from_frontmatter(self, meditate_repair, tmp_path):
        """B8: category lives in YAML frontmatter (`category: <value>`), not a
        `**Category**:` body marker. Recovery must fall back to frontmatter."""
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-fmcat"
        report = (
            f'---\ndream_id: "{did}"\ncategory: "bug hunting"\nverdict: useful\n---\n'
            "# Good Title\n\nBody with no Category marker.\n"
        )
        make_dream(dreams, did, report=report)

        parts = ["", did, "unknown", "useful", did, "branch", "main", "abc"]
        result = meditate_repair.repair_row(parts, str(dreams))
        assert result is not None
        new_parts, changed = result
        assert changed
        assert new_parts[2] == "bug hunting"

    def test_no_report_returns_none(self, meditate_repair, tmp_path):
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-gone"
        (dreams / did).mkdir(parents=True)
        parts = ["", did, "unknown", "unknown", did, "branch", "main", "abc"]
        result = meditate_repair.repair_row(parts, str(dreams))
        assert result is None


# ---------------------------------------------------------------------------
# resolve_parent_in_index tests
# ---------------------------------------------------------------------------

class TestResolveParentInIndex:
    def test_exact_match(self, meditate_repair):
        all_ids = ["20250101-120000Z-base", "20250102-120000Z-extend"]
        result = meditate_repair.resolve_parent_in_index("20250101-120000Z-base", all_ids)
        assert result == "20250101-120000Z-base"

    def test_slug_match(self, meditate_repair):
        all_ids = ["20250101-120000Z-base", "20250102-120000Z-extend"]
        # Different timestamp, same slug
        result = meditate_repair.resolve_parent_in_index("99999999-999999Z-base", all_ids)
        assert result == "20250101-120000Z-base"

    def test_no_match_returns_none(self, meditate_repair):
        all_ids = ["20250101-120000Z-base"]
        result = meditate_repair.resolve_parent_in_index("20250101-120000Z-nonexist", all_ids)
        assert result is None

    def test_invalid_id_returns_none(self, meditate_repair):
        all_ids = ["20250101-120000Z-base"]
        result = meditate_repair.resolve_parent_in_index("not-valid", all_ids)
        assert result is None


# ---------------------------------------------------------------------------
# repair_parent tests (I4 regression — step 10/11)
# ---------------------------------------------------------------------------

class TestRepairParent:
    def test_manifest_provides_parent(self, meditate_repair, tmp_path):
        """Step 10: manifest parent_branch repairs the parent cell."""
        dreams = tmp_path / "_dreams"
        parent_did = "20250101-120000Z-base"
        child_did = "20250102-120000Z-child"
        make_dream(dreams, parent_did, report=f'---\ndream_id: "{parent_did}"\n---\n# Base\n')
        make_dream(
            dreams, child_did,
            report=f'---\ndream_id: "{child_did}"\n---\n# Child\n',
            manifest={"parent_branch": f"dream/proj/{parent_did}"},
        )

        all_ids = [parent_did, child_did]
        bmap = {d: f"dream/proj/{d}" for d in all_ids}
        parts = ["", child_did, "investigation", "useful", "Child",
                 f"dream/proj/{child_did}", "main", "abc"]
        changed = meditate_repair.repair_parent(parts, str(dreams), all_ids, bmap)
        assert changed
        # Parent column is a BRANCH NAME (the resolved row's branch), not a dream_id.
        assert parts[6] == f"dream/proj/{parent_did}"

    def test_missing_manifest_and_slug_heuristic(self, meditate_repair, tmp_path):
        """Step 11: slug heuristic with compounding suffix."""
        dreams = tmp_path / "_dreams"
        parent_did = "20250101-120000Z-retry-logic"
        child_did = "20250102-120000Z-retry-logic-extend"
        make_dream(dreams, parent_did, report=f'---\ndream_id: "{parent_did}"\n---\n# Base\n')
        make_dream(dreams, child_did, report=f'---\ndream_id: "{child_did}"\n---\n# Extend\n')

        all_ids = [parent_did, child_did]
        bmap = {d: f"dream/proj/{d}" for d in all_ids}
        parts = ["", child_did, "investigation", "useful", "Extend",
                 f"dream/proj/{child_did}", "main", "abc"]
        changed = meditate_repair.repair_parent(parts, str(dreams), all_ids, bmap)
        assert changed
        assert parts[6] == f"dream/proj/{parent_did}"

    def test_ambiguity_warning_keeps_main(self, meditate_repair, tmp_path, capsys):
        """Multiple candidate parents with no exact slug match → emits warning, keeps main."""
        dreams = tmp_path / "_dreams"
        # Two candidates whose slugs both start with "cache" (the base_slug after removing -fix)
        parent1 = "20250101-120000Z-cache-v1"
        parent2 = "20250101-130000Z-cache-v2"
        # Child slug is "cache-fix"; stripping "-fix" → base_slug = "cache"
        # Both parents' slugs start with "cache" so both match, neither is exact "cache"
        child_did = "20250102-120000Z-cache-fix"
        make_dream(dreams, parent1, report=f'---\ndream_id: "{parent1}"\n---\n# P1\n')
        make_dream(dreams, parent2, report=f'---\ndream_id: "{parent2}"\n---\n# P2\n')
        make_dream(dreams, child_did, report=f'---\ndream_id: "{child_did}"\n---\n# Child\n')

        all_ids = [parent1, parent2, child_did]
        bmap = {d: f"dream/proj/{d}" for d in all_ids}
        parts = ["", child_did, "investigation", "useful", "Child",
                 f"dream/proj/{child_did}", "main", "abc"]
        changed = meditate_repair.repair_parent(parts, str(dreams), all_ids, bmap)
        assert not changed
        assert parts[6] == "main"
        # Check warning was emitted
        captured = capsys.readouterr()
        assert "AMBIGUOUS" in captured.err

    def test_no_change_when_parent_not_main(self, meditate_repair, tmp_path):
        """If parent is already not 'main', no repair attempted."""
        dreams = tmp_path / "_dreams"
        did = "20250101-120000Z-test"
        make_dream(dreams, did, report=f'---\ndream_id: "{did}"\n---\n# T\n')

        parts = ["", did, "investigation", "useful", "T", "branch",
                 "dream/proj/20250101-110000Z-other", "abc"]
        changed = meditate_repair.repair_parent(
            parts, str(dreams), [did], {did: f"dream/proj/{did}"}
        )
        assert not changed


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestCLI:
    def test_help_exits_zero(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True, text=True,
        )
        assert result.returncode == 0

    def test_repair_corrupted_index(self, tmp_path):
        """Runs repair against a synthetic corrupted index."""
        shadow = tmp_path / ".shadow"
        dreams = shadow / "_dreams"
        dreams.mkdir(parents=True)

        did_ok = "20250101-120000Z-good"
        did_bad = "20250102-120000Z-needsfix"

        make_dream(
            dreams, did_ok,
            report=f'---\ndream_id: "{did_ok}"\ncategory: investigation\nverdict: useful\n---\n# Good Title\n',
            manifest={"verdict": "useful"},
        )
        make_dream(
            dreams, did_bad,
            report=f'---\ndream_id: "{did_bad}"\n---\n# Real Title\n\n**Category**: bug hunting\n\n## Verdict\nAll tests pass.\n',
            manifest={"verdict": "useful"},
        )

        rows = [
            f"| {did_ok} | investigation | useful | Good Title | dream/proj/{did_ok} | main | aaa |",
            f"| {did_bad} | unknown | unknown | {did_bad} | dream/proj/{did_bad} | main | bbb |",
        ]
        make_index(dreams, rows)

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shadow-dir", str(shadow)],
            capture_output=True, text=True,
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "Repaired" in result.stdout

        # Verify the index was actually fixed
        repaired_content = (dreams / "_index.md").read_text()
        assert "bug hunting" in repaired_content
        assert "Real Title" in repaired_content

    def test_clean_index_no_repairs(self, coupon_demo):
        """Running against clean coupon_demo reports 0 repairs."""
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True,
        )
        assert result.returncode == 0
        # Should say "Repaired 0 rows"
        assert "Repaired 0" in result.stdout or "No repairs" in result.stdout.lower()
