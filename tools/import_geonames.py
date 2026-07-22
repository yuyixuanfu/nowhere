"""GeoNames 全量 → places.db(SQLite + FTS5)。

跑一次: python tools/import_geonames.py [样本行数]
全量约 1200 万行,过滤后留 P/S/H/T/V 五类,几分钟。
产物: portal/data/places.db(gitignore,买断数据)。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / "portal" / "data" / "packs" / "allc" / "allCountries.txt"
DB = ROOT / "portal" / "data" / "places.db"

KEEP_CLASSES = {"P", "S", "H", "T", "V"}


def main() -> None:
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    if DB.exists():
        DB.unlink()
    conn = sqlite3.connect(DB)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """CREATE TABLE places (
            id INTEGER PRIMARY KEY, name TEXT, ascii TEXT, alts TEXT,
            lat REAL, lon REAL, fclass TEXT, fcode TEXT, country TEXT, pop INTEGER)"""
    )
    conn.execute("CREATE INDEX idx_ll ON places(lat, lon)")

    kept = 0
    with open(SRC, encoding="utf-8") as f:
        batch = []
        for n, line in enumerate(f, 1):
            if limit and n > limit:
                break
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 19 or parts[6] not in KEEP_CLASSES:
                continue
            pop = int(parts[14]) if parts[14].isdigit() else 0
            batch.append((int(parts[0]), parts[1], parts[2], parts[3],
                          float(parts[4]), float(parts[5]), parts[6], parts[7],
                          parts[8], pop))
            if len(batch) >= 5000:
                conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
                kept += len(batch)
                batch.clear()
                if kept % 200000 < 5000:
                    print(f"  {kept} rows...")
        if batch:
            conn.executemany("INSERT INTO places VALUES (?,?,?,?,?,?,?,?,?,?)", batch)
            kept += len(batch)

    print(f"inserted {kept}, building FTS...")
    conn.execute(
        "CREATE VIRTUAL TABLE places_fts USING fts5(name, ascii, alts, content='places', content_rowid='id')"
    )
    conn.execute("INSERT INTO places_fts(places_fts) VALUES('rebuild')")
    conn.commit()
    conn.close()
    print(f"done: {DB} ({kept} places)")


if __name__ == "__main__":
    main()
