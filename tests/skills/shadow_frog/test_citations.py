"""Real SQLite and Git tests for local, multiprocess citation bookkeeping."""

from pathlib import Path
import hashlib
import json
import sqlite3
import subprocess
import sys
import time

import pytest

from tests.conftest import _load_script


HELPER = Path(__file__).resolve().parents[3] / "skills/shadow-frog/_citations.py"


@pytest.fixture
def citations():
    return _load_script(HELPER)


def test_identity_is_stable_without_mutable_metadata(citations):
    identity = citations.discovery_id("file", "src/a.py::run", " A claim with spacing. ")
    assert identity == citations.discovery_id("file", "src/a.py::run", "A claim with spacing.")
    assert identity != citations.discovery_id("file", "src/a.py::run", "A different claim.")
    assert identity != citations.discovery_id("file", "src/a.py::other", "A claim with spacing.")
    assert identity != citations.discovery_id("preference", "src/a.py::run", "A claim with spacing.")
    assert citations.discovery_id("cross", "_cross/a.md", "Claim", ["b::f", "a::g"]) == (
        citations.discovery_id("cross", "_cross/a.md", "Claim", ["a::g", "b::f"])
    )


@pytest.mark.parametrize("text", ['Key `a  b` is accepted.', 'Key "a  b" is accepted.', "Indented:\n    value"])
def test_identity_preserves_literal_whitespace(citations, text):
    assert citations.discovery_id("file", "a::f", text) != citations.discovery_id(
        "file", "a::f", " ".join(text.split()),
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
    outcomes = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=40)
            outcomes.append((process.returncode, stdout, stderr))
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.communicate(timeout=10)
    assert all(code == 0 for code, _, _ in outcomes), outcomes
    assert citations.CitationStore(path, ".shadow").scores([identity])[identity] == 180


