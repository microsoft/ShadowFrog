"""Citation ranking, bounded context, expansion, and stable pagination."""

from dataclasses import replace
import io
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import unicodedata

import pytest


def make_shadow(tmp_path, count=4, text_size=20):
    shadow = tmp_path / ".shadow"
    shadow.mkdir(exist_ok=True)
    path = shadow / "source.py.md"
    path.write_text(
        "# Shadow: source.py\n\n## `run`\n\n" + "\n".join(
            f"- Claim {index:05}: " + ("details " * text_size) +
            "\n  _(verified, source: exploration, labels: [bug])_\n"
            for index in range(count)
        ) + "\n## Cross-References\n",
        encoding="utf-8",
    )
    return shadow


def ids(text):
    return re.findall(r"\bid=(d_[0-9a-f]{32})", text)


def cursor(text):
    match = re.search(r"--cursor ([0-9a-f]{32}:\d+)", text)
    return match.group(1) if match else None


def text_cursor(text):
    match = re.search(r"--text-cursor (\S+)", text)
    return match.group(1) if match else None


def test_only_emitted_entries_count_and_no_markdown_changes(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 12)
    before = {path: path.read_bytes() for path in shadow.rglob("*") if path.is_file()}
    entries = shadow_viewer._knowledge_entries(shadow)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    assert store.scores([entry["id"] for entry in entries]) == {}
    shadow_viewer.view_search(
        shadow, "Claim", options=shadow_viewer.RetrievalOptions(limit=2, event_id="first"),
    )
    output = capsys.readouterr().out
    emitted = ids(output)
    assert len(emitted) == 2 and "citation_score=0" in output
    assert store.scores([entry["id"] for entry in entries]) == dict.fromkeys(emitted, 1)
    assert all(path.read_bytes() == content for path, content in before.items())
    assert sorted(path.name for path in shadow.iterdir()) == ["source.py.md"]


