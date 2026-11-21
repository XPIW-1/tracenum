"""backend/answer_check.py: system text and unsupported quotes in answers."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import answer_check as A  # noqa: E402

CHUNK = ("【比亚迪 2023年年报｜PDF物理页139】\n"
         "单位（第153页原文）：除有特别说明外，均以人民币千元为单位表示\n"
         "〔系统注：非年报原文〕报表类型：资产负债表；被投资主体按本片行序：第1行 某某新能源有限公司\n"
         "本片原始行序：\n| 负债合计 | 529,085,557 |")
EVIDENCE = [(139, CHUNK)]


def codes(answer):
    return [(f["code"], f["severity"]) for f in A.check(answer, EVIDENCE)]


class AnswerCheckTest(unittest.TestCase):
    def test_clean_answer(self):
        self.assertEqual(codes("负债合计为 529,085,557 千元[第139页]，单位见[第153页]“均以人民币千元为单位表示”。"), [])

    def test_placeholder_and_note_wording_block(self):
        self.assertIn(("system_note_leak", "block"), codes("原文标注单位“待核验”。"))
        self.assertIn(("system_note_leak", "block"), codes("单位（第153页原文）：千元"))

    def test_dynamic_note_phrase_blocks(self):
        # a phrase that exists only in the system note, not in the report text
        self.assertIn(("system_note_leak", "block"), codes("第1行为某某新能源有限公司（被投资主体按本片行序）。"))

    def test_quote_only_in_system_note_is_unsupported(self):
        self.assertIn(("unsupported_quote", "warn"), codes("年报称“报表类型：资产负债表”。"))

    def test_verifier_and_english_are_warnings(self):
        found = codes("外部核验反馈为 UNSUPPORTED；字段为 None。")
        self.assertIn(("verifier_talk", "warn"), found)
        self.assertIn(("english_internal_value", "warn"), found)
        self.assertFalse(A.blocking(A.check("外部核验反馈为 UNSUPPORTED。", EVIDENCE)))

    def test_cited_page_outside_evidence(self):
        self.assertIn(("page_not_in_evidence", "warn"), codes("见[第200页]。"))

    def test_split_context(self):
        text = "[第21页]\n【A 2023年年报｜PDF物理页21】\n本片原始行序：\n| a | 1 |\n\n[第22页]\n正文"
        self.assertEqual([p for p, _ in A.split_context(text)], [21, 22])


if __name__ == "__main__":
    unittest.main()
