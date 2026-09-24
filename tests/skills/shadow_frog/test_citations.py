"""Visible citation updates use real Markdown and per-file writer coordination."""

from pathlib import Path
import subprocess
import sys

import pytest

from tests.conftest import _load_script


CORE = Path(__file__).resolve().parents[3] / "skills/shadow-frog"


@pytest.fixture
def citations():
    return _load_script(CORE / "_citations.py")


def per_file(tmp_path, *, score="", newline="\n"):
    path = tmp_path / ".shadow/src/auth.py.md"
    path.parent.mkdir(parents=True)
    text = (
        "# Shadow: src/auth.py\n\n## File-Level\n\n"
        "- Importing opens no sockets.\n  _(verified, source: exploration)_\n\n"
        "## `class Auth`\n\n"
        "- Construction leaves credentials untouched.\n  _(verified, source: user)_\n\n"
        "### `Auth.login`\n\n"
        "- Key `a  b` is distinct.\n  _(verified, source: exploration, labels: [bug]" + score + ")_\n\n"
        "- Rejects expired tokens.\n  _(verified, source: interaction, citation_score: 4)_\n\n"
        "## Cross-References\n\n- [auth](../_cross/auth.md)\n"
    )
    path.write_bytes(text.replace("\n", newline).encode("utf-8"))
    return path


def test_direct_read_then_increment_only_the_selected_visible_score(citations, tmp_path):
    path = per_file(tmp_path)
    before = path.read_text(encoding="utf-8")
    assert "Key `a  b`" in before
    updates = citations.record_citations(path, ["Key `a  b` is distinct."], symbol="Auth.login")
    after = path.read_text(encoding="utf-8")
    assert after == before.replace("labels: [bug])_", "labels: [bug], citation_score: 1)_")
    assert updates[0]["before"] == 0 and updates[0]["after"] == 1
    assert sorted(p.name for p in path.parent.iterdir()) == ["auth.py.md"]


def test_batch_is_atomic_and_repeated_text_counts_once(citations, tmp_path):
    path = per_file(tmp_path)
    citations.record_citations(
        path, ["Key `a  b` is distinct.", "Rejects expired tokens.", "Rejects expired tokens."],
        symbol="Auth.login",
    )
    content = path.read_text(encoding="utf-8")
    assert "citation_score: 1" in content and "citation_score: 5" in content
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="found 0"):
        citations.record_citations(path, ["Rejects expired tokens.", "Does not exist."], symbol="Auth.login")
    assert path.read_bytes() == before
    assert not list(path.parent.glob("*.citation.lock"))


def test_duplicate_claim_is_ambiguous_not_a_bulk_increment(citations, tmp_path):
    path = per_file(tmp_path)
    text = path.read_text(encoding="utf-8")
    claim = "- Rejects expired tokens.\n  _(verified, source: interaction, citation_score: 4)_\n"
    path.write_text(text.replace(claim, claim + "\n" + claim), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="found 2"):
        citations.record_citations(path, ["Rejects expired tokens."], symbol="Auth.login")
    assert path.read_bytes() == before


@pytest.mark.parametrize("symbol,text", [
    ("File-Level", "Importing opens no sockets."),
    ("Auth", "Construction leaves credentials untouched."),
])
def test_file_level_and_container_symbols(citations, tmp_path, symbol, text):
    path = per_file(tmp_path)
    updates = citations.record_citations(path, [text], symbol=symbol)
    assert updates == [{"text": text, "before": 0, "after": 1}]


def test_literal_whitespace_is_not_normalized_away(citations, tmp_path):
    path = per_file(tmp_path)
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="found 0"):
        citations.record_citations(path, ["Key `a b` is distinct."], symbol="Auth.login")
    assert path.read_bytes() == before


