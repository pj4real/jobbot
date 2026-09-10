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


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        pass  # WAL is unsupported on some mounted filesystems; rollback journal is fine

    # An interrupted first init leaves the file present but empty, and every
    # caller then dies on "no such table". The file existing is not the same
    # as the schema existing, so check the thing that actually matters.
    global _schema_checked
    if not _schema_checked:
        _schema_checked = True
        have = conn.execute(
            "SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name='jobs'"
        ).fetchone()["n"]
        if not have:
            global schema_created
            schema_created = True
            sql = (Path(__file__).parent / "schema.sql").read_text()
            conn.executescript(sql)
            conn.commit()
    return conn


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
    """Idempotent. CREATE TABLE IF NOT EXISTS handles itself, but ALTER TABLE
    ADD COLUMN does not, so those run one at a time and a duplicate is fine."""
    sql = (Path(__file__).parent / "schema.sql").read_text()
    head, _, alters = sql.partition("-- v2 --")
    with tx() as c:
        c.executescript(head)
    for stmt in [s.strip() for s in alters.split(";") if s.strip()]:
        if not stmt.upper().startswith(("ALTER", "CREATE")):
            continue
        try:
            with tx() as c:
                c.execute(stmt)
        except sqlite3.OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


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
