"""Accounting checks and the combined offline pilot report."""

import json
import re
from collections import Counter
from decimal import Decimal
from pathlib import Path

SCOPES = ("合并", "母公司")
TABLE_SCOPES = (*SCOPES, "不适用", "被投资主体", "unknown")
REQUIRED = ("资产负债表", "利润表", "现金流量表")
SCOPE_SOURCES = ("statement_title", "section_heading", "group_wording", "section_default", "disclosure_heading",
                 "investee_summary", "unknown")
UNKNOWN_REASONS = {
    "before_outline": "尚无可定位的章节标题",
    "no_scoped_section": "所在章节或同级附注未提供适用的口径",
    "metrics_without_attribution": "指标表内缺少可定位的归属口径行",
    "statement_title_without_unique_scope": "报表标题无法确定单一口径",
    "continuation_unconfirmed": "疑似报表续页未通过续接条件，且无可用章节口径",
    "fragment_without_scope": "单列碎片或备选表缺少独立口径证据",
}
BALANCE = {
    "assets": r"资产(?:总计|合计|总额)",
    "liabilities": r"负债(?:总计|合计|总额)",
    "equity": r"(?:所有者权益(?:\(或股东权益\)|（或股东权益）)?|股东权益)(?:总计|合计|总额)",
}
CASH = {
    "net": (r"现金及现金等价物(?:净?(?:增加|减少)|净?增加[/／]?[（(]减少[)）]|"
            r"净?[（(]减少[)）][/／]?增加)额"),
    "end": r"(?:期|年|本期|本年)末现金及现金等价物余额",
    "start": r"(?:期|年|本期|本年)初现金及现金等价物余额",
}
PREFIX = re.compile(r"^(?:(?:[一二三四五六七八九十\d]+[、.．]|加[:：])\s*)+")
AMOUNT = re.compile(r"[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)(?:\.\d+)?")


def amount(text):
    text = (text or "").strip().translate(str.maketrans("−－＋（）", "--+()"))
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1].strip()
    if AMOUNT.fullmatch(text):
        return Decimal(text.replace(",", ""))
    return None


def count_scope(tables):
    scopes, sources, unknown, allowed = Counter(), Counter(), Counter(), 0
    for table in tables:
        context = table["context"]
        count = table["chunk_count"]
        scope = context["scope"]
        scopes["合并" if scope == "合并(章节推定)" else scope] += count
        sources[context["scope_source"]] += count
        if scope == "unknown":
            unknown["non_fragment" if table["primary"] else "fragment"] += count
        if context["numeric_answer_allowed"]:
            allowed += count
    return scopes, sources, unknown, allowed


def round2_snapshot(out, previous):
    if "scope_counts" in previous or not previous.get("round1_baseline"):
        return previous.get("round2_baseline")
    scopes, sources, unknown, allowed = count_scope(
        table for number in range(1, previous["pages_processed"] + 1)
        for table in json.loads((out / "cleaned" / f"page_{number:04d}.json").read_text(encoding="utf-8"))["tables"]
    )
    if sum(scopes.values()) != previous["chunk_counts"]["table"]:
        raise ValueError(f"Round 2 scope counts disagree with QC for {out.name}")
    return {"scope_counts": dict(scopes), "scope_source_counts": dict(sources),
            "unknown_scope_split": dict(unknown), "numeric_allowed_table_chunks": allowed}


