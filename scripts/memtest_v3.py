"""Server memory test for the v3 corpus (run inside the api container).

    python /app/tracenum/scripts/memtest_v3.py --questions 6

Phase 1 (free): load models, open all 36 v3 collections and run one similarity search in
each, so every HNSW index is resident — the worst case for memory.
Phase 2 (paid, ~0.05 yuan/question): run real S3 questions across different companies.
Memory is read from the container cgroup (memory.current / memory.peak) after each step.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def cgroup(name: str) -> int | None:
    try:
        return int(Path(f"/sys/fs/cgroup/{name}").read_text().split()[0])
    except (OSError, ValueError):
        return None


def mib(v):
    return None if v is None else round(v / 1048576)


def mark(label, log):
    row = {"step": label, "current_mib": mib(cgroup("memory.current")), "peak_mib": mib(cgroup("memory.peak")),
           "t": round(time.time(), 1)}
    log.append(row)
    print(json.dumps(row, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--questions", type=int, default=6)
    parser.add_argument("--out", default="/data/memtest_v3.json")
    args = parser.parse_args()
    log = []
    mark("start", log)
    import expansion as X
    import pipeline as P
    P.PIPELINE.load()
    mark("models loaded", log)
    for (code, year) in sorted(X.REPORTS):
        info = X.route(code, year)
        store = X.store_for(info, P.PIPELINE.namespace["corpus_embeddings"])
        store.similarity_search("营业收入", k=20)
    mark(f"all {len(X.REPORTS)} collections opened and queried", log)
    gold = json.loads((REPO / "eval" / "numeric_pilot24.json").read_text(encoding="utf-8"))
    gold = gold if isinstance(gold, list) else gold.get("questions", [])
    seen, picked = set(), []
    for q in gold:  # one question per company
        if q["code"] not in seen:
            seen.add(q["code"])
            picked.append(q)
    for q in picked[:args.questions]:
        info = X.route(q["code"], int(q["report_year"]), int(q["data_year"]))
        final = X.run_expansion(P.PIPELINE.namespace, info, X.build_question(info, q["question"]))
        print(f"  {q['code']} {q['metric']}: {(final.get('draft_answer') or '')[:80]!r}", flush=True)
        mark(f"question {q['code']} done", log)
    Path(args.out).write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
