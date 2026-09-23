"""Local citation scores; Markdown remains the authoritative knowledge store."""

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import time
import uuid


PAGE_TTL = 24 * 60 * 60


def discovery_id(kind, anchor, text, refs=()):
    """Content identity excludes status, provenance, labels, and usage metadata."""
    value = [kind, anchor, " ".join(text.split()), sorted(set(refs))]
    digest = hashlib.sha256(
        json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return "d_" + digest[:32]


def validate_event_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", value):
        raise ValueError("event ID must be 1-128 letters, digits, or . _ : -")
    return value


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
        shadow = Path(shadow_dir).resolve()
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
            root = Path(root_text).resolve()
            common = Path(common_text)
            if not common.is_absolute():
                common = shadow / common
            return cls(
                common.resolve() / "shadowfrog/citations.sqlite3",
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
    def _connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=self.timeout)
        try:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1):
                raise ValueError(f"Unsupported citation database version {version}; use a compatible helper")
            if version == 0:
                with db:
                    db.execute("BEGIN IMMEDIATE")
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS scores (scope TEXT, id TEXT, "
                        "citation_score INTEGER NOT NULL CHECK(citation_score >= 0), PRIMARY KEY(scope, id))"
                    )
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS events (scope TEXT, event TEXT, id TEXT, "
                        "PRIMARY KEY(scope, event, id))"
                    )
                    db.execute(
                        "CREATE TABLE IF NOT EXISTS pages (token TEXT PRIMARY KEY, scope TEXT, "
                        "request TEXT, catalog TEXT, ids TEXT, created REAL)"
                    )
                    db.execute("PRAGMA user_version = 1")
            yield db
        finally:
            db.close()

    def scores(self, ids):
        if not self.path.exists() or not ids:
            return {}
        result = {}
        ids = list(dict.fromkeys(ids))
        with self._connection() as db:
            for offset in range(0, len(ids), 400):
                chunk = ids[offset:offset + 400]
                placeholders = ",".join("?" for _ in chunk)
                result.update(db.execute(
                    f"SELECT id, citation_score FROM scores WHERE scope=? AND id IN ({placeholders})",
                    [self.scope, *chunk],
                ))
        return result

    def record(self, ids, event):
        """Increment each emitted identity once per event, including across retries."""
        validate_event_id(event)
        ids = list(dict.fromkeys(ids))
        if not ids:
            return
        with self._connection() as db, db:
            db.execute("BEGIN IMMEDIATE")
            for identity in ids:
                inserted = db.execute(
                    "INSERT OR IGNORE INTO events(scope, event, id) VALUES (?, ?, ?)",
                    (self.scope, event, identity),
                )
                if inserted.rowcount:
                    db.execute(
                        "INSERT INTO scores(scope, id, citation_score) VALUES (?, ?, 1) "
                        "ON CONFLICT(scope, id) DO UPDATE SET citation_score=citation_score+1",
                        (self.scope, identity),
                    )

    def save_page(self, request, catalog, ids):
        token = uuid.uuid4().hex
        with self._connection() as db, db:
            db.execute("DELETE FROM pages WHERE created < ?", (time.time() - PAGE_TTL,))
            db.execute(
                "INSERT INTO pages VALUES (?, ?, ?, ?, ?, ?)",
                (token, self.scope, request, catalog, json.dumps(ids), time.time()),
            )
        return token

    def load_page(self, token, request, catalog):
        with self._connection() as db:
            row = db.execute(
                "SELECT request, catalog, ids, created FROM pages WHERE token=? AND scope=?",
                (token, self.scope),
            ).fetchone()
        if not row or row[3] < time.time() - PAGE_TTL:
            raise ValueError("Retrieval cursor expired or unavailable; rerun the query without --cursor")
        if row[0] != request:
            raise ValueError("Cursor belongs to a different query; reuse its original view and filters")
        if row[1] != catalog:
            raise ValueError("Shadow knowledge changed; rerun the query without --cursor")
        return json.loads(row[2])
