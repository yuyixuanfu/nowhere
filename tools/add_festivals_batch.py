"""Batch festival addition script for nowhere/data/festivals.json.

Reads new_festivals from a JSONL file and appends to festivals.json.
Usage: python tools/add_festivals_batch.py batch1.jsonl
"""
import json
import sys
import pathlib

DATA = pathlib.Path(__file__).resolve().parent.parent / "nowhere" / "data"
FESTIVALS = DATA / "festivals.json"


def load_festivals():
    with open(FESTIVALS, encoding="utf-8") as f:
        return json.load(f)


def add_batch(batch_file):
    fests = load_festivals()
    existing_names = {(f["name"], f["place"]) for f in fests}

    with open(batch_file, encoding="utf-8") as f:
        new_fests = json.load(f)

    added = 0
    skipped = 0
    for fest in new_fests:
        key = (fest["name"], fest["place"])
        if key in existing_names:
            skipped += 1
            continue
        fests.append(fest)
        existing_names.add(key)
        added += 1

    with open(FESTIVALS, "w", encoding="utf-8") as f:
        json.dump(fests, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Added {added}, skipped {skipped} duplicates. Total: {len(fests)}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python tools/add_festivals_batch.py batch.jsonl")
        sys.exit(1)
    add_batch(sys.argv[1])