def test_retries_and_no_record_do_not_double_count(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    options = shadow_viewer.RetrievalOptions(event_id="retry")
    for _ in range(2):
        shadow_viewer.view_search(shadow, "Claim", options=options)
    output = capsys.readouterr().out
    identity = ids(output)[0]
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    assert store.scores([identity]) == {identity: 1}
    shadow_viewer.view_get(shadow, identity, options=replace(options, record=False))
    capsys.readouterr()
    assert store.scores([identity]) == {identity: 1}


def test_ten_thousand_claims_have_bounded_output(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 10000)
    options = shadow_viewer.RetrievalOptions(limit=10, max_chars=1800)
    started = time.monotonic()
    shadow_viewer.view_symbol(shadow, "source.py::run", options=options)
    output = capsys.readouterr().out
    assert len(output) <= 1800
    assert "10000 results" in output and len(ids(output)) <= 10
    assert ids(output) and cursor(output)
    assert time.monotonic() - started < 10
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    all_ids = [entry["id"] for entry in shadow_viewer._knowledge_entries(shadow)]
    assert len(all_ids) == len(set(all_ids)) == 10000
    assert store.scores(all_ids) == dict.fromkeys(ids(output), 1)


def test_cursor_is_stable_despite_other_readers_updating_scores(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 23, text_size=2)
    options = shadow_viewer.RetrievalOptions(limit=3, max_chars=1400)
    entries = shadow_viewer._knowledge_entries(shadow)
    expected = {entry["id"] for entry in entries}
    found = []
    next_cursor = None
    while True:
        shadow_viewer.view_symbol(
            shadow, "source.py::run", options=replace(options, cursor=next_cursor),
        )
        output = capsys.readouterr().out
        assert len(output) <= 1400
        found.extend(ids(output))
        next_cursor = cursor(output)
        if next_cursor is None:
            break
        shadow_viewer.CitationStore.for_shadow(shadow).record(
            [entries[-1]["id"]], f"concurrent-{len(found)}",
        )
    assert len(found) == len(set(found)) == 23
    assert set(found) == expected


def test_changed_knowledge_invalidates_cursor(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path)
    options = shadow_viewer.RetrievalOptions(limit=1)
    shadow_viewer.view_search(shadow, "Claim", options=options)
    previous = cursor(capsys.readouterr().out)
    path = shadow / "source.py.md"
    path.write_text(path.read_text(encoding="utf-8").replace("details", "different"), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        shadow_viewer.view_search(shadow, "Claim", options=replace(options, cursor=previous))


def test_removed_results_invalidate_cursor_instead_of_claiming_empty_success(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path)
    options = shadow_viewer.RetrievalOptions(limit=1)
    shadow_viewer.view_search(shadow, "Claim", options=options)
    previous = cursor(capsys.readouterr().out)
    (shadow / "source.py.md").unlink()
    with pytest.raises(ValueError, match="changed"):
        shadow_viewer.view_search(shadow, "Claim", options=replace(options, cursor=previous))


def test_popularity_never_overrides_trust_and_allows_new_claim(shadow_viewer, tmp_path):
    entries = [
        {"id": "user", "source": "user", "status": "verified"},
        {"id": "popular", "source": "exploration", "status": "verified"},
        {"id": "popular2", "source": "exploration", "status": "verified"},
        {"id": "popular3", "source": "exploration", "status": "verified"},
        {"id": "new", "source": "exploration", "status": "verified"},
        {"id": "refuted", "source": "user", "status": "refuted"},
    ]
    scores = {"popular": 100, "popular2": 90, "popular3": 80, "refuted": 10000}
    ranked = shadow_viewer._rank_entries(entries, scores, 3)
    assert ranked[0]["id"] == "user" and ranked[-1]["id"] == "refuted"
    assert [entry["id"] for entry in ranked][:3] == ["user", "popular", "new"]


def test_zero_score_opportunity_accounts_for_character_budget(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 7)
    entries = shadow_viewer._knowledge_entries(shadow)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    store.record([entry["id"] for entry in entries[:-1]], "prior-read")
    shadow_viewer.view_search(
        shadow, "Claim", options=shadow_viewer.RetrievalOptions(limit=10, max_chars=1000),
    )
    output = capsys.readouterr().out
    assert len(output) <= 1000
    assert len(ids(output)) >= 2
    assert entries[-1]["id"] in ids(output)


@pytest.mark.parametrize("view", ["top", "symbol"])
def test_zero_score_slot_accounts_for_higher_trust_rows(shadow_viewer, tmp_path, capsys, view):
    shadow = make_shadow(tmp_path, 5, text_size=2)
    path = shadow / "source.py.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("source: exploration", "source: user", 1),
        encoding="utf-8",
    )
    entries = shadow_viewer._knowledge_entries(shadow)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    store.record([entry["id"] for entry in entries[1:4]], "prior")
    options = shadow_viewer.RetrievalOptions(limit=3, max_chars=600)
    if view == "top":
        shadow_viewer.view_top(shadow, "source.py", "bug", 3, 600, options=options)
    else:
        shadow_viewer.view_symbol(shadow, "source.py::run", options=options)
    output = capsys.readouterr().out
    assert len(output) <= 600
    assert ids(output)[0] == entries[0]["id"]
    assert entries[-1]["id"] in ids(output)


@pytest.mark.parametrize("file", ["cart.py", "inventory.py", "test_cart.py"])
def test_default_hook_budget_returns_multiple_warnings(shadow_viewer, coupon_demo, capsys, file):
    shadow_viewer.view_top(coupon_demo / ".shadow", file, "bug,security", 3, 600)
    output = capsys.readouterr().out
    assert len(output) <= 600
    assert len(ids(output)) == 3
    assert "citation_score=" not in output


def test_metadata_changes_preserve_identity_but_not_claim_changes(shadow_viewer, tmp_path):
    shadow = make_shadow(tmp_path, 1)
    original = shadow_viewer._knowledge_entries(shadow)[0]["id"]
    path = shadow / "source.py.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "verified, source: exploration, labels: [bug]", "uncertain, source: interaction"
        ),
        encoding="utf-8",
    )
    assert shadow_viewer._knowledge_entries(shadow)[0]["id"] == original
    path.write_text(path.read_text(encoding="utf-8").replace("Claim", "Different claim"), encoding="utf-8")
    assert shadow_viewer._knowledge_entries(shadow)[0]["id"] != original


