"""End-to-end check: run the featured questions (S3) through the live pipeline and grade them.

Used twice: (1) to accept the hosted reranker (scripts/accept_rerank.py reads its output),
(2) as the post-deploy smoke test on the server. Each run makes real DeepSeek calls
(about 0.02-0.1 元 per question).

    python scripts/e2e_featured.py --out e2e.json

Grading uses backend/review_recordings.hit: the final answer must contain the Gold's key
figure, or decline for a boundary question. It is a quick screen, not blind scoring.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--fresh-cache", action="store_true",
                        help="score every round anew instead of reusing cached rerank scores")
    args = parser.parse_args()
    if args.fresh_cache:
        os.environ["TRACENUM_CACHE_DIR"] = tempfile.mkdtemp(prefix="tracenum-e2e-")

    import pipeline as P
    import review_recordings as RR
    featured = json.loads((REPO / "web/public/data/showcase.json").read_text(encoding="utf-8"))["featured"]
    rows, fingerprints = [], set()

    def sink(event):
        if event.get("type") == "usage" and event.get("system_fingerprint"):
            fingerprints.add(event["system_fingerprint"])

    for item in featured:
        question = next(q for q in P.QUESTIONS if q["question_id"] == item["question_id"])
        final = P.PIPELINE.run(question, "S3", sink)
        summary = P.summarize_final(final, P.COMPANY_TO_CODE[question["company"]])
        rows.append({"question_id": item["question_id"], "hit_gold": bool(RR.hit(item["question_id"], summary)),
                     "refused": summary.get("refused"), "cited_pages": summary.get("cited_pages"),
                     "rounds": summary.get("rounds"), "tokens": summary.get("total_tokens")})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    result = {"ran_at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
              "reranker": P.PIPELINE.reranker_label, "fingerprints": sorted(fingerprints),
              "hits": sum(r["hit_gold"] for r in rows), "total": len(rows), "rows": rows}
    Path(args.out).write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{result['hits']}/{result['total']} featured questions hit the Gold")
    sys.exit(0 if result["hits"] == result["total"] else 1)


if __name__ == "__main__":
    main()
