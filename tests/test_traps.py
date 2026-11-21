"""Trap-set grading rules (eval/run_traps.py), without model calls."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))
from run_traps import grade_item  # noqa: E402

TOL = {"元": {"absolute_yuan": 0}, "万元": {"relative": 0.005}, "亿元": {"relative": 0.005}}
PARENT = {"kind": "parent_scope", "unit": "元", "tolerance": TOL,
          "expected_value_yuan": "2886701207.85", "trap_value_yuan": "45104844200.13"}


class TrapGradingTest(unittest.TestCase):
    def test_parent_scope(self):
        self.assertEqual(grade_item(PARENT, "母公司营业收入为 **2,886,701,207.85 元**。[第122页]"), "correct")
        self.assertEqual(grade_item(PARENT, "营业收入为 **45,104,844,200.13 元**。[第118页]"), "trap")
        self.assertEqual(grade_item(PARENT, "年报中未找到相关数据。"), "refused")
        self.assertEqual(grade_item(PARENT, "营业收入为 **1,000.00 元**。"), "wrong")

    def test_unit(self):
        item = {"kind": "unit", "expected_unit": "千元"}
        self.assertEqual(grade_item(item, "合并资产负债表金额单位为**人民币千元**。[第153页]"), "correct")
        self.assertEqual(grade_item(item, "单位为元。[第139页]"), "wrong")
        self.assertEqual(grade_item({"kind": "unit", "expected_unit": "元"}, "单位：元（即人民币元）"), "correct")

    def test_refuse(self):
        item = {"kind": "refuse"}
        self.assertEqual(grade_item(item, "年报中未找到相关数据。"), "correct")
        self.assertEqual(grade_item(item, "南极洲地区营业收入为 **12,345.00 元**。"), "wrong")


if __name__ == "__main__":
    unittest.main()
