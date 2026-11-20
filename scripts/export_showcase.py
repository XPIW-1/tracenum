"""Export the featured recordings and their source PDFs without loading models."""
from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
import expansion
import pipeline as P

RECORDINGS = ROOT / "backend" / "recordings"
OUTPUT = ROOT / "web" / "public" / "data"
SUMMARY_FIELDS = ("id", "recorded_at", "company", "question_id", "question", "strategies", "duration", "summary")
EVENT_FIELDS = {
    "meta": "question question_id paper strategies budget company report_year data_year",
    "start": "strategy",
    "node_start": "strategy node round",
    "progress": "strategy node round done total",
    "node": (
        "strategy node round latency total_tokens model_calls budget budget_stop "
        "query candidate_count selected_pages new_evidence evidence_pages answer "
        "generated_rounds feedback missing metric_audit stop_reason"
    ),
    "final": "strategy boundary result",
    "done": "",
}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")


def without_trace(value):
    if isinstance(value, dict):
        return {key: without_trace(item) for key, item in value.items() if key != "trace"}
    if isinstance(value, list):
        return [without_trace(item) for item in value]
    return value


def public_recording(recording_id, *, question_id=None, corpus_key=None):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", recording_id):
        raise ValueError(f"Invalid recording id: {recording_id!r}")
    record = read_json(RECORDINGS / f"{recording_id}.json")
    if (record["id"] != recording_id
            or (question_id is not None and record["question_id"] != question_id)
            or (corpus_key is not None and record["company"] != corpus_key)):
        raise ValueError(f"Featured recording identity mismatch: {recording_id}")

    events = []
    for item in record["events"]:
        event = item["event"]
        kind = event["type"]
        if kind == "usage":  # billing detail, never public
            continue
        if kind == "error":
            raise ValueError(f"Featured recording contains an error event: {recording_id}")
        if kind not in EVENT_FIELDS:
            raise ValueError(f"Unrecognized event type {kind!r} in {recording_id}")
        allowed = {"type", *EVENT_FIELDS[kind].split()}
        events.append({"t": item["t"], "event": without_trace({k: v for k, v in event.items() if k in allowed})})

    return {field: record[field] for field in SUMMARY_FIELDS} | {"events": events}


def write_featured(out, records):
    records.sort(key=lambda record: record["id"], reverse=True)
    showcase = {
        # Use the most recent source timestamp so identical inputs yield identical output.
        "generated_at": max((record["recorded_at"] for record in records), key=datetime.fromisoformat,
                            default=None),
        "featured": [
            {
                "question_id": record["question_id"],
                "company": record["company"],
                "question": record["question"],
                "recording_id": record["id"],
                "recorded_at": record["recorded_at"],
            }
            for record in records
        ],
        "model": P.FREEZE["model"]["alias"],
        "note": "精选问题来自已录制的真实运行，可离线回放。",
    }
    write_json(out / "showcase.json", showcase)
    write_json(out / "recordings" / "index.json", [
        {field: record[field] for field in SUMMARY_FIELDS} | {"featured": True}
        for record in records
    ])
    for record in records:
        write_json(out / "recordings" / f"{record['id']}.json", record)


def export_v3(out):
    corpora = []
    # Registry order follows the industry chain (整车 → 电池 → 材料 → 锂 → 电驱); years ascending.
    for (code, year), info in expansion.REPORTS.items():
        key = f"{code}_{year}"
        corpora.append({"key": key, "code": code, "company": info["company"], "report_year": year,
                        "pages": info["pages"], "pdf_url": f"/reports/{key}.pdf"})
    try:
        featured = read_json(RECORDINGS / "featured_v3.json")
    except FileNotFoundError:
        featured = {}
    records = []
    for key, rid in featured.items():
        expansion.route_key(key)
        records.append(public_recording(rid, corpus_key=key))
    write_json(out / "corpora.json", corpora)
    write_featured(out, records)
    return len(corpora), len(records)


def main():
    # Build the whole public folder in a fresh staging directory and swap it in only when
    # everything was written: a failed export never leaves a half-updated site, and a
    # recording taken off the featured list stops being served.
    import shutil
    out = OUTPUT.with_name(".data-staging")
    previous = OUTPUT.with_name(".data-previous")
    if out.exists():
        shutil.rmtree(out)
    corpora = [
        {"code": code, **{key: value for key, value in info.items() if key != "pdf_path"}}
        for code, info in P.CORPORA.items()
    ]
    questions = [
        {
            "question_id": q["question_id"],
            "company": P.COMPANY_TO_CODE[q["company"]],
            "question": q["question"],
            "question_type": q["question_type"],
            "metric": q.get("metric"),
            "boundary": q.get("boundary_question", False),
            "results": P.PAPER.get(q["question_id"], {}),
        }
        for q in P.QUESTIONS
    ]
    featured = read_json(RECORDINGS / "featured.json")
    records = [public_recording(rid, question_id=qid) for qid, rid in featured.items()]

    pdfs = {}
    for code, info in P.CORPORA.items():
        manifest_entry = next(row for row in P.MANIFEST if row["corpus_id"] == info["corpus_id"])
        data = Path(info["pdf_path"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != manifest_entry["pdf_sha256"]:
            raise ValueError(f"{code}: corpus PDF SHA-256 does not match the frozen manifest")
        pdfs[code] = data

    write_json(out / "corpora.json", corpora)
    write_json(out / "questions.json", questions)
    write_featured(out, records)
    v3_corpora, v3_records = export_v3(out / "v3")
    (out / "pdf").mkdir(parents=True, exist_ok=True)
    for code, data in pdfs.items():
        (out / "pdf" / f"{code}.pdf").write_bytes(data)
    if previous.exists():
        shutil.rmtree(previous)
    if OUTPUT.exists():
        OUTPUT.rename(previous)
    out.rename(OUTPUT)
    shutil.rmtree(previous, ignore_errors=True)
    print(f"Showcase exported: {len(corpora)} corpora, {len(questions)} questions, "
          f"{len(records)} featured recordings, {len(pdfs)} SHA-256-verified PDFs; "
          f"v3: {v3_corpora} corpora, {v3_records} featured recordings.")


if __name__ == "__main__":
    main()
