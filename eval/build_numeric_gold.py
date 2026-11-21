"""Build report-specific numeric gold from the local v3 corpus and AKShare cache."""

import json
import re
import unicodedata
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
METRICS = {
    "OPERATE_INCOME": ("营业收入", "利润表", "profit", r"营业收入"),
    "OPERATE_COST": ("营业成本", "利润表", "profit", r"营业成本"),
    "PARENT_NETPROFIT": (
        "归属于母公司股东的净利润", "利润表", "profit",
        r"归属于母公司(?:所有者|股东)(?:的)?净利润",
    ),
    "NETCASH_OPERATE": (
        "经营活动产生的现金流量净额", "现金流量表", "cashflow",
        r"经营活动产生的现金流量净额",
    ),
    "TOTAL_ASSETS": ("资产总计", "资产负债表", "balance", r"资产总计"),
    "TOTAL_LIABILITIES": ("负债合计", "资产负债表", "balance", r"负债合计"),
}
UNITS = {"元": 0, "千元": 3, "万元": 4}
STATUSES = ("match", "restated_in_next_report", "mismatch", "not_found")
TOLERANCE = {"元": {"absolute_yuan": 0}, "万元": {"relative": 0.005}, "亿元": {"relative": 0.005}}
# Statement-wide unit declarations are found by the parser (qc.json "document_unit"),
# e.g. BYD 2023 p153; used only when a statement table prints no unit of its own.
CONTINUATIONS = {"002812_2025_v3": (101, "3、合并利润表", 102, "财务费用", 103, "4、母公司利润表")}
METRIC_LABELS = ("营收", "营业成本", "归母净利润", "经营现金流净额", "资产总计", "负债合计")
STATUS_LABELS = {"match": "一致", "restated_in_next_report": "下一年报比较期一致",
                 "mismatch": "不一致", "not_found": "未定位"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)


