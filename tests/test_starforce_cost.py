import unittest

from app.starforce_cost import EOK, base_price, expected_cost, optimal_plan


class StarforceCostTests(unittest.TestCase):
    def test_matches_starforce_gg_shata_table(self):
        # starforce.gg/ev 기본 화면(140제 · 노작 1억 · 샤타 without 15-16 · 강화 최적화)의 누적 기대값
        self.assertEqual(optimal_plan(140, 1e8), ((), (15,)))
        for end, eok in ((15, 1.48), (18, 6.08), (22, 59.08), (25, 948.55)):
            self.assertAlmostEqual(expected_cost(140, 1e8, 12, end) / EOK, eok, places=2)
        self.assertEqual(expected_cost(140, 1e8, 0, 12), 0)  # 12성 미만은 계산하지 않음

    def test_base_price_matches_name_and_part_or_slot(self):
        self.assertEqual(base_price("에테르넬 시프반다나", 250, "모자", "모자"), (0.3 * EOK, True))
        self.assertEqual(base_price("에테르넬 시프글러브", 250, "장갑", "장갑"), (20 * EOK, True))
        self.assertEqual(base_price("데스티니 대거", 250, "단검", "무기"), (100 * EOK, True))  # 무기는 장착 칸으로 맞춤
        self.assertEqual(base_price("처음 보는 반지", 200, "반지", "반지1"), (0.1 * EOK, False))  # 레벨별 기본값


if __name__ == "__main__":
    unittest.main()
