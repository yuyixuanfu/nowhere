"""Bake content_src/ into nowhere/content.db (SQLite).

Steps:
  1. Walk content_src/, parse each file by type
  2. Run content_lint on each file; ERROR → stop
  3. Rebuild content.db from scratch
  4. Write manifest table (sha1 per source file)
  5. Print summary: cards per pool, lint warnings

Usage:
    python tools/bake_content.py
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import sqlite3
import sys

# GBK console fix
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

_REPO = pathlib.Path(__file__).resolve().parent.parent
_SRC = _REPO / "content_src"
_DB = _REPO / "nowhere" / "content.db"

# Import lint
sys.path.insert(0, str(_REPO / "tools"))
from content_lint import lint_all, lint_phenology, lint_txt_pool, LintResult


def _sha1(fp: pathlib.Path) -> str:
    return hashlib.sha1(fp.read_bytes()).hexdigest()


def _bake_phenology(conn: sqlite3.Connection, fp: pathlib.Path,
                    result: LintResult) -> int:
    """Bake phenology.json into cards table. Returns card count."""
    raw = fp.read_text(encoding="utf-8")
    data = json.loads(raw)
    events = data.get("events", {})
    count = 0
    for hemi_key, hemi in events.items():
        for band, months in hemi.items():
            key = f"{hemi_key}/{band}"
            for month_str, entries in months.items():
                for entry in entries:
                    text = entry.get("text", "")
                    if not text:
                        continue
                    # Build constraints JSON from entry metadata
                    constraints = {}
                    cz = entry.get("climate_zone")
                    if cz:
                        constraints["climate_zone"] = cz
                    lb = entry.get("lat_band")
                    if lb:
                        constraints["lat_band"] = lb
                    oc = entry.get("ocean")
                    if oc:
                        constraints["ocean"] = oc
                    hum = entry.get("humidity")
                    if hum:
                        constraints["humidity"] = hum
                    co = entry.get("coast_only")
                    if co:
                        constraints["coast_only"] = co
                    me = entry.get("max_elev")
                    if me:
                        constraints["max_elev"] = me
                    hemi_val = entry.get("hemisphere")
                    if hemi_val:
                        constraints["hemisphere"] = hemi_val
                    lm = entry.get("lat_min")
                    if lm:
                        constraints["lat_min"] = lm
                    conn.execute(
                        "INSERT OR IGNORE INTO cards(pool, key, subkey, text, constraints, source_file) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        ("phenology", key, month_str, text,
                         json.dumps(constraints, ensure_ascii=False) if constraints else None,
                         str(fp.relative_to(_SRC)))
                    )
                    count += 1
    return count


def _bake_txt_pool(conn: sqlite3.Connection, fp: pathlib.Path,
                   pool_name: str, result: LintResult) -> int:
    """Bake a .txt pool file into cards. Returns card count."""
    raw = fp.read_text(encoding="utf-8")
    count = 0
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        text = line
        if text.startswith("[") and "] " in text:
            text = text[text.index("] ") + 2:]
        if not text:
            continue
        conn.execute(
            "INSERT OR IGNORE INTO cards(pool, key, subkey, text, constraints, source_file) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (pool_name, None, None, text, None,
             str(fp.relative_to(_SRC)))
        )
        count += 1
    return count


def bake() -> None:
    """Full bake: lint → rebuild db → write manifest → print summary."""
    if not _SRC.is_dir():
        print(f"ERROR: content_src/ not found at {_SRC}", file=sys.stderr)
        sys.exit(1)

    # Step 1-2: Lint everything first
    result = lint_all(_SRC)
    for w in result.warnings:
        print(w, file=sys.stderr)
    if not result.ok:
        for e in result.errors:
            print(e, file=sys.stderr)
        print(f"\nBAKE ABORTED: {len(result.errors)} lint error(s)", file=sys.stderr)
        sys.exit(1)

    # Step 3: Rebuild db
    _DB.parent.mkdir(parents=True, exist_ok=True)
    if _DB.exists():
        _DB.unlink()

    conn = sqlite3.connect(str(_DB))
    conn.executescript("""
        CREATE TABLE pools (
            name TEXT PRIMARY KEY,
            entry_points TEXT NOT NULL,
            reader TEXT NOT NULL,
            description TEXT
        );
        CREATE TABLE cards (
            id INTEGER PRIMARY KEY,
            pool TEXT NOT NULL REFERENCES pools(name),
            key TEXT,
            subkey TEXT,
            text TEXT NOT NULL,
            constraints TEXT,
            source_file TEXT NOT NULL,
            UNIQUE(pool, key, subkey, text)
        );
        CREATE TABLE manifest (
            source_file TEXT PRIMARY KEY,
            sha1 TEXT NOT NULL
        );
        CREATE INDEX idx_cards_pool_key ON cards(pool, key);
        CREATE INDEX idx_cards_pool_subkey ON cards(pool, subkey);
    """)

    pool_counts: dict[str, int] = {}

    # Bake phenology
    pheno_fp = _SRC / "phenology.json"
    if pheno_fp.exists():
        n = _bake_phenology(conn, pheno_fp, result)
        pool_counts["phenology"] = n
        conn.execute(
            "INSERT INTO pools(name, entry_points, reader, description) VALUES(?,?,?,?)",
            ("phenology",
             "server._check_phenology",
             "nowhere.server",
             "Phenology events by hemisphere/band/month")
        )
        conn.execute(
            "INSERT OR REPLACE INTO manifest(source_file, sha1) VALUES(?,?)",
            ("phenology.json", _sha1(pheno_fp))
        )

    # Bake discovery_city pools
    disc_dir = _SRC / "discovery_city"
    if disc_dir.is_dir():
        for fp in sorted(disc_dir.glob("*.txt")):
            cc = fp.stem
            pool_name = f"discovery_city_{cc}"
            n = _bake_txt_pool(conn, fp, pool_name, result)
            pool_counts[pool_name] = n
            conn.execute(
                "INSERT INTO pools(name, entry_points, reader, description) VALUES(?,?,?,?)",
                (pool_name,
                 f"describe._load_scenes('discovery_city_{cc}')",
                 "nowhere.server",
                 f"Country-specific city discovery pool for {cc}")
            )
            conn.execute(
                "INSERT OR REPLACE INTO manifest(source_file, sha1) VALUES(?,?)",
                (f"discovery_city/{cc}.txt", _sha1(fp))
            )

    conn.commit()
    conn.close()

    # Step 5: Print summary
    total_cards = sum(pool_counts.values())
    total_pools = len(pool_counts)
    print(f"\nBake complete: {total_pools} pools, {total_cards} total cards")
    print(f"Lint warnings: {len(result.warnings)}")
    print(f"Database: {_DB}")
    print()
    for pool, count in sorted(pool_counts.items()):
        print(f"  {pool}: {count} cards")


if __name__ == "__main__":
    bake()
