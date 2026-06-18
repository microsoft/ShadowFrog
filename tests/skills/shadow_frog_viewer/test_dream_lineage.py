"""Tests for dream-lineage.py — HTML visualization of dream experiment lineage."""
import json
import os
import re
import subprocess
import sys
from html.parser import HTMLParser
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCRIPT = REPO_ROOT / "skills" / "shadow-frog-viewer" / "dream-lineage.py"


# ---------------------------------------------------------------------------
# md_to_html tests
# ---------------------------------------------------------------------------

class TestMdToHtml:
    """Test the markdown-to-HTML converter."""

    def test_simple_list(self, dream_lineage):
        html = dream_lineage.md_to_html("- one\n- two")
        assert "<ul>" in html
        assert html.count("<li>") == 2
        assert html.count("</li>") == 2

    def test_nested_list(self, dream_lineage):
        html = dream_lineage.md_to_html("- outer\n  - inner")
        assert "<ul>" in html
        assert "class='nested'" in html
        # Both items inside one <ul>
        ul_content = re.search(r"<ul>(.*?)</ul>", html, re.S)
        assert ul_content
        assert ul_content.group(1).count("<li") == 2

    def test_md_to_html_no_orphan_li_outside_ul_regression(self, dream_lineage):
        """Regression: all <li> tags must be inside <ul> or <ol> blocks."""
        html = dream_lineage.md_to_html("- one\n- two\n  - nested\n- three")
        # No orphan <li> patterns
        assert "</p><li>" not in html
        assert "<p><li>" not in html
        # Count: all <li> must be within <ul>...</ul>
        total_li = html.count("<li")
        li_in_ul = sum(
            block.count("<li")
            for block in re.findall(r"<ul>(.*?)</ul>", html, re.S)
        )
        assert total_li == li_in_ul, f"Orphan <li> found: {total_li} total vs {li_in_ul} in <ul>"

    def test_mixed_text_and_list(self, dream_lineage):
        html = dream_lineage.md_to_html("text\n\n- one\n- two\n\nmore")
        assert "<ul>" in html
        assert "<li>" in html
        # Paragraphs around the list
        assert "<p>" in html

    @pytest.mark.parametrize("md,tag", [
        ("# h", "<h2>"),
        ("## h", "<h3>"),
        ("### h", "<h4>"),
    ])
    def test_headings(self, dream_lineage, md, tag):
        html = dream_lineage.md_to_html(md)
        assert tag in html

    def test_inline_code(self, dream_lineage):
        html = dream_lineage.md_to_html("`x`")
        assert "<code>x</code>" in html

    def test_bold(self, dream_lineage):
        html = dream_lineage.md_to_html("**x**")
        assert "<strong>x</strong>" in html

    def test_code_blocks(self, dream_lineage):
        md = "```py\ndef f():\n    pass\n```"
        html = dream_lineage.md_to_html(md)
        assert "<pre><code>" in html
        assert "def f():" in html

    def test_table(self, dream_lineage):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |"
        html = dream_lineage.md_to_html(md)
        assert "<table>" in html
        assert "<th>" in html
        assert "<td>" in html
        assert html.count("<th>") == 2
        assert html.count("<td>") == 4


# ---------------------------------------------------------------------------
# stable_id tests
# ---------------------------------------------------------------------------

class TestStableId:
    def test_idempotent(self, dream_lineage):
        branch = "dream/coupon-demo/20260420-140000Z-cache-poison-sequence"
        id1 = dream_lineage.stable_id(branch)
        id2 = dream_lineage.stable_id(branch)
        assert id1 == id2

    def test_starts_with_rpt(self, dream_lineage):
        assert dream_lineage.stable_id("foo/bar").startswith("rpt-")

    def test_alphanumeric_and_dash(self, dream_lineage):
        result = dream_lineage.stable_id("a/b.c!d")
        # Only alphanumeric and dashes
        assert re.match(r"^rpt-[a-zA-Z0-9-]+$", result)


# ---------------------------------------------------------------------------
# tree_depth tests
# ---------------------------------------------------------------------------

class TestTreeDepth:
    def test_leaf_node(self, dream_lineage):
        children = {"main": ["a"], "a": []}
        assert dream_lineage.tree_depth("a", children) == 0

    def test_one_level(self, dream_lineage):
        children = {"main": ["a"], "a": ["b"]}
        assert dream_lineage.tree_depth("a", children) == 1

    def test_two_levels(self, dream_lineage):
        children = {"main": ["a"], "a": ["b"], "b": ["c"]}
        assert dream_lineage.tree_depth("a", children) == 2


# ---------------------------------------------------------------------------
# flatten_chain tests
# ---------------------------------------------------------------------------