def test_wal_writes_keep_full_synchronization_and_checkpoint_limits(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    identity = citations.discovery_id("file", "a::f", "Claim")
    store.record([identity], "first")
    with store._connection() as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert db.execute("PRAGMA synchronous").fetchone()[0] >= 2
        assert db.execute("PRAGMA journal_size_limit").fetchone()[0] == citations.JOURNAL_BYTES
        assert db.execute("PRAGMA wal_autocheckpoint").fetchone()[0] == citations.CHECKPOINT_PAGES
    store.record([identity], "second")
    assert store.scores([identity])[identity] == 2


def test_active_reader_does_not_block_committing_citations(citations, tmp_path):
    path = tmp_path / "citations.sqlite3"
    store = citations.CitationStore(path, ".shadow")
    identity = citations.discovery_id("file", "a::f", "Claim")
    store.record([identity], "initial")
    reader = sqlite3.connect(path)
    reader.execute("BEGIN")
    reader.execute("SELECT * FROM scores").fetchall()
    code = """
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _citations import CitationStore
store = CitationStore(Path(sys.argv[2]), ".shadow")
store.record([sys.argv[3]], "concurrent")
store.record([sys.argv[3]], "concurrent")
"""
    process = subprocess.Popen(
        [sys.executable, "-c", code, str(HELPER.parent), str(path), identity],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
    )
    try:
        # A held read snapshot no longer prevents the writer's commit.
        stdout, stderr = process.communicate(timeout=15)
        assert process.returncode == 0, stdout + stderr
        assert reader.execute("SELECT citation_score FROM scores").fetchone()[0] == 1
        reader.rollback()
    finally:
        reader.close()
        if process.poll() is None:
            process.terminate()
            process.communicate(timeout=10)
    assert store.scores([identity])[identity] == 2


def test_non_lock_errors_are_not_retried(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    attempts = []

    def invalid_sql(db):
        attempts.append(True)
        db.execute("INSERT INTO nonexistent_table VALUES (1)")

    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        store._operation(invalid_sql, write=True)
    assert len(attempts) == 1


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
        start = time.monotonic()
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            store.record([identity], "second")
        assert time.monotonic() - start < 0.5
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


def test_pagination_stores_query_hash_not_search_text(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    query = "distinctive search phrase about the code"
    cursor = store.save_page(query, "catalog", ["d_" + "a" * 32])
    with sqlite3.connect(store.path) as db:
        stored = db.execute("SELECT request FROM pages").fetchone()[0]
    assert stored != query and len(stored) == 64
    assert store.load_page(cursor, query, "catalog") == ["d_" + "a" * 32]
    assert query.encode() not in store.path.read_bytes()


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


def test_anonymous_reads_do_not_leave_retry_receipts(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    for _ in range(30):
        store.record(["a", "b"])
    assert store.scores(["a", "b"]) == {"a": 30, "b": 30}
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0


def test_explicit_receipts_expire_without_deleting_scores(citations, tmp_path):
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    store.record(["a"], "event")
    with store._connection() as db, db:
        db.execute("UPDATE events SET created=0")
    store.record(["b"])
    assert store.scores(["a", "b"]) == {"a": 1, "b": 1}
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 0
    store.record(["a"], "event")
    assert store.scores(["a"]) == {"a": 2}


def test_receipt_capacity_rejects_new_events_atomically(citations, tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "MAX_RECEIPTS", 2)
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    with pytest.raises(ValueError, match="capacity"):
        store.record(["a", "b", "c"], "too-many")
    assert store.scores(["a", "b", "c"]) == {}
    store.record(["a", "b"], "fits")
    store.record(["a", "b"], "fits")
    assert store.scores(["a", "b"]) == {"a": 1, "b": 1}
    store.record(["c"])
    assert store.scores(["c"]) == {"c": 1}


def test_identical_snapshots_reuse_storage_and_cache_size_is_bounded(citations, tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "MAX_PAGES", 3)
    monkeypatch.setattr(citations, "MAX_PAGE_BYTES", 4096)
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    ids = [hashlib.sha256(str(index).encode()).hexdigest() for index in range(50)]
    token = store.save_page("same", "catalog", ids)
    assert store.save_page("same", "catalog", ids) == token
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 1
        assert db.execute("SELECT length(ids) FROM pages").fetchone()[0] < len(json.dumps(ids))
    for index in range(8):
        latest = store.save_page(f"query-{index}", "catalog", ids)
        with store._connection() as db:
            count, size = db.execute("SELECT COUNT(*), SUM(length(ids)) FROM pages").fetchone()
            assert count <= 3 and size <= 4096
    assert store.load_page(latest, "query-7", "catalog") == ids
    with pytest.raises(ValueError, match="expired|unavailable"):
        store.load_page(token, "same", "catalog")


def test_journal_size_remains_capped_after_large_snapshot_cleanup(citations, tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "JOURNAL_BYTES", 4096)
    store = citations.CitationStore(tmp_path / "citations.sqlite3", ".shadow")
    ids = [hashlib.sha256(str(index).encode()).hexdigest() for index in range(1000)]
    store.save_page("large", "catalog", ids)
    with store._connection() as db, db:
        db.execute("UPDATE pages SET created=0")
    store.save_page("small", "catalog", ["one"])
    journal = tmp_path / "citations.sqlite3-wal"
    assert not journal.exists() or journal.stat().st_size <= 4096


def test_production_budget_preserves_all_successful_writes(citations, tmp_path):
    path = tmp_path / "citations.sqlite3"
    code = """
import json, sqlite3, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from _citations import CitationStore, is_busy_error
store = CitationStore(Path(sys.argv[2]), ".shadow")
assert store.timeout == 0.1
ok = busy = 0
for index in range(30):
    try:
        store.record(["shared"])
    except sqlite3.OperationalError as exc:
        if not is_busy_error(exc):
            raise
        busy += 1
    else:
        ok += 1
print(json.dumps({"ok": ok, "busy": busy}))
"""
    workers = [
        subprocess.Popen(
            [sys.executable, "-c", code, str(HELPER.parent), str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
        )
        for _ in range(6)
    ]
    outcomes = []
    try:
        for worker in workers:
            stdout, stderr = worker.communicate(timeout=40)
            assert worker.returncode == 0, stderr
            outcomes.append(json.loads(stdout))
    finally:
        for worker in workers:
            if worker.poll() is None:
                worker.terminate()
                worker.communicate(timeout=10)
    successful = sum(item["ok"] for item in outcomes)
    assert successful > 0
    assert successful + sum(item["busy"] for item in outcomes) == 180
    assert store_score(citations, path) == successful


def store_score(citations, path):
    return citations.CitationStore(path, ".shadow").scores(["shared"]).get("shared", 0)
