import unittest

from app.potential import reference_context
from app.spec_up import attach_spec_up, starforce_action, starforce_recommendation_cap


class StarforceTargets(unittest.TestCase):
    def test_caps(self):
        cases = {'에테르넬 시프셔츠': 26, '죽음의 맹세': 26, '컴플리트 언더컨트롤': 25, '루즈 컨트롤 머신 마크': 23, '거대한 공포': 25,
                 '아케인셰이드 시프슈즈': 23, '트와일라이트 마크': 23, '마이스터링': 23,
                 '마이스터 이어링': 22, '앱솔랩스 시프슈즈': 22}
        for name, cap in cases.items():
            with self.subTest(name=name):
                self.assertEqual(starforce_recommendation_cap(name), cap)

    def test_target_gain_is_cumulative_and_has_no_price(self):
        c = reference_context('나이트로드', 'luk')
        args = ('몽환의 벨트', '벨트', '벨트', 200, 22, 30, '나이트로드', 'luk', c)
        a, b = starforce_action(*args, 23), starforce_action(*args, 25)
        self.assertGreater(b['final_damage'], a['final_damage'])
        self.assertNotIn('cost', b)  # 비용은 노작 시세로 화면이 계산합니다
        self.assertIsNone(starforce_action(*args, 26))
        self.assertIsNone(starforce_action(*args, 22))

    def test_all_targets_are_attached_and_cap_reached_stops(self):
        c = reference_context('나이트로드', 'luk')
        row = {'slot': '벨트', 'name': '몽환의 벨트', 'part': '벨트',
               'is_wse': False, 'is_special': False, 'level': 200}
        item = {'item_equipment_slot': '벨트', 'item_name': row['name'], 'starforce': '22'}
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(sorted(a['target_star'] for a in row['spec_up']), [23, 24, 25])
        stages = sorted(row['spec_up'], key=lambda a: a['star'])
        self.assertEqual([(a['star'], a['target_star']) for a in stages], [(22, 23), (23, 24), (24, 25)])
        cumulative = starforce_action(row['name'], '벨트', '벨트', 200, 22, 25, '나이트로드', 'luk', c, 25)
        import math
        self.assertAlmostEqual(100 * (math.prod(1 + a['final_damage'] / 100 for a in stages) - 1), cumulative['final_damage'])
        item['starforce'] = '25'
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(row['spec_up'], [])
        item['starforce'] = '11'
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(row['spec_up'], [])
