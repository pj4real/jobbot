"""Thin sqlite layer. No ORM: the queries are the interesting part."""
from __future__ import annotations
import sqlite3
import json
from pathlib import Path
from contextlib import contextmanager
from .config import path

DB_PATH = path("data", "jobs.db")


_schema_checked = False
schema_created = False   # True if this process had to build the schema itself
schema_migrated = False  # True if it had to add columns a newer version wants


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # WAL is unsupported on some mounted filesystems; rollback journal is fine

    # Two ways a database can be behind, and both used to surface as a crash
    # in whatever command you happened to run:
    #   the file exists but has no tables, from an interrupted first init
    #   the tables exist but are missing columns a newer version added
    # Neither is worth making you diagnose, so both are repaired on the first
    # connection of the process.
    global _schema_checked
    if not _schema_checked:
        _schema_checked = True
        _ensure_schema(conn)
    return conn


# Columns added after the first release. Expressed here rather than as ALTER
# statements in schema.sql because ALTER TABLE ADD COLUMN is not idempotent,
# and every attempt to work around that by splitting the .sql file on
# semicolons broke on a comment sooner or later.
COLUMNS = [
    ("jobs", "triage", "TEXT"),
    ("jobs", "triage_at", "TEXT"),
    ("jobs", "sender_id", "INTEGER"),
    ("jobs", "resolved_at", "TEXT"),
    ("senders", "decided_by", "TEXT NOT NULL DEFAULT 'system'"),
]


def _ensure_schema(conn) -> None:
    global schema_created, schema_migrated

    had_jobs = conn.execute(
        "SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name='jobs'"
    ).fetchone()["n"]

    # every CREATE in the file is IF NOT EXISTS, so this is safe to rerun and
    # sqlite parses its own comments correctly, which is the whole point
    conn.executescript((Path(__file__).parent / "schema.sql").read_text())
    if not had_jobs:
        schema_created = True

    for table, column, decl in COLUMNS:
        try:
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        except sqlite3.OperationalError:
            continue                      # table not there yet, nothing to add
        if not cols or column in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        schema_migrated = True
    conn.commit()


@contextmanager
def tx():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init() -> None:
    """Idempotent. Connecting already repairs the schema, so this exists to be
    an explicit, obvious thing you can run."""
    conn = connect()
    try:
        _ensure_schema(conn)
    finally:
        conn.close()


def log(entity: str, entity_id, kind: str, **payload) -> None:
    with tx() as c:
        c.execute(
            "INSERT INTO events (entity, entity_id, kind, payload_json) VALUES (?,?,?,?)",
            (entity, entity_id, kind, json.dumps(payload, default=str)),
        )


def upsert_source(kind: str, name: str, config_json: str = "{}") -> int:
    with tx() as c:
        c.execute(
            "INSERT INTO sources (kind, name, config_json) VALUES (?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind",
            (kind, name, config_json),
        )
        return c.execute("SELECT id FROM sources WHERE name=?", (name,)).fetchone()["id"]


def set_cursor(source_id: int, cursor: str) -> None:
    with tx() as c:
        c.execute("UPDATE sources SET last_cursor=? WHERE id=?", (cursor, source_id))


def get_cursor(source_id: int):
    with tx() as c:
        r = c.execute("SELECT last_cursor FROM sources WHERE id=?", (source_id,)).fetchone()
        return r["last_cursor"] if r else None


def add_raw(source_id: int, external_id: str, body: str, **kw) -> bool:
    """Returns True if this was new. Duplicates are silently skipped, which is
    what lets you rerun any collector as often as you like."""
    with tx() as c:
        cur = c.execute(
            "INSERT OR IGNORE INTO raw_items "
            "(source_id, external_id, subject, sender, body, url, received_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (source_id, external_id, kw.get("subject"), kw.get("sender"),
             body, kw.get("url"), kw.get("received_at")),
        )
        return cur.rowcount > 0


def table_names() -> list[str]:
    with tx() as c:
        return sorted(r["name"] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
            " AND name NOT LIKE 'sqlite_%'"))


def counts() -> dict:
    q = {
        "raw_items": "SELECT COUNT(*) n FROM raw_items",
        "raw_unprocessed": "SELECT COUNT(*) n FROM raw_items WHERE processed=0",
        "jobs": "SELECT COUNT(*) n FROM jobs",
        "companies": "SELECT COUNT(*) n FROM companies",
        "contacts_verified": "SELECT COUNT(*) n FROM contacts WHERE verified=1",
        "drafts": "SELECT COUNT(*) n FROM drafts",
        "resumes": "SELECT COUNT(*) n FROM resumes",
        "form_maps": "SELECT COUNT(*) n FROM form_maps",
    }
    out = {}
    with tx() as c:
        for k, sql in q.items():
            out[k] = c.execute(sql).fetchone()["n"]
        for row in c.execute(
            "SELECT status, COUNT(*) n FROM applications GROUP BY status"
        ):
            out[f"app:{row['status']}"] = row["n"]
        out["sent_today"] = c.execute(
            "SELECT COUNT(*) n FROM send_log WHERE day = date('now','localtime')"
        ).fetchone()["n"]
    return out