def test_duplicate_preferences_share_one_identity_without_losing_trust(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    (shadow / "_prefs.md").write_text(
        "# Preferences\n\n- Preserve the contract.\n  _(source: interaction)_\n"
        "\n- Preserve the contract.\n  _(source: user)_\n",
        encoding="utf-8",
    )
    shadow_viewer.view_prefs(shadow)
    output = capsys.readouterr().out
    assert "Project Preferences (1 total)" in output and "[user]" in output
    assert len(ids(output)) == 1
    assert shadow_viewer.CitationStore.for_shadow(shadow).scores(ids(output)) == dict.fromkeys(ids(output), 1)


@pytest.mark.parametrize("kind", ["class", "interface", "enum", "trait", "struct", "protocol", "module"])
def test_container_symbols_use_canonical_anchors(shadow_viewer, shadow_init, tmp_path, capsys, kind):
    shadow = make_shadow(tmp_path, 1)
    heading = shadow_init.Symbol("Container", kind).heading_text
    path = shadow / "source.py.md"
    path.write_text(path.read_text(encoding="utf-8").replace("`run`", f"`{heading}`"), encoding="utf-8")
    shadow_viewer.view_symbol(shadow, "source.py::Container")
    result = capsys.readouterr().out
    assert "Claim 00000" in result and "source.py::Container" in result
    shadow_viewer.view_get(shadow, ids(result)[0])
    assert "source.py::Container" in capsys.readouterr().out


@pytest.mark.parametrize("query", ["src/auth.py", unicodedata.normalize("NFD", "Src/caf\u00e9.py")])
def test_filesystem_alias_ids_expand_and_include_cross_refs(shadow_viewer, tmp_path, capsys, query):
    actual = "Src/Auth.py" if query == "src/auth.py" else "Src/caf\u00e9.py"
    shadow = tmp_path / ".shadow"
    path = shadow / f"{actual}.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        f"# Shadow: {actual}\n\n## `run`\n\n- Keep the audit trail.\n"
        "  _(verified, source: exploration, labels: [bug])_\n",
        encoding="utf-8",
    )
    if not (shadow / (query + ".md")).is_file():
        pytest.skip("Filesystem distinguishes these case/Unicode spellings")
    cross = shadow / "_cross/audit.md"
    cross.parent.mkdir()
    cross.write_text(
        f"# Audit\n\n**Category**: contract\n**Refs**:\n- `{actual}::run`\n\n"
        "**Discovery**: Cross-file audit contract.\n\n_(verified, source: exploration)_\n",
        encoding="utf-8",
    )
    global_entries = shadow_viewer._knowledge_entries(shadow)
    shadow_viewer.view_symbol(shadow, f"{query}::run")
    result = capsys.readouterr().out
    assert "Cross-file audit contract." in result and "Keep the audit trail." in result
    assert set(ids(result)) == {entry["id"] for entry in global_entries}
    for identity in ids(result):
        shadow_viewer.view_get(shadow, identity, options=shadow_viewer.RetrievalOptions(record=False))
        assert identity in capsys.readouterr().out


def test_distinct_case_sensitive_files_are_not_folded(shadow_viewer, tmp_path):
    shadow = tmp_path / ".shadow"
    shadow.mkdir()
    upper, lower = shadow / "A.py.md", shadow / "a.py.md"
    upper.write_text("# Shadow: A.py\n\n## `run`\n\n- Claim.\n", encoding="utf-8")
    if lower.exists():
        pytest.skip("Filesystem does not support distinct case-only names")
    lower.write_text("# Shadow: a.py\n\n## `run`\n\n- Claim.\n", encoding="utf-8")
    assert len({entry["id"] for entry in shadow_viewer._knowledge_entries(shadow)}) == 2


def test_literal_whitespace_remains_separately_searchable(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 0)
    path = shadow / "source.py.md"
    path.write_text(
        "# Shadow: source.py\n\n## `run`\n\n"
        "- Key `a b` is accepted.\n  _(verified, source: exploration)_\n\n"
        "- Key `a  b` is accepted.\n  _(verified, source: exploration)_\n",
        encoding="utf-8",
    )
    entries = shadow_viewer._knowledge_entries(shadow)
    assert len(entries) == 2 and entries[0]["id"] != entries[1]["id"]
    shadow_viewer.view_search(shadow, "a  b")
    result = capsys.readouterr().out
    assert ids(result) == [entries[1]["id"]]
    shadow_viewer.view_get(shadow, entries[1]["id"])
    assert "`a  b`" in capsys.readouterr().out


