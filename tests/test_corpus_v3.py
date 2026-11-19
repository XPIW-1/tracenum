import json
from shutil import copyfile, rmtree
import unittest
from unittest.mock import patch
from uuid import uuid4

from corpus.v3.parse import (
    DEFAULT_OUT, ROOT, clean_cell, clean_page, make_chunks, parse_report,
    raw_pages, registry, resolve_segments,
)
from corpus.v3.qc import identities
from corpus.v3 import signoff


def page(number, prefix=(), header=("项目", "2025年", "2024年"),
         label="经营活动产生的现金流量：", rows=None, in_table=()):
    raw = {"page": number, "source_id": "sample",
           "lines": [{"text": text, "line_id": f"p{number}:l{i}",
                      "bbox": [10, 10 + i * 12, 100, 20 + i * 12]}
                     for i, text in enumerate(prefix)]
                    + [{"text": text, "line_id": f"p{number}:tline{i}",
                        "bbox": [10, 110 + 12 * i, 490, 120 + 12 * i]}
                       for i, text in enumerate(in_table)]}
    if rows is None:
        rows = (header, (label, "", ""), ("示例金额", "10.00", "8.00"))
    table = {"table_id": f"p{number}:t0", "bbox": [0, 100, 500, 250], "primary": True,
             "rows": [{"cells": cells, "row_id": f"p{number}:r{i}"} for i, cells in enumerate(rows)]}
    return raw, {"tables": [table]}


def resolve(*pages):
    results = list(resolve_segments(pages))
    return [r[1]["tables"][0]["context"] for r in results], results[-1][2]


