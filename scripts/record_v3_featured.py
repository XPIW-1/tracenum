"""Record featured S3 answers using the live pipeline (makes real model calls).

    python scripts/record_v3_featured.py --items items.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def load_items(path, max_chars):
    import expansion

    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        raise ValueError("--items must contain a JSON array")
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict) or not isinstance(item.get("key"), str) or not isinstance(item.get("question"), str):
            raise ValueError(f"Item {i} must have a string key and question")
        data_year = item.get("data_year")
        if data_year is not None and type(data_year) is not int:
            raise ValueError(f"Item {i}: data_year must be an integer")
        text = " ".join(item["question"].split())
        if not 1 <= len(text) <= max_chars:
            raise ValueError(f"Item {i}: question must contain 1–{max_chars} characters")
        route_info = expansion.route_key(item["key"], data_year)
        yield item["key"], route_info, expansion.build_question(route_info, text)


def update_featured(path, key, recording_id):
    try:
        featured = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        featured = {}
    featured[key] = recording_id
    staged = path.with_suffix(".json.tmp")
    staged.write_text(json.dumps(featured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    staged.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--items", required=True, type=Path)
    args = parser.parse_args()

    from server import FEATURED_V3, MAX_QUESTION_CHARS, Tape, ask_meta, run_strategy

    try:
        items = list(load_items(args.items, MAX_QUESTION_CHARS))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for key, route_info, question in items:
        tape = Tape(key, question, ["S3"])
        tape.add(ask_meta(key, question, ["S3"], route_info=route_info))

        def record(event):
            if event["type"] != "usage":
                tape.add(event)

        run_strategy(key, question, "S3", record, route_info)
        tape.add({"type": "done"})
        rid = tape.save()
        if not rid:
            raise RuntimeError(f"No completed recording for {key}")
        update_featured(FEATURED_V3, key, rid)
        print(f"{key}: {rid}", flush=True)


if __name__ == "__main__":
    main()