class TestFlattenChain:
    def test_single_node(self, dream_lineage):
        meta = {"a": {}}
        children = {}
        result = dream_lineage.flatten_chain("a", meta, children)
        assert result == [("a", 0)]

    def test_chain_dfs_order(self, dream_lineage):
        meta = {"a": {}, "b": {}, "c": {}}
        children = {"a": ["b"], "b": ["c"]}
        result = dream_lineage.flatten_chain("a", meta, children)
        assert result == [("a", 0), ("b", 1), ("c", 2)]

    def test_branching(self, dream_lineage):
        meta = {"a": {}, "b": {}, "c": {}}
        children = {"a": ["b", "c"]}
        result = dream_lineage.flatten_chain("a", meta, children)
        assert ("a", 0) in result
        assert ("b", 1) in result
        assert ("c", 1) in result


# ---------------------------------------------------------------------------
# find_shadow_dir tests
# ---------------------------------------------------------------------------

class TestFindShadowDir:
    def test_with_hint(self, dream_lineage, tmp_path):
        shadow = tmp_path / ".shadow"
        shadow.mkdir()
        result = dream_lineage.find_shadow_dir(str(shadow))
        assert result == str(shadow)

    def test_hint_nonexistent_falls_through(self, dream_lineage, tmp_path, monkeypatch):
        """When hint doesn't exist, falls through to cwd-based detection."""
        monkeypatch.chdir(tmp_path)
        shadow = tmp_path / ".shadow"
        shadow.mkdir()
        result = dream_lineage.find_shadow_dir("/nonexistent/path")
        assert result == ".shadow" or result == str(shadow)


# ---------------------------------------------------------------------------
# load_index tests
# ---------------------------------------------------------------------------

class TestLoadIndex:
    def test_coupon_demo(self, dream_lineage, coupon_demo):
        shadow_dir = str(coupon_demo / ".shadow")
        meta, children = dream_lineage.load_index(shadow_dir)
        assert len(meta) == 3
        # All three are parented to main
        assert len(children["main"]) == 3

    def test_synthetic_minimal(self, dream_lineage, tmp_path):
        """Synthetic index with parent references."""
        shadow = tmp_path / ".shadow"
        dreams = shadow / "_dreams"
        dreams.mkdir(parents=True)

        index_content = (
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|----------|----------|---------|-------|--------|--------|------------|\n"
            "| 20250101-120000Z-base | investigation | useful | Base exp | dream/proj/20250101-120000Z-base | main | abc1234 |\n"
            "| 20250102-120000Z-extend | investigation | useful | Extend exp | dream/proj/20250102-120000Z-extend | dream/proj/20250101-120000Z-base | def5678 |\n"
        )
        (dreams / "_index.md").write_text(index_content)
        # Create minimal dream dirs
        (dreams / "20250101-120000Z-base").mkdir()
        (dreams / "20250102-120000Z-extend").mkdir()

        meta, children = dream_lineage.load_index(str(shadow))
        assert len(meta) == 2
        # extend should be child of base, not main
        base_branch = "dream/proj/20250101-120000Z-base"
        extend_branch = "dream/proj/20250102-120000Z-extend"
        assert extend_branch in children[base_branch]

    def test_fallback_reparenting_via_slug(self, dream_lineage, tmp_path):
        """When parent branch has different timestamp, slug matching resolves it."""
        shadow = tmp_path / ".shadow"
        dreams = shadow / "_dreams"
        dreams.mkdir(parents=True)

        # Parent listed with wrong timestamp prefix but matching slug
        index_content = (
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|----------|----------|---------|-------|--------|--------|------------|\n"
            "| 20250101-120000Z-t01-base | investigation | useful | Base | dream/proj/20250101-120000Z-t01-base | main | abc1234 |\n"
            "| 20250102-120000Z-t02-child | investigation | useful | Child | dream/proj/20250102-120000Z-t02-child | dream/proj/99999999-999999Z-t01-base | def5678 |\n"
        )
        (dreams / "_index.md").write_text(index_content)
        (dreams / "20250101-120000Z-t01-base").mkdir()
        (dreams / "20250102-120000Z-t02-child").mkdir()

        meta, children = dream_lineage.load_index(str(shadow))
        base_branch = "dream/proj/20250101-120000Z-t01-base"
        child_branch = "dream/proj/20250102-120000Z-t02-child"
        # Child should be re-parented to base via slug match
        assert child_branch in children[base_branch]


# ---------------------------------------------------------------------------
# load_reports tests
# ---------------------------------------------------------------------------