class SegmentTests(unittest.TestCase):
    def setUp(self):
        self.start = page(8, ("4、合并现金流量表", "单位：元"))

    def test_matching_header_continues_with_anchors(self):
        contexts, segments = resolve(self.start, page(9))
        self.assertEqual([c["scope"] for c in contexts], ["合并", "合并"])
        self.assertEqual(contexts[0]["segment_id"], contexts[1]["segment_id"])
        self.assertEqual((segments[0]["start_page"], segments[0]["end_page"]), (8, 9))
        decision = segments[0]["evidence"][-1]
        self.assertEqual(decision["rule"], "adjacent_page_matching_header")
        self.assertEqual(decision["source_header_row_ids"], ["p8:r0"])
        self.assertEqual(decision["header_row_ids"], ["p9:r0"])

    def test_title_starts_a_new_segment(self):
        contexts, segments = resolve(self.start, page(9, ("2、母公司利润表（续）",)))
        self.assertEqual([c["scope"] for c in contexts], ["合并", "母公司"])
        self.assertEqual([s["statement_type"] for s in segments], ["现金流量表", "利润表"])
        self.assertEqual(segments[0]["end_page"], 8)

    def test_combined_title_has_no_unique_scope(self):
        contexts, segments = resolve(page(8, ("合并及公司资产负债表",)))
        self.assertEqual(segments[0]["statement_type"], "资产负债表")
        self.assertEqual(contexts[0]["scope"], "unknown")
        self.assertFalse(contexts[0]["numeric_answer_allowed"])

    def test_mixed_scope_row_groups_override_single_scope_title(self):
        rows = (("项目", "2025年", "2024年"),
                ("未归类费用", "3.00", "2.00"),
                ("（一）合并", None, None),
                ("销售费用", "10.00", "8.00"),
                ("（二）公司", None, None),
                ("销售费用", "4.00", "3.00"))
        contexts, segments = resolve(page(8, ("2、母公司利润表",), rows=rows))
        context, = contexts
        self.assertEqual(segments[0]["scope"], "混合")
        self.assertEqual((context["scope"], context["scope_source"]), ("混合", "row_scope_heading"))
        self.assertEqual(context["scope_by_row"], {"p8:r3": "合并", "p8:r5": "母公司"})
        self.assertEqual(context["scope_by_row_evidence"]["p8:r5"][0]["row_id"], "p8:r4")
        self.assertEqual(context["numeric_answer_allowed_by_row"],
                         {"p8:r1": False, "p8:r3": True, "p8:r5": True})

    def test_mixed_scope_with_unresolved_and_partly_bound_columns(self):
        cases = (
            (("项目", "合并及母公司", None), {}, {1: False, 2: False}),
            (("项目", "合并", "母公司", None), {1: "合并", 2: "母公司"},
             {1: True, 2: True, 3: False}),
        )
        for header, mapping, allowed in cases:
            with self.subTest(header=header):
                with_data = ("费用", *("1.00" for _ in header[1:]))
                contexts, _ = resolve(page(8, ("九、合并财务报表附注",), rows=(header, with_data)))
                context, = contexts
                self.assertEqual(context["scope"], "混合")
                self.assertEqual(context["scope_by_column"], mapping)
                self.assertEqual(context["numeric_answer_allowed_by_column"], allowed)
                self.assertEqual(context["numeric_answer_allowed"], bool(mapping))

    def test_continuation_breaks(self):
        cases = {
            "gap": page(10),
            "unit change": page(9, ("单位：千元",)),
            "scope keyword": page(9, ("母公司数据",)),
            "consolidation keyword": page(9, ("合并范围说明",)),
            "section heading": page(9, ("三、财务报表附注",)),
            "statement keyword": page(9, ("以下为利润表数据",)),
            "year mismatch": page(9, header=("项目", "2024年", "2023年")),
            "column mismatch": page(9, header=("项目", "附注", "2025年", "2024年")),
        }
        for reason, next_page in cases.items():
            with self.subTest(reason=reason):
                contexts, segments = resolve(page(8, ("4、合并现金流量表", "单位：元")), next_page)
                self.assertEqual(contexts[-1]["scope"], "unknown")
                self.assertFalse(contexts[-1]["numeric_answer_allowed"])
                self.assertEqual(segments[0]["end_page"], 8)

    def test_headerless_next_row_sequence(self):
        following = page(9, header=("", "", ""), label="二、投资活动产生的现金流量：")
        contexts, segments = resolve(self.start, following)
        self.assertEqual(contexts[1]["scope"], "合并")
        self.assertEqual(segments[0]["evidence"][-1]["rule"], "adjacent_page_row_sequence")
        self.assertEqual(segments[0]["evidence"][-1]["row_sequence"]["to_row_id"], "p9:r1")
        self.assertEqual(following[1]["tables"][0]["data_years"], [None, None, None])

    def test_scoped_outline_applies_and_statement_title_overrides_it(self):
        contexts, _ = resolve(
            page(1, ("第三节 管理层讨论与分析",)),
            page(2, ("第四节 财务报告", "七、合并财务报表主要项目注释", "1、货币资金")),
            page(3, ("2、应收款项融资", "一、合营企业")),
            page(4, ("母公司资产负债表",)),
            page(5, ("八、公司财务报表主要项目附注", "1、应收账款")),
            page(6, ("九、补充资料",)),
            page(7, ("第五节 其他信息",)),
        )
        self.assertEqual([(c["scope"], c["scope_source"]) for c in contexts], [
            ("合并(章节推定)", "section_default"), ("合并", "section_heading"), ("合并", "section_heading"),
            ("母公司", "statement_title"), ("母公司", "section_heading"), ("unknown", "unknown"),
            ("unknown", "unknown"),
        ])
        self.assertEqual(contexts[2]["scope_evidence"][0]["line_id"], "p2:l1")
        self.assertEqual(contexts[3]["scope_evidence"][0]["text"], "母公司资产负债表")
        self.assertEqual([c["numeric_answer_allowed"] for c in contexts], [True] * 5 + [False] * 2)

    def test_metrics_default_when_table_does_not_print_attribution(self):
        contexts, _ = resolve(
            page(1, ("第二节 公司简介和主要财务指标", "五、主要会计数据和财务指标"),
                 in_table=("归属于上市公司股东的净利润 15.00 12.00",)),
            page(2),
        )
        self.assertEqual(contexts[0]["scope"], "合并")
        attribution = contexts[0]["scope_evidence"][1]
        self.assertEqual((attribution["source_page"], attribution["line_id"], attribution["text"]),
                         (1, "p1:tline0", "归属于上市公司股东的净利润 15.00 12.00"))
        self.assertEqual((contexts[1]["scope"], contexts[1]["scope_source"]), ("合并", "section_default"))
        self.assertEqual(contexts[1]["scope_evidence"][0]["line_id"], "p1:l0")

    def test_quarterly_metrics_use_chapter_default(self):
        contexts, _ = resolve(
            page(1, ("第二节 公司简介和主要财务指标", "八、分季度主要财务指标")),
            page(2, ("九、境内外会计准则下会计数据差异",)),
        )
        self.assertEqual((contexts[0]["scope"], contexts[0]["scope_source"]), ("合并", "section_default"))
        self.assertEqual(contexts[0]["scope_evidence"][0]["line_id"], "p1:l0")
        self.assertEqual(contexts[1]["scope"], "unknown")

    def test_guarantee_uses_nearest_table_title(self):
        raw, cleaned = page(1, ("第五节 重要事项", "十二、重大担保", "1、担保情况", "单位：元"))
        fragment = {**cleaned["tables"][0], "primary": False, "table_id": "fragment"}
        cleaned["tables"].append(fragment)
        contexts, _ = resolve((raw, cleaned), page(2, ("母公司资产负债表",)))
        guarantee = contexts[0]
        self.assertEqual(guarantee["scope"], "不适用")
        self.assertTrue(guarantee["numeric_answer_allowed"])
        self.assertEqual(guarantee["table_title"]["line_id"], "p1:l2")
        self.assertIn(guarantee["table_title"], guarantee["scope_evidence"])
        self.assertEqual(fragment["context"]["scope_unknown_reason"], "fragment_without_scope")
        self.assertFalse(fragment["context"]["numeric_answer_allowed"])
        self.assertEqual(contexts[1]["scope"], "母公司")
        self.assertNotIn("table_title", contexts[1])

    def test_investee_summary_requires_one_named_entity(self):
        contexts, _ = resolve(
            page(1, ("第八节 财务报告", "十、在其他主体中的权益", "（2） 重要合营企业的主要财务信息"),
                 rows=(("项目", "华东新能源有限公司"), ("流动资产", "10.00"), ("非流动资产", "12.00"))),
            page(2, ("（3） 不重要的联营企业的汇总财务信息",)),
            page(3, ("十一、政府补助",)),
        )
        named, unnamed, next_note = contexts
        self.assertEqual((named["scope"], named["entity"]), ("被投资主体", "华东新能源有限公司"))
        self.assertTrue(named["numeric_answer_allowed"])
        self.assertEqual(named["scope_evidence"][-1]["row_id"], "p1:r0")
        self.assertEqual(unnamed["scope"], "被投资主体")
        self.assertTrue(unnamed["entity_unknown"])
        self.assertFalse(unnamed["numeric_answer_allowed"])
        self.assertEqual(next_note["scope"], "unknown")

    def test_board_compensation_note_has_title(self):
        contexts, _ = resolve(page(
            1, ("第八节 财务报告", "十八、其他重要事项（续）", "2、#董事及监事薪酬（续）")
        ))
        self.assertEqual(contexts[0]["scope"], "不适用")
        self.assertEqual(contexts[0]["table_title"]["line_id"], "p1:l2")

    def test_row_sequence_with_amount_column_alignment_and_blockers(self):
        def start():
            return page(8, ("母公司资产负债表", "单位：元"),
                        rows=(("项目", "附注", "2025年", "2024年"),
                              ("流动资产合计", "", "20.00", "15.00")))

        def following(prefix=(), first_year="", extra=""):
            return page(9, prefix, rows=(("", "", first_year, "", ""),
                                         ("长期股权投资", "", "15.00", "", "7.00"),
                                         ("固定资产", extra, "5.00", "", "3.00")))

        next_page = following()
        contexts, segments = resolve(start(), next_page)
        self.assertEqual(contexts[-1]["scope"], "母公司")
        evidence = segments[0]["evidence"][-1]
        self.assertEqual(evidence["rule"], "row_sequence")
        self.assertEqual(evidence["row_sequence"]["from_row"], "流动资产合计")
        self.assertEqual([item["text"] for item in evidence["row_sequence"]["matched_items"]], ["长期股权投资", "固定资产"])
        periods = next_page[1]["tables"][0]["period_columns"]
        self.assertEqual([(p["column"], p["data_year"], p["source_page"]) for p in periods],
                         [(2, 2025, 8), (4, 2024, 8)])
        for refused in (following(("合并范围说明",)), following(("单位：千元",)),
                        following(first_year="2023年"), following(extra="12.00")):
            with self.subTest(refused=refused):
                contexts, segments = resolve(start(), refused)
                self.assertEqual(contexts[-1]["scope"], "unknown")
                self.assertEqual(segments[0]["end_page"], 8)

    def test_same_cell_amount_requires_all_glyphs(self):
        chars = [{"text": char, "x0": i, "x1": i + 1,
                  "top": 10 if i < 3 else 20, "bottom": 15 if i < 3 else 25}
                 for i, char in enumerate("1,234.56")]
        bbox = [0, 0, 30, 30]
        value, evidence = clean_cell("1,2\n34.56", bbox, chars)
        self.assertEqual(value, "1,234.56")
        self.assertEqual(evidence["rule"], "same_cell_glyph_numeric_wrap")
        self.assertEqual(clean_cell("1,2\n34.56", bbox, chars[:-1])[0], "1,2\n34.56")

    def test_missing_inputs_keep_all_eight_qc_checks(self):
        checks = list(identities({"report_year": 2025}, [], [], [], []))
        self.assertEqual(len(checks), 8)
        self.assertEqual(len({(c["statement_type"], c["scope"], c["data_year"]) for c in checks}), 8)
        self.assertTrue(all(c["status"] == "not_found" and c["reason"] for c in checks))