def summarize_page(raw, cleaned):
    """Keep only counters and source cells relevant to the requested checks."""
    scopes, sources, unknown_split, allowed = count_scope(cleaned["tables"])
    summary = {"page": raw["page"], "empty": not raw["text"].strip(), "errors": raw["errors"],
               "tables": len(cleaned["tables"]), "default_tables": len(raw["tables"]),
               "geometry_tables": len(raw["recovery_tables"]), "unknown": 0, "primary_unknown": 0,
               "glyph_period_tables": sum(t["method"] == "pdfplumber_glyph_period_grid" for t in cleaned["tables"]),
               "repairs": 0, "issues": Counter(issue["code"] for issue in cleaned["issues"]),
               "cells": [], "table_scopes": [], "scope_counts": scopes, "scope_source_counts": sources,
               "unknown_scope_split": unknown_split, "numeric_allowed_table_chunks": allowed,
               "unknown_chunks": Counter(), "continuation_rejections": [], "unbound_identity_rows": []}
    for table in cleaned["tables"]:
        context = table["context"]
        unknown = context["scope"] == "unknown"
        chunk_count = table["chunk_count"]
        summary["unknown"] += unknown
        summary["primary_unknown"] += unknown and table["primary"]
        if unknown:
            heading = context.get("outline_heading") or {}
            summary["unknown_chunks"][context["scope_unknown_reason"], heading.get("text"),
                                      "non_fragment" if table["primary"] else "fragment"] += chunk_count
        if rejected := table.get("blocked_continuation"):
            summary["continuation_rejections"].append({"page": raw["page"], **rejected})
        if table["primary"]:
            summary["table_scopes"].append({"page": raw["page"], "table_id": table["table_id"],
                                            "scope": context["scope"], "statement_type": context["statement_type"],
                                            "scope_source": context["scope_source"],
                                            "scope_evidence": context["scope_evidence"]})
        patterns = {"资产负债表": BALANCE, "现金流量表": CASH}.get(context["statement_type"], {})
        for row in table["rows"]:
            summary["repairs"] += sum(repair["rule"] == "same_cell_glyph_numeric_wrap"
                                      for repair in row["repairs"])
            if not context["numeric_answer_allowed"] or not patterns:
                continue
            labels = [re.sub(r"\s+", "", value or "") for value in row["cells"][:2]]
            if len(labels) == 2 and re.fullmatch(r"[一-龥]", labels[0]):
                labels.append("".join(labels))
            labels = [PREFIX.sub("", label).strip(":：") for label in labels]
            role = next((key for key, pattern in patterns.items()
                         if any(re.fullmatch(pattern, label) for label in labels)), None)
            if role is None:
                continue
            if not any(period["data_year"] is not None for period in table["period_columns"]):
                summary["unbound_identity_rows"].append({"page": raw["page"], "table_id": table["table_id"],
                                                         "row_id": row["row_id"], "role": role,
                                                         "segment_id": context["segment_id"]})
            for period in table["period_columns"]:
                column = period["column"]
                year = period["data_year"]
                if year is None or column >= len(row["cells"]):
                    continue
                value = amount(row["cells"][column])
                if value is not None:
                    summary["cells"].append({"value": str(value), "role": role, "data_year": year,
                                             "page": raw["page"], "table_id": table["table_id"],
                                             "row_id": row["row_id"], "column": column,
                                             "bbox": row["cell_bboxes"][column],
                                             "header_row_id": period["row_id"],
                                             "header_source_page": period.get("source_page", raw["page"]),
                                             "date_evidence": period.get("date_evidence"),
                                             "segment_id": context["segment_id"],
                                             "statement_type": context["statement_type"], "scope": context["scope"]})
    return summary


def identity_groups(segments, statement, scope):
    """Collect explicitly titled parts without changing their parser segments."""
    groups = []
    previous = None
    for segment in segments:
        if (segment["statement_type"], segment["scope"]) != (statement, scope):
            previous = None
            continue
        if (previous and segment["explicit_title_continued"] and
                segment["start_page"] == previous["end_page"] + 1 and
                segment["unit"] and segment["unit"] == previous["unit"] and
                segment["header_signature"] == previous["header_signature"] and segment["period_columns"]):
            groups[-1].append(segment)
        else:
            groups.append([segment])
        previous = segment
    return groups