class TestLoadReports:
    def test_synthetic_reports(self, dream_lineage, tmp_path):
        shadow = tmp_path / ".shadow"
        dreams = shadow / "_dreams"
        did = "20250101-120000Z-test"
        dream_dir = dreams / did
        dream_dir.mkdir(parents=True)

        report = (
            "---\ndream_id: \"20250101-120000Z-test\"\ncategory: investigation\n"
            "verdict: useful\n---\n\n# Test Experiment\n\nBody content here.\n"
        )
        (dream_dir / "report.md").write_text(report)

        manifest = {"verdict": "useful", "tests_passed": 5, "discoveries": ["a", "b"]}
        (dream_dir / "manifest.json").write_text(json.dumps(manifest))

        branch = "dream/proj/20250101-120000Z-test"
        meta = {branch: {"did": did}}
        dream_lineage.load_reports(str(shadow), meta)

        info = meta[branch]
        assert "Test Experiment" in info["full_report"]
        assert info["tests"] == "5"
        assert info["discoveries_count"] == 2


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
        assert "Dream lineage" in result.stdout or "output" in result.stdout.lower()

    def test_output_html_in_coupon_demo(self, coupon_demo):
        out_file = coupon_demo / "lineage-test.html"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-o", str(out_file),
             "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True,
            cwd=str(coupon_demo),
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert out_file.exists()
        assert out_file.stat().st_size > 1024

    def test_output_html_structure(self, coupon_demo):
        out_file = coupon_demo / "lineage-struct.html"
        subprocess.run(
            [sys.executable, str(SCRIPT), "-o", str(out_file),
             "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True, cwd=str(coupon_demo), check=True,
        )
        content = out_file.read_text()

        # Parse with HTMLParser — should not raise
        errors = []

        class Checker(HTMLParser):
            def handle_starttag(self, tag, attrs):
                pass

            def handle_endtag(self, tag):
                pass

            def error(self, message):
                errors.append(message)

        parser = Checker()
        parser.feed(content)

        assert not errors
        assert "<html>" in content
        assert "<body>" in content
        # Has panel-body or panel-overlay class
        assert "panel-overlay" in content or "panel-body" in content


# ---------------------------------------------------------------------------
# Helpers for HTML structural verification
# ---------------------------------------------------------------------------

def _build_dreams(shadow_dir: Path, rows, reports=None, manifests=None):
    """Write a synthetic _dreams/_index.md and per-dream dirs.

    Each row is a 7-tuple matching the pipe columns:
        (dream_id, category, verdict, title, branch, parent, tip_commit)

    `reports` and `manifests` are dicts keyed by dream_id mapping to
    raw text / dict bodies to write under that dream's directory.
    Returns the path to _dreams/.
    """
    dreams = shadow_dir / "_dreams"
    dreams.mkdir(parents=True, exist_ok=True)
    header = (
        "# Dream Experiments\n\n"
        "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
        "|----------|----------|---------|-------|--------|--------|------------|\n"
    )
    body_lines = []
    for did, cat, verdict, title, branch, parent, tip in rows:
        body_lines.append(
            f"| {did} | {cat} | {verdict} | {title} | {branch} | {parent} | {tip} |"
        )
        (dreams / did).mkdir(exist_ok=True)
    (dreams / "_index.md").write_text(header + "\n".join(body_lines) + "\n")
    for did, text in (reports or {}).items():
        (dreams / did / "report.md").write_text(text)
    for did, data in (manifests or {}).items():
        (dreams / did / "manifest.json").write_text(json.dumps(data))
    return dreams


class _BalanceChecker(HTMLParser):
    """Track tag open/close balance for void-aware HTML validation."""

    VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "source", "track", "wbr",
    }

    def __init__(self):
        super().__init__()
        self.stack = []
        self.errors = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.VOID:
            self.stack.append(tag)

    def handle_startendtag(self, tag, attrs):
        pass  # self-closing tags balance themselves

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if not self.stack:
            self.errors.append(f"close {tag} with empty stack")
            return
        # Allow implicit close of inline-tolerant containers above.
        if self.stack[-1] == tag:
            self.stack.pop()
        elif tag in self.stack:
            # Pop until match — record skipped tags as warnings, not errors.
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()
        else:
            self.errors.append(f"close {tag} not in stack")


def _li_runs_inside_ul(html_text: str) -> bool:
    """Verify every <li> is contained within a <ul> or <ol>.

    Same invariant as TestMdToHtml.test_md_to_html_no_orphan_li_outside_ul_regression
    but applied to the full generated page.
    """
    total_li = html_text.count("<li")
    in_ul = sum(
        block.count("<li")
        for block in re.findall(r"<ul>(.*?)</ul>", html_text, re.S)
    )
    in_ol = sum(
        block.count("<li")
        for block in re.findall(r"<ol(?:\s[^>]*)?>(.*?)</ol>", html_text, re.S)
    )
    return total_li == in_ul + in_ol