def test_duplicate_claims_union_labels_and_preserve_stronger_source(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 0)
    (shadow / "source.py.md").write_text(
        "# Shadow: source.py\n\n## `run`\n\n"
        "- Shared claim.\n  _(verified, source: user, labels: [bug])_\n\n"
        "- Shared claim.\n  _(verified, source: exploration, labels: [security])_\n",
        encoding="utf-8",
    )
    entries = shadow_viewer._knowledge_entries(shadow)
    assert len(entries) == 1
    assert entries[0]["labels"] == ["bug", "security"] and entries[0]["source"] == "user"
    shadow_viewer.view_labels(shadow, "security")
    assert ids(capsys.readouterr().out) == [entries[0]["id"]]


def test_installed_import_does_not_create_bytecode(repo_root, tmp_path):
    installed = tmp_path / ".github/skills/shadow-frog-viewer"
    shutil.copytree(
        repo_root / "skills/shadow-frog-viewer", installed,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    result = subprocess.run(
        [sys.executable, str(installed / "shadow-viewer.py"), "--help"],
        env=env, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    assert not list(installed.rglob("*.pyc"))


def test_missing_home_does_not_hide_standalone_knowledge(shadow_viewer, tmp_path, monkeypatch, capsys):
    from pathlib import Path

    shadow = make_shadow(tmp_path, 1)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)

    def missing_home():
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "home", missing_home)
    shadow_viewer.view_search(shadow, "Claim")
    result = capsys.readouterr()
    assert "Claim 00000" in result.out
    assert "will not be recorded" in result.err and "home" in result.err


def test_get_chunks_long_claim_without_exceeding_budget(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1, text_size=500)
    entry = shadow_viewer._knowledge_entries(shadow)[0]
    identity = entry["id"]
    continuation = None
    pieces = []
    while True:
        shadow_viewer.view_get(
            shadow, identity,
            options=shadow_viewer.RetrievalOptions(max_chars=500, text_cursor=continuation),
        )
        output = capsys.readouterr().out
        assert len(output) <= 500
        continuation = text_cursor(output)
        pieces.append(output.removesuffix("\n").split("\n", 2)[2].split("\nContinue:", 1)[0])
        if continuation is None:
            break
    assert "".join(pieces) == entry["anchor"] + "\n\n" + entry["text"] + "\nLabels: bug"
    assert shadow_viewer.CitationStore.for_shadow(shadow).scores([identity]) == {identity: 1}


