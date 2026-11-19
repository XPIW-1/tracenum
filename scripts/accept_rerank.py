"""Write the hosted-reranker release file (data/rerank_validation.json) that the API checks.

Release rule (owner decisions of 2026-11-18/19):
  - the regression report covers >= 10 logged rounds whose candidate texts were verified
    against the paper's cache; and
  - either every round reproduces the paper's exact top-5 order, or the owner accepted the
    measured deviation AND the hosted reranker is no worse end to end: in every hosted run
    of the featured questions, each question it misses is also missed by a control run
    with the paper's local reranker (same day, same DeepSeek service).
"Everything must hit the Gold" was dropped as the rule because it tests DeepSeek's state
of the day, not the reranker: on 2026-11-18 WSI-2025-008 was refused with either reranker.

    python scripts/accept_rerank.py --report deploy/validation/rerank_report.json \
        --hosted deploy/validation/e2e_hosted_run1.json deploy/validation/e2e_hosted_run2.json \
        --control deploy/validation/e2e_local_control.json --accepted-by "owner" \
        --out deploy/validation/rerank_validation.json
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def misses(run):
    return sorted(r["question_id"] for r in run["rows"] if not r["hit_gold"])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True, help="output of scripts/compare_rerank.py --out")
    parser.add_argument("--hosted", nargs="+", required=True, help="e2e_featured.py runs with the hosted reranker")
    parser.add_argument("--control", required=True, help="e2e_featured.py run with the local reranker")
    parser.add_argument("--accepted-by", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    report, control = load(args.report), load(args.control)
    hosted = [load(p) for p in args.hosted]
    summary, rounds = report["summary"], report["rounds"]
    if not control["reranker"].startswith("local:"):
        raise SystemExit("the control run did not use the local reranker")
    for run in hosted:
        if not run["reranker"].startswith("siliconflow:"):
            raise SystemExit("a hosted run did not use the hosted reranker")
        if {r["question_id"] for r in run["rows"]} != {r["question_id"] for r in control["rows"]}:
            raise SystemExit("hosted and control runs cover different questions")
    control_misses = set(misses(control))
    worse = sorted({q for run in hosted for q in misses(run)} - control_misses)
    if worse:
        raise SystemExit(f"hosted reranker is worse than the local control on: {worse}")
    diffs = [r for r in rounds if not r["top5_ordered_equal"]]
    summary["accepted"] = {
        "by": args.accepted_by,
        "at": datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"),
        "rule": "hosted misses must be a subset of local-control misses",
        "top5_ordered_equal_rate": round(summary["top5_ordered_equal"] / summary["rounds"], 3),
        "diff_rounds": len(diffs),
        "diff_same_top5_set": sum(r["top5_set_overlap"] == 5 for r in diffs),
        "hosted_runs": [{"ran_at": r["ran_at"], "hits": r["hits"], "total": r["total"], "misses": misses(r)}
                        for r in hosted],
        "control": {"ran_at": control["ran_at"], "hits": control["hits"], "total": control["total"],
                    "misses": sorted(control_misses)},
        "parity": True,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"summary": summary, "rounds": rounds}, ensure_ascii=False, indent=1) + "\n",
                   encoding="utf-8")
    print(json.dumps(summary["accepted"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