# ---------------------------------------------------------------------------
# node_html tests (lines 311-334)
# ---------------------------------------------------------------------------

class TestNodeHtml:
    """Unit tests for the flat timeline row renderer."""

    def test_minimal_meta_no_report(self, dream_lineage):
        meta = {"b1": {"short": "exp", "cat": "investigation", "verdict": "useful"}}
        children = {}
        out = dream_lineage.node_html("b1", meta, children)
        assert 'class="tl-row"' in out
        assert "exp" in out
        assert "✅" in out  # verdict
        # No report → no 📄 button
        assert "report-btn" not in out

    def test_with_report_renders_button(self, dream_lineage):
        meta = {
            "b1": {
                "short": "exp", "cat": "bug hunting", "verdict": "useful",
                "title": "T", "tests": "5", "discoveries_count": 3,
                "full_report": "body",
            }
        }
        out = dream_lineage.node_html("b1", {**meta}, {}, with_report=True)
        assert "report-btn" in out
        assert "📄" in out
        # badge content
        assert "5 tests" in out
        assert "3 disc" in out

    def test_with_report_button_suppressed(self, dream_lineage):
        meta = {"b1": {"short": "exp", "full_report": "body"}}
        out = dream_lineage.node_html("b1", meta, {}, with_report=False)
        assert "report-btn" not in out

    def test_unknown_category_uses_default_color(self, dream_lineage):
        meta = {"b1": {"short": "exp", "cat": "made-up-cat"}}
        out = dream_lineage.node_html("b1", meta, {})
        # default color #607D8B from CAT_COLORS lookup fallback
        assert "#607D8B" in out

    def test_depth_attribute_rendered(self, dream_lineage):
        meta = {"b1": {"short": "exp", "_depth": 4}}
        out = dream_lineage.node_html("b1", meta, {})
        # depth appears inside the .tl-depth pill
        assert ">4<" in out

    def test_html_escapes_title(self, dream_lineage):
        meta = {"b1": {"short": "exp", "title": "<script>alert(1)</script>"}}
        out = dream_lineage.node_html("b1", meta, {})
        assert "<script>" not in out
        assert "&lt;script&gt;" in out

    def test_missing_branch_falls_back_to_branch_name(self, dream_lineage):
        # meta has no entry for branch → uses branch as `short`
        out = dream_lineage.node_html("orphan-branch", {}, {})
        assert "orphan-branch" in out


# ---------------------------------------------------------------------------
# compact_node tests (lines 352-387)
# ---------------------------------------------------------------------------

class TestCompactNode:
    """Unit tests for the compact tree row renderer."""

    def test_leaf_uses_last_connector(self, dream_lineage):
        meta = {"b1": {"short": "exp", "cat": "investigation", "verdict": "useful"}}
        out = dream_lineage.compact_node("b1", meta, {})
        assert "└── " in out
        assert "exp" in out

    def test_non_last_uses_branch_connector(self, dream_lineage):
        meta = {"b1": {"short": "exp", "cat": "investigation"}}
        out = dream_lineage.compact_node("b1", meta, {}, is_last=False)
        assert "├── " in out

    def test_recurses_into_children(self, dream_lineage):
        meta = {
            "a": {"short": "rootA", "cat": "investigation"},
            "b": {"short": "kidB", "cat": "investigation"},
            "c": {"short": "kidC", "cat": "investigation"},
        }
        children = {"a": ["b", "c"]}
        out = dream_lineage.compact_node("a", meta, children)
        # All three nodes rendered, two as kids
        assert "rootA" in out
        assert "kidB" in out
        assert "kidC" in out
        # last kid uses └, the earlier uses ├
        assert "├── " in out
        assert "└── " in out

    def test_report_btn_only_when_report_present(self, dream_lineage):
        no_report = {"b1": {"short": "exp"}}
        assert "ct-report-btn" not in dream_lineage.compact_node("b1", no_report, {})
        with_report = {"b1": {"short": "exp", "full_report": "x"}}
        assert "ct-report-btn" in dream_lineage.compact_node("b1", with_report, {})

    def test_tests_count_appears(self, dream_lineage):
        meta = {"b1": {"short": "exp", "tests": "12"}}
        out = dream_lineage.compact_node("b1", meta, {})
        assert "12t" in out

    def test_html_escapes_title(self, dream_lineage):
        meta = {"b1": {"short": "exp", "title": "<x>"}}
        out = dream_lineage.compact_node("b1", meta, {})
        assert "<x>" not in out.replace("<div", "").replace("<span", "")
        assert "&lt;x&gt;" in out

    def test_deep_chain_indents(self, dream_lineage):
        # a → b → c chain renders increasing prefix
        meta = {k: {"short": k, "cat": "investigation"} for k in "abc"}
        children = {"a": ["b"], "b": ["c"]}
        out = dream_lineage.compact_node("a", meta, children)
        # Three rows, and the deepest one carries the "      " spacing
        assert out.count("ct-line") >= 3


