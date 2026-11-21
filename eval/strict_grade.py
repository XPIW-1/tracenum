"""Strict re-grading of numeric results (no model calls; reads a results file).

The original grade() passes an answer if ANY number in it matches the gold value. That
lets an answer through that states several candidates (both years, or a wrong headline
figure next to the right one). Strict grading asks for the answer's headline figure:

  result amounts - in the answer's conclusion (text after an "答案：/最终答案：" line, else
             the answer after any leading revision note): bold amounts and the amount after
             each "="; if there are none, the first amount. Years, page numbers,
             percentages and unit-conversion factors are not amounts.
  strict   - lenient correct AND one of the result amounts equals gold (same tolerance).
  mixed    - lenient correct but the gold value only appears as an operand or aside.

    python eval/strict_grade.py eval/results/full431_20261120.json [--out report.json]
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_numeric import UNITS, grade  # noqa: E402

CONCLUSION = re.compile(r"^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*|__)?(?:答案|最终答案|最终回答|修正后(?:的)?答案)(?:\*\*|__)?[ \t]*[：:]", re.M)
REVISION_HEAD = re.compile(r"^\s*(?:#{1,6}\s*)?(?:\*\*)?(?:修正说明|核对与修正说明|核对说明|复核结果|已核对|经核对)")
AMOUNT = re.compile(r"(?<![\d.第页])(-?\d[\d,，]*(?:\.\d+)?)\s*(亿元|亿|万元|千元|元|万)?(?![\d.]*\s*(?:%|％|年|页|月|日|号|行|列|个|家|项|倍))")
PAGE_REF = re.compile(r"(?:第|物理页|PDF\s*物理页|页码)\s*\d+\s*(?:页)?")


def conclusion(answer: str) -> str:
    m = CONCLUSION.search(answer or "")
    if m:
        return answer[m.end():]
    text = answer or ""
    if REVISION_HEAD.match(text):
        parts = re.split(r"\n\s*\n", text, maxsplit=1)
        text = parts[1] if len(parts) > 1 else text
    return text


CONVERSION_FACTORS = {"1000", "10000", "100000000"}
BOLD = re.compile(r"\*\*([^*]+)\*\*")
EQUALS = re.compile(r"[=＝]([^=＝\n]*)")


def candidates(answer: str, printed_unit: str | None) -> list:
    """Amounts the answer presents as its result: bold amounts and the amount after each
    '='. Empty when the answer has neither; then its first amount is the result."""
    text = PAGE_REF.sub(" ", conclusion(answer))
    found = [first_amount(part, printed_unit) for part in [*BOLD.findall(text), *EQUALS.findall(text)]]
    found = [f for f in found if f]
    return found or [f for f in [first_amount(text, printed_unit)] if f]


def first_amount(text: str, printed_unit: str | None):
    for raw, unit in AMOUNT.findall(text):
        digits = raw.replace(",", "").replace("，", "").lstrip("-")
        if not unit and (len(digits.split(".")[0]) < 4 or re.fullmatch(r"(19|20)\d\d", digits)):
            continue  # small counts, years
        if digits in CONVERSION_FACTORS:
            continue  # "1万元=10,000元"
        try:
            value = Decimal(raw.replace(",", "").replace("，", ""))
        except Exception:
            continue
        return value, unit or printed_unit
    return None


def strict_row(row: dict, gold: dict) -> dict:
    expected = Decimal(row["expected_yuan"])
    lenient = row["verdict"] == "correct"
    heads = candidates(row.get("answer") or "", gold.get("unit"))
    head_ok = any(unit in UNITS and grade(f"{value}{unit}", expected, gold.get("tolerance"), gold.get("unit"))
                  for value, unit in heads)
    verdict = row["verdict"] if not lenient else ("strict" if head_ok else "mixed")
    # A result amount near the gold value but not equal to it (e.g. the other year's figure
    # stated as a result): the answer is ambiguous even if it also states the right one.
    conflicts = [f"{v}{u}" for v, u in heads if u in UNITS and expected
                 and Decimal("0.5") <= abs(v * UNITS[u] / expected) <= 2
                 and not grade(f"{v}{u}", expected, gold.get("tolerance"), gold.get("unit"))]
    return {"id": row.get("id") or f"q{row.get('n')}", "lenient": row["verdict"], "strict_verdict": verdict,
            "conflicting_results": conflicts,
            "result_amounts": [f"{v}{u or ''}" for v, u in heads]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("results")
    ap.add_argument("--gold", default=str(Path(__file__).resolve().parent / "numeric_gold.json"))
    ap.add_argument("--out")
    args = ap.parse_args()
    rows = json.loads(Path(args.results).read_text(encoding="utf-8"))
    gold = {q["id"]: q for q in json.loads(Path(args.gold).read_text(encoding="utf-8"))["questions"]}
    out = [strict_row(r, gold.get(r.get("id"), {})) for r in rows]
    counts = {}
    for r in out:
        counts[r["strict_verdict"]] = counts.get(r["strict_verdict"], 0) + 1
    counts["with_conflicting_results"] = sum(1 for r in out if r["strict_verdict"] == "strict" and r["conflicting_results"])
    print(json.dumps(counts, ensure_ascii=False))
    if args.out:
        Path(args.out).write_text(json.dumps({"counts": counts, "rows": out}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
