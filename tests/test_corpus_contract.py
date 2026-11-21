"""The chunk-text contract (corpus/v3/lint.py): model-visible text is either verbatim
annual-report text with its page, or a marked system note; unknowns are left out."""
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "corpus" / "v3"))

import lint  # noqa: E402
from parse import DOCUMENT_UNIT, SYSTEM_MARK, render_table_header  # noqa: E402

BASE = {"company": "示例公司", "report_year": 2025}
ROWS = [{"row_id": "r1", "cells": ["营业收入", "1,234,567.00"]}]


def ctx(**kw):
    return {"statement_type": "other", "scope": "unknown", "unit": None, "anchors": [], "scope_evidence": [], **kw}


class FakePages(lint.Pages):
    def __init__(self, pages):
        self._cache = {p: lint.norm(t) for p, t in pages.items()}


class HeaderTest(unittest.TestCase):
    def test_unknowns_are_left_out(self):
        text = render_table_header(BASE, 10, ctx(), ROWS, [], True)
        self.assertNotRegex(text, lint.BANNED)
        self.assertIn(SYSTEM_MARK + "本表附近未找到单位说明", text)

    def test_unit_shown_in_table_needs_no_note(self):
        rows = [{"row_id": "h", "cells": ["项目", "金额（元）"]}, *ROWS]
        self.assertNotIn(SYSTEM_MARK, render_table_header(BASE, 10, ctx(), rows, [], True))

    def test_printed_unit_is_quoted_with_its_page(self):
        anchor = {"rule": "printed_unit", "source_page": 9, "text": "单位：元", "value": "元"}
        text = render_table_header(BASE, 10, ctx(unit="元", anchors=[anchor]), ROWS, [], True)
        self.assertIn("单位（第9页原文）：单位：元", text)

    def test_document_unit_is_quoted(self):
        declared = {"page": 153, "text": "除有特别说明外，均以人民币千元为单位表示", "unit": "千元"}
        text = render_table_header(BASE, 139, ctx(statement_type="资产负债表"), ROWS, [], True, declared)
        self.assertIn("单位（第153页原文）：除有特别说明外，均以人民币千元为单位表示", text)
        self.assertIn(SYSTEM_MARK + "报表类型：资产负债表", text)

    def test_inferred_scope_is_a_marked_note(self):
        chapter = {"source_page": 150, "text": "七、合并财务报表项目注释"}
        text = render_table_header(BASE, 151, ctx(scope="合并(章节推定)", scope_evidence=[chapter]), ROWS, [], False)
        self.assertIn("口径依据（第150页原文）：七、合并财务报表项目注释", text)
        self.assertIn(SYSTEM_MARK + "财务口径：合并（系统按所在章节判断）", text)
        self.assertNotIn("章节推定", text)

    def test_document_unit_wordings(self):
        for sentence, unit in [("本公司记账本位币和编制本财务报表所采用的货币均为人民币，除有特别说明外，均以人民币千元为单位表示", "千元"),
                               ("除有特别说明外，均以人民币元为单位表示", "元")]:
            self.assertEqual(DOCUMENT_UNIT.search(sentence)["unit"], unit)


class LintTest(unittest.TestCase):
    report = {"company": "示例公司", "report_year": 2025}
    pages = FakePages({9: "单位：元\n营业收入 1,234,567.00", 10: "营业收入 1,234,567.00"})

    def record(self, text):
        return {"id": "x_2025_v3:p0010:table:abc", "text": text,
                "metadata": {"type": "table", "page": 10, "statement_type": "other", "unit": None,
                             "anchors": [{"source_page": 9}]}}

    def check(self, header):
        text = "【示例公司 2025年年报｜PDF物理页10】\n" + header + "本片原始行序：\n| 营业收入 | 1,234,567.00 |"
        return lint.check_record(self.record(text), self.pages, self.report)

    def test_contract_header_passes(self):
        self.assertEqual(self.check("单位（第9页原文）：单位：元\n" + SYSTEM_MARK + "报表类型：利润表\n"), [])

    def test_placeholder_fails(self):
        self.assertTrue(any("待核验" in p for p in self.check("单位：待核验\n")))

    def test_invented_quote_fails(self):
        self.assertTrue(any("not on page" in p for p in self.check("单位（第9页原文）：单位：千元\n")))

    def test_cell_not_in_pdf_fails(self):
        text = "【示例公司 2025年年报｜PDF物理页10】\n本片原始行序：\n| 营业收入 | 9,999.00 |"
        self.assertTrue(any("cell not on source pages" in p
                            for p in lint.check_record(self.record(text), self.pages, self.report)))


if __name__ == "__main__":
    unittest.main()
