import unittest

from app.potential import reference_context
from app.spec_up import attach_spec_up, sequential_recommendations, starforce_action, starforce_recommendation_cap
from app.starforce_cost import expected_cost


class StarforceTargets(unittest.TestCase):
    def test_caps(self):
        cases = {'에테르넬 시프셔츠': 26, '죽음의 맹세': 26, '컴플리트 언더컨트롤': 25,
                 '아케인셰이드 시프슈즈': 23, '트와일라이트 마크': 23, '마이스터링': 23,
                 '마이스터 이어링': 22, '앱솔랩스 시프슈즈': 22}
        for name, cap in cases.items():
            with self.subTest(name=name):
                self.assertEqual(starforce_recommendation_cap(name), cap)

    def test_target_gain_and_cost_are_cumulative(self):
        c = reference_context('나이트로드', 'luk')
        args = ('루즈 컨트롤 머신 마크', '눈장식', '눈장식', 160, 22, 30, '나이트로드', 'luk', c)
        a, b = starforce_action(*args, 23), starforce_action(*args, 25)
        self.assertGreater(b['final_damage'], a['final_damage'])
        self.assertEqual(b['cost'], expected_cost(160, b['base_price'], 22, 25))
        self.assertIsNone(starforce_action(*args, 26))
        self.assertIsNone(starforce_action(*args, 22))

    def test_all_targets_are_attached_and_cap_reached_stops(self):
        c = reference_context('나이트로드', 'luk')
        row = {'slot': '눈장식', 'name': '루즈 컨트롤 머신 마크', 'part': '눈장식',
               'is_wse': False, 'is_special': False, 'level': 160}
        item = {'item_equipment_slot': '눈장식', 'item_name': row['name'], 'starforce': '22'}
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(sorted(a['target_star'] for a in row['spec_up']), [23, 24, 25])
        stages = sorted(row['spec_up'], key=lambda a: a['star'])
        self.assertEqual([(a['star'], a['target_star']) for a in stages], [(22, 23), (23, 24), (24, 25)])
        cumulative = starforce_action(row['name'], '눈장식', '눈장식', 160, 22, 25, '나이트로드', 'luk', c, 25)
        import math
        self.assertAlmostEqual(100 * (math.prod(1 + a['final_damage'] / 100 for a in stages) - 1), cumulative['final_damage'])
        self.assertAlmostEqual(sum(a['cost'] for a in stages), cumulative['cost'], delta=2)
        item['starforce'] = '25'
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(row['spec_up'], [])
        item['starforce'] = '11'
        attach_spec_up([row], [item], c, '나이트로드', 'luk', 298)
        self.assertEqual(row['spec_up'], [])

    def test_chain_unlock_merge_and_interleaving(self):
        def star(start, damage):
            return dict(axis='스타포스', slot='a', star=start, target_star=start+1, cost=1e10,
                        final_damage=damage, gain=damage*10, damage_efficiency=damage, efficiency=damage*10)
        other = dict(axis='잠재', slot='b', cost=1e10, final_damage=2, gain=20, damage_efficiency=2)
        result = sequential_recommendations([star(23, 4), star(22, 3), other])
        self.assertEqual([(a['axis'], a.get('target_star')) for a in result], [('스타포스', 24), ('잠재', None)])
        self.assertAlmostEqual(result[0]['final_damage'], 7.12)
        self.assertEqual(result[0]['cost'], 2e10)
        result = sequential_recommendations([star(22, 3), star(23, 1), other])
        self.assertEqual([a['axis'] for a in result], ['스타포스', '잠재', '스타포스'])
        self.assertEqual((result[2]['star'], result[2]['target_star']), (23, 24))
        # A lucrative successor cannot jump ahead of the weak prerequisite.
        result = sequential_recommendations([star(22, 1), star(23, 5), other])
        self.assertEqual(result[0]['axis'], '잠재')
