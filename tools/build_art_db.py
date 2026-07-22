"""Build a local art database from the Met Museum's open API.

Fetches artwork metadata via the Met's public API, filters to public domain
items with images, and saves as a compact JSON for offline use.

Usage:
    python tools/build_art_db.py              # Fetch top works from key departments
    python tools/build_art_db.py --limit 5000 # Limit total artworks
"""
import argparse
import asyncio
import gzip
import io
import json
import pathlib
import sys
import urllib.request

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

DATA_DIR = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data"
OUTPUT = DATA_DIR / "art_met.json.gz"
API = "https://collectionapi.metmuseum.org/public/collection/v1"

# Key departments (IDs from Met API - verified)
DEPARTMENTS = {
    1: "American Decorative Arts",
    3: "Ancient West Asian Art",
    4: "Arms and Armor",
    5: "Arts of Africa, Oceania, and the Americas",
    6: "Asian Art",
    7: "The Cloisters",
    9: "Drawings and Prints",
    10: "Egyptian Art",
    11: "European Paintings",
    12: "European Sculpture and Decorative Arts",
    13: "Greek and Roman Art",
    14: "Islamic Art",
    15: "The Robert Lehman Collection",
    17: "Medieval Art",
    18: "Musical Instruments",
    19: "Photographs",
    21: "Modern Art",
}


def fetch_json(url: str) -> dict | None:
    """Fetch JSON from URL, return None on failure."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "nowhere-mcp/0.1"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def fetch_department_ids(dept_id: int) -> list[int]:
    """Get all object IDs for a department."""
    data = fetch_json(f"{API}/objects?departmentIds={dept_id}")
    if data and data.get("objectIDs"):
        return data["objectIDs"]
    return []


def fetch_object(obj_id: int) -> dict | None:
    """Fetch metadata for one object."""
    data = fetch_json(f"{API}/objects/{obj_id}")
    if not data:
        return None

    # Filter: must be public domain
    if not data.get("isPublicDomain"):
        return None

    # Filter: must have image
    image = data.get("primaryImageSmall", "")
    if not image:
        return None

    # Filter: must have title
    title = data.get("title", "").strip()
    if not title or title.lower() == "none":
        return None

    return {
        "id": data.get("objectID"),
        "title": title,
        "artist": data.get("artistDisplayName", "") or "佚名",
        "bio": data.get("artistDisplayBio", ""),
        "year": data.get("objectDate", ""),
        "dept": data.get("department", ""),
        "culture": data.get("culture", ""),
        "class": data.get("classification", ""),
        "country": data.get("country", ""),
        "image": image,
    }


async def build_database(limit: int = 10000) -> dict:
    """Build the art database from Met API."""
    import time

    print("Building Met Museum art database...")
    print(f"  Target: {limit} artworks from {len(DEPARTMENTS)} departments\n")

    all_artworks = []
    total_fetched = 0

    for dept_id, dept_name in DEPARTMENTS.items():
        if len(all_artworks) >= limit:
            break

        print(f"  [{dept_id:2d}] {dept_name}...")
        ids = fetch_department_ids(dept_id)
        if not ids:
            print(f"       No objects found")
            continue

        # Sample: take every Nth ID to get ~limit/len(DEPARTMENTS) per dept
        per_dept = max(100, limit // len(DEPARTMENTS))
        step = max(1, len(ids) // per_dept)
        sampled = ids[::step][:per_dept]

        dept_count = 0
        for obj_id in sampled:
            if len(all_artworks) >= limit:
                break
            total_fetched += 1

            art = fetch_object(obj_id)
            if art:
                all_artworks.append(art)
                dept_count += 1

            # Rate limit: ~5 requests per second
            if total_fetched % 5 == 0:
                time.sleep(0.2)

            if total_fetched % 100 == 0:
                print(f"       {total_fetched} fetched, {len(all_artworks)} kept...")

        print(f"       → {dept_count} artworks")

    print(f"\n  Total fetched: {total_fetched}")
    print(f"  Total kept: {len(all_artworks)}")

    # Build indexes
    by_culture: dict[str, list[int]] = {}
    by_dept: dict[str, list[int]] = {}
    by_country: dict[str, list[int]] = {}

    for i, art in enumerate(all_artworks):
        if art["culture"]:
            by_culture.setdefault(art["culture"].lower().strip(), []).append(i)
        if art["dept"]:
            by_dept.setdefault(art["dept"], []).append(i)
        if art["country"]:
            by_country.setdefault(art["country"].lower().strip(), []).append(i)

    result = {
        "artworks": all_artworks,
        "by_culture": by_culture,
        "by_dept": by_dept,
        "by_country": by_country,
        "count": len(all_artworks),
    }

    # Save compressed
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    json_bytes = json.dumps(result, ensure_ascii=False).encode("utf-8")
    with gzip.open(OUTPUT, "wb") as gz:
        gz.write(json_bytes)

    size_mb = OUTPUT.stat().st_size / 1024 / 1024
    print(f"\n  Saved: {OUTPUT}")
    print(f"  Size: {size_mb:.1f} MB (compressed)")
    print(f"  Artworks: {len(all_artworks)}")
    print(f"  Cultures: {len(by_culture)}")
    print(f"  Departments: {len(by_dept)}")

    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10000)
    args = parser.parse_args()
    asyncio.run(build_database(limit=args.limit))