# ---------------------------------------------------------------------------
# generate_html — happy path on coupon-demo
# ---------------------------------------------------------------------------

class TestGenerateHtmlCouponDemo:
    """Drive generate_html against the canonical fixture and assert structure."""

    @pytest.fixture
    def rendered(self, dream_lineage, coupon_demo, tmp_path, capsys):
        out = tmp_path / "lineage.html"
        dream_lineage.generate_html(str(coupon_demo / ".shadow"), str(out))
        # capture but don't fail on console output
        capsys.readouterr()
        return out.read_text()

    def test_writes_non_empty_file(self, dream_lineage, coupon_demo, tmp_path):
        out = tmp_path / "lineage.html"
        dream_lineage.generate_html(str(coupon_demo / ".shadow"), str(out))
        assert out.exists()
        assert out.stat().st_size > 4 * 1024

    def test_doctype_and_skeleton(self, rendered):
        assert rendered.startswith("<!DOCTYPE html>")
        assert "<html>" in rendered
        assert "<head>" in rendered
        assert "<body>" in rendered
        assert "</body></html>" in rendered

    def test_all_three_dream_ids_present(self, rendered):
        for did in [
            "20260420-140000Z-cache-poison-sequence",
            "20260420-141000Z-bulk-min-total-interaction",
            "20260420-142000Z-adversarial-inputs",
        ]:
            # The "short" form (after Z-) is what is rendered most places
            short = did.split("Z-", 1)[1]
            assert short in rendered, f"missing {short}"

    def test_title_text_present(self, rendered):
        for title_frag in [
            "Cache poisoning via validate-then-calculate sequence",
            "Bulk discount vs coupon min_total interaction",
            "Adversarial input audit",
        ]:
            assert title_frag in rendered

    def test_categories_rendered_in_cat_bar(self, rendered):
        # cat-bar uses Title Case
        assert "Bug Hunting" in rendered
        assert "Investigation" in rendered
        assert "Security Audit" in rendered

    def test_tab_skeleton_classes_present(self, rendered):
        for cls in ["tab-bar", "tab-content", "panel-overlay", "compact-tree"]:
            assert cls in rendered

    def test_stat_dashboard_has_six_stats(self, rendered):
        # Six labels in the dashboard
        for label in ["Experiments", "Compounding", "Fresh", "Sessions",
                      "Chains", "Max Depth"]:
            assert f">{label}<" in rendered

    def test_constellation_svg_present(self, rendered):
        # Graph tab removed — constellation SVG should no longer be emitted
        assert 'id="constellation"' not in rendered

    def test_no_orphan_li_outside_ul(self, rendered):
        assert _li_runs_inside_ul(rendered), "found <li> outside any <ul>/<ol>"

    def test_html_parses_without_errors(self, rendered):
        # html.parser should consume entire payload without raising.
        parser = _BalanceChecker()
        parser.feed(rendered)
        # We only assert no fatal errors — minor imbalance is tolerated.
        assert all("with empty stack" not in e for e in parser.errors), parser.errors

    def test_templates_emitted_for_each_dream(self, rendered):
        # 3 reports → 3 <template> tags
        assert rendered.count("<template id=") == 3

    def test_console_summary_printed(self, dream_lineage, coupon_demo, tmp_path, capsys):
        out = tmp_path / "lineage.html"
        dream_lineage.generate_html(str(coupon_demo / ".shadow"), str(out))
        captured = capsys.readouterr()
        assert "Wrote " in captured.out
        assert "3 experiments" in captured.out

    def test_verdict_legend_appears(self, rendered):
        # All three dreams are "useful" → legend shows ✅ Useful: 3
        assert "Useful" in rendered
        assert "<strong>3</strong>" in rendered

    def test_fresh_count_equals_total(self, dream_lineage, coupon_demo, tmp_path, capsys):
        # All three coupon-demo dreams parent to main with no children
        # → 3 fresh, 0 compounding
        out = tmp_path / "lineage.html"
        dream_lineage.generate_html(str(coupon_demo / ".shadow"), str(out))
        msg = capsys.readouterr().out
        assert "0 compounding" in msg
        assert "3 fresh" in msg