def normalized(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def parse_amount(printed):
    value = normalized(printed).replace("−", "-")
    if value.startswith("(") and value.endswith(")"):
        value = "-" + value[1:-1]
    if not re.fullmatch(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?", value):
        raise ValueError(f"Not a printed amount: {printed!r}")
    return Decimal(value.replace(",", ""))


def to_yuan(printed, unit):
    return parse_amount(printed).scaleb(UNITS[unit])


def label_matches(label, metric):
    label = re.sub(
        r"^(?:[一二三四五六七八九十\d]+[、.]|\([一二三四五六七八九十\d]+\)|(?:减|加|其中):)+",
        "", normalized(label),
    )
    label = re.sub(r"\((?:净?亏损|损失).*?号填列\)$", "", label)
    return re.fullmatch(METRICS[metric][3], label) is not None


def equal_to_printed(cell, reference):
    if reference is None:
        return False
    return abs(Decimal(cell["value_yuan"]) - reference) <= max(Decimal("0.5"), half_printed_step(cell))


def half_printed_step(cell):
    amount = to_yuan(cell["printed_value"], cell["unit"])
    return Decimal("0.5").scaleb(amount.as_tuple().exponent)


def raw_page(directory, page):
    return read_json(directory / "raw" / f"page_{page:04d}.json")


def recover_continuation(directory, segments, table_rows):
    if directory.name not in CONTINUATIONS:
        return {}
    header_page, title, page, first_row, next_page, next_title = CONTINUATIONS[directory.name]
    assert title in raw_page(directory, header_page)["text"]
    assert next_title in raw_page(directory, next_page)["text"]
    assert raw_page(directory, page)["text"].splitlines()[1].startswith(first_row)
    segment, = (s for s in segments.values()
                if s["statement_type"] == "利润表" and s["end_page"] == header_page)
    cleaned = read_json(directory / "cleaned" / f"page_{page:04d}.json")
    table, = cleaned["tables"]
    assert not table["period_columns"]
    header = next(t for t in read_json(directory / "cleaned" / f"page_{header_page:04d}.json")["tables"]
                  if t["context"]["segment_id"] == segment["segment_id"])
    assert header["column_count"] == table["column_count"]
    row_ids = {row["row_id"] for row in table["rows"]}
    assert row_ids <= table_rows[page, table["table_id"]]
    return {(page, table["table_id"]): (segment, header["period_columns"], header_page)}


def extract_report(directory, year, scope="合并"):
    segments = {
        segment["segment_id"]: segment
        for segment in read_json(directory / "segments.json")
        if segment["scope"] == scope
    }
    table_rows = defaultdict(set)
    with (directory / "chunks.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            meta = json.loads(line)["metadata"]
            if meta.get("type") == "table":
                table_rows[meta["page"], meta["table_id"]].update(meta["row_ids"])
    continuation = recover_continuation(directory, segments, table_rows)
    declared = read_json(directory / "qc.json").get("document_unit") or {}
    unit_note = (declared["page"], declared["text"], declared["unit"]) if declared.get("status") == "found" else None
    if unit_note:
        note_page, note_text, _ = unit_note
        assert note_text in re.sub(r"\s+", "", raw_page(directory, note_page)["text"])

    candidates = defaultdict(list)
    problems = defaultdict(set)
    pages_by_statement = defaultdict(set)
    for segment in segments.values():
        pages_by_statement[segment["statement_type"]].update(
            range(segment["start_page"], segment["end_page"] + 1))
    checked_pages = set().union(*pages_by_statement.values(), {page for page, _ in continuation})
    for page in sorted(checked_pages):
        cleaned = read_json(directory / "cleaned" / f"page_{page:04d}.json")
        for table in cleaned["tables"]:
            row_ids = table_rows.get((page, table["table_id"]))
            if not row_ids:
                continue
            context = table["context"]
            recovered = continuation.get((page, table["table_id"]))
            if recovered:
                segment, periods, header_page = recovered
                pages_by_statement[segment["statement_type"]].add(page)
            else:
                segment = segments.get(context["segment_id"])
                if not segment:
                    continue
                periods = table["period_columns"]
            unit = context["unit"] or segment["unit"]
            unit_evidence = {}
            if not unit and unit_note:
                note_page, note_text, unit = unit_note
                unit_evidence = {"unit_evidence_page": note_page, "unit_evidence_text": note_text}
            metrics = [key for key, spec in METRICS.items() if spec[1] == segment["statement_type"]]
            if not metrics or not periods:
                continue
            label_end = min(period["column"] for period in periods)
            for row in table["rows"]:
                if row["row_id"] not in row_ids:
                    continue
                label = "".join(cell for cell in row["cells"][:label_end]
                                if cell and re.search(r"[\u3400-\u9fff]", cell))
                if not any(label_matches(label, metric) for metric in metrics):
                    label = next((cell for cell in row["cells"][:label_end]
                                  if cell and any(label_matches(cell, metric) for metric in metrics)), "")
                for metric in metrics:
                    if not label_matches(label, metric):
                        continue
                    for period in periods:
                        data_year = period["data_year"]
                        if data_year not in (year, year - 1):
                            continue
                        key = metric, data_year
                        column = period["column"]
                        printed = (row["cells"][column] or "") if column < len(row["cells"]) else ""
                        try:
                            value = to_yuan(printed, unit)
                        except (KeyError, ValueError):
                            problems[key].add(f"第{page}页金额或单位无法解析（{printed!r}，{unit}）")
                            continue
                        candidates[key].append({
                            "value_yuan": format(value, "f"),
                            "printed_value": printed,
                            "unit": unit,
                            "pdf_page": page,
                            "table_id": table["table_id"],
                            "row_id": row["row_id"],
                            "row_label": label,
                            "column_label": period.get("printed_label", period["label"]),
                            "column_row_id": period["row_id"],
                            "column_qualifier": period.get("printed_qualifier"),
                            **unit_evidence,
                            **({"column_evidence_page": header_page} if recovered else {}),
                        })
    result = {}
    for metric, (_, statement, _, _) in METRICS.items():
        for data_year in (year, year - 1):
            key = metric, data_year
            cells = candidates[key]
            if cells and len({Decimal(cell["value_yuan"]) for cell in cells}) == 1:
                result[key] = cells[0]
            else:
                pages = sorted({cell["pdf_page"] for cell in cells} or pages_by_statement[statement])
                detail = "；".join(sorted(problems[key]))
                if cells:
                    detail = "同一报表、年度和指标存在不同候选金额"
                result[key] = {
                    "checked_pages": pages,
                    "reason": detail or "在已定位的合并报表中未找到可绑定到该年度的指标单元格",
                }
    return result


def cross_check(cell, reference, next_comparative):
    if "value_yuan" not in cell:
        return "not_found"
    if equal_to_printed(cell, reference):
        return "match"
    if next_comparative and "value_yuan" in next_comparative and equal_to_printed(next_comparative, reference):
        return "restated_in_next_report"
    return "mismatch"


def precision_only(original, comparative):
    """Check whether the two printed values can represent the same underlying amount."""
    return (abs(Decimal(original["value_yuan"]) - Decimal(comparative["value_yuan"]))
            <= half_printed_step(original) + half_printed_step(comparative))


def make_question(record):
    year = record["data_year"]
    period = "（比较期）" if year < record["report_year"] else ""
    return (
        f"根据{record['company']} {record['report_year']} 年年报，"
        f"{year} 年{period}合并口径下的{record['metric']}是多少？"
    )


def annual_cache(code):
    cache = {}
    for kind in dict.fromkeys(spec[2] for spec in METRICS.values()):
        rows = read_json(ROOT / "build" / "akshare_cache" / f"{code}_{kind}.json")
        for row in rows:
            if row["REPORT_TYPE"] != "年报":
                continue
            key = kind, int(row["REPORT_DATE"][:4])
            if key not in cache or row["UPDATE_DATE"] > cache[key]["UPDATE_DATE"]:
                cache[key] = row
    return cache


def build_records(reports, extracted, caches):
    records = []
    for report in reports:
        code, year = report["code"], report["report_year"]
        for metric, (name, statement, kind, _) in METRICS.items():
            for data_year in (year, year - 1):
                cell = extracted[code, year][metric, data_year]
                next_cells = extracted.get((code, year + 1), {})
                later = next_cells.get((metric, data_year))
                cached = caches[code].get((kind, data_year), {})
                raw_reference = cached.get(metric)
                reference = Decimal(raw_reference) if raw_reference is not None else None
                status = cross_check(cell, reference, later)
                record = {
                    "id": f"{code}-{year}-{data_year}-{metric}",
                    "report_id": f"{code}_{year}_v3",
                    "code": code,
                    "company": report["company"],
                    "report_year": year,
                    "data_year": data_year,
                    "scope": "合并",
                    "statement_type": statement,
                    "metric": name,
                    "metric_code": metric,
                    **cell,
                    "akshare_value_yuan": format(reference, "f") if reference is not None else None,
                    "akshare_notice_date": cached.get("NOTICE_DATE"),
                    "akshare_update_date": cached.get("UPDATE_DATE"),
                    "akshare_check": status,
                    "usable": status in ("match", "restated_in_next_report"),
                }
                if status == "restated_in_next_report":
                    record["restated_in"] = {"report_year": year + 1, **later}
                    record["difference_kind"] = (
                        "compatible_printed_precision" if precision_only(cell, later) else "amount_changed"
                    )
                if status == "mismatch":
                    if later and "value_yuan" in later:
                        record["next_comparative"] = {"report_year": year + 1, **later}
                    record["reason"] = mismatch_reason(reference, later, year)
                    if (code, year, data_year, metric) == ("300124", 2025, 2025, "TOTAL_ASSETS"):
                        explain_inovance_assets(record, cached)
                if record["usable"]:
                    record.update(question=make_question(record), expected_value_yuan=cell["value_yuan"],
                                  tolerance=TOLERANCE, evidence_page=cell["pdf_page"])
                records.append(record)
    by_key = {(r["code"], r["report_year"], r["data_year"], r["metric_code"]): r for r in records}
    for original in records:
        if original["akshare_check"] != "restated_in_next_report":
            continue
        code, year, data_year, metric = (original[k] for k in ("code", "report_year", "data_year", "metric_code"))
        comparative = by_key[code, year + 1, data_year, metric]
        comparative["original_in"] = {
            "report_year": year, **extracted[code, year][metric, data_year],
        }
        comparative["difference_kind"] = original["difference_kind"]
    return records


def mismatch_reason(reference, later, year):
    if reference is None:
        return "AKShare 缓存缺少该年度的指标值"
    if later and "value_yuan" in later:
        return f"{year + 1} 年年报比较期金额与 AKShare 也不同"
    if later:
        return f"{year + 1} 年年报比较期单元格未能定位"
    return "语料内无下一年年报的同一数据年度比较期可核对"


def explain_inovance_assets(record, cached):
    directory = ROOT / "build" / "corpus_v3" / record["report_id"]

    def first_amount(page, label):
        pattern = re.compile(rf"^{re.escape(label)}\s+([\d,]+\.\d{{2}})\s")
        line_match = next(pattern.match(line["text"]) for line in raw_page(directory, page)["lines"]
                          if pattern.match(line["text"]))
        return parse_amount(line_match[1])

    total = Decimal(record["value_yuan"])
    difference = Decimal(record["akshare_value_yuan"]).quantize(Decimal("0.01")) - total
    asset_difference = (cached["OTHER_NONCURRENT_ASSET"].quantize(Decimal("0.01"))
                        - first_amount(117, "其他非流动资产"))
    equity_difference = (cached["UNASSIGN_RPOFIT"].quantize(Decimal("0.01"))
                         - first_amount(118, "未分配利润"))
    if (total == first_amount(9, "资产总额（元）") == first_amount(118, "负债和所有者权益总计")
            and difference == asset_difference == equity_difference and difference > 0):
        record["reason"] += (
            "。年报第 9 页主要会计数据、第 117 页资产总计及第 118 页负债和所有者权益总计一致。"
            f"缓存的其他非流动资产和未分配利润均比年报对应项目高 {difference:,.2f} 元"
            "（年报第 117、118 页），分别解释资产端和权益端的差额。差异来源未能从本地资料确认"
        )
        record["supporting_pages"] = [9, 117, 118]


def select_pilot(questions):
    by_company = defaultdict(list)
    for question in questions:
        by_company[question["code"]].append(question)
    pilot = []
    for rows in by_company.values():
        current = next(row for row in rows if row["report_year"] == row["data_year"] == 2025)
        pool = [row for row in rows if row["akshare_check"] == "restated_in_next_report"
                or row["data_year"] < row["report_year"]
                or (row["report_year"] == 2024 and row["metric_code"] != current["metric_code"])]
        second = min(pool, key=lambda row: (
            row.get("difference_kind") != "amount_changed",
            row["akshare_check"] != "restated_in_next_report",
            row["report_year"] == row["data_year"] and row["akshare_check"] != "restated_in_next_report",
            -row["report_year"],
            row["metric_code"] == current["metric_code"],
        ))
        pilot.extend((current, second))
    if len(pilot) != 24:
        raise ValueError("Expected two questions per company for 12 companies")
    return pilot


def display_amount(value):
    if value is None:
        return "缺失"
    return f"{Decimal(value):,f}"


def render_report(records, counts, pilot):
    lines = [
        "# v3 年报数值评测集",
        "",
        "覆盖 12 家公司、36 份年报、6 项合并报表指标。每份年报分别核查本期和比较期。"
        "资产负债表明确绑定为上年年末的期初列用于比较期。以下页码均为 PDF 物理页。",
        "",
        "标准答案来自对应年报的报表单元格。以十进制将原值换算为元，并与本地 AKShare 缓存比对。"
        "比对允许 0.5 元或半个印刷最小单位，取较大者。评测时，以元作答要求数值精确相等，"
        "以万元或亿元作答允许 0.5% 相对误差。JSON 的金额以十进制字符串保存。",
        "",
        "| 状态 | 本期 | 比较期 | 合计 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for status in STATUSES:
        current = sum(r["akshare_check"] == status and r["report_year"] == r["data_year"] for r in records)
        lines.append(f"| `{status}` | {current} | {counts[status] - current} | {counts[status]} |")
    lines.extend([
        "",
        f"全量题库收录 {sum(r['usable'] for r in records)} 道可用题。"
        f"试点收录 {len(pilot)} 道题，每家公司一题取自 2025 年年报本期，"
        "另一题优先取经下一年报确认的金额变动原值，其次选比较期。",
        "",
        "## 按年报和指标核对",
        "",
        "每格依次为本期、比较期。状态后的数字为对应 PDF 物理页。",
        "",
        "| 公司 | 年报 | " + " | ".join(METRIC_LABELS) + " |",
        "| --- | ---: | " + " | ".join(["---"] * len(METRICS)) + " |",
    ])
    by_report = defaultdict(list)
    for row in records:
        by_report[row["report_id"]].append(row)
    for rows in by_report.values():
        descriptions = []
        for row in rows:
            pages = "、".join(map(str, row.get("checked_pages", [row.get("pdf_page")])))
            descriptions.append(f"{STATUS_LABELS[row['akshare_check']]}（{pages or '无已定位页'}）")
        pairs = [" / ".join(descriptions[i:i + 2]) for i in range(0, len(descriptions), 2)]
        lines.append(f"| {rows[0]['company']} | {rows[0]['report_year']} | " + " | ".join(pairs) + " |")
    for kind, title in (("amount_changed", "金额重述"), ("compatible_printed_precision", "列报精度变化")):
        lines.extend([
            "", f"## {title}", "",
            "| 公司 | 数据年度 | 指标 | 原年报金额（元）及页码 | 下一年报比较期金额（元）及页码 |",
            "| --- | ---: | --- | --- | --- |",
        ])
        for row in records:
            if row["akshare_check"] != "restated_in_next_report" or row["difference_kind"] != kind:
                continue
            later = row["restated_in"]
            lines.append(
                f"| {row['company']} | {row['data_year']} | {row['metric']} | "
                f"{display_amount(row['value_yuan'])}（{row['report_year']} 年报第 {row['pdf_page']} 页） | "
                f"{display_amount(later['value_yuan'])}（{later['report_year']} 年报第 {later['pdf_page']} 页） |"
            )
        if kind == "compatible_printed_precision":
            lines.extend([
                "",
                "这组记录按比对规则归为 `restated_in_next_report`。两份年报的印刷精度区间相交，"
                "差值与计量单位及四舍五入相容，表中保留两个报表各自的印刷金额。",
            ])
        else:
            lines.extend([
                "",
                "比亚迪、长城汽车、亿纬锂能和汇川技术的 2024 年年报分别在第 184、219、140、157–158 页"
                "披露了 2023 年保证类质保费用重分类对营业成本的影响。长城汽车 2025 年年报第 146 页"
                "将 2024 年比较期标为已重述，并披露同一控制下企业合并。",
            ])
    lines.extend(["", "## 不一致和未定位记录", ""])
    for row in records:
        if row["usable"]:
            continue
        pages = "、".join(map(str, row.get("checked_pages", [row.get("pdf_page")])))
        lines.append(
            f"- {row['company']} {row['report_year']} 年报第 {pages} 页，"
            f"{row['data_year']} 年{row['metric']}（`{row['akshare_check']}`）。{row['reason']}。"
        )
        if row["akshare_check"] == "mismatch":
            lines.append(
                f"  年报为 {display_amount(row['value_yuan'])} 元，"
                f"AKShare 为 {display_amount(row['akshare_value_yuan'])} 元。"
            )
    if all(row["usable"] for row in records):
        lines.append("全部目标单元格均已定位并完成核对。")
    return "\n".join(lines) + "\n"


def write_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8", newline="\n")


def main():
    reports = read_json(ROOT / "corpus" / "registry.json")["reports"]
    extracted = {
        (r["code"], r["report_year"]): extract_report(
            ROOT / "build" / "corpus_v3" / f"{r['code']}_{r['report_year']}_v3", r["report_year"])
        for r in reports
    }
    caches = {code: annual_cache(code) for code in dict.fromkeys(r["code"] for r in reports)}
    records = build_records(reports, extracted, caches)
    counts = Counter(record["akshare_check"] for record in records)
    questions = [record for record in records if record["usable"]]
    pilot = select_pilot(questions)
    output = ROOT / "eval"
    write_json(output / "numeric_gold.json", {
        "status_counts": {status: counts[status] for status in STATUSES},
        "questions": questions,
        "excluded": [record for record in records if not record["usable"]],
    })
    write_json(output / "numeric_pilot24.json", pilot)
    (output / "NUMERIC_GOLD_REPORT.md").write_text(render_report(records, counts, pilot),
                                                   encoding="utf-8", newline="\n")
    print(" | ".join(f"{status}={counts[status]}" for status in STATUSES))


if __name__ == "__main__":
    main()