def identities(report, cells, segments, rejections, unbound):
    for statement, patterns in (("资产负债表", BALANCE), ("现金流量表", CASH)):
        for scope in SCOPES:
            groups = identity_groups(segments, statement, scope)
            reference_pages = {p for group in groups for segment in group for p in
                               [segment["evidence"][0]["source_page"], *(t["page"] for t in segment["tables"])]}
            period_labels = sorted({period["label"] for group in groups for s in group
                                    for period in s["period_columns"]})
            for year in (report["report_year"], report["report_year"] - 1):
                selected = [c for c in cells if (c["statement_type"], c["scope"], c["data_year"])
                            == (statement, scope, year)]
                complete = []
                for group in groups:
                    group_ids = [segment["segment_id"] for segment in group]
                    by_role = {role: [c for c in selected if c["role"] == role and
                                     c["segment_id"] in group_ids] for role in patterns}
                    if all(len(matches) == 1 for matches in by_role.values()):
                        complete.append(({role: matches[0] for role, matches in by_role.items()}, group_ids))
                record = {"statement_type": statement, "scope": scope, "data_year": year,
                          "status": "not_found", "page_refs": sorted(reference_pages),
                          "candidates": selected}
                if len(complete) != 1:
                    segment_ids = {s["segment_id"] for group in groups for s in group}
                    table_ids = {t["table_id"] for group in groups for s in group for t in s["tables"]}
                    record["unbound_period_rows"] = [row for row in unbound if row["segment_id"] in segment_ids]
                    record["continuation_rejections"] = [r for r in rejections if r["source_table_id"] in table_ids]
                    record["page_refs"] = sorted(reference_pages | {r["page"] for r in record["continuation_rejections"]})
                    role_counts = {role: sum(c["role"] == role for c in selected) for role in patterns}
                    record["reason"] = ("未识别到对应口径的报表段" if not groups else
                                        f"可用报表段组 {len(complete)} 个，项目候选数 {role_counts}，"
                                        f"未绑定年份的项目 {len(record['unbound_period_rows'])} 行，"
                                        f"未接续的相邻表 {len(record['continuation_rejections'])} 张，"
                                        f"已识别表头 {'、'.join(period_labels) or '无'}")
                else:
                    refs, group_ids = complete[0]
                    values = {role: Decimal(ref["value"]) for role, ref in refs.items()}
                    if statement == "资产负债表":
                        lhs, rhs = values["assets"], values["liabilities"] + values["equity"]
                        tolerance = Decimal(0)
                    else:
                        lhs, rhs = values["net"], values["end"] - values["start"]
                        tolerance = max(Decimal(1).scaleb(value.as_tuple().exponent) for value in values.values())
                    record.update(status="pass" if abs(lhs - rhs) <= tolerance else "fail",
                                  left=str(lhs), right=str(rhs), tolerance=str(tolerance),
                                  difference=str(lhs - rhs), operands=refs,
                                  segment_ids=group_ids,
                                  binding_rule="explicit_titled_parts" if len(group_ids) > 1 else "single_segment",
                                  page_refs=sorted({ref["page"] for ref in refs.values()}))
                yield record


def report_qc(report, pages, segments, chunks, duration):
    counts = Counter((segment["statement_type"], segment["scope"]) for segment in segments)
    requirements = [{"statement_type": statement, "scope": scope} for scope in SCOPES
                    for statement in REQUIRED if not counts[statement, scope]]
    issues = Counter()
    for page in pages:
        issues.update(page["issues"])
    continuations = [a for segment in segments for a in segment["evidence"]
                     if a["rule"].startswith(("adjacent_page_", "equity_")) or a["rule"] == "row_sequence"]
    scopes, scope_sources, unknown_split = Counter(), Counter(), Counter()
    unknown_pages = []
    for page in pages:
        scope_sources.update(page["scope_source_counts"])
        scopes.update(page["scope_counts"])
        unknown_split.update(page["unknown_scope_split"])
        if page["unknown_chunks"]:
            unknown_pages.append({"page": page["page"], "count": sum(page["unknown_chunks"].values()),
                                  "reasons": [{"code": code, "heading": heading, "kind": kind, "count": count}
                                              for (code, heading, kind), count in page["unknown_chunks"].most_common()]})
    unknown_pages.sort(key=lambda page: (-page["count"], page["page"]))
    identifier = f"{report['code']}_{report['report_year']}"
    result = {"report_id": identifier, "company": report["company"], "pdf_sha256": report["sha256"],
              "runtime_seconds": round(duration, 3), "pages_processed": len(pages),
              "pages_registry": report["pages"], "page_count_status": "full" if len(pages) == report["pages"] else "subset",
              "text_empty_pages": [p["page"] for p in pages if p["empty"]],
              "extraction_errors": [{"page": p["page"], "errors": p["errors"]} for p in pages if p["errors"]],
              "tables_found": sum(p["tables"] for p in pages),
              "default_tables": sum(p["default_tables"] for p in pages),
              "geometry_tables": sum(p["geometry_tables"] for p in pages),
              "glyph_period_tables": sum(p["glyph_period_tables"] for p in pages),
              "unknown_scope_tables": sum(p["unknown"] for p in pages),
              "primary_unknown_scope_tables": sum(p["primary_unknown"] for p in pages),
              "chunk_counts": {kind: chunks[kind] for kind in ("text", "table")},
              "scope_counts": {scope: scopes[scope] for scope in TABLE_SCOPES},
              "scope_source_counts": {source: scope_sources[source] for source in SCOPE_SOURCES},
              "unknown_scope_chunks": scope_sources["unknown"],
              "unknown_scope_split": {kind: unknown_split[kind] for kind in ("fragment", "non_fragment")},
              "numeric_allowed_table_chunks": sum(p["numeric_allowed_table_chunks"] for p in pages),
              "unknown_scope_top_pages": unknown_pages[:20],
              "unknown_scope_pages": unknown_pages,
              "segment_counts": [{"statement_type": statement, "scope": scope, "count": count}
                                 for (statement, scope), count in sorted(counts.items())],
              "missing_required_segments": requirements, "accepted_continuations": continuations,
              "amount_repairs": sum(p["repairs"] for p in pages), "issue_counts": dict(sorted(issues.items())),
              "identity_checks": list(identities(report, [c for p in pages for c in p["cells"]], segments,
                                                 [r for p in pages for r in p["continuation_rejections"]],
                                                 [row for p in pages for row in p["unbound_identity_rows"]]))}
    if identifier == "002594_2025" and len(pages) >= 137:
        targets = pages[136]["table_scopes"]
        passed = (any((t["scope"], t["statement_type"], t["scope_source"]) ==
                      ("母公司", "现金流量表", "statement_title") for t in targets)
                  and all(t["scope"] != "合并" for t in targets))
        result["byd_physical_page_137"] = {"tables": targets, "status": "pass" if passed else "fail"}
    return result


