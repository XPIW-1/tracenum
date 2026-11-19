"""Run numeric gold questions through the expansion pipeline (S3) and grade them.

    python eval/run_numeric.py --set eval/numeric_pilot24.json --out eval/results/pilot24_<date>.json

Each question makes real DeepSeek + reranker calls. Grading is automatic and strict:
  correct      - a number in the final answer equals the gold value within tolerance
                 (after converting 元/千元/万元/亿元), and the answer did not refuse;
  refused      - the pipeline declined (no number);
  wrong        - a number was given but none matches.
Citation: whether the gold evidence page is among the pages the answer cites.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

UNITS = {"亿元": Decimal("100000000"), "亿": Decimal("100000000"), "万元": Decimal("10000"), "万": Decimal("10000"),
         "千元": Decimal("1000"), "元": Decimal("1")}
NUM = re.compile(r"(-?\d[\d,，]*(?:\.\d+)?)\s*(亿元|亿|万元|千元|元|万)?")


def stated_numbers(text: str) -> list[tuple[Decimal, str | None]]:
    """(value as written, unit word or None) for every number in the text."""
    out = []
    for raw, unit in NUM.findall(text or ""):
        try:
            out.append((Decimal(raw.replace(",", "").replace("，", "")), unit or None))
        except Exception:
            continue
    return out


def numbers_in(text: str) -> list[Decimal]:
    return [v for v, _ in stated_numbers(text)]


def grade(answer: str, expected: Decimal, tolerance: dict, printed_unit: str | None) -> bool:
    """Tolerance follows the unit the answer states (gold: 元 exact; 万元/亿元 0.5% relative).
    千元 and unit-less numbers are read in the statement's printed unit and must be exact
    to that precision (half a unit)."""
    tolerance = tolerance or {}
    for value, unit in stated_numbers(answer):
        unit = unit or printed_unit
        if unit not in UNITS:
            continue
        yuan = value * UNITS[unit]
        diff = abs(yuan - expected)
        key = {"亿": "亿元", "万": "万元"}.get(unit, unit)
        rule = tolerance.get(key) or {}
        if "relative" in rule and expected and diff / abs(expected) <= Decimal(str(rule["relative"])):
            return True
        if diff <= max(Decimal(str(rule.get("absolute_yuan", 0))), UNITS[unit] / 2 if unit != "元" else Decimal("0.005")):
            return True
    return False


def cited_pages(answer: str) -> set[int]:
    return {int(p) for p in re.findall(r"第\s*(\d+)\s*页", answer or "")}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--set", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    import expansion as X
    import pipeline as P

    data = json.loads(Path(args.set).read_text(encoding="utf-8"))
    questions = data if isinstance(data, list) else data.get("questions", data.get("records", []))
    if args.limit:
        questions = questions[:args.limit]
    P.PIPELINE.load()
    results = []
    for i, q in enumerate(questions, 1):
        info = X.route(q["code"], int(q["report_year"]), int(q["data_year"]))
        question = X.build_question(info, q["question"])
        started = time.time()
        try:
            final = X.run_expansion(P.PIPELINE.namespace, info, question)
            answer = final.get("draft_answer") or ""
            error = None
        except Exception as exc:  # recorded, never hidden
            final, answer, error = {}, "", f"{type(exc).__name__}: {exc}"
        expected = Decimal(str(q["expected_value_yuan"]))
        ok = grade(answer, expected, q.get("tolerance"), q.get("unit"))
        refused = (not error) and not numbers_in(answer)
        pages = cited_pages(answer)
        row = {"n": i, "question": q["question"], "code": q["code"], "report_year": q["report_year"],
               "data_year": q["data_year"], "metric": q.get("metric"), "akshare_check": q.get("akshare_check"),
               "expected_yuan": str(expected), "evidence_page": q.get("evidence_page"),
               "verdict": "error" if error else ("correct" if ok else ("refused" if refused else "wrong")),
               "cites_gold_page": q.get("evidence_page") in pages, "cited_pages": sorted(pages),
               "rounds": final.get("retrieval_count"), "tokens": final.get("total_tokens"),
               "stop": final.get("stop_reason"), "seconds": round(time.time() - started, 1),
               "answer": answer[:600], "error": error}
        results.append(row)
        print(f"{i:>2} {row['verdict']:<8} {q['code']} {q['report_year']}/{q['data_year']} {q.get('metric')} "
              f"pages={row['cited_pages']} gold_p={row['evidence_page']} tokens={row['tokens']}", flush=True)
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    from collections import Counter
    c = Counter(r["verdict"] for r in results)
    print(json.dumps({"n": len(results), **c, "accuracy": round(c["correct"] / len(results), 3),
                      "cites_gold_page": sum(r["cites_gold_page"] for r in results),
                      "tokens": sum(r["tokens"] or 0 for r in results)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