@pytest.mark.parametrize("boundary", ["notes-heading", "fenced-example"])
def test_non_discovery_sections_cannot_be_cited_as_previous_symbol(citations, tmp_path, boundary):
    path = per_file(tmp_path)
    sample = "- Example-only claim.\n  _(verified, source: exploration, citation_score: 2)_\n"
    if boundary == "notes-heading":
        sample = "## Notes\n\n" + sample
    else:
        sample = "```markdown\n" + sample + "```\n"
    path.write_text(
        path.read_text(encoding="utf-8").replace("## Cross-References", sample + "\n## Cross-References"),
        encoding="utf-8",
    )
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="found 0"):
        citations.record_citations(path, ["Example-only claim."], symbol="Auth.login")
    assert path.read_bytes() == before


def test_discovery_after_fenced_example_keeps_its_actual_symbol(citations, tmp_path):
    path = per_file(tmp_path)
    fenced = (
        "```markdown\n## `WrongSymbol`\n\n- Example-only claim.\n"
        "  _(verified, source: exploration, citation_score: 2)_\n```\n\n"
        "- Actual later claim.\n  _(verified, source: user, citation_score: 0)_\n\n"
    )
    path.write_text(
        path.read_text(encoding="utf-8").replace("## Cross-References", fenced + "## Cross-References"),
        encoding="utf-8",
    )
    before = path.read_text(encoding="utf-8")
    citations.record_citations(path, ["Actual later claim."], symbol="Auth.login")
    assert path.read_text(encoding="utf-8") == before.replace(
        "source: user, citation_score: 0", "source: user, citation_score: 1",
    )


def test_crlf_bom_and_existing_metadata_are_preserved(citations, tmp_path):
    path = per_file(tmp_path, score=", citation_score: 7", newline="\r\n")
    before = b"\xef\xbb\xbf" + path.read_bytes()
    path.write_bytes(before)
    citations.record_citations(path, ["Key `a  b` is distinct."], symbol="Auth.login")
    assert path.read_bytes() == before.replace(b"citation_score: 7", b"citation_score: 8")


def test_wrapped_claim_text_can_be_reported_without_copying_layout(citations, tmp_path):
    path = per_file(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace("Rejects expired tokens.", "Rejects expired\n  tokens."),
        encoding="utf-8",
    )
    citations.record_citations(path, ["Rejects expired tokens."], symbol="Auth.login")
    assert "citation_score: 5" in path.read_text(encoding="utf-8")


@pytest.mark.parametrize("kind", ["preference", "cross"])
def test_preferences_and_cross_cutting_have_visible_scores(citations, tmp_path, kind):
    path = tmp_path / ".shadow" / ("_prefs.md" if kind == "preference" else "_cross/shared.md")
    path.parent.mkdir(parents=True)
    if kind == "preference":
        path.write_text("# Preferences\n\n- Keep the contract.\n  _(source: user)_\n", encoding="utf-8")
    else:
        path.write_text(
            "# Shared\n\n**Category**: contract\n**Refs**:\n- `src/a.py::run`\n\n"
            "**Discovery**: Keep the contract.\n\n_(verified, source: exploration)_\n",
            encoding="utf-8",
        )
    citations.record_citations(path, ["Keep the contract."])
    assert "citation_score: 1" in path.read_text(encoding="utf-8")


def test_concurrent_processes_preserve_all_increments(citations, tmp_path):
    path = per_file(tmp_path)
    command = [
        sys.executable, str(CORE / "shadow-cite.py"), str(path),
        "--symbol", "Auth.login", "--text", "Key `a  b` is distinct.",
    ]
    processes = [subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(8)]
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode("utf-8")
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    content = path.read_text(encoding="utf-8")
    assert "labels: [bug], citation_score: 8" in content
    assert "source: interaction, citation_score: 4" in content
    assert sorted(p.name for p in path.parent.iterdir()) == ["auth.py.md"]


def test_existing_lock_fails_visibly_without_removing_it(citations, tmp_path):
    path = per_file(tmp_path)
    lock = path.with_name(path.name + ".citation.lock")
    lock.write_text("other writer", encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="writer busy"):
        citations.record_citations(path, ["Key `a  b` is distinct."], symbol="Auth.login", timeout=0.02)
    assert path.read_bytes() == before and lock.read_text() == "other writer"


