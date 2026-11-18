"""Regression: does the hosted reranker order candidates exactly like the paper's local model?

Every logged retrieval round (backend/runs/*/retrieval.jsonl) holds the query, all 20
candidate texts and the local bge-reranker-v2-m3 scores. We resend the same candidates
to the hosted model and compare orders. No local model is needed.

    set SILICONFLOW_API_KEY=...   (never commit it)
    python scripts/compare_rerank.py --qids WSI-2025-005,BYD-2025-002 --out report.json

Release rule (agreed plan): live questions stay off unless, for every sampled round,
the ordered top-5 is identical. Anything else is reported round by round.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
from rerank import HostedReranker  # noqa: E402

RUNS = REPO / "backend" / "runs"
CACHE = REPO / "backend" / "cache" / "rerank"
TOP_K = 5


def kendall_tau(a: list[str], b: list[str]) -> float:
    pos = {x: i for i, x in enumerate(b)}
    n, concordant, discordant = len(a), 0, 0
    for i in range(n):
        for j in range(i + 1, n):
            if pos[a[i]] < pos[a[j]]:
                concordant += 1
            else:
                discordant += 1
    total = concordant + discordant
    return (concordant - discordant) / total if total else 1.0


def rounds(qids: set[str] | None):
    seen = set()
    for run in sorted(RUNS.iterdir()):
        path = run / "retrieval.jsonl"
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if qids and row.get("qid") not in qids:
                continue
            key = (row["query"], tuple(c["id"] for c in row["candidates"]))
            if key in seen:
                continue
            seen.add(key)
            yield run.name, row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--qids", default="", help="comma-separated question ids; empty = all logged rounds")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--max-chunks-per-doc", type=int, default=None)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    qids = {q for q in args.qids.split(",") if q} or None
    reranker = HostedReranker(os.environ.get("SILICONFLOW_API_KEY", ""), max_chunks_per_doc=args.max_chunks_per_doc)
    report = []
    for index, (run, row) in enumerate(rounds(qids)):
        if args.limit and index >= args.limit:
            break
        candidates = row["candidates"]
        pairs = [[row["query"], c["text"]] for c in candidates]
        # The cache key is the sha256 of the exact pairs the local model scored; a hit proves
        # the logged texts are complete (not truncated by the logger).
        cache_hit = (CACHE / (hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()
                              + ".json")).is_file()
        local_order = [c["id"] for c in sorted(row["reranked"], key=lambda c: c["rank"])]
        local_scores = {c["id"]: c["score"] for c in row["reranked"]}
        hosted = reranker.predict(pairs)
        ids = [c["id"] for c in candidates]
        # Stable sort, same tie rule as retrieval.py (sorted(..., reverse=True) on float score).
        hosted_order = [i for i, _ in sorted(zip(ids, hosted), key=lambda p: float(p[1]), reverse=True)]
        shift = max(abs(local_order.index(i) - hosted_order.index(i)) for i in ids)
        top_equal = local_order[:TOP_K] == hosted_order[:TOP_K]
        report.append({
            "run": run, "qid": row["qid"], "group": row.get("group"), "round": row.get("round"),
            "cache_hit": cache_hit, "top5_ordered_equal": top_equal,
            "top5_set_overlap": len(set(local_order[:TOP_K]) & set(hosted_order[:TOP_K])),
            "kendall_tau": round(kendall_tau(local_order, hosted_order), 4), "max_rank_shift": shift,
            "max_abs_score_diff": round(max(abs(local_scores[i] - float(s)) for i, s in zip(ids, hosted)), 4),
            "longest_candidate_chars": max(len(c["text"]) for c in candidates),
            "local_top5_pages": [next(c["page"] for c in candidates if c["id"] == i) for i in local_order[:TOP_K]],
            "hosted_top5_pages": [next(c["page"] for c in candidates if c["id"] == i) for i in hosted_order[:TOP_K]],
        })
        mark = "OK " if top_equal else "DIFF"
        print(f"{mark} {row['qid']:<13} {row.get('group')} r{row.get('round')} tau={report[-1]['kendall_tau']:.3f} "
              f"shift={shift} cache={'Y' if cache_hit else 'N'}", flush=True)

    ok = sum(r["top5_ordered_equal"] for r in report)
    summary = {"rounds": len(report), "top5_ordered_equal": ok,
               "all_equal": bool(report) and ok == len(report),
               "logged_texts_verified_by_cache": sum(r["cache_hit"] for r in report),
               "model": reranker.model, "max_chunks_per_doc": args.max_chunks_per_doc}
    print(json.dumps(summary, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps({"summary": summary, "rounds": report}, ensure_ascii=False, indent=1),
                                  encoding="utf-8")


if __name__ == "__main__":
    main()
