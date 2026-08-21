"""Content pipeline runtime entry point.

Reads from content.db (read-only). All data flows through this module.
Build with: python tools/bake_content.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3

_DB_PATH = pathlib.Path(__file__).resolve().parent / "content.db"
_SRC_PATH = pathlib.Path(__file__).resolve().parent.parent / "content_src"

_conn: sqlite3.Connection | None = None


def _db() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        _conn.row_factory = sqlite3.Row
    return _conn


def cards(pool: str, key: str | None = None,
          subkey: str | None = None) -> list[dict]:
    """Return list of {text, constraints} dicts for the given pool/key/subkey."""
    conn = _db()
    sql = "SELECT text, constraints FROM cards WHERE pool = ?"
    params: list = [pool]
    if key is not None:
        sql += " AND key = ?"
        params.append(key)
    if subkey is not None:
        sql += " AND subkey = ?"
        params.append(subkey)
    sql += " ORDER BY id"
    rows = conn.execute(sql, params).fetchall()
    result = []
    for row in rows:
        c = row["constraints"]
        result.append({
            "text": row["text"],
            "constraints": json.loads(c) if c else None,
        })
    return result


def fresh() -> bool:
    """Check manifest sha1 vs actual content_src files. True if all match."""
    conn = _db()
    rows = conn.execute("SELECT source_file, sha1 FROM manifest").fetchall()
    for row in rows:
        src_file = row["source_file"]
        expected = row["sha1"]
        fp = _SRC_PATH / src_file
        if not fp.exists():
            return False
        actual = hashlib.sha1(fp.read_bytes()).hexdigest()
        if actual != expected:
            return False
    return True