@pytest.mark.parametrize("change", ["labels", "source", "status", "text"])
def test_expansion_rejects_changed_body_or_metadata(shadow_viewer, tmp_path, capsys, change):
    shadow = make_shadow(tmp_path, 1, text_size=200)
    entry = shadow_viewer._knowledge_entries(shadow)[0]
    shadow_viewer.view_get(shadow, entry["id"], options=shadow_viewer.RetrievalOptions(max_chars=500))
    continuation = text_cursor(capsys.readouterr().out)
    path = shadow / "source.py.md"
    replacements = {
        "labels": ("labels: [bug]", "labels: [bug, security]"),
        "source": ("source: exploration", "source: user"),
        "status": ("verified,", "refuted,"),
        "text": ("details ", "new details "),
    }
    path.write_text(path.read_text(encoding="utf-8").replace(*replacements[change]), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        shadow_viewer.view_get(
            shadow, entry["id"], options=shadow_viewer.RetrievalOptions(text_cursor=continuation),
        )


def test_no_record_continuation_stays_unrecorded(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1, text_size=200)
    entry = shadow_viewer._knowledge_entries(shadow)[0]
    shadow_viewer.view_get(
        shadow, entry["id"], options=shadow_viewer.RetrievalOptions(max_chars=500, record=False),
    )
    continuation = text_cursor(capsys.readouterr().out)
    shadow_viewer.view_get(
        shadow, entry["id"], options=shadow_viewer.RetrievalOptions(text_cursor=continuation),
    )
    capsys.readouterr()
    assert shadow_viewer.CitationStore.for_shadow(shadow).scores([entry["id"]]) == {}


@pytest.mark.parametrize("change", ["mtime", "other-symbol"])
def test_nonrecent_pages_survive_irrelevant_file_changes(shadow_viewer, tmp_path, capsys, change):
    shadow = make_shadow(tmp_path)
    options = shadow_viewer.RetrievalOptions(limit=1)
    shadow_viewer.view_symbol(shadow, "source.py::run", options=options)
    first = capsys.readouterr().out
    path = shadow / "source.py.md"
    if change == "mtime":
        timestamp = path.stat().st_mtime + 10
        os.utime(path, (timestamp, timestamp))
    else:
        with path.open("a", encoding="utf-8") as stream:
            stream.write("\n## `other`\n\n- Other knowledge.\n  _(verified, source: exploration)_\n")
    shadow_viewer.view_symbol(shadow, "source.py::run", options=replace(options, cursor=cursor(first)))
    assert not set(ids(first)) & set(ids(capsys.readouterr().out))


@pytest.mark.parametrize("change", ["source", "labels", "status", "recent-mtime"])
def test_pages_invalidate_on_relevant_metadata_changes(shadow_viewer, tmp_path, capsys, change):
    shadow = make_shadow(tmp_path)
    options = shadow_viewer.RetrievalOptions(limit=1)
    view = shadow_viewer.view_recent if change == "recent-mtime" else shadow_viewer.view_symbol
    args = [shadow] if change == "recent-mtime" else [shadow, "source.py::run"]
    view(*args, options=options)
    continuation = cursor(capsys.readouterr().out)
    path = shadow / "source.py.md"
    if change == "recent-mtime":
        timestamp = path.stat().st_mtime + 10
        os.utime(path, (timestamp, timestamp))
    else:
        replacements = {
            "source": ("source: exploration", "source: user"),
            "labels": ("labels: [bug]", "labels: [security]"),
            "status": ("verified,", "refuted,"),
        }
        path.write_text(path.read_text(encoding="utf-8").replace(*replacements[change]), encoding="utf-8")
    with pytest.raises(ValueError, match="changed"):
        view(*args, options=replace(options, cursor=continuation))


def test_summary_and_invariant_checks_do_not_count(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    shadow_viewer.view_summary(shadow)
    shadow_viewer.view_check_invariants(shadow)
    capsys.readouterr()
    assert not store.path.exists()


def test_all_creation_sources_start_at_zero_without_schema_changes(
    shadow_viewer, dream_reconcile, tmp_path, capsys,
):
    shadow = tmp_path / ".shadow"
    discoveries = [
        {"anchor": f"source.py::run_{source}", "text": f"Behavior from {source}.",
         "source": source, "status": "verified"}
        for source in ("exploration", "user", "interaction")
    ]
    dream_reconcile.merge_discoveries(
        str(tmp_path),
        [("dream/test/20260923-000000Z-test", "20260923-000000Z-test",
          {"discoveries": discoveries})],
    )
    entries = shadow_viewer._knowledge_entries(shadow)
    assert len(entries) == 3
    assert shadow_viewer.CitationStore.for_shadow(shadow).scores([entry["id"] for entry in entries]) == {}
    shadow_viewer.view_search(shadow, "Behavior", options=shadow_viewer.RetrievalOptions(record=False))
    output = capsys.readouterr().out
    assert output.count("citation_score=0") == 3
    assert "citation_score" not in (shadow / "source.py.md").read_text(encoding="utf-8")


def test_top_hard_budget_counts_only_visible_claims(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 10, text_size=150)
    shadow_viewer.view_top(shadow, "source.py", "bug", 3, 600)
    output = capsys.readouterr().out
    assert len(output) <= 600
    emitted = ids(output)
    assert emitted and len(emitted) <= 3
    assert f"Top {len(emitted)} of 10" in output
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    assert store.scores([entry["id"] for entry in shadow_viewer._knowledge_entries(shadow)]) == dict.fromkeys(emitted, 1)


def test_smallest_budget_still_emits_identifiable_content(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 10)
    shadow_viewer.view_search(shadow, "Claim", options=shadow_viewer.RetrievalOptions(max_chars=256))
    output = capsys.readouterr().out
    assert len(output) <= 256 and len(ids(output)) == 1
    assert "verified" in output and cursor(output)


def test_broken_ledger_returns_knowledge_with_warning(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    store.path.parent.mkdir(parents=True)
    store.path.write_bytes(b"not sqlite")
    shadow_viewer.view_search(shadow, "Claim")
    captured = capsys.readouterr()
    assert "Claim 00000" in captured.out and "citation_score=?" in captured.out
    assert "warning" in captured.err and "citation" in captured.err
    assert store.path.read_bytes() == b"not sqlite"


@pytest.mark.parametrize("view", ["search", "symbol", "get", "prefs", "labels", "recent", "top"])
def test_all_content_views_share_identity_and_score(shadow_viewer, tmp_path, capsys, view):
    shadow = make_shadow(tmp_path, 1, text_size=2)
    (shadow / "_prefs.md").write_text("# Preferences\n\n- Keep the contract.\n  _(source: user)_\n", encoding="utf-8")
    options = shadow_viewer.RetrievalOptions(event_id="same-visit")
    entry = next(item for item in shadow_viewer._knowledge_entries(shadow) if item["kind"] != "preference")
    if view == "search":
        shadow_viewer.view_search(shadow, "Claim", options=options)
    elif view == "symbol":
        shadow_viewer.view_symbol(shadow, "source.py::run", options=options)
    elif view == "get":
        shadow_viewer.view_get(shadow, entry["id"], options=options)
    elif view == "prefs":
        shadow_viewer.view_prefs(shadow, options=options)
    elif view == "labels":
        shadow_viewer.view_labels(shadow, "bug", options=options)
    elif view == "recent":
        shadow_viewer.view_recent(shadow, options=options)
    else:
        shadow_viewer.view_top(shadow, "source.py", "", 3, 600, options=options)
    output = capsys.readouterr().out
    emitted = ids(output)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    assert emitted and store.scores(emitted) == dict.fromkeys(emitted, 1)
    if view != "prefs":
        assert entry["id"] in emitted


def test_write_failure_warns_without_hiding_knowledge(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    identity = shadow_viewer._knowledge_entries(shadow)[0]["id"]
    store.record([identity], "initial")
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        shadow_viewer.view_search(shadow, "Claim")
        captured = capsys.readouterr()
        assert identity in captured.out
        assert "this visit was not recorded" in captured.err
    finally:
        connection.rollback()
        connection.close()
    assert store.scores([identity])[identity] == 1


def test_counting_can_recover_after_a_failed_score_read(shadow_viewer, tmp_path, monkeypatch, capsys):
    shadow = make_shadow(tmp_path, 1)
    identity = shadow_viewer._knowledge_entries(shadow)[0]["id"]
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    store.record([identity])
    lock = sqlite3.connect(store.path)
    lock.execute("BEGIN EXCLUSIVE")

    class UnlockOnOutput(io.StringIO):
        def flush(self):
            lock.rollback()
            super().flush()

    output = UnlockOnOutput()
    try:
        with monkeypatch.context() as context:
            context.setattr(sys, "stdout", output)
            shadow_viewer.view_search(shadow, "Claim")
    finally:
        lock.close()
    warnings = capsys.readouterr().err
    assert "ledger busy" in warnings and "was not recorded" not in warnings
    assert "citation_score=?" in output.getvalue()
    assert store.scores([identity])[identity] == 2


def test_failed_stdout_does_not_increment_score(shadow_viewer, tmp_path, monkeypatch):
    shadow = make_shadow(tmp_path, 1)
    store = shadow_viewer.CitationStore.for_shadow(shadow)
    identity = shadow_viewer._knowledge_entries(shadow)[0]["id"]

    class ClosedOutput:
        def write(self, text):
            raise BrokenPipeError("reader closed the pipe")

        def flush(self):
            pass

    monkeypatch.setattr(sys, "stdout", ClosedOutput())
    with pytest.raises(BrokenPipeError):
        shadow_viewer.view_search(shadow, "Claim")
    assert store.scores([identity]) == {}


def test_symbol_context_includes_related_cross_but_not_other_symbols(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    with (shadow / "source.py.md").open("a", encoding="utf-8") as stream:
        stream.write("\n## `unrelated`\n\n- Unrelated claim.\n  _(verified, source: user)_\n")
    cross = shadow / "_cross/related.md"
    cross.parent.mkdir()
    cross.write_text(
        "# Related behavior\n\n**Category**: contract\n**Refs**:\n"
        "- `source.py::run`\n- `other.py::call`\n\n"
        "**Discovery**: Cross-file constraint.\n\n_(verified, source: user)_\n",
        encoding="utf-8",
    )
    shadow_viewer.view_symbol(shadow, "source.py::run")
    output = capsys.readouterr().out
    assert "Cross-file constraint." in output
    assert "Claim 00000" in output and "Unrelated claim." not in output


def test_nested_paths_have_one_identity_across_scoped_and_global_views(shadow_viewer, tmp_path):
    shadow = tmp_path / ".shadow"
    source = shadow / "src/caf\u00e9 tools.py.md"
    source.parent.mkdir(parents=True)
    source.write_text(
        "# Shadow: src/caf\u00e9 tools.py\n\n## `run`\n\n"
        "- Nested claim.\n  _(verified, source: exploration)_\n",
        encoding="utf-8",
    )
    global_entry = shadow_viewer._knowledge_entries(shadow)[0]
    scoped_entry = shadow_viewer._knowledge_entries(shadow, "src/caf\u00e9 tools.py")[0]
    assert global_entry["anchor"] == scoped_entry["anchor"] == "src/caf\u00e9 tools.py::run"
    assert global_entry["id"] == scoped_entry["id"]


def test_expansion_preserves_long_anchors(shadow_viewer, tmp_path, capsys):
    shadow = make_shadow(tmp_path, 1)
    path = shadow / "source.py.md"
    symbol = "method_" + "name" * 50
    path.write_text(path.read_text(encoding="utf-8").replace("`run`", f"`{symbol}`"), encoding="utf-8")
    entry = shadow_viewer._knowledge_entries(shadow)[0]
    shadow_viewer.view_get(shadow, entry["id"])
    assert "source.py::" + symbol in capsys.readouterr().out


def test_cursor_cli_continuation_and_get_are_wired(repo_root, tmp_path):
    shadow = make_shadow(tmp_path)
    command = [
        sys.executable, str(repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"),
        "--shadow-dir", str(shadow), "--symbol", "source.py::run",
        "--limit", "1", "--max-chars", "600",
    ]
    first = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
    second = subprocess.run(
        [*command, "--cursor", cursor(first.stdout)],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert ids(first.stdout) != ids(second.stdout)
    expanded = subprocess.run(
        command[:4] + ["--get", ids(first.stdout)[0], "--max-chars", "600", "--no-record"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    assert ids(expanded.stdout) == ids(first.stdout)
    assert "citation_score=1" in expanded.stdout


def test_cli_text_continuation_is_revision_bound_and_counts_one_read(repo_root, tmp_path):
    shadow = make_shadow(tmp_path, 1, text_size=300)
    script = repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"
    prefix = [sys.executable, str(script), "--shadow-dir", str(shadow)]
    found = subprocess.run(
        [*prefix, "--symbol", "source.py::run", "--no-record"],
        capture_output=True, text=True, encoding="utf-8", check=True,
    )
    identity = ids(found.stdout)[0]
    command = ["--get", identity, "--max-chars", "500"]
    last = None
    for _ in range(40):
        result = subprocess.run(
            [*prefix, *command], capture_output=True, text=True, encoding="utf-8", check=True,
        )
        assert len(result.stdout) <= 500
        last = result.stdout
        if "\nContinue: " not in result.stdout:
            break
        command = result.stdout.split("\nContinue: ", 1)[1].strip().split()
    else:
        pytest.fail("Text continuation did not finish")
    assert "citation_score=1" in last


@pytest.mark.slow
def test_installed_layout_and_cli_options_are_wired(repo_root, coupon_demo, tmp_path):
    import shutil

    for agent in (".github", ".claude"):
        installed = tmp_path / agent / "skills/shadow-frog-viewer"
        shutil.copytree(repo_root / "skills/shadow-frog-viewer", installed)
        command = [
            sys.executable, str(installed / "shadow-viewer.py"),
            "--shadow-dir", str(coupon_demo / ".shadow"), "--search", "coupon",
            "--limit", "2", "--max-chars", "1000", "--event-id", agent,
        ]
        result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
        assert len(result.stdout) <= 1000 and len(ids(result.stdout)) == 2
        assert "citation_score=" in result.stdout


@pytest.mark.parametrize("args", [
    ["--summary", "--limit", "2"],
    ["--search", "claim", "--limit", "0"],
    ["--search", "claim", "--max-chars", "20"],
    ["--search", "claim", "--text-cursor", "bad"],
    ["--top", "source.py", "--max-chars", "600"],
    ["--get", "d_" + "a" * 32, "--cursor", "bad"],
])
def test_invalid_cli_combinations_are_explicit(repo_root, tmp_path, args):
    result = subprocess.run(
        [sys.executable, str(repo_root / "skills/shadow-frog-viewer/shadow-viewer.py"), *args],
        cwd=tmp_path, capture_output=True, text=True, encoding="utf-8",
    )
    assert result.returncode == 2 and "error:" in result.stderr