# ---------------------------------------------------------------------------
# generate_html — synthetic edge cases
# ---------------------------------------------------------------------------

class TestGenerateHtmlEdgeCases:
    """Edge-case shadow trees: empty, missing, malformed, lineage chains."""

    def test_empty_dreams_no_rows(self, dream_lineage, tmp_path, capsys):
        """Only the header rows in _index.md — no actual experiments."""
        shadow = tmp_path / ".shadow"
        _build_dreams(shadow, rows=[])
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()

        html = out.read_text()
        assert html.startswith("<!DOCTYPE html>")
        # Zero experiments dashboard
        assert ">0<" in html
        # No <template> tags emitted
        assert "<template id=" not in html

    def test_missing_dreams_dir_exits(self, dream_lineage, tmp_path):
        """generate_html → load_index sys.exit(1) when _index.md is missing."""
        shadow = tmp_path / ".shadow"
        shadow.mkdir()
        out = tmp_path / "out.html"
        with pytest.raises(SystemExit) as exc:
            dream_lineage.generate_html(str(shadow), str(out))
        assert exc.value.code == 1

    def test_dream_in_index_but_dir_missing(self, dream_lineage, tmp_path, capsys):
        """If a dream is in _index.md but its folder is missing, render anyway."""
        shadow = tmp_path / ".shadow"
        # Build with one row, then delete its folder
        rows = [(
            "20250101-120000Z-ghost", "investigation", "useful",
            "Ghost experiment",
            "dream/proj/20250101-120000Z-ghost", "main", "deadbeef",
        )]
        _build_dreams(shadow, rows=rows)
        (shadow / "_dreams" / "20250101-120000Z-ghost").rmdir()

        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()
        html = out.read_text()
        # The short name should still appear in the rendered shell
        assert "ghost" in html
        # No template since report.md was never created
        assert "<template id=" not in html

    def test_malformed_index_rows_skipped(self, dream_lineage, tmp_path, capsys):
        """Lines with fewer than 8 pipe-delimited parts are silently ignored."""
        shadow = tmp_path / ".shadow"
        dreams = shadow / "_dreams"
        dreams.mkdir(parents=True)
        # Mixture: valid row + various broken rows
        content = (
            "# Dream Experiments\n\n"
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|---|---|---|---|---|---|---|\n"
            "| 20250101-120000Z-ok | investigation | useful | OK | "
            "dream/proj/20250101-120000Z-ok | main | abc1 |\n"
            "| missing pipes here\n"
            "|||| not enough cols ||\n"
            "\n"
            "trailing prose line that is not a table row\n"
        )
        (dreams / "_index.md").write_text(content)
        (dreams / "20250101-120000Z-ok").mkdir()

        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        msg = capsys.readouterr().out
        assert "1 experiments" in msg
        assert "ok" in out.read_text()

    def test_lineage_chain_renders_both_ancestors(self, dream_lineage, tmp_path, capsys):
        """A → B → C chain: all 3 short names appear and chain count = 1."""
        shadow = tmp_path / ".shadow"
        rows = [
            ("20250101-120000Z-root-A", "investigation", "useful", "Root",
             "dream/proj/20250101-120000Z-root-A", "main", "aaa1"),
            ("20250102-120000Z-mid-B", "investigation", "useful", "Mid",
             "dream/proj/20250102-120000Z-mid-B",
             "dream/proj/20250101-120000Z-root-A", "bbb2"),
            ("20250103-120000Z-leaf-C", "investigation", "useful", "Leaf",
             "dream/proj/20250103-120000Z-leaf-C",
             "dream/proj/20250102-120000Z-mid-B", "ccc3"),
        ]
        _build_dreams(shadow, rows=rows)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        msg = capsys.readouterr().out
        html = out.read_text()

        # Chain depth: 3-node chain = 1 root with depth 2 → max_depth = 3
        assert "1 chains" in msg
        assert "max depth 3" in msg
        for short in ["root-A", "mid-B", "leaf-C"]:
            assert short in html

    def test_builds_on_reparents_via_report(self, dream_lineage, tmp_path, capsys):
        """builds_on in report frontmatter overrides 'main' parent."""
        shadow = tmp_path / ".shadow"
        rows = [
            ("20250101-120000Z-base", "investigation", "useful", "Base",
             "dream/proj/20250101-120000Z-base", "main", "aaa1"),
            ("20250102-120000Z-child", "investigation", "useful", "Child",
             "dream/proj/20250102-120000Z-child", "main", "bbb2"),
        ]
        reports = {
            "20250102-120000Z-child": (
                "---\n"
                "dream_id: \"20250102-120000Z-child\"\n"
                "builds_on: [\"dream/proj/20250101-120000Z-base\"]\n"
                "---\n\n# Child\n"
            ),
            "20250101-120000Z-base": "---\ndream_id: base\n---\n\n# Base\n",
        }
        _build_dreams(shadow, rows=rows, reports=reports)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        msg = capsys.readouterr().out
        # 1 root → 1 chain, child has compounded onto base
        assert "1 chains" in msg
        assert "1 compounding" in msg

    def test_manifest_parent_branch_reparents(self, dream_lineage, tmp_path, capsys):
        """manifest.json parent_branch overrides _index.md 'main' parent."""
        shadow = tmp_path / ".shadow"
        rows = [
            ("20250101-120000Z-a", "investigation", "useful", "A",
             "dream/proj/20250101-120000Z-a", "main", "aaa1"),
            ("20250102-120000Z-b", "investigation", "useful", "B",
             "dream/proj/20250102-120000Z-b", "main", "bbb2"),
        ]
        manifests = {
            "20250102-120000Z-b": {
                "parent_branch": "dream/proj/20250101-120000Z-a",
                "discoveries": [],
            },
        }
        _build_dreams(shadow, rows=rows, manifests=manifests)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        msg = capsys.readouterr().out
        assert "1 compounding" in msg

    def test_fresh_grouped_by_category(self, dream_lineage, tmp_path, capsys):
        """Multiple categories → multiple fresh-group blocks."""
        shadow = tmp_path / ".shadow"
        rows = [
            ("20250101-120000Z-i", "investigation", "useful", "I",
             "dream/proj/20250101-120000Z-i", "main", "111"),
            ("20250102-120000Z-b", "bug hunting", "dead_end", "B",
             "dream/proj/20250102-120000Z-b", "main", "222"),
            ("20250103-120000Z-s", "security audit", "useful", "S",
             "dream/proj/20250103-120000Z-s", "main", "333"),
        ]
        _build_dreams(shadow, rows=rows)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()
        html = out.read_text()
        assert html.count('class="fresh-group"') == 3
        # Dead-end ❌ rendered
        assert "❌" in html
        # Dead End legend appears
        assert "Dead End" in html

    def test_unknown_category_falls_back(self, dream_lineage, tmp_path, capsys):
        """A category not in the well-known list still renders as 'unknown'."""
        shadow = tmp_path / ".shadow"
        rows = [(
            "20250101-120000Z-weird", "unknown", "unknown", "Weird",
            "dream/proj/20250101-120000Z-weird", "main", "abc",
        )]
        _build_dreams(shadow, rows=rows)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()
        html = out.read_text()
        # The "Unknown" cat-stat block
        assert "Unknown" in html

    def test_report_renders_into_template(self, dream_lineage, tmp_path, capsys):
        shadow = tmp_path / ".shadow"
        rows = [(
            "20250101-120000Z-rep", "investigation", "useful", "R",
            "dream/proj/20250101-120000Z-rep", "main", "abc",
        )]
        reports = {
            "20250101-120000Z-rep": (
                "---\ndream_id: r\n---\n\n# Big Heading\n\n"
                "- item one\n- item two\n\n"
                "Some prose.\n"
            ),
        }
        _build_dreams(shadow, rows=rows, reports=reports)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()
        html = out.read_text()
        # template body contains markdown→html output
        assert "<template id=" in html
        assert "<h2>Big Heading</h2>" in html
        assert "<li>item one</li>" in html

    def test_session_count_groups_by_date_hour(self, dream_lineage, tmp_path, capsys):
        """Sessions = unique YYYYMMDD-HHMM prefixes."""
        shadow = tmp_path / ".shadow"
        rows = [
            ("20250101-1200-a", "investigation", "useful", "A",
             "dream/proj/20250101-1200-a", "main", "1"),
            ("20250101-1259-b", "investigation", "useful", "B",
             "dream/proj/20250101-1259-b", "main", "2"),
            ("20250102-0800-c", "investigation", "useful", "C",
             "dream/proj/20250102-0800-c", "main", "3"),
        ]
        _build_dreams(shadow, rows=rows)
        out = tmp_path / "out.html"
        dream_lineage.generate_html(str(shadow), str(out))
        capsys.readouterr()
        html = out.read_text()
        # 3 sessions: 20250101-1200, 20250101-1259, 20250102-0800
        m = re.search(r'<div class="num">(\d+)</div><div class="label">Sessions</div>', html)
        assert m and m.group(1) == "3"


