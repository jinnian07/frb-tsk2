"""judger.core.final_score 综合分与静态扣分。"""
import unittest

from judger.core.final_score import (
    clip01,
    compute_baremetal_final_score,
    count_clang_tidy_violations,
    flash_efficiency_points,
    ram_efficiency_points,
    static_check_points,
)


class TestFinalScore(unittest.TestCase):
    def test_clip01(self):
        self.assertEqual(clip01(-1), 0.0)
        self.assertEqual(clip01(0.5), 0.5)
        self.assertEqual(clip01(2), 1.0)

    def test_flash_ram_efficiency(self):
        rep = {
            "sections_summary_bytes": {
                "flash": {"total_used": 500},
                "ram": {"total_used": 250},
            },
            "limits": {"flash_bytes": 1000, "ram_bytes": 1000},
        }
        self.assertAlmostEqual(flash_efficiency_points(rep), 5.0)
        self.assertAlmostEqual(ram_efficiency_points(rep), 7.5)

    def test_flash_invalid_limit(self):
        rep = {
            "sections_summary_bytes": {
                "flash": {"total_used": 100},
                "ram": {"total_used": 0},
            },
            "limits": {"flash_bytes": None, "ram_bytes": 1024},
        }
        self.assertEqual(flash_efficiency_points(rep), 0.0)
        self.assertAlmostEqual(ram_efficiency_points(rep), 10.0)

    def test_static_deduction_caps(self):
        # 10 MISRA * 2 = 20 -> cap 12
        lines = "\n".join(
            f"x.c:{i}:1: warning: x [misc-misra-c2012-1.1]" for i in range(10)
        )
        counts = count_clang_tidy_violations(lines)
        self.assertEqual(counts["misra"], 10)
        s = static_check_points(lines, True)
        self.assertEqual(s, 18.0)  # 30 - 12

    def test_static_unused_cap(self):
        lines = "\n".join(
            f"x.c:{i}:1: warning: x [misc-unused-parameters]" for i in range(20)
        )
        s = static_check_points(lines, True)
        self.assertEqual(s, 27.0)  # 30 - min(6, 3) = 27

    def test_static_ineligible(self):
        self.assertEqual(static_check_points("anything", False), 0.0)

    def test_full_compute(self):
        rep = {
            "sections_summary_bytes": {
                "flash": {"total_used": 0},
                "ram": {"total_used": 0},
            },
            "limits": {"flash_bytes": 1000, "ram_bytes": 1000},
        }
        total, bd = compute_baremetal_final_score(
            static_eligible=True,
            static_clang_output="",
            line_pct=100.0,
            branch_pct=100.0,
            survival_rate=1.0,
            injection_total=3,
            normal_stack_scores=[100],
            map_report=rep,
        )
        # 30 + 10 + 10 + 20 + 10 + 10 + 10 = 100
        self.assertEqual(total, 100.0)
        self.assertEqual(bd["total"], 100.0)


if __name__ == "__main__":
    unittest.main()
