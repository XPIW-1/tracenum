"""Run the trap set (eval/trap_set.json) through the expansion pipeline (S3) and grade it.

    python eval/run_traps.py --out eval/results/traps_<date>.json [--workers 3]

Makes real DeepSeek + reranker calls (28 questions, about 1 yuan). Grading per kind:
  parent_scope  correct  - a result amount equals the parent-company value (strict grading)
                trap     - a result amount equals the consolidated value instead
                refused / wrong otherwise
  unit          correct  - the first unit word in the conclusion is the expected unit
  refuse        correct  - no result amount and no invented figure: the answer declines
Every row also carries the answer check findings, the chunks sha256 and model fingerprint.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))
sys.path.insert(0, str(REPO / "scripts"))
sys.path.insert(0, str(REPO / "eval"))

import re  # noqa: E402

from run_numeric import UNITS, cited_pages, grade  # noqa: E402
from strict_grade import candidates, conclusion  # noqa: E402

UNIT_WORD = re.compile(r"美元|港元|百万元|千元|万元|亿元|元")
DECLINE = re.compile(r"未找到|没有(?:披露|找到|相关)|未披露|无法(?:确定|回答|找到|提供)|不包含|未提供")


def grade_item(item: dict, answer: str) -> str:
    kind = item["kind"]
    if kind == "parent_scope":
        heads = candidates(answer, item.get("unit"))
        hit = lambda value: any(u in UNITS and grade(f"{v}{u}", Decimal(value), item.get("tolerance"), item.get("unit"))
                                for v, u in heads)
        if hit(item["trap_value_yuan"]):  # the consolidated value as a result: trap, even next to the right one
            return "trap"
        if hit(item["expected_value_yuan"]):
            return "correct"
        return "refused" if not heads or DECLINE.search(answer) else "wrong"
    if kind == "unit":
        text = re.sub(r"第\s*\d+\s*页", " ", conclusion(answer))
        first = UNIT_WORD.search(text)
        return "correct" if first and first[0] == item["expected_unit"] else "wrong"
    if kind == "refuse":
        # Declines, and gives no figure at all (not even an estimate such as 「约32.5岁」/「85%」):
        # page references and years are not figures.
        text = re.sub(r"第\s*\d+\s*页|(?:19|20)\d\d\s*年(?:度)?|PDF物理页\s*\d+", " ", answer)
        return "correct" if DECLINE.search(answer) and not re.search(r"\d", text) else "wrong"
    raise ValueError(kind)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--set", default=str(REPO / "eval" / "trap_set.json"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=1)
    args = ap.parse_args()
    import answer_check
    import expansion as X
    import pipeline as P
    import provenance

    items = json.loads(Path(args.set).read_text(encoding="utf-8"))
    P.PIPELINE.load()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = {}
    if out.is_file():
        for row in json.loads(out.read_text(encoding="utf-8")):
            current = provenance.chunks_sha256(f"{row['code']}_{row['report_year']}")
            if row.get("verdict") != "error" and current and row.get("chunks_sha256") == current:
                done[row["id"]] = row
    results, lock = dict(done), threading.Lock()

    def save():
        ordered = [results[i["id"]] for i in items if i["id"] in results]
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(ordered, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(out)

    def run_one(n, item):
        info = X.route(item["code"], int(item["report_year"]), int(item["data_year"]))
        question = X.build_question(info, item["question"])
        started = time.time()
        try:
            final = X.run_expansion(P.PIPELINE.namespace, info, question)
            answer, error = final.get("draft_answer") or "", None
        except Exception as exc:  # recorded, never hidden
            final, answer, error = {}, "", f"{type(exc).__name__}: {exc}"
        record = provenance.run_record(final["observation_dir"]) if final.get("observation_dir") else {}
        row = {"n": n, "id": item["id"], "kind": item["kind"], "code": item["code"],
               "report_year": item["report_year"], "question": item["question"],
               "verdict": "error" if error else grade_item(item, answer),
               "cited_pages": sorted(cited_pages(answer)), "evidence_page": item.get("evidence_page"),
               "rounds": final.get("retrieval_count"), "tokens": final.get("total_tokens"),
               "stop": final.get("stop_reason"), "seconds": round(time.time() - started, 1),
               "answer": answer, "error": error,
               "blocking": answer_check.blocking(record.get("findings", [])),
               "chunks_sha256": final.get("collection_chunks_sha256"),  # the collection actually read
               **{k: v for k, v in record.items() if k != "run"},
               "observation_dir": Path(final["observation_dir"]).name if final.get("observation_dir") else None}
        with lock:
            results[item["id"]] = row
            save()
            print(f"{n:>3} {row['verdict']:<8} {item['id']} tokens={row['tokens']} [{len(results)}/{len(items)}]",
                  flush=True)

    todo = [(n, i) for n, i in enumerate(items, 1) if i["id"] not in done]
    print(f"{len(done)} already graded, {len(todo)} to run with {args.workers} worker(s)", flush=True)
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        for future in [pool.submit(run_one, n, i) for n, i in todo]:
            future.result()
    from collections import Counter
    rows = list(results.values())
    by_kind = {k: dict(Counter(r["verdict"] for r in rows if r["kind"] == k)) for k in ("parent_scope", "unit", "refuse")}
    print(json.dumps({"n": len(rows), "by_kind": by_kind, "blocking": sum(r["blocking"] for r in rows),
                      "tokens": sum(r["tokens"] or 0 for r in rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