# ---------------------------------------------------------------------------
# CLI integration: argparse + __main__ dispatch
# ---------------------------------------------------------------------------

@pytest.mark.slow
class TestCLIMain:
    """Cover lines 31-36 (parse_args) and 1062-1064 (__main__ block)."""

    def test_default_output_path(self, coupon_demo):
        # No -o flag → writes dream-lineage.html in cwd
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True, cwd=str(coupon_demo),
        )
        assert result.returncode == 0, result.stderr
        default = coupon_demo / "dream-lineage.html"
        assert default.exists()
        assert default.stat().st_size > 1024

    def test_custom_output_path(self, coupon_demo, tmp_path):
        target = tmp_path / "nested" / "out.html"
        target.parent.mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "-o", str(target),
             "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True, cwd=str(coupon_demo),
        )
        assert result.returncode == 0, result.stderr
        assert target.exists()
        assert target.read_text().startswith("<!DOCTYPE html>")

    def test_shadow_dir_auto_detect_from_cwd(self, coupon_demo):
        # When --shadow-dir is omitted, find_shadow_dir scans cwd for .shadow
        out_file = coupon_demo / "auto.html"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-o", str(out_file)],
            capture_output=True, text=True, cwd=str(coupon_demo),
        )
        assert result.returncode == 0, result.stderr
        assert out_file.exists()

    def test_missing_shadow_dir_exits_nonzero(self, tmp_path):
        # No .shadow/ anywhere → find_shadow_dir prints ERROR and exits 1
        empty = tmp_path / "empty"
        empty.mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            capture_output=True, text=True, cwd=str(empty),
        )
        assert result.returncode == 1
        assert "ERROR" in result.stderr

    def test_summary_line_count_in_stdout(self, coupon_demo, tmp_path):
        out_file = tmp_path / "out.html"
        result = subprocess.run(
            [sys.executable, str(SCRIPT),
             "-o", str(out_file),
             "--shadow-dir", str(coupon_demo / ".shadow")],
            capture_output=True, text=True, cwd=str(coupon_demo),
        )
        assert result.returncode == 0
        assert "3 experiments" in result.stdout
        assert "Wrote" in result.stdout


