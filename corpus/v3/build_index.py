"""Embed v3 chunks into a local Chroma database: one collection per annual report.

    python corpus/v3/build_index.py --ids 002594_2025,601633_2024     # or --all
    python corpus/v3/build_index.py --all --verify                   # counts only

Same embedding as the paper (BAAI/bge-base-zh-v1.5, normalised, CPU). Collection name
v3_<code>_<year>; collection metadata pins the corpus identity (registry PDF sha256,
chunks.jsonl sha256) so the expansion pipeline can verify it before every run.
Output: build/vector_v3 (copied to the server's data volume at deploy time).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "build" / "corpus_v3"
DB = Path(os.environ.get("TRACENUM_V3_DB") or REPO / "build" / "vector_v3")
REGISTRY = REPO / "corpus" / "registry.json"
EMBED_MODEL = "BAAI/bge-base-zh-v1.5"
BATCH = 64


def flat(meta: dict, chunk_id: str) -> dict:
    def s(v):
        return "" if v is None else (",".join(map(str, v)) if isinstance(v, list) else str(v))
    return {
        "page": int(meta["page"]), "type": meta.get("type", ""), "chunk_id": chunk_id,
        "corpus_id": meta["corpus_id"], "code": meta.get("code", ""), "company": meta["company"],
        "report_year": int(meta["report_year"]), "pdf_sha256": meta["pdf_sha256"],
        "table_id": s(meta.get("table_id")), "segment_id": s(meta.get("segment_id")),
        "statement_type": s(meta.get("statement_type")), "scope": s(meta.get("scope")),
        "scope_source": s(meta.get("scope_source")), "entity": s(meta.get("entity")),
        "unit": s(meta.get("unit")), "data_years": s(meta.get("data_years")),
        "numeric_answer_allowed": bool(meta.get("numeric_answer_allowed")),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--ids", default="")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    registry = {f"{e['code']}_{e['report_year']}": e for e in
                json.loads(REGISTRY.read_text(encoding="utf-8"))["reports"]}
    ids = sorted(registry) if args.all else [i for i in args.ids.split(",") if i]
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_chroma import Chroma
    embeddings = HuggingFaceEmbeddings(model_name=EMBED_MODEL, model_kwargs={"device": "cpu"},
                                       encode_kwargs={"normalize_embeddings": True})
    audit = []
    for rid in ids:
        entry = registry[rid]
        path = CORPUS / f"{rid}_v3" / "chunks.jsonl"
        if not path.is_file():
            print(f"skip {rid}: no chunks", flush=True)
            continue
        chunks_sha = hashlib.sha256(path.read_bytes()).hexdigest()
        chunks = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        name = f"v3_{rid}"
        store = Chroma(collection_name=name, persist_directory=str(DB), embedding_function=embeddings,
                       collection_metadata={"corpus_id": f"{rid}_v3", "code": entry["code"],
                                            "company": entry["company"], "report_year": entry["report_year"],
                                            "pdf_sha256": entry["sha256"], "chunks_sha256": chunks_sha,
                                            "hnsw:space": "l2"})
        meta = store._collection.metadata or {}
        if not args.verify:
            if store._collection.count() and meta.get("chunks_sha256") != chunks_sha:
                store.delete_collection()
                store = Chroma(collection_name=name, persist_directory=str(DB), embedding_function=embeddings,
                               collection_metadata={"corpus_id": f"{rid}_v3", "code": entry["code"],
                                                    "company": entry["company"], "report_year": entry["report_year"],
                                                    "pdf_sha256": entry["sha256"], "chunks_sha256": chunks_sha,
                                                    "hnsw:space": "l2"})
            if store._collection.count() != len(chunks):
                t0 = time.time()
                for start in range(0, len(chunks), BATCH):
                    batch = chunks[start:start + BATCH]
                    ids_b = [c.get("id") or c["metadata"]["chunk_id"] for c in batch]
                    store.add_texts(texts=[c["text"] for c in batch],
                                    metadatas=[flat(c["metadata"], i) for c, i in zip(batch, ids_b)], ids=ids_b)
                print(f"{rid}: {len(chunks)} chunks in {time.time() - t0:.0f}s "
                      f"({len(chunks) / max(time.time() - t0, 1e-9):.1f}/s)", flush=True)
        count = store._collection.count()
        audit.append({"id": rid, "collection": name, "chunks": len(chunks), "indexed": count,
                      "ok": count == len(chunks), "chunks_sha256": chunks_sha})
    (DB / "index_build.json").write_text(json.dumps({"embedding_model": EMBED_MODEL, "collections": audit},
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    bad = [a for a in audit if not a["ok"]]
    print(f"{len(audit) - len(bad)}/{len(audit)} collections complete")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