def unknown_reason(reason):
    heading = f"「{reason['heading']}」" if reason["heading"] else "无可用章节标题"
    return f"{UNKNOWN_REASONS[reason['code']]}（{heading}）"


def batch_unknown(reports):
    return sum(reason["count"] for report in reports for page in report["unknown_scope_pages"]
               for reason in page["reasons"] if reason["code"] != "fragment_without_scope")


def write_batch(path, before, reports):
    if set(before) != {r["report_id"] for r in reports}:
        raise ValueError("The batch and baseline must cover the same reports")
    statuses = ("pass", "fail", "not_found")

    def tally(checks):
        return "/".join(str(checks.get(status, 0)) for status in statuses)

    def rate(unknown, table):
        return unknown / table if table else 0

    def unknown_cell(unknown, table):
        return f"{unknown:,}（{rate(unknown, table):.2%}）"

    lines = ["# TraceNum v3 第五轮年报 QC", "",
             "共 36 份登记年报，使用已缓存的原始页。页码均为 PDF 物理页。",
             "未知率以全部表格块为分母，分子为未知口径块扣除标记 `fragment_without_scope` 的块。目标为逐份 ≤8%，整体 ≤6%。",
             "核心报表段为资产负债表、利润表、现金流量表各自的合并及母公司口径。恒等式检查每份固定八项。",
             "旧版 QC 中的 `not-found` 与本轮的 `not_found` 在下表按同一状态计数。",
             "", "## 每份年报改动前后", "",
             "| 年报 | 公司 | 已处理/登记页数 | 文本块 | 表格块 | 未知块（占比） | 恒等式通过/失败/未找到 | 核心段 |",
             "|---|---|---:|---:|---:|---:|---:|---:|"]
    over_target, issues, missing = [], [], []
    overall_checks, old_checks = Counter(), Counter()
    totals, old_totals = Counter(), Counter()
    for report in sorted(reports, key=lambda r: r["report_id"]):
        identifier = report["report_id"]
        old = before[identifier]
        chunks, old_chunks = report["chunk_counts"], old["chunk_counts"]
        checks = Counter(check["status"] for check in report["identity_checks"])
        old_count = Counter({status.replace("-", "_"): count for status, count in old["identity"].items()})
        overall_checks.update(checks)
        old_checks.update(old_count)
        totals.update(chunks)
        old_totals.update(old_chunks)
        unknown = batch_unknown([report])
        table = chunks["table"]
        old_unknown = unknown_cell(old["unknown_by_reason"], old_chunks["table"])
        core = len(SCOPES) * len(REQUIRED)
        old_core = core - len(old["missing_required_segments"])
        new_core = core - len(report["missing_required_segments"])
        lines.append(f"| {identifier} | {report['company']} | {report['pages_processed']}/{report['pages_registry']} | "
                     f"{old_chunks['text']:,} → {chunks['text']:,} | {old_chunks['table']:,} → {table:,} | "
                     f"{old_unknown} → {unknown_cell(unknown, table)} | {tally(old_count)} → {tally(checks)} | "
                     f"{old_core} → {new_core} |")
        link = f"[{identifier} {report['company']}]({identifier}_v3/qc.json)"
        if rate(unknown, table) > 0.08:
            over_target.extend(["", f"### {link} · {unknown_cell(unknown, table)}", ""])
            for page in sorted(report["unknown_scope_pages"], key=lambda p: p["page"]):
                reasons = [f"{unknown_reason(reason)} {reason['count']} 块" for reason in page["reasons"]
                           if reason["code"] != "fragment_without_scope"]
                if reasons:
                    over_target.append(f"- 第 {page['page']} 页，{'、'.join(reasons)}。")
        for check in report["identity_checks"]:
            if check["status"] == "pass":
                continue
            refs = "、".join(map(str, check["page_refs"])) or "无"
            reason = check.get("reason") or f"左边 {check['left']}，右边 {check['right']}，差额 {check['difference']}"
            issues.append(f"| {link} | {check['statement_type']} / {check['scope']} | {check['data_year']} | "
                          f"`{check['status']}` | {refs} | {reason} |")
        for segment in report["missing_required_segments"]:
            missing.append(f"- {link}，{segment['scope']}{segment['statement_type']}。")
    old_unknown = sum(r["unknown_by_reason"] for r in before.values())
    unknown = batch_unknown(reports)
    lines.extend(["", f"合计 {sum(r['pages_processed'] for r in reports):,} 页，"
                  f"文本块 {old_totals['text']:,} → {totals['text']:,}，表格块 {old_totals['table']:,} → {totals['table']:,}，"
                  f"未知 {unknown_cell(old_unknown, old_totals['table'])} → {unknown_cell(unknown, totals['table'])}，"
                  f"恒等式 {tally(old_checks)} → {tally(overall_checks)}。"
                  f"整体未知率{'达标' if rate(unknown, totals['table']) <= .06 else '未达标'}。", "",
                  "## 核心报表段", ""])
    lines.extend(missing or ["36/36 份均识别出全部六个核心报表段。"])
    lines.extend(["", "## 未通过或未找到的恒等式", "",
                  "| 年报 | 报表 / 口径 | 年份 | 状态 | 物理页 | 原因 |",
                  "|---|---|---:|---|---|---|", *issues,
                  "", "## 未知率超过 8% 的年报", *over_target])
    if not over_target:
        lines.extend(["", "无。"])
    lines.extend(["", "## 文件", "",
                  "- `corpus/v3/parse.py`　缓存回放、章节边界、报表标题与续表识别。",
                  "- `corpus/v3/qc.py`　固定恒等式检查和批量 QC。",
                  "- `tests/test_corpus_v3.py`　真实缓存回归、比亚迪第 137 页及切块确定性。",
                  "- `batch5_baseline.json`　改动前逐份统计。各年报子目录提供 `qc.json`、`segments.json`、"
                  "`chunks.jsonl`、`trace.jsonl`、`issues.jsonl` 和清洗页。"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_pilot(path, reports):
    path = Path(path).resolve()
    root = Path(__file__).resolve().parents[2]
    report_path = path.relative_to(root).as_posix()
    lines = ["# TraceNum v3 年报试点 QC", "",
             "页码均为 PDF 物理页。表格及未知口径计数包含保留的几何候选和原始碎片。",
             "恒等式按印刷表头及资产负债表日期绑定年份。相邻无标题续页可用报表项目行序及金额列对齐绑定。"
             "明确印有「续」的相邻报表段仍按标题、口径、单位和表头核对。",
             "缺失、冲突或无法定位的项目记为 `not_found`。这些断点的判定证据见各年报的 `qc.json`。",
             "资产恒等式要求精确相等。现金恒等式容差为参与运算的最粗印刷精度的一个最小单位。", "",
             "| 年报 | 运行秒数 | 已处理/登记页数 | 空文本页 | 表格 | 未知口径表 | 文本块/表格块 | 金额修复 | 校验通过/失败/未找到 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in reports:
        checks = Counter(check["status"] for check in r["identity_checks"])
        identifier = r["report_id"]
        runtime = f"{r['runtime_seconds']:.2f}"
        if "extraction_runtime_seconds" in r:
            runtime = f"{r['extraction_runtime_seconds']:.2f} 提取 + {runtime} 派生"
        lines.append(f"| {identifier} {r['company']} | {runtime} | {r['pages_processed']}/{r['pages_registry']} | "
                     f"{len(r['text_empty_pages'])} | {r['tables_found']} | {r['unknown_scope_tables']} | "
                     f"{r['chunk_counts']['text']}/{r['chunk_counts']['table']} | {r['amount_repairs']} | "
                     f"{checks['pass']}/{checks['fail']}/{checks['not_found']} |")
    lines.extend(["", "## 第二轮至第三轮口径变化", "",
                  "「合并」计数包含沿用第二轮标记的管理层讨论与分析章节推定。"
                  "「不适用」块以表题为引用依据，续表可回溯原题。"
                  "「被投资主体」块仅在唯一主体可核实时开放数值回答。", "",
                  "| 年报 | " + " | ".join(TABLE_SCOPES) +
                  " | 未知碎片 | 未知非碎片 | 未知非碎片占全部表格块 | <5% | 可答数值 |",
                  "|" + "---|" * 11])
    for r in reports:
        before = r.get("round2_baseline", {})
        scopes = [f"{before.get('scope_counts', {}).get(s, 0) if before else '—'}→{r['scope_counts'][s]}"
                  for s in TABLE_SCOPES]
        splits = [f"{before.get('unknown_scope_split', {}).get(k, 0) if before else '—'}→{r['unknown_scope_split'][k]}"
                  for k in ("fragment", "non_fragment")]
        total = r["chunk_counts"]["table"]
        ratio = r["unknown_scope_split"]["non_fragment"] / total if total else 0
        allowed = f"{before.get('numeric_allowed_table_chunks', '—')}→{r['numeric_allowed_table_chunks']}"
        lines.append(f"| {r['report_id']} {r['company']} | " + " | ".join(scopes + splits) +
                     f" | {ratio:.2%} | {'达标' if ratio < 0.05 else '未达标'} | {allowed} |")
    total = sum(r["chunk_counts"]["table"] for r in reports)
    non_fragment = sum(r["unknown_scope_split"]["non_fragment"] for r in reports)
    ratio = non_fragment / total if total else 0
    lines.extend(["", f"合计 {non_fragment}/{total} 个表格块为未知非碎片，占 {ratio:.2%}。"])
    lines.extend(["", "表格块按口径来源统计，第二轮 → 第三轮。", "",
                  "| 年报 | " + " | ".join(f"`{s}`" for s in SCOPE_SOURCES) + " |",
                  "|" + "---|" * (1 + len(SCOPE_SOURCES))])
    for r in reports:
        before = r.get("round2_baseline", {}).get("scope_source_counts")
        counts = (f"{before.get(s, 0) if before else '—'}→{r['scope_source_counts'][s]}" for s in SCOPE_SOURCES)
        lines.append(f"| {r['report_id']} {r['company']} | " + " | ".join(counts) + " |")
    for r in reports:
        lines.extend(["", f"## {r['report_id']} {r['company']}", "",
                      f"默认表格 {r['default_tables']}，几何恢复候选 {r['geometry_tables']}，"
                      f"字形定位复原 {r['glyph_period_tables']}，"
                      f"未知口径的主要表格 {r['primary_unknown_scope_tables']}。",
                      f"空文本物理页 {r['text_empty_pages']}。"])
        if r["extraction_errors"]:
            lines.extend(f"- 提取问题，物理页 {error['page']}，{', '.join(error['errors'])}"
                         for error in r["extraction_errors"])
        lines.extend(["", "### 报表段", ""])
        lines.extend(f"- {s['statement_type']} / {s['scope']}，{s['count']} 段" for s in r["segment_counts"])
        if not r["segment_counts"]:
            lines.append("- 无已识别段")
        lines.extend(f"- 缺少必查段，{s['statement_type']} / {s['scope']}" for s in r["missing_required_segments"])
        if r.get("byd_physical_page_137"):
            regression = r["byd_physical_page_137"]
            lines.extend(["", f"物理页 137 回归结果 `{regression['status']}`。"])
            lines.extend(f"- {t['scope']}{t['statement_type']}，来源「{t['scope_evidence'][0]['text']}」"
                         if t["scope_evidence"] else f"- {t['scope']}，{t['table_id']}"
                         for t in regression["tables"])
        lines.extend(["", "### 已接受的续页", ""])
        for a in r["accepted_continuations"]:
            if a["rule"] in {"adjacent_page_statement_opening", "equity_grid_continuation", "equity_comparative_header"}:
                lines.append(f"- {a['source_page']} → {a['page']}，`{a['rule']}`，表 ID `{a['table_id']}`。")
                continue
            lines.append(f"- {a['source_page']} → {a['page']}，`{a['rule']}`，{a['column_count']} 列，"
                         f"单位 {a['previous_unit'] or '未识别'} → {a['printed_unit'] or '未重印'}，"
                         f"表头 {a['source_header_signature']} → {a['header_signature']}，"
                         f"表 ID `{a['source_table_id']}` → `{a['table_id']}`，"
                         f"前置行 `{', '.join(a['preceding_line_ids']) or '无'}`。")
            if a["row_sequence"]:
                lines.append(f"  行序证据 `{a['row_sequence']}`。")
        if not r["accepted_continuations"]:
            lines.append("- 无")
        split = r["unknown_scope_split"]
        lines.extend(["", "### 剩余未知口径", "",
                      f"碎片 {split['fragment']} 块，非碎片 {split['non_fragment']} 块。"
                      f"全部未知块涉及 {len(r['unknown_scope_pages'])} 个物理页。", "",
                      "以下列出每个含未知非碎片的物理页及原因。", ""])
        for page in sorted(r["unknown_scope_pages"], key=lambda p: p["page"]):
            reasons = [f"{unknown_reason(reason)} {reason['count']} 块"
                       for reason in page["reasons"] if reason["kind"] == "non_fragment"]
            if reasons:
                lines.append(f"- 第 {page['page']} 页，{'、'.join(reasons)}。")
        if not split["non_fragment"]:
            lines.append("- 无")
        lines.extend(["", "### 恒等式", ""])
        for c in r["identity_checks"]:
            values = (f"，{c['left']} 对 {c['right']}，差额 {c['difference']}"
                      if "left" in c else f"，{c['reason']}")
            lines.append(f"- {c['statement_type']} / {c['scope']} / {c['data_year']}，"
                         f"`{c['status']}`，物理页 {c['page_refs'] or '无'}{values}。")
            if c.get("binding_rule") == "explicit_titled_parts":
                lines.append("  两个物理相邻段各有明确同口径标题，后段标题印有「续」。")
            for rejected in c.get("continuation_rejections", []):
                lines.append(f"  物理页 {rejected['source_page']} → {rejected['page']}，"
                             f"未续接（{', '.join(rejected['reasons'])}）。")
        lines.extend(["", "### 待复核", ""])
        lines.extend(f"- `{code}`，{count} 条" for code, count in r["issue_counts"].items())
    lines.extend(["", "## 文件清单", "",
                  "- `corpus/v3/parse.py`　提取、清洗、分段和切块入口。",
                  "- `corpus/v3/qc.py`　按段核对会计恒等式并汇总试点。",
                  "- `tests/test_corpus_v3.py`　章节口径、续页、比亚迪第 137 页与确定性测试。",
                  f"- `{report_path}`　年报合并 QC 和输出清单。", "",
                  f"下列年报文件相对于 `{path.parent.relative_to(root).as_posix()}/`。", ""])
    for r in reports:
        identifier = r["report_id"]
        folder = f"{identifier}_v3"
        files = {"chunks.jsonl": "文本块及表格块", "trace.jsonl": "原始页、行、表及块的映射",
                 "issues.jsonl": "待复核问题日志", "segments.json": "报表段及证据", "qc.json": "单份年报 QC"}
        lines.extend(f"- `{folder}/{name}`　{purpose}。" for name, purpose in files.items())
        lines.extend(f"- `{folder}/{subfolder}/page_{page:04d}.json`　{purpose}。"
                     for subfolder, purpose in (("raw", "原始 PDF 页字形与表格"), ("cleaned", "清洗后的页文与表格"))
                     for page in range(1, r["pages_processed"] + 1))
    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
