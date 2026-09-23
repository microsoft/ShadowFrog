"""Real SQLite and Git tests for local, multiprocess citation bookkeeping."""

from pathlib import Path
import sqlite3
import subprocess
import sys

import pytest

from tests.conftest import _load_script


HELPER = Path(__file__).resolve().parents[3] / "skills/shadow-frog-viewer/_citations.py"


@pytest.fixture
def citations():
    return _load_script(HELPER)


def test_identity_is_stable_without_mutable_metadata(citations):
    identity = citations.discovery_id("file", "src/a.py::run", "A claim\nwith spacing.")
    assert identity == citations.discovery_id("file", "src/a.py::run", "A claim with spacing.")
    assert identity != citations.discovery_id("file", "src/a.py::run", "A different claim.")
    assert identity != citations.discovery_id("file", "src/a.py::other", "A claim with spacing.")
    assert identity != citations.discovery_id("preference", "src/a.py::run", "A claim with spacing.")
    assert citations.discovery_id("cross", "_cross/a.md", "Claim", ["b::f", "a::g"]) == (
        citations.discovery_id("cross", "_cross/a.md", "Claim", ["a::g", "b::f"])
    )


def test_zero_default_atomic_increment_and_event_retry(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    first = citations.discovery_id("file", "a::run", "One")
    second = citations.discovery_id("file", "a::run", "Two")
    assert store.scores([first, second]) == {}
    store.record([first, first], "event-a")
    store.record([first], "event-a")
    store.record([first, second], "event-b")
    assert store.scores([first, second]) == {first: 2, second: 1}
    other = citations.CitationStore(store.path, "examples/demo/.shadow")
    assert other.scores([first]) == {}


def test_processes_do_not_lose_increments(citations, tmp_path):
    path = tmp_path / "citations.sqlite3"
    identity = citations.discovery_id("file", "a::run", "A concurrent claim")
    code = """
import sys
sys.path.insert(0, sys.argv[1])
from _citations import CitationStore
from pathlib import Path
store = CitationStore(Path(sys.argv[2]), ".shadow", timeout=5)
for index in range(30):
    store.record([sys.argv[3]], f"{sys.argv[4]}-{index}")
"""
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(HELPER.parent), str(path), identity, str(worker)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        for worker in range(6)
    ]
    for process in processes:
        stdout, stderr = process.communicate(timeout=40)
        assert process.returncode == 0, stdout + stderr
    assert citations.CitationStore(path, ".shadow").scores([identity])[identity] == 180


def test_worktrees_share_scores_but_not_nested_shadows(citations, coupon_demo, tmp_path):
    source = coupon_demo / ".shadow"
    checkout = tmp_path / "linked"
    subprocess.run(
        ["git", "-C", str(coupon_demo), "worktree", "add", "--detach", str(checkout), "HEAD"],
        check=True, capture_output=True,
    )
    try:
        first = citations.CitationStore.for_shadow(source)
        second = citations.CitationStore.for_shadow(checkout / ".shadow")
        assert first == second
        identity = citations.discovery_id("file", "cart.py::calculate_total", "A claim")
        first.record([identity], "one")
        assert second.scores([identity]) == {identity: 1}
        nested = coupon_demo / "example/.shadow"
        nested.mkdir(parents=True)
        third = citations.CitationStore.for_shadow(nested)
        assert third.path == first.path and third.scope != first.scope
        assert third.scores([identity]) == {}
        status = subprocess.check_output(
            ["git", "-C", str(coupon_demo), "status", "--porcelain"], text=True,
        )
        assert status == ""
    finally:
        subprocess.run(
            ["git", "-C", str(coupon_demo), "worktree", "remove", str(checkout)],
            check=True, capture_output=True,
        )


def test_standalone_cache_stays_outside_shadow(citations, tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    shadow = tmp_path / "repo/.shadow"
    shadow.mkdir(parents=True)
    store = citations.CitationStore.for_shadow(shadow)
    identity = citations.discovery_id("file", "a::f", "Claim")
    store.record([identity], "event")
    assert not store.path.is_relative_to(shadow)
    assert list(shadow.iterdir()) == []
    assert store.scores([identity]) == {identity: 1}


def test_locked_ledger_fails_within_bounded_wait(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow", timeout=0.02)
    identity = citations.discovery_id("file", "a::f", "Claim")
    store.record([identity], "first")
    connection = sqlite3.connect(store.path)
    try:
        connection.execute("BEGIN EXCLUSIVE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record([identity], "second")
    finally:
        connection.rollback()
        connection.close()
    assert store.scores([identity])[identity] == 1


def test_cursor_freezes_ids_not_scores(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    ids = [citations.discovery_id("file", "a::f", text) for text in ("A", "B", "C")]
    cursor = store.save_page("request", "catalog", ids)
    store.record([ids[-1]], "later")
    assert store.load_page(cursor, "request", "catalog") == ids
    with pytest.raises(ValueError, match="changed"):
        store.load_page(cursor, "request", "different catalog")
    with pytest.raises(ValueError, match="query"):
        store.load_page(cursor, "different request", "catalog")
    with pytest.raises(ValueError, match="expired|unavailable"):
        store.load_page("0" * 32, "request", "catalog")


def test_expired_cursor_gives_restart_guidance(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    cursor = store.save_page("q", "c", ["d_" + "a" * 32])
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE pages SET created = 0")
    with pytest.raises(ValueError, match="expired.*rerun"):
        store.load_page(cursor, "q", "c")


def test_schema_does_not_reset_newer_database(citations, tmp_path):
    path = tmp_path / "citations.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA user_version = 99")
    with pytest.raises(ValueError, match="version"):
        citations.CitationStore(path, ".shadow").record(["d_" + "a" * 32], "event")
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 99


@pytest.mark.parametrize("event", ["", "x" * 129, "bad\nvalue"])
def test_invalid_event_id_fails_early(citations, tmp_path, event):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    with pytest.raises(ValueError, match="event"):
        store.record(["d_" + "a" * 32], event)
    assert not store.path.exists()
