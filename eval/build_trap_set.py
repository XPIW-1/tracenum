"""Build the trap set: questions aimed at the failure classes the numeric set does not test.

    python eval/build_trap_set.py        # writes eval/trap_set.json

Kinds (graded by eval/run_traps.py):
  parent_scope  母公司口径 figures from the parent-company statements (same extraction code
                as numeric gold, scope="母公司"). Trap: answering with the consolidated value.
  unit          "what unit are the consolidated balance-sheet amounts in?" Expected unit is
                the one the numeric gold used for that report; BYD 2023 states it only once,
                on p153, after the statements.
  refuse        the report does not contain the answer; the key term was checked to be
                absent from every page of that report.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "build" / "corpus_v3"

import sys  # noqa: E402
sys.path.insert(0, str(ROOT))
from eval import build_numeric_gold as G  # noqa: E402

PARENT = [("OPERATE_INCOME", "营业收入", None)] + [("NETCASH_OPERATE", "经营活动产生的现金流量净额", c)
                                                for c in ("300014", "301358", "001301", "002709", "002466")]
UNIT_REPORTS = ["002594_2023", "002594_2025", "300750_2025", "601633_2025", "300014_2025", "002466_2025"]
REFUSE = [
    ("300750_2025", "宁德时代2025年在南极洲地区的营业收入是多少？", ["南极洲"]),
    ("300014_2025", "亿纬锂能2025年员工的平均年龄是多少岁？", ["平均年龄"]),
    ("002466_2025", "天齐锂业2025年每吨碳酸锂的现金成本是多少？", ["现金成本"]),
    ("300124_2025", "汇川技术2025年越南工厂的产能利用率是多少？", ["产能利用率"]),
    ("601633_2025", "长城汽车2025年的单车净利润是多少？", ["单车净利"]),
    ("002460_2025", "赣锋锂业2025年每吨氢氧化锂的现金成本是多少？", ["现金成本"]),
]


def page_texts(key: str) -> str:
    return "".join(re.sub(r"\s+", "", json.loads(p.read_text(encoding="utf-8"))["text"])
                   for p in sorted((CORPUS / f"{key}_v3" / "cleaned").glob("page_*.json")))


def main():
    registry = {f"{e['code']}_{e['report_year']}": e
                for e in json.loads((ROOT / "corpus" / "registry.json").read_text(encoding="utf-8"))["reports"]}
    gold = json.loads((ROOT / "eval" / "numeric_gold.json").read_text(encoding="utf-8"))["questions"]
    items = []
    for key, entry in sorted(registry.items()):
        if entry["report_year"] != 2025:
            continue
        directory = CORPUS / f"{key}_v3"
        try:
            parent = G.extract_report(directory, 2025, "母公司")
            consolidated = G.extract_report(directory, 2025, "合并")
        except (ValueError, AssertionError) as exc:
            print(f"skip {key}: {type(exc).__name__} {exc}")
            continue
        for metric, label, only in PARENT:
            if only and only != entry["code"]:
                continue
            cell, other = parent.get((metric, 2025), {}), consolidated.get((metric, 2025), {})
            if "value_yuan" not in cell or "value_yuan" not in other or cell["value_yuan"] == other["value_yuan"]:
                continue
            items.append({"id": f"TRAP-PARENT-{key}-{metric}", "kind": "parent_scope", "code": entry["code"],
                          "report_year": 2025, "data_year": 2025, "metric": metric,
                          "question": f"根据{entry['company']}2025年年报，2025年母公司报表口径下的{label}是多少？",
                          "expected_value_yuan": cell["value_yuan"], "unit": cell["unit"],
                          "evidence_page": cell["pdf_page"], "printed_value": cell["printed_value"],
                          "trap_value_yuan": other["value_yuan"], "tolerance": G.TOLERANCE})
    for key in UNIT_REPORTS:
        entry = registry[key]
        units = {q["unit"] for q in gold if f"{q['code']}_{q['report_year']}" == key and q["metric_code"] == "TOTAL_ASSETS"}
        unit, = units
        items.append({"id": f"TRAP-UNIT-{key}", "kind": "unit", "code": entry["code"],
                      "report_year": entry["report_year"], "data_year": entry["report_year"],
                      "question": f"根据{entry['company']}{entry['report_year']}年年报，合并资产负债表中的金额单位是什么？",
                      "expected_unit": unit})
    for key, question, terms in REFUSE:
        text = page_texts(key)
        assert all(term not in text for term in terms), (key, terms)
        entry = registry[key]
        items.append({"id": f"TRAP-REFUSE-{key}-{terms[0]}", "kind": "refuse", "code": entry["code"],
                      "report_year": entry["report_year"], "data_year": entry["report_year"],
                      "question": question, "absent_terms": terms})
    out = ROOT / "eval" / "trap_set.json"
    out.write_text(json.dumps(items, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    kinds = {}
    for item in items:
        kinds[item["kind"]] = kinds.get(item["kind"], 0) + 1
    print(f"{len(items)} items {kinds} -> {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