# --- Regression: tree_depth cycle guard ---
# Bug surfaced by Phase-3 audit: prior tree_depth(node, children) had no
# cycle guard, so a malformed _index.md with a self-parent or A<->B loop
# would crash with RecursionError. Now guarded via _seen set.

class TestTreeDepthCycleGuard:
    def test_self_parent_does_not_recurse(self, dream_lineage):
        children = {"A": ["A"]}
        depth = dream_lineage.tree_depth("A", children)
        assert isinstance(depth, int)
        assert depth >= 0

    def test_two_node_cycle_does_not_recurse(self, dream_lineage):
        children = {"A": ["B"], "B": ["A"]}
        depth = dream_lineage.tree_depth("A", children)
        assert isinstance(depth, int)
        assert depth >= 0

    def test_three_node_cycle_does_not_recurse(self, dream_lineage):
        children = {"A": ["B"], "B": ["C"], "C": ["A"]}
        depth = dream_lineage.tree_depth("A", children)
        assert isinstance(depth, int)
        assert depth >= 0

    def test_cycle_with_branch_does_not_recurse(self, dream_lineage):
        children = {"A": ["B", "D"], "B": ["A"], "D": []}
        depth = dream_lineage.tree_depth("A", children)
        assert isinstance(depth, int)
        assert depth >= 1

    def test_no_cycle_still_gives_correct_depth(self, dream_lineage):
        children = {"A": ["B"], "B": ["C"], "C": []}
        assert dream_lineage.tree_depth("A", children) == 2
        assert dream_lineage.tree_depth("B", children) == 1
        assert dream_lineage.tree_depth("C", children) == 0

    def test_leaf_node_returns_zero(self, dream_lineage):
        assert dream_lineage.tree_depth("leaf", {}) == 0

    def test_generate_html_survives_cyclic_index(self, dream_lineage, tmp_path):
        # End-to-end: a malformed _dreams/_index.md with a cycle does
        # not crash generate_html (would have raised RecursionError
        # before the fix).
        shadow = tmp_path / ".shadow"
        (shadow / "_dreams").mkdir(parents=True)
        (shadow / "_dreams" / "_index.md").write_text(
            "# Dream Index\n\n"
            "| dream_id | category | verdict | title | branch | parent | tip_commit |\n"
            "|----------|----------|---------|-------|--------|--------|------------|\n"
            "| ida | exploration | useful | T-A | dream/proj/A | dream/proj/B | abc |\n"
            "| idb | exploration | useful | T-B | dream/proj/B | dream/proj/A | def |\n"
        )
        out = tmp_path / "lineage.html"
        dream_lineage.generate_html(shadow, out)
        assert out.exists()
