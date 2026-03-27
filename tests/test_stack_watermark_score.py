"""栈水位线 0～100 分量化（方案 A）手工验算用例。"""
from __future__ import annotations

import unittest

from judger.core.stack_watermark import (
    format_stack_watermark_composite_log_line,
    merge_stack_watermark_config,
    score_stack_watermark,
    stack_watermark_tier,
)


class TestStackWatermarkScore(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = merge_stack_watermark_config(None, None)

    def test_d_06s_safe_100(self) -> None:
        # D=0.6S ≤ 0.7S
        self.assertEqual(score_stack_watermark(600, 1000, self.cfg), 100)
        self.assertEqual(stack_watermark_tier(600, 1000, self.cfg), "安全")

    def test_d_08s_warn_95(self) -> None:
        # D=0.8S：100 - (0.1S/S)*50 = 95
        self.assertEqual(score_stack_watermark(800, 1000, self.cfg), 95)
        self.assertEqual(stack_watermark_tier(800, 1000, self.cfg), "预警")

    def test_d_09s_boundary_warn_90(self) -> None:
        # D=0.9S：100 - (0.2S/S)*50 = 90
        self.assertEqual(score_stack_watermark(900, 1000, self.cfg), 90)
        self.assertEqual(stack_watermark_tier(900, 1000, self.cfg), "预警")

    def test_d_095s_danger_65(self) -> None:
        # score_at_09=90；90 - ((0.05S/S)*100 + 20) = 90 - 25 = 65
        self.assertEqual(score_stack_watermark(950, 1000, self.cfg), 65)
        self.assertEqual(stack_watermark_tier(950, 1000, self.cfg), "危险")

    def test_d_s_danger_60(self) -> None:
        # D=S：90 - (0.1*100 + 20) = 60
        self.assertEqual(score_stack_watermark(1000, 1000, self.cfg), 60)
        self.assertEqual(stack_watermark_tier(1000, 1000, self.cfg), "危险")

    def test_d_gt_s_severe_0(self) -> None:
        self.assertEqual(score_stack_watermark(1100, 1000, self.cfg), 0)
        self.assertEqual(stack_watermark_tier(1100, 1000, self.cfg), "严重")

    def test_s_zero_or_negative_none(self) -> None:
        self.assertIsNone(score_stack_watermark(100, 0, self.cfg))
        self.assertIsNone(stack_watermark_tier(100, 0, self.cfg))
        self.assertIsNone(score_stack_watermark(100, -1, self.cfg))

    def test_d_none_none(self) -> None:
        self.assertIsNone(score_stack_watermark(None, 1000, self.cfg))
        self.assertIsNone(stack_watermark_tier(None, 1000, self.cfg))

    def test_s_none_none(self) -> None:
        self.assertIsNone(score_stack_watermark(500, None, self.cfg))
        self.assertIsNone(stack_watermark_tier(500, None, self.cfg))

    def test_composite_log_line(self) -> None:
        self.assertEqual(
            format_stack_watermark_composite_log_line([]),
            "堆栈水位线综合评分：无有效数据",
        )
        self.assertEqual(
            format_stack_watermark_composite_log_line([None, 100]),
            "堆栈水位线综合评分==100.0（安全）",
        )
        self.assertIn("95.0", format_stack_watermark_composite_log_line([100, 90]))
        self.assertIn("预警", format_stack_watermark_composite_log_line([100, 90]))


if __name__ == "__main__":
    unittest.main()
