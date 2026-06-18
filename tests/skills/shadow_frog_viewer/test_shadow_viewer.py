r"""Tests for `skills/shadow-frog-viewer/shadow-viewer.py`.

Philosophy: USE REAL FILES (per `minimal-mocking-tests`). The viewer is a
pure-read tool — every test either constructs a small shadow tree on
disk and calls a viewer function, or exercises the CLI via subprocess
against the `coupon_demo` fixture.

Test categories:
  * In-process function tests (no `@pytest.mark.slow`): exercise
    parsing helpers directly via the `shadow_viewer` fixture.
  * CLI integration tests (`@pytest.mark.slow @pytest.mark.integration`):
    invoke shadow-viewer.py as a subprocess against `coupon_demo`.

B3 regression: a discovery whose continuation lines include
``Dream report: `_dreams/<slug>/` `` must extract the slug path into
`meta["dream_report"]` and must NOT include "Dream report" or the slug
in the discovery body text.
"""
import json
import os
import re
import subprocess
import sys
import textwrap
from datetime import datetime

import pytest


# --- Helpers ---------------------------------------------------------------


def _write_shadow(shadow_dir, rel_path, content):
    """Write `content` to <shadow_dir>/<rel_path>, creating parents."""
    p = shadow_dir / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content), encoding="utf-8")
    return p


def _make_shadow_root(tmp_path):
    """Create an empty .shadow/ dir under tmp_path and return it."""
    sd = tmp_path / ".shadow"
    sd.mkdir()
    return sd


