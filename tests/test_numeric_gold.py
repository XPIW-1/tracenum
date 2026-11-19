from decimal import Decimal
import unittest

from eval.build_numeric_gold import cross_check, label_matches, precision_only, to_yuan


def cell(printed, unit="元"):
    return {"value_yuan": str(to_yuan(printed, unit)), "printed_value": printed, "unit": unit}


class NumericGoldTests(unittest.TestCase):
    def test_units_and_parenthesized_negatives(self):
        for printed, unit, expected in (
            ("1,234.56", "元", "1234.56"),
            ("1,234.56", "千元", "1234560"),
            ("1,234.56", "万元", "12345600"),
            ("（1,234.56 ）", "千元", "-1234560"),
            ("−0.01", "元", "-0.01"),
        ):
            with self.subTest(printed=printed, unit=unit):
                self.assertEqual(to_yuan(printed, unit), Decimal(expected))
        with self.assertRaises(ValueError):
            to_yuan("—", "元")

    def test_next_report_can_confirm_restatement(self):
        original = cell("1,001.00")
        comparative = cell("1,031.00")
        reference = Decimal("1031.0000000001")
        self.assertEqual(cross_check(original, reference, comparative), "restated_in_next_report")
        self.assertEqual(cross_check(comparative, reference, None), "match")
        self.assertEqual(cross_check(original, reference, None), "mismatch")
        self.assertEqual(cross_check(original, reference, cell("1,030.49")), "mismatch")
        self.assertEqual(cross_check({}, reference, comparative), "not_found")
        self.assertEqual(original["value_yuan"], "1001.00")

    def test_printed_precision(self):
        self.assertEqual(cross_check(cell("1,000", "千元"), Decimal("1000499"), None), "match")
        self.assertEqual(cross_check(cell("1,000", "千元"), Decimal("1000501"), None), "mismatch")
        self.assertTrue(precision_only(cell("100.04", "万元"), cell("1000", "千元")))
        self.assertFalse(precision_only(cell("100.06", "万元"), cell("1000", "千元")))

    def test_row_label_variants(self):
        for label, metric in (
            ("一、 营业收入", "OPERATE_INCOME"),
            ("其中： 营业收入", "OPERATE_INCOME"),
            ("其中：营业成本", "OPERATE_COST"),
            ("减：营业成本", "OPERATE_COST"),
            ("1. 归属于母公司股东的净利润（净亏损以“－”号填列）", "PARENT_NETPROFIT"),
            ("（一）归属于母公司所有者的\n净利润", "PARENT_NETPROFIT"),
            ("经营活动产生的现金流量净额", "NETCASH_OPERATE"),
        ):
            with self.subTest(label=label):
                self.assertTrue(label_matches(label, metric))
        self.assertFalse(label_matches("营业总成本", "OPERATE_COST"))
        self.assertFalse(label_matches("营业总收入", "OPERATE_INCOME"))
        self.assertFalse(label_matches("少数股东损益", "PARENT_NETPROFIT"))
        self.assertFalse(label_matches("流动负债合计", "TOTAL_LIABILITIES"))


if __name__ == "__main__":
    unittest.main()