def test_failed_publication_preserves_knowledge_and_cleans_temporary_files(citations, tmp_path, monkeypatch):
    path = per_file(tmp_path)
    before = path.read_bytes()

    def denied(*args):
        raise PermissionError("simulated sharing violation")

    monkeypatch.setattr(citations.os, "replace", denied)
    with pytest.raises(PermissionError, match="sharing violation"):
        citations.record_citations(path, ["Rejects expired tokens."], symbol="Auth.login")
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == ["auth.py.md"]


def test_detected_ordinary_editor_race_is_not_overwritten(citations, tmp_path, monkeypatch):
    path = per_file(tmp_path)
    before = path.read_bytes()
    actual_fsync = citations.os.fsync

    def another_writer(fd):
        path.write_bytes(before + b"\nAdditional knowledge from another writer.\n")
        actual_fsync(fd)

    monkeypatch.setattr(citations.os, "fsync", another_writer)
    with pytest.raises(citations.CitationError, match="changed during"):
        citations.record_citations(path, ["Rejects expired tokens."], symbol="Auth.login")
    assert path.read_bytes() == before + b"\nAdditional knowledge from another writer.\n"
    assert sorted(p.name for p in path.parent.iterdir()) == ["auth.py.md"]


@pytest.mark.parametrize("score", [-1, 1.2, True, None, "3"])
def test_json_score_requires_a_nonnegative_integer(citations, score):
    with pytest.raises(citations.CitationError, match="nonnegative integer"):
        citations.validate_score(score)


@pytest.mark.parametrize("raw", ["-1", "1.2", "true", "NaN", ""])
def test_invalid_existing_score_is_not_reset(citations, tmp_path, raw):
    path = per_file(tmp_path, score=f", citation_score: {raw}")
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="Line .*Malformed metadata") as exc:
        citations.record_citations(path, ["Key `a  b` is distinct."], symbol="Auth.login")
    assert str(path.resolve()) in str(exc.value)
    assert path.read_bytes() == before


def test_cli_error_names_the_entry_and_preserves_file(tmp_path):
    path = per_file(tmp_path)
    before = path.read_bytes()
    result = subprocess.run(
        [sys.executable, str(CORE / "shadow-cite.py"), str(path), "--symbol", "Auth.login", "--text", "Wrong claim."],
        capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 1 and "ERROR:" in result.stderr and "Re-read" in result.stderr
    assert result.stdout == "" and path.read_bytes() == before


@pytest.mark.parametrize("relative", ["_index.md", "_dreams/dream/report.md", "_meta/notes.md"])
def test_indexes_and_experiment_reports_are_not_citation_targets(citations, tmp_path, relative):
    path = tmp_path / ".shadow" / relative
    path.parent.mkdir(parents=True)
    path.write_text("# Metadata\n", encoding="utf-8")
    with pytest.raises(citations.CitationError, match="not indexes or dream reports"):
        citations.record_citations(path, ["Metadata"], symbol="File-Level")


def test_outside_target_symlink_is_refused(citations, tmp_path, make_symlink):
    path = per_file(tmp_path)
    outside = tmp_path / "outside.md"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    make_symlink(path, outside)
    before = outside.read_bytes()
    with pytest.raises(citations.CitationError, match="inside"):
        citations.record_citations(path, ["Key `a  b` is distinct."], symbol="Auth.login")
    assert outside.read_bytes() == before


def test_aliased_shadow_root_is_refused(citations, tmp_path, make_symlink):
    real_root = tmp_path / "real"
    real_root.mkdir()
    path = real_root / "file.md"
    path.write_text("# Shadow: file\n\n## `run`\n\n- Claim.\n  _(verified, source: exploration)_\n", encoding="utf-8")
    make_symlink(tmp_path / ".shadow", real_root, target_is_directory=True)
    before = path.read_bytes()
    with pytest.raises(citations.CitationError, match="filesystem alias"):
        citations.record_citations(tmp_path / ".shadow/file.md", ["Claim."], symbol="run")
    assert path.read_bytes() == before