def _run_viewer(repo_root, cwd, *args):
    """Run shadow-viewer.py as a subprocess from `cwd`."""
    script = repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"
    return subprocess.run(
        [sys.executable, str(script), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )


# --- parse_discovery -------------------------------------------------------


def test_parse_discovery_standard(shadow_viewer):
    """Basic verified/exploration discovery, no labels."""
    line = "- Caches None for invalid codes"
    cont = ["  _(verified, source: exploration)_"]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["text"] == "Caches None for invalid codes"
    assert d["status"] == "verified"
    assert d["source"] == "exploration"
    assert "labels" not in d
    assert "dream_report" not in d


def test_parse_discovery_with_labels(shadow_viewer):
    """Labels are parsed into a list, trimmed, lowercase comma-split."""
    line = "- Foo"
    cont = ["  _(verified, source: user, labels: [bug, security])_"]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["text"] == "Foo"
    assert d["status"] == "verified"
    assert d["source"] == "user"
    assert d["labels"] == ["bug", "security"]


@pytest.mark.parametrize("status", ["verified", "uncertain", "refuted"])
def test_parse_discovery_status_variants(shadow_viewer, status):
    line = "- Some discovery"
    cont = [f"  _({status}, source: exploration)_"]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["status"] == status
    assert d["source"] == "exploration"


@pytest.mark.parametrize("source", ["exploration", "user", "interaction"])
def test_parse_discovery_source_variants(shadow_viewer, source):
    line = "- Some discovery"
    cont = [f"  _(verified, source: {source})_"]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["source"] == source


def test_parse_discovery_also_involves(shadow_viewer):
    """`Also involves:` populates a list of file::symbol anchors."""
    line = "- A multi-symbol discovery"
    cont = [
        "  _(verified, source: exploration)_",
        "  Also involves: `inventory.py::validate_coupon`, `cart.py::COUPON_CACHE`",
    ]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["text"] == "A multi-symbol discovery"
    assert d["also_involves"] == [
        "inventory.py::validate_coupon",
        "cart.py::COUPON_CACHE",
    ]
    # also_involves line must not leak into the body text
    assert "Also involves" not in d["text"]


def test_parse_discovery_b3_dream_report_regression(shadow_viewer):
    """B3 regression: Dream report goes into meta['dream_report'] and is
    excluded from the body text."""
    line = "- Case-variant lookups create duplicate cache entries"
    cont = [
        "  _(verified, source: exploration, labels: [bug, performance])_",
        "  Dream report: `_dreams/20260420-140000Z-cache-poison-sequence/`",
    ]
    d = shadow_viewer.parse_discovery(line, cont)
    # Body text is preserved, with no Dream report leakage
    assert d["text"] == "Case-variant lookups create duplicate cache entries"
    assert "Dream report" not in d["text"]
    assert "_dreams/" not in d["text"]
    # meta["dream_report"] captures the backtick payload (slug folder path)
    assert d["dream_report"] == (
        "_dreams/20260420-140000Z-cache-poison-sequence/"
    )


def test_parse_discovery_b3_dream_report_with_also_involves(shadow_viewer):
    """Dream report + Also involves on the same discovery — both extracted,
    neither leaks into body text."""
    line = "- Discovery with both extras"
    cont = [
        "  _(verified, source: exploration, labels: [security])_",
        "  Dream report: `_dreams/20260420-142000Z-adversarial-inputs/`",
        "  Also involves: `cart.py::get_coupon`, `cart.py::COUPON_CACHE`",
    ]
    d = shadow_viewer.parse_discovery(line, cont)
    assert d["text"] == "Discovery with both extras"
    assert "Dream report" not in d["text"]
    assert "Also involves" not in d["text"]
    assert d["dream_report"] == (
        "_dreams/20260420-142000Z-adversarial-inputs/"
    )
    assert d["also_involves"] == [
        "cart.py::get_coupon",
        "cart.py::COUPON_CACHE",
    ]


def test_parse_discovery_multiline_body(shadow_viewer):
    """Lines that are neither metadata nor structured extras are appended to
    the body text."""
    line = "- Lead sentence."
    cont = [
        "  continuation prose",
        "  _(verified, source: exploration)_",
    ]
    d = shadow_viewer.parse_discovery(line, cont)
    assert "Lead sentence." in d["text"]
    assert "continuation prose" in d["text"]
    assert d["status"] == "verified"


def test_parse_discovery_no_metadata(shadow_viewer):
    """Bullet with no metadata blob still returns a dict with text but no
    status/source keys."""
    d = shadow_viewer.parse_discovery("- bare bullet", [])
    assert d["text"] == "bare bullet"
    assert "status" not in d
    assert "source" not in d


def test_parse_discovery_preferences_source_only(shadow_viewer):
    """Preferences use `_(source: user)_` (no status). Extracts source."""
    d = shadow_viewer.parse_discovery(
        "- Prefer X over Y", ["  _(source: user)_"]
    )
    assert d["text"] == "Prefer X over Y"
    assert d["source"] == "user"
    assert "status" not in d


def test_parse_discovery_none_input_does_not_crash(shadow_viewer):
    """Passing a non-string line shouldn't raise — should return a dict."""
    d = shadow_viewer.parse_discovery(None, None)
    assert isinstance(d, dict)
    assert "text" in d


# --- parse_shadow_file -----------------------------------------------------


def test_parse_shadow_file_placeholder(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    f = _write_shadow(sd, "foo.py.md", """\
        # Shadow: foo.py

        **Language**: Python | **Lines**: 10

        _No discoveries yet._
    """)
    res = shadow_viewer.parse_shadow_file(f)
    assert res["source_file"] == "foo.py"
    assert res["language"] == "Python"
    assert res["lines"] == 10
    assert res["symbols"] == []
    assert res["discoveries"] == []
    assert res["cross_references"] == []
    assert res["parse_errors"] == []


def test_parse_shadow_file_one_symbol_one_discovery(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    f = _write_shadow(sd, "auth.py.md", """\
        # Shadow: auth.py

        **Language**: Python | **Lines**: 42

        ## `authenticate`

        - Returns None on expired tokens, silently.
          _(verified, source: exploration, labels: [security])_
    """)
    res = shadow_viewer.parse_shadow_file(f)
    assert res["source_file"] == "auth.py"
    assert res["symbols"] == ["authenticate"]
    assert len(res["discoveries"]) == 1
    d = res["discoveries"][0]
    assert d["symbol"] == "authenticate"
    assert d["file"] == "auth.py"
    assert d["status"] == "verified"
    assert d["source"] == "exploration"
    assert d["labels"] == ["security"]
    assert "Returns None" in d["text"]


def test_parse_shadow_file_cross_references_backpointers(
    shadow_viewer, tmp_path
):
    sd = _make_shadow_root(tmp_path)
    f = _write_shadow(sd, "bar.py.md", """\
        # Shadow: bar.py

        ## `func`

        - A discovery.
          _(verified, source: exploration)_

        ## Cross-References

        - [my-cross-cutting](_cross/my-cross-cutting.md)
          (involves `bar.py::func`)
        - [another-one](_cross/another-one.md)
    """)
    res = shadow_viewer.parse_shadow_file(f)
    assert res["symbols"] == ["func"]
    # Cross-references back-pointer link labels are collected
    assert "my-cross-cutting" in res["cross_references"]
    assert "another-one" in res["cross_references"]
    # Cross-ref bullets are NOT mistaken for discoveries
    assert len(res["discoveries"]) == 1


def test_parse_shadow_file_file_level_and_cross_refs(shadow_viewer, tmp_path):
    """`## File-Level` discoveries are tagged with symbol='file-level' and
    `## Cross-References` bullets are not treated as discoveries."""
    sd = _make_shadow_root(tmp_path)
    f = _write_shadow(sd, "mix.py.md", """\
        # Shadow: mix.py

        ## File-Level

        - A module-wide observation.
          _(verified, source: exploration)_

        ## `helper`

        - A symbol discovery.
          _(verified, source: user)_

        ## Cross-References

        - [shared](_cross/shared.md)
    """)
    res = shadow_viewer.parse_shadow_file(f)
    discs = res["discoveries"]
    assert len(discs) == 2
    by_sym = {d["symbol"]: d for d in discs}
    assert "file-level" in by_sym
    assert "helper" in by_sym
    assert by_sym["file-level"]["source"] == "exploration"
    assert by_sym["helper"]["source"] == "user"
    assert res["cross_references"] == ["shared"]


def test_parse_shadow_file_malformed_does_not_crash(shadow_viewer, tmp_path):
    """Bizarre / structurally broken content shouldn't raise."""
    sd = _make_shadow_root(tmp_path)
    f = _write_shadow(sd, "junk.py.md", """\
        # Shadow: junk.py
        **Language**: notnumeric | **Lines**: notanumber

        ## not a backtick heading
        - orphan bullet with no metadata
        ## `realsym`
        - real disc
          _(verified, source: exploration)_
    """)
    res = shadow_viewer.parse_shadow_file(f)
    # Parse succeeds despite weirdness
    assert res["source_file"] == "junk.py"
    # The bad "Lines" cell stays None (int parse skipped)
    assert res["lines"] is None
    # `realsym` is captured; the non-backtick heading is not a symbol
    assert "realsym" in res["symbols"]
    assert "not a backtick heading" not in res["symbols"]


def test_parse_shadow_file_missing_file_returns_error(
    shadow_viewer, tmp_path
):
    """Reading a non-existent path records a parse_error, doesn't raise."""
    res = shadow_viewer.parse_shadow_file(tmp_path / "ghost.md")
    assert res["parse_errors"]
    assert res["symbols"] == []
    assert res["discoveries"] == []


# --- parse_cross_cutting ---------------------------------------------------


def test_parse_cross_cutting_empty_dir(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    (sd / "_cross").mkdir()
    assert shadow_viewer.parse_cross_cutting(sd) == []


def test_parse_cross_cutting_no_dir(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    # _cross/ not created
    assert shadow_viewer.parse_cross_cutting(sd) == []


def test_parse_cross_cutting_one_entry(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_cross/example-pattern.md", """\
        # Example pattern

        **Category**: pattern
        **Refs**:
        - `cart.py::calculate_total`
        - `inventory.py::validate_coupon`

        **Discovery**: A multi-file pattern observed across the codebase.

        _(verified, source: exploration, labels: [bug])_
    """)
    entries = shadow_viewer.parse_cross_cutting(sd)
    assert len(entries) == 1
    e = entries[0]
    assert e["slug"] == "example-pattern"
    assert e["title"] == "Example pattern"
    assert e["category"] == "pattern"
    assert "cart.py::calculate_total" in e["refs"]
    assert "inventory.py::validate_coupon" in e["refs"]
    assert "multi-file pattern" in e["discovery"]
    assert e["status"] == "verified"
    assert e["source"] == "exploration"
    assert e["labels"] == ["bug"]


def test_parse_cross_cutting_no_labels(shadow_viewer, tmp_path):
    """A cross-cutting entry without labels is still parsed, with no
    `labels` key in the entry."""
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_cross/no-label.md", """\
        # Plain entry

        **Category**: behavior
        **Refs**:
        - `foo.py::bar`

        **Discovery**: Something happens.

        _(uncertain, source: exploration)_
    """)
    entries = shadow_viewer.parse_cross_cutting(sd)
    assert len(entries) == 1
    e = entries[0]
    assert e["status"] == "uncertain"
    assert e["source"] == "exploration"
    assert "labels" not in e


def test_parse_cross_cutting_minor_format_variation(shadow_viewer, tmp_path):
    """File missing a `**Category**:` field doesn't crash; entry is still
    emitted with whatever fields could be parsed."""
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_cross/sparse.md", """\
        # Sparse entry

        Some prose with no structured fields.

        _(refuted, source: user)_
    """)
    entries = shadow_viewer.parse_cross_cutting(sd)
    assert len(entries) == 1
    e = entries[0]
    assert e["slug"] == "sparse"
    assert e["title"] == "Sparse entry"
    assert e["status"] == "refuted"
    assert e["source"] == "user"


# --- parse_prefs -----------------------------------------------------------


def test_parse_prefs_missing_file(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    assert shadow_viewer.parse_prefs(sd) == []


def test_parse_prefs_zero(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_prefs.md", """\
        # Preferences

        _No preferences recorded yet._
    """)
    assert shadow_viewer.parse_prefs(sd) == []


def test_parse_prefs_one(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_prefs.md", """\
        # Preferences

        - Always use type hints on public APIs.
          _(source: user)_
    """)
    prefs = shadow_viewer.parse_prefs(sd)
    assert len(prefs) == 1
    assert prefs[0]["text"] == "Always use type hints on public APIs."
    assert prefs[0]["source"] == "user"
    assert prefs[0]["type"] == "preference"


def test_parse_prefs_three(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    _write_shadow(sd, "_prefs.md", """\
        # Preferences

        - Use kebab-case for slugs.
          _(source: user)_
        - Never commit secrets to source control.
          _(source: interaction)_
        - Prefer fail-fast for required dependencies.
          _(source: user)_
    """)
    prefs = shadow_viewer.parse_prefs(sd)
    assert len(prefs) == 3
    texts = [p["text"] for p in prefs]
    assert any("kebab-case" in t for t in texts)
    assert any("secrets" in t for t in texts)
    assert any("fail-fast" in t for t in texts)
    sources = [p["source"] for p in prefs]
    assert "user" in sources
    assert "interaction" in sources


# --- load_state ------------------------------------------------------------


def test_load_state_missing(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    assert shadow_viewer.load_state(sd) == {}


def test_load_state_valid_json(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    (sd / "_meta").mkdir()
    payload = {
        "version": 1,
        "total_files": 5,
        "total_discoveries": 42,
        "last_update_type": "auto",
    }
    (sd / "_meta" / "state.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    state = shadow_viewer.load_state(sd)
    assert state == payload


def test_load_state_malformed_json(shadow_viewer, tmp_path):
    sd = _make_shadow_root(tmp_path)
    (sd / "_meta").mkdir()
    (sd / "_meta" / "state.json").write_text(
        "{not valid json", encoding="utf-8"
    )
    state = shadow_viewer.load_state(sd)
    assert state == {}


def test_load_state_non_object_json(shadow_viewer, tmp_path):
    """JSON parses but is not a dict — returns empty dict sentinel."""
    sd = _make_shadow_root(tmp_path)
    (sd / "_meta").mkdir()
    (sd / "_meta" / "state.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert shadow_viewer.load_state(sd) == {}


# --- get_all_shadow_files --------------------------------------------------


def test_get_all_shadow_files_excludes_special(shadow_viewer, tmp_path):
    """Per-file shadows are returned; _cross/, _dreams/, _meta/, _index.md,
    _prefs.md, state.json are all excluded."""
    sd = _make_shadow_root(tmp_path)
    # Files that SHOULD be returned
    _write_shadow(sd, "a.py.md", "# Shadow: a.py\n")
    _write_shadow(sd, "src/b.py.md", "# Shadow: src/b.py\n")
    _write_shadow(sd, "deep/nested/c.py.md", "# Shadow: deep/nested/c.py\n")
    # Files that should be EXCLUDED
    _write_shadow(sd, "_index.md", "# Shadow Index\n")
    _write_shadow(sd, "_prefs.md", "# Preferences\n")
    _write_shadow(sd, "_cross/some.md", "# Some cross\n")
    _write_shadow(sd, "_dreams/dream-1/report.md", "# A dream\n")
    _write_shadow(sd, "_meta/state.json", "{}")
    # Junk files that are not .md and shouldn't show up anyway
    (sd / "notes.json").write_text("{}", encoding="utf-8")

    files = shadow_viewer.get_all_shadow_files(sd)
    rels = sorted(f.relative_to(sd).as_posix() for f in files)
    assert rels == ["a.py.md", "deep/nested/c.py.md", "src/b.py.md"]


def test_get_all_shadow_files_against_coupon_demo(shadow_viewer, coupon_demo):
    sd = coupon_demo / ".shadow"
    files = shadow_viewer.get_all_shadow_files(sd)
    rels = sorted(f.relative_to(sd).as_posix() for f in files)
    assert rels == ["cart.py.md", "inventory.py.md", "test_cart.py.md"]
    # Make sure none of the special files leaked through
    for r in rels:
        assert not r.startswith(("_cross/", "_dreams/", "_meta/"))
        assert r not in ("_index.md", "_prefs.md")


# --- collect_all_discoveries (against fixture) -----------------------------


def test_collect_all_discoveries_counts(shadow_viewer, coupon_demo):
    """Coupon demo has 33 per-file discoveries (matches state.json)."""
    sd = coupon_demo / ".shadow"
    all_disc = shadow_viewer.collect_all_discoveries(sd)
    # state.json claims 33 total discoveries
    assert len(all_disc) == 33

    # File breakdown: cart=14, inventory=10, test_cart=9
    by_file = {}
    for d in all_disc:
        by_file.setdefault(d.get("file"), 0)
        by_file[d.get("file")] += 1
    assert by_file == {"cart.py": 14, "inventory.py": 10, "test_cart.py": 9}


def test_collect_all_discoveries_shadow_path_and_mtime(
    shadow_viewer, coupon_demo
):
    sd = coupon_demo / ".shadow"
    all_disc = shadow_viewer.collect_all_discoveries(sd)
    for d in all_disc:
        assert "shadow_path" in d
        assert d["shadow_path"].endswith(".md")
        assert "shadow_mtime" in d
        assert isinstance(d["shadow_mtime"], float)


def test_collect_all_discoveries_b3_no_dream_report_in_text(
    shadow_viewer, coupon_demo
):
    """B3 regression on real fixture: no discovery body should contain
    'Dream report' or the literal `_dreams/` slug path."""
    sd = coupon_demo / ".shadow"
    all_disc = shadow_viewer.collect_all_discoveries(sd)
    leaks = [
        d for d in all_disc
        if "Dream report" in d.get("text", "")
        or "_dreams/" in d.get("text", "")
    ]
    assert leaks == [], (
        f"Dream report leaked into {len(leaks)} discovery body/bodies: "
        f"{[d['text'][:80] for d in leaks]}"
    )

    # And at least some discoveries actually have dream_report metadata
    # (the fixture has several Dream report continuation lines)
    with_dream = [d for d in all_disc if d.get("dream_report")]
    assert len(with_dream) >= 3, (
        "Expected coupon-demo fixture to have multiple Dream-report-tagged "
        f"discoveries; found {len(with_dream)}"
    )
    for d in with_dream:
        assert d["dream_report"].startswith("_dreams/")


# --- CLI: --summary --------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_summary(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--summary")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Header is "Files shadowed:", "Symbols tracked:", "Discoveries:" per
    # current --summary output. Task wording used "Total files:"/"Symbols:"
    # /"Discoveries:" loosely — match the actual labels.
    assert "Files shadowed:" in out
    assert "Symbols tracked:" in out
    assert "Discoveries:" in out
    # Cross-cutting block exists
    assert "Cross-cutting" in out


@pytest.mark.slow
@pytest.mark.integration
def test_cli_default_view_is_summary(repo_root, coupon_demo):
    """Running with no flags should produce the summary view."""
    r = _run_viewer(repo_root, coupon_demo)
    assert r.returncode == 0, r.stderr
    assert "Shadow Knowledge Base Summary" in r.stdout


# --- CLI: --search ---------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_search_matches_text(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--search", "coupon")
    assert r.returncode == 0, r.stderr
    # Header echoes the query and results were found
    assert "'coupon'" in r.stdout
    # Some matched line should contain the query (case-insensitive)
    assert "coupon" in r.stdout.lower()


@pytest.mark.slow
@pytest.mark.integration
def test_cli_search_no_results(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--search", "zzznotpresentzzz")
    assert r.returncode == 0, r.stderr
    assert "No results for 'zzznotpresentzzz'" in r.stdout


# --- CLI: --top ------------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_top_cart_py_header_and_no_dream_report_leak(
    repo_root, coupon_demo
):
    """B3 regression at the CLI layer: --top output for a file whose shadow
    contains `Dream report:` continuation lines must NOT inline that text
    in any discovery body."""
    r = _run_viewer(repo_root, coupon_demo, "--top", "cart.py")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    # Header form: "Top N of M actionable discoveries for cart.py:"
    assert "actionable discoveries for cart.py:" in out
    # Match the documented prefix exactly
    assert out.splitlines()[0].startswith("Top ")
    assert "for cart.py:" in out.splitlines()[0]

    # B3: no literal "Dream report:" or raw `_dreams/...` slug paths in any
    # of the discovery bullets
    assert "Dream report:" not in out
    assert "_dreams/" not in out


@pytest.mark.slow
@pytest.mark.integration
def test_cli_top_label_filter_bug(repo_root, coupon_demo):
    r = _run_viewer(
        repo_root, coupon_demo, "--top", "cart.py", "--top-labels", "bug"
    )
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "for cart.py:" in out
    # Every bulleted result line should mention 'bug' in its label bracket
    bullet_lines = [
        l for l in out.splitlines() if l.startswith("- [")
    ]
    assert bullet_lines, f"No bullets in --top output:\n{out}"
    for line in bullet_lines:
        bracket = line.split("]", 1)[0]
        assert "bug" in bracket, (
            f"Expected 'bug' label in bracket of: {line}"
        )


@pytest.mark.slow
@pytest.mark.integration
def test_cli_top_unknown_file(repo_root, coupon_demo):
    """A file with no shadow + no cross refs reports nothing actionable."""
    r = _run_viewer(repo_root, coupon_demo, "--top", "does/not/exist.py")
    assert r.returncode == 0, r.stderr
    assert "No actionable discoveries" in r.stdout


# --- CLI: --labels (repo-wide) ---------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_labels_bug(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--labels", "bug")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "label(s): bug" in out
    # There are multiple bug-labeled discoveries in the fixture
    assert "[bug]" in out


@pytest.mark.slow
@pytest.mark.integration
def test_cli_labels_unknown(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--labels", "nonexistent")
    assert r.returncode == 0, r.stderr
    assert "No discoveries with label(s): nonexistent" in r.stdout


# --- CLI: --recent ---------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_recent_caps_at_n(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--recent", "5")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "Most Recent Discoveries (top 5)" in out
    # Each recent entry has a timestamp prefix "  [YYYY-MM-DD HH:MM]"
    entries = [l for l in out.splitlines() if l.strip().startswith("[20")]
    assert len(entries) <= 5
    # Coupon-demo has > 5 total items so we expect exactly 5
    assert len(entries) == 5


# --- CLI: --prefs ----------------------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_prefs_empty(repo_root, coupon_demo):
    """Coupon-demo ships with no preferences recorded."""
    r = _run_viewer(repo_root, coupon_demo, "--prefs")
    assert r.returncode == 0, r.stderr
    assert "No preferences recorded yet." in r.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_prefs_populated(repo_root, coupon_demo):
    """After populating _prefs.md, --prefs lists them."""
    prefs_path = coupon_demo / ".shadow" / "_prefs.md"
    prefs_path.write_text(
        textwrap.dedent("""\
            # Preferences

            - Use snake_case for Python identifiers.
              _(source: user)_
            - Avoid mutable default arguments.
              _(source: interaction)_
        """),
        encoding="utf-8",
    )
    r = _run_viewer(repo_root, coupon_demo, "--prefs")
    assert r.returncode == 0, r.stderr
    assert "Project Preferences (2 total)" in r.stdout
    assert "snake_case" in r.stdout
    assert "mutable default" in r.stdout


# --- CLI: --check-invariants -----------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_check_invariants_clean(repo_root, coupon_demo):
    r = _run_viewer(repo_root, coupon_demo, "--check-invariants")
    assert r.returncode == 0, (
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "✓ Invariants OK" in r.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_check_invariants_detects_missing_cross_file(
    repo_root, coupon_demo
):
    """Deleting a _cross/ file leaves dangling back-pointers in per-file
    shadows. Invariant #5 must flag this as a violation."""
    cross_path = (
        coupon_demo / ".shadow" / "_cross"
        / "coupon-case-normalization-mismatch.md"
    )
    assert cross_path.is_file()
    cross_path.unlink()

    r = _run_viewer(repo_root, coupon_demo, "--check-invariants")
    assert r.returncode != 0, (
        f"Expected nonzero exit when _cross/ file missing; got 0.\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    # Violations report the dangling slug
    assert "coupon-case-normalization-mismatch" in r.stdout
    assert "cross-ref" in r.stdout


@pytest.mark.slow
@pytest.mark.integration
def test_cli_check_invariants_detects_bad_heading(repo_root, coupon_demo):
    """Renaming a symbol heading to a non-backtick form is a heading-format
    violation."""
    cart_path = coupon_demo / ".shadow" / "cart.py.md"
    text = cart_path.read_text(encoding="utf-8")
    # `## `COUPON_CACHE`` -> `## COUPON_CACHE` (drop backticks)
    mutated = text.replace("## `COUPON_CACHE`", "## COUPON_CACHE", 1)
    assert mutated != text, "Substitution did not match"
    cart_path.write_text(mutated, encoding="utf-8")

    r = _run_viewer(repo_root, coupon_demo, "--check-invariants")
    assert r.returncode != 0, (
        f"Expected nonzero exit for bad heading; got 0.\n"
        f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    )
    assert "heading" in r.stdout


# --- CLI: missing shadow dir ----------------------------------------------


@pytest.mark.slow
@pytest.mark.integration
def test_cli_missing_shadow_dir_fails(repo_root, tmp_path):
    """Running with no shadow and a bogus --shadow-dir exits 1."""
    r = _run_viewer(
        repo_root, tmp_path,
        "--shadow-dir", str(tmp_path / "nope"), "--summary",
    )
    assert r.returncode == 1
    assert "No .shadow/ directory found" in r.stderr


# ===========================================================================
# RENDER FUNCTIONS: in-process tests
#
# The CLI integration tests above invoke shadow-viewer.py as a subprocess —
# that exercises the dispatcher but doesn't contribute to coverage of the
# loaded module. The tests below call view_* and main() directly via the
# `shadow_viewer` fixture so coverage actually accumulates.
# ===========================================================================


# --- shared helpers --------------------------------------------------------


def _call_main(shadow_viewer, argv):
    """Invoke `shadow_viewer.main()` in-process with the given argv.

    Returns the integer exit code. We mutate `sys.argv` directly (no
    monkeypatch / no mocking) and always restore it in a finally.
    """
    saved_argv = sys.argv
    sys.argv = ["shadow-viewer.py", *argv]
    try:
        shadow_viewer.main()
        return 0
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    finally:
        sys.argv = saved_argv


def _make_minimal_shadow(tmp_path, extras=None):
    """Build a minimal but valid .shadow/ tree in tmp_path.

    Returns the .shadow/ Path. `extras` is a dict of {relpath: content}
    appended on top of the baseline.
    """
    sd = tmp_path / ".shadow"
    sd.mkdir()
    (sd / "_meta").mkdir()
    (sd / "_meta" / "state.json").write_text(
        json.dumps({
            "version": 1,
            "last_update_at": "2026-04-20T16:30:00Z",
            "last_update_type": "manual",
            "last_commit": "deadbeef" * 5,
        }),
        encoding="utf-8",
    )
    _write_shadow(sd, "foo.py.md", """\
        # Shadow: foo.py

        **Language**: Python | **Lines**: 10

        ## `bar`

        - A neat bug.
          _(verified, source: exploration, labels: [bug])_
    """)
    for rel, content in (extras or {}).items():
        _write_shadow(sd, rel, content)
    return sd


# ===========================================================================
# view_summary
# ===========================================================================


class TestViewSummary:
    """In-process tests for `view_summary(shadow_dir)`."""

    def test_basic_header_on_coupon_demo(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Shadow Knowledge Base Summary" in out
        assert "=" * 50 in out
        assert "Files shadowed:" in out
        assert "Symbols tracked:" in out
        assert "Discoveries:" in out
        assert "Preferences:" in out
        assert "Cross-cutting:" in out

    def test_counts_reflect_fixture(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        # 3 source files, 33 discoveries, 3 cross-cutting (per fixture)
        assert "Files shadowed:    3" in out
        assert "Discoveries:       33" in out
        assert "Cross-cutting:     3" in out

    def test_by_source_section_renders(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "By source:" in out
        # All discoveries in the fixture are source: exploration
        assert "exploration" in out

    def test_by_status_section_renders(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "By status:" in out
        assert "verified" in out

    def test_by_label_section_renders(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "By label:" in out
        assert "bug" in out
        assert "security" in out

    def test_per_file_table_lists_files(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        # Per-file table header
        assert "File" in out
        assert "Symbols" in out
        assert "Disc." in out
        # All three fixture files appear in the table
        assert "cart.py" in out
        assert "inventory.py" in out
        assert "test_cart.py" in out

    def test_cross_cutting_titles_section(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Cross-cutting discoveries:" in out
        assert "Coupon case normalization mismatch" in out
        assert "[edge-case]" in out

    def test_state_info_section(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Last update:" in out
        assert "Last commit:" in out
        # The fixture state.json says last_update_type: dream
        assert "(dream)" in out

    def test_empty_shadow_dir_renders_zeros(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Files shadowed:    0" in out
        assert "Discoveries:       0" in out
        # With zero discoveries there's no source/status breakdown
        assert "By source:" not in out
        assert "By status:" not in out
        assert "By label:" not in out
        # And no state info (no state.json)
        assert "Last update:" not in out

    def test_corrupted_state_json_does_not_break_summary(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        # Clobber state.json with junk
        (sd / "_meta" / "state.json").write_text(
            "{ not json", encoding="utf-8"
        )
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        # Header and counts still rendered
        assert "Shadow Knowledge Base Summary" in out
        assert "Files shadowed:    1" in out
        # State section silently dropped
        assert "Last update:" not in out

    def test_unreadable_shadow_file_does_not_break_summary(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        # Create a file with invalid UTF-8 — parse_shadow_file records a
        # parse_error but returns a result. view_summary should still
        # render the rest of the report.
        bad = sd / "bad.py.md"
        bad.write_bytes(b"\xff\xfe\x00garbage\x00")
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Shadow Knowledge Base Summary" in out
        assert "Files shadowed:    2" in out

    def test_more_than_20_files_truncated(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        for i in range(25):
            _write_shadow(
                sd, f"file{i:02d}.py.md",
                f"# Shadow: file{i:02d}.py\n\n## `sym{i}`\n\n"
                f"- D\n  _(verified, source: exploration)_\n",
            )
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Files shadowed:    25" in out
        assert "... and 5 more files" in out

    def test_no_cross_cutting_dir_omits_section(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        # no _cross/ dir
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Cross-cutting:     0" in out
        # The titled list is suppressed when empty
        assert "Cross-cutting discoveries:" not in out

    def test_summary_no_prefs(self, shadow_viewer, tmp_path, capsys):
        sd = _make_minimal_shadow(tmp_path)
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Preferences:       0" in out

    def test_summary_with_prefs(self, shadow_viewer, tmp_path, capsys):
        sd = _make_minimal_shadow(tmp_path)
        _write_shadow(sd, "_prefs.md", """\
            # Preferences

            - Pref one.
              _(source: user)_
            - Pref two.
              _(source: interaction)_
        """)
        shadow_viewer.view_summary(sd)
        out = capsys.readouterr().out
        assert "Preferences:       2" in out


# ===========================================================================
# view_search
# ===========================================================================


class TestViewSearch:
    """In-process tests for `view_search(shadow_dir, query)`."""

    def test_finds_text_match(self, shadow_viewer, coupon_demo, capsys):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "tax")
        out = capsys.readouterr().out
        assert "Search: 'tax'" in out
        assert "results" in out
        # The "8% tax" / "Tax rate" discoveries on cart.py match
        assert "cart.py" in out

    def test_finds_symbol_match(self, shadow_viewer, coupon_demo, capsys):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "COUPON_CACHE")
        out = capsys.readouterr().out
        assert "COUPON_CACHE" in out
        assert "cart.py" in out

    def test_finds_file_name_match(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Searching for the literal file name pulls every discovery in
        # that file (file_name_hit branch).
        shadow_viewer.view_search(sd, "inventory.py")
        out = capsys.readouterr().out
        assert "inventory.py" in out
        assert "matches" in out

    def test_case_insensitive(self, shadow_viewer, coupon_demo, capsys):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "COUPON")
        upper = capsys.readouterr().out
        shadow_viewer.view_search(sd, "coupon")
        lower = capsys.readouterr().out
        # Same number of result lines either way
        assert ("results" in upper) and ("results" in lower)
        # And both contain at least one match
        assert "::" in upper
        assert "::" in lower

    def test_no_matches_prints_no_results(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "zzzdoesnotexistzzz")
        out = capsys.readouterr().out
        assert "No results for 'zzzdoesnotexistzzz'." in out

    def test_finds_cross_cutting_title(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Title of _cross/coupon-case-normalization-mismatch.md is
        # "Coupon case normalization mismatch"
        shadow_viewer.view_search(sd, "normalization mismatch")
        out = capsys.readouterr().out
        assert "Cross-cutting" in out
        assert "Coupon case normalization mismatch" in out
        assert "Category:" in out
        assert "edge-case" in out

    def test_finds_cross_cutting_by_ref(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Search for a ref that appears in a _cross file
        shadow_viewer.view_search(sd, "apply_bulk_discount")
        out = capsys.readouterr().out
        assert "Cross-cutting" in out
        assert "Mutation through discount pipeline" in out

    def test_finds_also_involves_match(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - A discovery.
              _(verified, source: exploration)_
              Also involves: `b.py::weird_symbol_zzz`
        """)
        shadow_viewer.view_search(sd, "weird_symbol_zzz")
        out = capsys.readouterr().out
        # The match flag is "also_involves" and a line shows that ref
        assert "weird_symbol_zzz" in out
        assert "Also involves:" in out

    def test_finds_preference_match(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        _write_shadow(sd, "_prefs.md", """\
            # Preferences

            - Prefer rusty pelicans for everything.
              _(source: user)_
        """)
        shadow_viewer.view_search(sd, "pelican")
        out = capsys.readouterr().out
        assert "Preferences" in out
        assert "pelican" in out.lower()
        assert "[user]" in out

    def test_groups_per_file_results_by_file(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "coupon")
        out = capsys.readouterr().out
        # Per-file groups present a header like "cart.py (N matches)"
        assert re.search(r"cart\.py \(\d+ matches\)", out)

    def test_results_show_status_and_source(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "tax")
        out = capsys.readouterr().out
        assert "(verified, source: exploration)" in out

    def test_total_count_in_header(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_search(sd, "coupon")
        out = capsys.readouterr().out
        m = re.search(r"\((\d+) results\)", out)
        assert m is not None
        assert int(m.group(1)) > 0

    def test_empty_shadow_returns_no_results(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        shadow_viewer.view_search(sd, "anything")
        out = capsys.readouterr().out
        assert "No results for 'anything'." in out


# ===========================================================================
# view_prefs
# ===========================================================================


class TestViewPrefs:
    """In-process tests for `view_prefs(shadow_dir)`."""

    def test_missing_prefs_file(self, shadow_viewer, tmp_path, capsys):
        sd = _make_shadow_root(tmp_path)
        shadow_viewer.view_prefs(sd)
        out = capsys.readouterr().out
        assert "No preferences recorded yet." in out

    def test_empty_prefs_placeholder(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_prefs.md", """\
            # Preferences

            _No preferences recorded yet._
        """)
        shadow_viewer.view_prefs(sd)
        out = capsys.readouterr().out
        assert "No preferences recorded yet." in out

    def test_populated_prefs_lists_count_and_sources(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_prefs.md", """\
            # Preferences

            - Use snake_case.
              _(source: user)_
            - Avoid mutable default args.
              _(source: interaction)_
            - Prefer fail-fast for required deps.
              _(source: user)_
        """)
        shadow_viewer.view_prefs(sd)
        out = capsys.readouterr().out
        assert "Project Preferences (3 total)" in out
        assert "[user]" in out
        assert "[interaction]" in out
        assert "snake_case" in out
        assert "fail-fast" in out
        assert "mutable default" in out

    def test_against_coupon_demo_is_empty(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_prefs(sd)
        out = capsys.readouterr().out
        assert "No preferences recorded yet." in out


# ===========================================================================
# view_labels
# ===========================================================================


class TestViewLabels:
    """In-process tests for `view_labels(shadow_dir, label_filter)`."""

    @pytest.mark.parametrize("label", [
        "bug", "security", "performance", "feature-gap", "tech-debt",
    ])
    def test_each_label_returns_results_on_fixture(
        self, shadow_viewer, coupon_demo, capsys, label
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, label)
        out = capsys.readouterr().out
        assert f"label(s): {label}" in out
        assert f"[{label}]" in out
        # Result header always has "(N results)"
        m = re.search(r"\((\d+) results\)", out)
        assert m and int(m.group(1)) >= 1

    def test_unknown_label_no_results(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, "zznotalabelzz")
        out = capsys.readouterr().out
        assert "No discoveries with label(s): zznotalabelzz" in out

    def test_multiple_labels_comma_split(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, "bug,security")
        out = capsys.readouterr().out
        assert "label(s): bug, security" in out
        # Both grouped section headers present
        assert "[bug]" in out
        assert "[security]" in out

    def test_label_filter_is_lowercased(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, "BUG")
        out = capsys.readouterr().out
        # Filter is lowercased before matching
        assert "label(s): bug" in out
        assert "[bug]" in out

    def test_cross_cutting_labels_included(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, "bug")
        out = capsys.readouterr().out
        # Cross-cutting entries are prefixed with `_cross/<file>` in the
        # file column.
        assert "_cross/" in out

    def test_also_labeled_displayed(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # One cart.py discovery has labels [bug, performance]
        shadow_viewer.view_labels(sd, "bug")
        out = capsys.readouterr().out
        assert "Also labeled:" in out

    def test_empty_shadow_no_results(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        shadow_viewer.view_labels(sd, "bug")
        out = capsys.readouterr().out
        assert "No discoveries with label(s): bug" in out

    def test_each_result_row_has_file_and_symbol(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_labels(sd, "feature-gap")
        out = capsys.readouterr().out
        # `::` separator for file::symbol form
        assert "::" in out
        assert "(verified, source: exploration)" in out


# ===========================================================================
# view_recent
# ===========================================================================


class TestViewRecent:
    """In-process tests for `view_recent(shadow_dir, count)`."""

    def test_default_count_caps_at_10(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_recent(sd, 10)
        out = capsys.readouterr().out
        assert "Most Recent Discoveries (top 10)" in out
        entries = [
            l for l in out.splitlines() if l.strip().startswith("[20")
        ]
        # Fixture has 33 per-file + 3 cross + 0 prefs > 10
        assert len(entries) == 10

    def test_custom_count(self, shadow_viewer, coupon_demo, capsys):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_recent(sd, 3)
        out = capsys.readouterr().out
        assert "Most Recent Discoveries (top 3)" in out
        entries = [
            l for l in out.splitlines() if l.strip().startswith("[20")
        ]
        assert len(entries) == 3

    def test_count_larger_than_available(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        shadow_viewer.view_recent(sd, 50)
        out = capsys.readouterr().out
        # Only 1 discovery exists in the minimal shadow
        entries = [
            l for l in out.splitlines() if l.strip().startswith("[20")
        ]
        assert len(entries) == 1

    def test_empty_shadow_prints_nothing(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        shadow_viewer.view_recent(sd, 10)
        out = capsys.readouterr().out
        assert "No discoveries found." in out

    def test_recently_modified_file_appears_first(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Bump test_cart.py's mtime to "now" so its discoveries should
        # rank first.
        target = sd / "test_cart.py.md"
        now = datetime.now().timestamp()
        os.utime(target, (now + 10, now + 10))
        shadow_viewer.view_recent(sd, 3)
        out = capsys.readouterr().out
        # First entry block should reference test_cart.py
        first_entry_idx = out.find("[20")
        first_block = out[first_entry_idx:first_entry_idx + 400]
        assert "test_cart.py" in first_block

    def test_includes_cross_cutting_type(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Bump a cross file mtime so it shows up in the top
        cf = sd / "_cross" / "coupon-case-normalization-mismatch.md"
        now = datetime.now().timestamp()
        os.utime(cf, (now + 100, now + 100))
        shadow_viewer.view_recent(sd, 5)
        out = capsys.readouterr().out
        assert "(cross-cutting)" in out

    def test_includes_preferences_when_present(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        _write_shadow(sd, "_prefs.md", """\
            # Preferences

            - A pref we care about.
              _(source: user)_
        """)
        shadow_viewer.view_recent(sd, 10)
        out = capsys.readouterr().out
        assert "(preference)" in out
        assert "A pref we care about" in out
        # Preference-typed rows show `source:` not `(verified, ...)`
        assert "source: user" in out

    def test_no_cross_dir_does_not_crash(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_minimal_shadow(tmp_path)
        # No _cross/ created
        shadow_viewer.view_recent(sd, 10)
        out = capsys.readouterr().out
        assert "Most Recent Discoveries" in out

    def test_entries_include_timestamp_and_kind(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        shadow_viewer.view_recent(sd, 2)
        out = capsys.readouterr().out
        # Entry header line format: "  [YYYY-MM-DD HH:MM] (kind)"
        assert re.search(
            r"\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] \((discovery|cross-cutting|preference)\)",
            out,
        )


# ===========================================================================
# view_check_invariants
# ===========================================================================


class TestViewCheckInvariants:
    """In-process tests for `view_check_invariants(shadow_dir)`.

    Each test builds a deliberately broken `.shadow/` tree in tmp_path
    and asserts that the right violation kind is reported.
    """

    def test_clean_coupon_demo_returns_zero(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = shadow_viewer.view_check_invariants(coupon_demo / ".shadow")
        out = capsys.readouterr().out
        assert rc == 0
        assert "✓ Invariants OK" in out

    def test_empty_shadow_returns_zero(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 0
        assert "Invariants OK" in out

    def test_missing_cross_file_is_violation(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        (sd / "_cross" / "coupon-case-normalization-mismatch.md").unlink()
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "cross-ref" in out
        assert "coupon-case-normalization-mismatch" in out

    def test_invalid_status_enum(self, shadow_viewer, tmp_path, capsys):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - Bad status.
              _(maybeverified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "enum" in out
        assert "maybeverified" in out

    def test_invalid_source_enum(self, shadow_viewer, tmp_path, capsys):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - Bad source.
              _(verified, source: psychic)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "enum" in out
        assert "psychic" in out

    def test_invalid_label(self, shadow_viewer, tmp_path, capsys):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - Bad label.
              _(verified, source: exploration, labels: [unicorn])_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "enum" in out
        assert "unicorn" in out

    def test_heading_without_backticks(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## not_in_backticks

            - Hi.
              _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "heading" in out

    def test_file_level_heading_does_not_violate(
        self, shadow_viewer, tmp_path, capsys
    ):
        """`## File-Level` is allowed without backticks."""
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## File-Level

            - A file-level discovery.
              _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 0, out
        assert "Invariants OK" in out

    def test_also_involves_without_backticks(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - With bad anchors.
              _(verified, source: exploration)_
              Also involves: b.py::bar, c.py::baz
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "anchor" in out

    def test_also_involves_missing_symbol_after_colons(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - Empty sym.
              _(verified, source: exploration)_
              Also involves: `b.py::`
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        # The empty-symbol form fails the strict regex (which requires
        # non-empty after ::), so the file_sym_re finds zero anchors and
        # we hit the "needs backtick anchors" branch instead.
        assert "anchor" in out
        assert "needs `file::symbol`" in out

    def test_cross_missing_category(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - A discovery.
              _(verified, source: exploration)_
        """)
        _write_shadow(sd, "_cross/no-cat.md", """\
            # No category here

            **Refs**:
            - `a.py::foo`

            **Discovery**: Something.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "schema" in out
        assert "Category" in out

    def test_cross_invalid_category(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_cross/bad-cat.md", """\
            # Bad category here

            **Category**: bogus
            **Refs**:
            - `a.py::foo`

            **Discovery**: Something.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "enum" in out
        assert "bogus" in out

    def test_cross_missing_metadata_line(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_cross/no-meta.md", """\
            # No meta here

            **Category**: pattern
            **Refs**:
            - `a.py::foo`

            **Discovery**: Something.
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "schema" in out
        assert "missing trailing" in out

    def test_cross_missing_refs_block(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_cross/no-refs.md", """\
            # No refs here

            **Category**: pattern

            **Discovery**: Something.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "schema" in out
        assert "Refs" in out

    def test_cross_ref_missing_symbol(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        # Note: file_sym_re demands "::" in backticks. We use a backtick
        # ref with no symbol after `::`.
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - hi.
              _(verified, source: exploration)_

            ## Cross-References

            - [bad-anchor](_cross/bad-anchor.md)
        """)
        _write_shadow(sd, "_cross/bad-anchor.md", """\
            # Bad anchor

            **Category**: pattern
            **Refs**:
            - `a.py::`

            **Discovery**: stuff.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "anchor" in out

    def test_back_pointer_missing(
        self, shadow_viewer, tmp_path, capsys
    ):
        """_cross/x.md references a.py::foo but a.py.md has no
        Cross-References section pointing back to x.md."""
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## `foo`

            - A discovery.
              _(verified, source: exploration)_
        """)
        _write_shadow(sd, "_cross/orphan.md", """\
            # Orphan

            **Category**: pattern
            **Refs**:
            - `a.py::foo`

            **Discovery**: stuff.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "cross-ref" in out
        assert "does not link back to" in out

    def test_back_pointer_references_nonexistent_file(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "_cross/ghost.md", """\
            # Ghost

            **Category**: pattern
            **Refs**:
            - `does/not/exist.py::foo`

            **Discovery**: stuff.

            _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        assert "no such shadow file exists" in out

    def test_violation_line_format(
        self, shadow_viewer, tmp_path, capsys
    ):
        """Output is grep-friendly: `path:line: kind: message`."""
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## not_backticked
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 1
        # At least one line of form path:line: kind: msg
        assert re.search(r"a\.py\.md:\d+: heading: ", out)

    def test_multiple_violations_reported(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## not_backticked

            ## `foo`

            - bad.
              _(maybeverified, source: psychic, labels: [unicorn])_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        err = capsys.readouterr().err
        assert rc == 1
        # heading + 3 enum violations = at least 4 lines
        violation_lines = [
            l for l in out.splitlines()
            if re.match(r"^[^:]+:\d+: \w+: ", l)
        ]
        assert len(violation_lines) >= 3

    def test_count_summary_emitted_on_stderr(
        self, shadow_viewer, tmp_path, capsys
    ):
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", "## not_backticked\n")
        rc = shadow_viewer.view_check_invariants(sd)
        captured = capsys.readouterr()
        assert rc == 1
        # Final count line goes to stderr
        assert "invariant violation(s) found" in captured.err

    def test_special_headings_allowed(
        self, shadow_viewer, tmp_path, capsys
    ):
        """`## Notes`, `## Metadata`, `## File-Level Notes` are allowed
        without backticks."""
        sd = _make_shadow_root(tmp_path)
        _write_shadow(sd, "a.py.md", """\
            # Shadow: a.py

            ## Notes

            Some prose.

            ## Metadata

            Some metadata.

            ## File-Level Notes

            More prose.

            ## `real_sym`

            - A discovery.
              _(verified, source: exploration)_
        """)
        rc = shadow_viewer.view_check_invariants(sd)
        out = capsys.readouterr().out
        assert rc == 0, out


# ===========================================================================
# main() — in-process via sys.argv (covers the dispatcher)
# ===========================================================================


class TestMainInProcess:
    """Drive `main()` directly so coverage of the dispatch arms is captured.

    All paths use `--shadow-dir <coupon_demo/.shadow>` to avoid relying
    on cwd. Where we need a true CLI smoke (e.g., to verify `--help`
    output), use subprocess.
    """

    def _shadow(self, coupon_demo):
        return str(coupon_demo / ".shadow")

    def test_summary_dispatch(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer, ["--shadow-dir", self._shadow(coupon_demo),
                            "--summary"]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Shadow Knowledge Base Summary" in out

    def test_no_args_default_is_summary(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer, ["--shadow-dir", self._shadow(coupon_demo)]
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Shadow Knowledge Base Summary" in out

    def test_search_dispatch(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--search", "coupon"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Search: 'coupon'" in out

    def test_prefs_dispatch(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo), "--prefs"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "No preferences recorded yet." in out

    def test_labels_dispatch_bug(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--labels", "bug"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "label(s): bug" in out

    def test_recent_dispatch_with_n(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--recent", "4"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Most Recent Discoveries (top 4)" in out

    def test_recent_dispatch_no_n(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo), "--recent"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        # Default count is 10
        assert "Most Recent Discoveries (top 10)" in out

    def test_top_dispatch(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--top", "cart.py"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "for cart.py:" in out

    def test_top_dispatch_with_labels_and_limits(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--top", "cart.py",
             "--top-labels", "bug",
             "--top-limit", "2",
             "--top-max-chars", "0"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        bullets = [l for l in out.splitlines() if l.startswith("- [")]
        assert len(bullets) <= 2

    def test_check_invariants_clean_exits_zero(
        self, shadow_viewer, coupon_demo, capsys
    ):
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", self._shadow(coupon_demo),
             "--check-invariants"],
        )
        out = capsys.readouterr().out
        assert rc == 0
        assert "Invariants OK" in out

    def test_check_invariants_dirty_exits_one(
        self, shadow_viewer, coupon_demo, capsys
    ):
        sd = coupon_demo / ".shadow"
        # Inject a heading violation
        cart = sd / "cart.py.md"
        cart.write_text(
            cart.read_text(encoding="utf-8").replace(
                "## `COUPON_CACHE`", "## COUPON_CACHE", 1
            ),
            encoding="utf-8",
        )
        rc = _call_main(
            shadow_viewer,
            ["--shadow-dir", str(sd), "--check-invariants"],
        )
        out = capsys.readouterr().out
        assert rc == 1
        assert "heading" in out

    def test_explicit_shadow_dir_missing(
        self, shadow_viewer, tmp_path, capsys
    ):
        bogus = tmp_path / "does-not-exist"
        rc = _call_main(
            shadow_viewer, ["--shadow-dir", str(bogus), "--summary"]
        )
        err = capsys.readouterr().err
        assert rc == 1
        assert "No .shadow/ directory found" in err

    def test_auto_detect_via_chdir(
        self, shadow_viewer, coupon_demo, monkeypatch, capsys
    ):
        """No --shadow-dir: cwd is walked up to find .shadow/."""
        monkeypatch.chdir(coupon_demo)
        rc = _call_main(shadow_viewer, ["--summary"])
        out = capsys.readouterr().out
        assert rc == 0
        assert "Shadow Knowledge Base Summary" in out

    def test_auto_detect_no_shadow_in_cwd(
        self, shadow_viewer, tmp_path, monkeypatch, capsys
    ):
        monkeypatch.chdir(tmp_path)
        rc = _call_main(shadow_viewer, ["--summary"])
        err = capsys.readouterr().err
        assert rc == 1
        assert "No .shadow/ directory found" in err


# --- main(): subprocess smoke (covers true argv parsing, help, errors) ----


class TestMainSubprocess:
    """End-to-end CLI smoke. Subprocess output is the contract here —
    these don't add coverage but they catch dispatcher / argparse regressions
    the in-process tests can't (e.g. --help, mutually exclusive errors)."""

    @pytest.mark.slow
    @pytest.mark.integration
    def test_help_exits_zero(self, repo_root, coupon_demo):
        r = _run_viewer(repo_root, coupon_demo, "--help")
        assert r.returncode == 0
        # argparse prints usage and the description
        assert "usage:" in r.stdout.lower()
        assert "--summary" in r.stdout
        assert "--search" in r.stdout
        assert "--check-invariants" in r.stdout

    @pytest.mark.slow
    @pytest.mark.integration
    def test_unknown_flag_exits_nonzero(self, repo_root, coupon_demo):
        r = _run_viewer(repo_root, coupon_demo, "--no-such-flag")
        assert r.returncode != 0
        assert "unrecognized" in r.stderr or "unrecognized" in r.stdout

    @pytest.mark.slow
    @pytest.mark.integration
    def test_mutually_exclusive_flags(self, repo_root, coupon_demo):
        """--summary and --prefs are in the same exclusive group."""
        r = _run_viewer(
            repo_root, coupon_demo, "--summary", "--prefs"
        )
        assert r.returncode != 0
        # argparse error mentions "not allowed with"
        assert "not allowed with" in r.stderr
