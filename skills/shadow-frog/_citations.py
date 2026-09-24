"""Shared local citation scores; Markdown remains the authoritative knowledge store."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import random
import re
import sqlite3
import subprocess
import time
import unicodedata
import zlib


PAGE_TTL = 24 * 60 * 60
RECEIPT_TTL = PAGE_TTL
MAX_RECEIPTS = 100_000
MAX_PAGES = 32
MAX_PAGE_BYTES = 8 * 1024 * 1024
JOURNAL_BYTES = 1024 * 1024
CHECKPOINT_PAGES = 256
SCHEMA_VERSION = 2


def discovery_id(kind, anchor, text, refs=()):
    """Content identity excludes status, provenance, labels, and usage metadata."""
    value = [kind, anchor, text.strip(), sorted(set(refs))]
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "d_" + digest[:32]


def canonical_path(path):
    """Use actual directory-entry spelling without folding distinct filesystem names."""
    resolved = Path(path).resolve()
    current = Path(resolved.anchor)
    for part in resolved.parts[1:]:
        requested = current / part
        if requested.exists():
            key = unicodedata.normalize("NFC", part).casefold()
            matches = []
            for child in current.iterdir():
                if child.name == part:
                    matches = [child]
                    break
                if unicodedata.normalize("NFC", child.name).casefold() == key and child.samefile(requested):
                    matches.append(child)
            if len(matches) != 1:
                raise ValueError(f"Cannot identify a unique filesystem path for {requested}")
            current = matches[0]
        else:
            current = requested
    return current


def validate_event_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise ValueError("event ID must be 1-128 letters, digits, or . _ : -")
    return value


def is_busy_error(exc):
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, "sqlite_errorcode", None)
    return (
        (code is not None and code & 0xff in (5, 6))  # SQLITE_BUSY / SQLITE_LOCKED
        or (code is None and str(exc) in ("database is locked", "database table is locked"))
    )


@dataclass(frozen=True)
class CitationStore:
    """One short-lived connection per operation; SQLite coordinates processes."""

    path: Path
    scope: str
    timeout: float = 0.1

    def __post_init__(self):
        object.__setattr__(self, "path", Path(self.path))
        if not isinstance(self.scope, str) or not self.scope:
            raise ValueError("citation scope must be nonempty")
        if self.timeout <= 0:
            raise ValueError("citation timeout must be positive")

    @classmethod
    def for_shadow(cls, shadow_dir):
        shadow = canonical_path(shadow_dir)
        env = os.environ.copy()
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE"):
            env.pop(name, None)
        env["LC_ALL"] = "C"
        result = subprocess.run(
            ["git", "-C", str(shadow), "rev-parse", "--show-toplevel", "--git-common-dir"],
            capture_output=True, text=True, encoding="utf-8", timeout=0.2, env=env,
        )
        if result.returncode == 0:
            root_text, common_text = result.stdout.rstrip("\n").split("\n")
            root = canonical_path(root_text)
            common = Path(common_text)
            if not common.is_absolute():
                common = shadow / common
            return cls(
                canonical_path(common) / "shadowfrog/citations.sqlite3",
                shadow.relative_to(root).as_posix(),
            )
        if "not a git repository" not in result.stderr.lower():
            raise ValueError(f"Cannot resolve Git citation storage: {result.stderr.strip()}")
        # Standalone shadows use untracked local state, not a sidecar in .shadow/.
        base = os.environ.get("LOCALAPPDATA" if os.name == "nt" else "XDG_STATE_HOME")
        state = Path(base) if base else Path.home() / ".local/state"
        identity = hashlib.sha256(os.fsencode(shadow)).hexdigest()
        return cls(state / "shadowfrog/citations" / f"{identity}.sqlite3", str(shadow))

    @contextmanager
    def _connection(self, *, timeout=None):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=self.timeout if timeout is None else timeout)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError(
                    f"Unsupported citation database version {version} at {self.path}; "
                    "use a matching helper or move this local cache aside to reset scores"
                )
            # Local worktrees share WAL so readers do not contend with each
            # small score update. FULL still synchronizes successful commits.
            mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            if mode != "wal":
                mode = db.execute("PRAGMA journal_mode=WAL").fetchone()[0]
            if mode != "wal":
                raise ValueError(f"Cannot enable local WAL citation storage (got {mode})")
            db.execute("PRAGMA synchronous=FULL")
            db.execute(f"PRAGMA journal_size_limit={JOURNAL_BYTES}")
            db.execute(f"PRAGMA wal_autocheckpoint={CHECKPOINT_PAGES}")
            if version == 0:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS scores (scope TEXT, id TEXT, "
                        "citation_score INTEGER NOT NULL CHECK(citation_score >= 0), PRIMARY KEY(scope, id))"
                    )
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS events (scope TEXT, event TEXT, id TEXT, "
                        "created REAL NOT NULL, PRIMARY KEY(scope, event, id))"
                    )
                    db.execute("CREATE INDEX IF NOT EXISTS event_expiry ON events(created)")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS pages (token TEXT PRIMARY KEY, scope TEXT, "
                        "request TEXT, catalog TEXT, ids BLOB, created REAL)"
                    )
                    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            yield db
        finally:
            db.close()

    def _operation(self, action, *, write=False):
        """Retry only lock contention; each write attempt is one atomic transaction."""
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                wait = min(0.01, max(0, deadline - time.monotonic()))
                with self._connection(timeout=wait) as db:
                    if write:
                        with db:
                            db.execute("BEGIN IMMEDIATE")
                            return action(db)
                    return action(db)
            except sqlite3.OperationalError as exc:
                remaining = deadline - time.monotonic()
                if not is_busy_error(exc) or remaining <= 0:
                    raise
                # Avoid letting repeated writers monopolize SQLite's polling slots.
                time.sleep(min(remaining, random.uniform(0.001, 0.01)))

    def scores(self, ids):
        if not self.path.exists() or not ids:
            return {}
        ids = list(dict.fromkeys(ids))

        def read(db):
            result = {}
            for offset in range(0, len(ids), 400):
                chunk = ids[offset:offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                result.update(db.execute(
                    f"SELECT id, citation_score FROM scores WHERE scope=? AND id IN ({placeholders})",
                    [self.scope, *chunk],
                ))
            return result
        return self._operation(read)

    def record(self, ids, event=None):
        """Count anonymous reads directly; retain explicit retry receipts for one day."""
        if event is not None:
            validate_event_id(event)
        ids = list(dict.fromkeys(ids))
        if not ids:
            return

        def update(db):
            now = time.time()
            cutoff = now - RECEIPT_TTL
            db.execute(
                "DELETE FROM events WHERE rowid IN "
                "(SELECT rowid FROM events WHERE created < ? LIMIT 1000)", (cutoff,),
            )
            receipt_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0] if event else 0
            for identity in ids:
                if event is not None:
                    prior = db.execute(
                        "SELECT created FROM events WHERE scope=? AND event=? AND id=?",
                        (self.scope, event, identity),
                    ).fetchone()
                    if prior and prior[0] >= cutoff:
                        continue
                    if prior is None:
                        if receipt_count >= MAX_RECEIPTS:
                            raise ValueError("Citation retry receipt capacity reached; wait for expiry or use --no-record")
                        receipt_count += 1
                    db.execute(
                        "INSERT INTO events VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(scope, event, id) DO UPDATE SET created=excluded.created",
                        (self.scope, event, identity, now),
                    )
                db.execute(
                    "INSERT INTO scores(scope, id, citation_score) VALUES (?, ?, 1) "
                    "ON CONFLICT(scope, id) DO UPDATE SET citation_score=citation_score+1",
                    (self.scope, identity),
                )
        self._operation(update, write=True)

    def save_page(self, request, catalog, ids):
        request = hashlib.sha256(request.encode("utf-8")).hexdigest()
        payload = zlib.compress(json.dumps(ids, separators=(",", ":")).encode("utf-8"))
        if len(payload) > MAX_PAGE_BYTES:
            raise ValueError("Result snapshot exceeds local pagination capacity; narrow the query")
        key = json.dumps([self.scope, request, catalog]).encode("utf-8") + payload
        token = hashlib.sha256(key).hexdigest()[:32]

        def publish(db):
            now = time.time()
            db.execute("DELETE FROM pages WHERE created < ?", (now - PAGE_TTL,))
            db.execute("DELETE FROM pages WHERE token=?", (token,))
            rows = db.execute("SELECT token, length(ids) FROM pages ORDER BY created DESC, token").fetchall()
            used = len(payload)
            for index, (old_token, size) in enumerate(rows, 1):
                used += size
                if index >= MAX_PAGES or used > MAX_PAGE_BYTES:
                    db.execute("DELETE FROM pages WHERE token=?", (old_token,))
            db.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                (token, self.scope, request, catalog, payload, now),
            )
        self._operation(publish, write=True)
        return token

    def load_page(self, token, request, catalog):
        request = hashlib.sha256(request.encode("utf-8")).hexdigest()
        if not self.path.exists():
            raise ValueError("Retrieval cursor expired or unavailable; rerun the query without --cursor")
        row = self._operation(
            lambda db: db.execute(
                "SELECT request, catalog, ids, created FROM pages WHERE token=? AND scope=?",
                (token, self.scope),
            ).fetchone()
        )
        if not row or row[3] < time.time() - PAGE_TTL:
            raise ValueError("Retrieval cursor expired or unavailable; rerun the query without --cursor")
        if row[0] != request:
            raise ValueError("Cursor belongs to a different query; reuse its original view and filters")
        if row[1] != catalog:
            raise ValueError("Shadow knowledge changed; rerun the query without --cursor")
        try:
            ids = json.loads(zlib.decompress(row[2]).decode("utf-8"))
        except (zlib.error, UnicodeError, ValueError, TypeError) as exc:
            raise ValueError("Invalid local pagination snapshot; rerun the query without --cursor") from exc
        if not isinstance(ids, list) or not all(isinstance(identity, str) for identity in ids):
            raise ValueError("Invalid local pagination snapshot; rerun the query without --cursor")
        return ids