class PdfTests(unittest.TestCase):
    def report(self, identifier):
        report = registry()[identifier]
        if not (DEFAULT_OUT / f"{identifier}_v3" / "raw").is_dir():
            self.skipTest(f"Cached raw pages missing: {identifier}")
        return report

    def test_byd_2025_physical_page_137(self):
        report = self.report("002594_2025")
        numbers = {130, 136, 137}
        with patch("corpus.v3.parse.verified_pdf", side_effect=AssertionError("cached run opened PDF")):
            raw = [page for page in raw_pages(report, DEFAULT_OUT / "002594_2025_v3", max(numbers), cached_only=True)
                   if page["page"] in numbers]
        resolved = list(resolve_segments((page, clean_page(page)) for page in raw))
        prior = [t["context"] for t in resolved[0][1]["tables"] if t["primary"]]
        self.assertTrue(any(c["scope"] == "合并" and c["statement_type"] == "现金流量表" for c in prior))
        all_contexts = [t["context"] for t in resolved[-1][1]["tables"]]
        self.assertTrue(all(c["scope"] != "合并" for c in all_contexts))
        expected = [c for c in all_contexts if (c["scope"], c["statement_type"]) == ("母公司", "现金流量表")]
        self.assertTrue(expected, "Page 137 must contain the extracted company cash-flow statement")
        self.assertIn("公司现金流量表", expected[0]["scope_evidence"][0]["text"])

    def cached_segments(self, identifier, *numbers):
        folder = DEFAULT_OUT / f"{identifier}_v3" / "raw"
        paths = [folder / f"page_{p:04d}.json" for p in numbers]
        if not all(path.is_file() for path in paths):
            self.skipTest(f"Cached raw pages missing: {identifier}")
        raw = (json.loads(path.read_bytes()) for path in paths)
        return list(resolve_segments((p, clean_page(p)) for p in raw))

    def test_300014_2024_warranty_cost_scope_spans(self):
        identifier = "300014_2024"
        raw, cleaned, _ = self.cached_segments(identifier, 140)[0]
        table, = [t for t in cleaned["tables"] if t["table_id"].endswith(":t001")]
        context = table["context"]
        self.assertEqual((context["scope"], context["scope_source"]), ("混合", "column_scope_header"))
        self.assertEqual(context["scope_by_column"], {1: "合并", 2: "合并", 4: "母公司", 5: "母公司"})
        self.assertEqual(context["scope_by_column_evidence"][2][0]["text"], "2023年度（合并）")
        self.assertEqual(context["scope_by_column_evidence"][5][0]["text"], "2023年度（母公司）")
        self.assertTrue(context["numeric_answer_allowed"])
        chunk, = [c for c in make_chunks(raw, cleaned, registry()[identifier])
                  if c["metadata"]["table_id"] == table["table_id"]]
        self.assertIn("财务口径：混合（第2列 合并、第3列 合并、第5列 母公司、第6列 母公司", chunk["text"])
        self.assertTrue(chunk["metadata"]["numeric_answer_allowed"])

    def test_restatement_rows_print_scope_in_each_amount_row(self):
        _, cleaned, _ = self.cached_segments("601633_2024", 219)[0]
        table, = [t for t in cleaned["tables"] if t["table_id"].endswith(":t000")]
        self.assertEqual(list(table["context"]["scope_by_row"].values()), ["合并", "合并", "母公司", "母公司"])

    def test_wrapped_disposal_header_is_not_a_scope_split(self):
        _, cleaned, _ = self.cached_segments("301358_2024", 187)[0]
        table, = [t for t in cleaned["tables"] if t["table_id"].endswith(":t001")]
        self.assertNotEqual(table["context"]["scope"], "混合")

    def test_investee_names_bind_each_row_on_both_tables(self):
        _, cleaned, _ = self.cached_segments("002460_2023", 233, 237, 238)[-1]
        tables = [t for t in cleaned["tables"] if t["primary"]]
        self.assertEqual(len(tables), 2)
        for table in tables:
            context = table["context"]
            self.assertEqual(context["scope"], "被投资主体")
            self.assertNotIn("entity", context)
            self.assertTrue(context["numeric_answer_allowed"])
            self.assertEqual(list(context["entity_by_row"].values()),
                             ["Minera Exar", "江西赣锋锂电科技股份有限公司"])

    def test_investee_columns_and_unresolved_joint_heading(self):
        lead = ("十、在其他主体中的权益", "（3）重要非全资子公司的主要财务信息")
        investors = ("Minera Exar", "江西赣锋锂电科技股份有限公司")
        contexts, _ = resolve(page(1, lead, header=("项目", *investors), label="流动资产"))
        self.assertEqual(contexts[0]["entity_by_column"], dict(enumerate(investors, 1)))
        self.assertNotIn("entity", contexts[0])
        contexts, _ = resolve(page(1, lead, header=("项目", "、".join(investors), ""), label="流动资产"))
        self.assertEqual(contexts[0]["entities"], list(investors))
        self.assertTrue(contexts[0]["entity_unknown"])
        self.assertFalse(contexts[0]["numeric_answer_allowed"])

    def test_hedging_group_wording_and_parent_precedence(self):
        _, cleaned, _ = self.cached_segments("002460_2025", 169, 244, 248, 249)[-1]
        table, = [t for t in cleaned["tables"] if t["primary"]]
        context = table["context"]
        self.assertEqual((context["scope"], context["scope_source"]), ("合并", "group_wording"))
        self.assertIn("本集团采用现金", context["scope_evidence"][0]["text"])
        contexts, _ = resolve(page(1, ("九、母公司财务报表附注", "本集团采用现金流量套期和公允价值套期")))
        self.assertEqual((contexts[0]["scope"], contexts[0]["scope_source"]), ("母公司", "section_heading"))
        contexts, _ = resolve(page(1, ("九、合并财务报表附注", "本集团采用现金流量套期和公允价值套期")))
        self.assertEqual((contexts[0]["scope"], contexts[0]["scope_source"]), ("合并", "group_wording"))

    def test_supplement_follows_parent_notes(self):
        _, cleaned, _ = self.cached_segments("300014_2023", 224, 235)[-1]
        parent, supplement = [t["context"] for t in cleaned["tables"] if t["primary"]]
        self.assertEqual(parent["scope"], "母公司")
        self.assertEqual((supplement["scope"], supplement["scope_source"]), ("合并", "section_heading"))
        self.assertEqual([a["rule"] for a in supplement["scope_evidence"]],
                         ["supplement_section_heading", "supplement_table_title", "minority_interest_row"])

    def test_guarantee_heading_applies_to_lower_table_only(self):
        _, cleaned, _ = self.cached_segments("001301_2025", 128)[-1]
        upper, lower = [t["context"] for t in cleaned["tables"] if t["primary"]]
        self.assertEqual(lower["scope"], "不适用")
        self.assertEqual(lower["table_title"]["text"], "2、重大担保")
        self.assertNotEqual(upper.get("table_title"), lower["table_title"])

    def test_opening_period_survives_continuation_with_report_evidence(self):
        _, cleaned, _ = self.cached_segments("300750_2023", 115, 116)[-1]
        table, = [t for t in cleaned["tables"] if t["primary"]]
        closing, opening = table["period_columns"]
        self.assertEqual((closing["data_year"], opening["data_year"]), (2023, 2022))
        self.assertEqual((opening["printed_label"], opening["label"], opening["opening_year"]),
                         ("2023年1月1日", "2023年初（=2022年末）", 2023))
        self.assertFalse(opening["restated"])
        raw, cleaned = page(1, ("合并资产负债表",), header=("项目", "2023年12月31日", "2023年1月1日（经重述）"),
                            label="流动资产：")
        result = next(resolve_segments([(raw, cleaned)]))[1]
        self.assertTrue(result["tables"][0]["period_columns"][1]["restated"])
        raw, cleaned = page(1, ("合并资产负债表",), header=("项目", "期末余额", "期初余额"), label="流动资产：")
        result = next(resolve_segments([(raw, cleaned)], report_year=2023))[1]
        self.assertEqual(result["tables"][0]["period_columns"][1]["data_year"], 2022)

    def test_signoff_points_to_each_table_on_a_split_page(self):
        resolved = {raw["page"]: cleaned for raw, cleaned, _ in
                    self.cached_segments("300750_2023", 115, 116, 117)}
        upper, lower = [t for t in resolved[117]["tables"] if t["primary"]]
        read_page = signoff.cached_page
        with patch.object(signoff, "cached_page", side_effect=lambda rid, directory, number:
                          resolved[number] if directory == "cleaned" else read_page(rid, directory, number)):
            upper_claim = signoff.describe_table("300750_2023", 117, upper["table_id"])
            lower_claim = signoff.describe_table("300750_2023", 117, lower["table_id"])
        self.assertIn("上方第1张表", upper_claim)
        self.assertIn("第116页续接源表", upper_claim)
        self.assertIn("2023年1月1日", upper_claim)
        self.assertIn("下方第2张表", lower_claim)
        self.assertNotIn("续接源表", lower_claim)

    def test_statement_title_and_body_on_different_pages(self):
        cases = (
            ("002812_2025", 96, "1、合并资产负债表", "合并", "资产负债表", (2025, 2024)),
            ("301358_2025", 93, "2、母公司资产负债表", "母公司", "资产负债表", ()),
            ("002709_2023", 88, "3、合并利润表", "合并", "利润表", (2023, 2022)),
            ("688005_2023", 122, "母公司现金流量表", "母公司", "现金流量表", (2023, 2022)),
            ("688005_2025", 119, "合并资产负债表", "合并", "资产负债表", (2025, 2024)),
            ("002812_2024", 108, "6、母公司现金流量表", "母公司", "现金流量表", (2024, 2023)),
        )
        for identifier, source, title, scope, statement, years in cases:
            with self.subTest(identifier=identifier, source=source):
                _, _, segments = self.cached_segments(identifier, source, source + 1)[-1]
                opening, = [s for s in segments if s["start_page"] == source + 1]
                self.assertEqual((opening["scope"], opening["statement_type"]), (scope, statement))
                self.assertEqual(opening["evidence"][0]["source_page"], source)
                self.assertEqual(opening["evidence"][0]["text"], title)
                if years:
                    self.assertEqual(tuple(p["data_year"] for p in opening["period_columns"]), years)

    def test_equity_grid_continues_into_comparative_table(self):
        pages = self.cached_segments("002709_2023", 94, 95, 96, 97)
        _, _, segments = pages[-1]
        self.assertEqual([(s["scope"], s["start_page"], s["end_page"]) for s in segments],
                         [("合并", 94, 96), ("母公司", 96, 97)])
        self.assertTrue(any(e["rule"] == "equity_grid_continuation" for e in segments[0]["evidence"]))

    def test_audit_cover_ends_bond_scope_and_keeps_reviewed_periods(self):
        pages = self.cached_segments("601633_2025", 130, 136, 137, 142, 143, 144, 145)
        _, contents, _ = pages[2]
        self.assertTrue(all(t["context"]["scope"] != "不适用" for t in contents["tables"]))
        _, _, segments = pages[-1]
        self.assertEqual([(s["scope"], s["explicit_title_continued"]) for s in segments],
                         [("合并", False), ("合并", True), ("母公司", False), ("母公司", True)])
        self.assertTrue(all([p["data_year"] for p in s["period_columns"]] == [2025, 2024] for s in segments))

    def test_multiline_credit_policy_header(self):
        _, cleaned, _ = self.cached_segments("688005_2024", 121, 150)[-1]
        table, = [t for t in cleaned["tables"] if t["primary"]]
        context = table["context"]
        self.assertEqual(context["scope"], "不适用")
        self.assertTrue(any("预期信用损失率" in e.get("text", "") for e in context["scope_evidence"]))

    def test_guarantee_continues_without_printed_amounts(self):
        pages = self.cached_segments("002812_2025", 94, *range(194, 199))
        _, cleaned, _ = pages[-1]
        table, = [t for t in cleaned["tables"] if t["primary"]]
        context = table["context"]
        self.assertEqual(context["scope"], "不适用")
        self.assertEqual(context["scope_evidence"][-1]["rule"], "adjacent_guarantee_rows")
        self.assertEqual(context["scope_evidence"][-1]["source_page"], 197)

    def test_company_roster_and_related_lease_disclosures(self):
        pages = self.cached_segments("002709_2025", 76, 94, 95, 128, *range(168, 186))
        for number, rule in ((95, "company_roster_rows"), (185, "related_lease_role")):
            with self.subTest(number=number):
                _, cleaned, _ = next(p for p in pages if p[0]["page"] == number)
                contexts = [table["context"] for table in cleaned["tables"] if table["primary"]]
                self.assertTrue(all(c["scope"] == "不适用" for c in contexts))
                self.assertTrue(any(e["rule"] == rule for c in contexts for e in c["scope_evidence"]))

    def test_chunks_are_byte_identical_on_rerun(self):
        report = self.report("002594_2023")
        output = ROOT / "build" / f"corpus-v3-test-{uuid4().hex}"
        output.mkdir(parents=True)
        self.addCleanup(rmtree, output)
        folder = output / "002594_2023_v3"
        cached = DEFAULT_OUT / folder.name / "raw"
        if not all((cached / f"page_{number:04d}.json").is_file() for number in range(1, 31)):
            self.skipTest("Cached raw pages missing")
        (folder / "raw").mkdir(parents=True)
        for number in range(1, 31):
            filename = f"page_{number:04d}.json"
            copyfile(cached / filename, folder / "raw" / filename)
        parse_report(report, output, pages=30, cached_only=True)
        chunks = folder / "chunks.jsonl"
        first = chunks.read_bytes()
        rows = [json.loads(line) for line in first.splitlines()]
        self.assertTrue(any(row["metadata"]["type"] == "table" for row in rows))
        trace = [json.loads(line) for line in (folder / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([row["id"] for row in rows], [row["chunk_id"] for row in trace])
        self.assertTrue(all((folder / row["raw_ref"]).is_file() for row in trace))
        raw_before = {p.name: p.read_bytes() for p in (folder / "raw").glob("*.json")}
        parse_report(report, output, pages=30, cached_only=True)
        self.assertEqual(chunks.read_bytes(), first)
        self.assertEqual({p.name: p.read_bytes() for p in (folder / "raw").glob("*.json")}, raw_before)


if __name__ == "__main__":
    unittest.main()


class GroupWordingNegationTest(unittest.TestCase):
    def test_negated_consolidation_scope_is_not_group_evidence(self):
        from corpus.v3 import parse as P  # noqa: F401  (import path as used by the other tests)
        pattern = P.GROUP_WORDING
        self.assertIsNotNone(pattern.search("本集团采用现金流量套期"))
        self.assertIsNotNone(pattern.search("合并范围内关联方组合"))
        self.assertIsNone(pattern.search("按组合计提坏账准备：非合并范围内公司"))
