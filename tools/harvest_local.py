"""收割本地物产: Wikidata 代表菜 + iNat 招牌植物,烘焙成离线 JSON。

跑一次,永久离线:
  python tools/harvest_local.py
产物(进 git,都很小):
  portal/data/food_by_country.json   {国家码: [{"zh","en"}]}
  portal/data/flora_by_place.json    {落点名: [{"zh","la"}]}
"""

from __future__ import annotations

import json
import pathlib
import time

import httpx

ROOT = pathlib.Path(__file__).resolve().parent.parent
DATA = ROOT / "portal" / "data"

def _sparql(query: str) -> list[dict]:
    """带重试的 SPARQL 查询,服务端截断/超时降级 LIMIT。"""
    for attempt, limit in enumerate((4000, 2500, 1200)):
        q = query.replace("__LIMIT__", str(limit))
        try:
            r = httpx.get(
                "https://query.wikidata.org/sparql",
                params={"query": q, "format": "json"},
                headers={
                    "User-Agent": "PortalMCP/0.1 (https://github.com/portal; contact@example.com) httpx",
                    "Accept": "application/sparql-results+json",
                },
                timeout=90.0,
            )
            r.raise_for_status()
            return r.json()["results"]["bindings"]
        except Exception as e:  # noqa: BLE001
            print(f"  sparql attempt {attempt + 1} failed: {e}")
            time.sleep(3)
    return []


FOOD_SPARQL = """
SELECT ?dish ?hans ?zh ?en ?desc ?descEn ?cc WHERE {
  ?dish wdt:P31 wd:Q746549 .
  ?dish wdt:P495 ?country .
  ?country wdt:P297 ?cc .
  OPTIONAL { ?dish rdfs:label ?hans . FILTER(LANG(?hans)="zh-hans") }
  OPTIONAL { ?dish rdfs:label ?zh . FILTER(LANG(?zh)="zh") }
  OPTIONAL { ?dish rdfs:label ?en . FILTER(LANG(?en)="en") }
  OPTIONAL { ?dish schema:description ?desc . FILTER(LANG(?desc)="zh-hans") }
  OPTIONAL { ?dish schema:description ?descEn . FILTER(LANG(?descEn)="en") }
}
LIMIT __LIMIT__
"""


def harvest_food() -> None:
    rows = _sparql(FOOD_SPARQL)
    out: dict[str, dict[str, dict]] = {}
    for row in rows:
        cc = row["cc"]["value"]
        zh = row.get("hans", {}).get("value") or row.get("zh", {}).get("value", "")
        en = row.get("en", {}).get("value", "")
        desc = row.get("desc", {}).get("value") or row.get("descEn", {}).get("value", "")
        if not zh and not en:
            continue
        key = row["dish"]["value"]
        prev = out.setdefault(cc, {}).get(key)
        cand = {"zh": zh, "en": en, "desc": desc}
        if prev is None or (not prev["zh"] and zh):
            out[cc][key] = cand
    slim: dict[str, list[dict]] = {cc: list(d.values())[:20] for cc, d in out.items()}
    (DATA / "food_by_country.json").write_text(
        json.dumps(slim, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"food: {len(slim)} countries, {sum(len(v) for v in slim.values())} dishes")


def harvest_flora() -> None:
    pool = json.loads((DATA / "pool.json").read_text(encoding="utf-8"))
    out: dict[str, list[dict]] = {}
    with httpx.Client(timeout=15.0, headers={"User-Agent": "portal-mcp/0.1"}) as client:
        for spot in pool:
            name = spot["name_hint"]
            try:
                r = client.get(
                    "https://api.inaturalist.org/v1/observations/species_counts",
                    params={
                        "lat": spot["lat"],
                        "lng": spot["lon"],
                        "radius": 50,
                        "iconic_taxa": "Plantae",
                        "per_page": 12,
                        "locale": "zh-CN",
                    },
                )
                r.raise_for_status()
                results = r.json().get("results", [])
                plants = []
                for it in results:
                    taxon = it.get("taxon") or {}
                    zh = taxon.get("preferred_common_name", "")
                    la = taxon.get("name", "")
                    if la:
                        plants.append({"zh": zh, "la": la})
                out[name] = plants
                print(f"  {name}: {len(plants)} plants")
            except Exception as e:  # noqa: BLE001
                print(f"  {name}: FAIL {e}")
                out[name] = []
            time.sleep(1.1)  # iNat 限速 1/s
    (DATA / "flora_by_place.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"flora: {len(out)} places")


if __name__ == "__main__":
    harvest_food()
    harvest_flora()
