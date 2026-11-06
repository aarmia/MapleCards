import itertools
import math
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.calculator import calculate_item_score
from app.damage_index import ReplacementModel, boss_index, flame_options, line_options
from app.flame_damage import distribution, rewards
from app.potential import reference_context
from app.scoring_config import POTENTIAL, SPEC_UP
from app.spec_up import _flame_option, best_target, finish, flame_distribution, potential_actions


class DamageIndexTests(unittest.TestCase):
    def setUp(self):
        self.c = reference_context('나이트로드', 'luk')
        self.model = ReplacementModel(self.c)

    def test_identical_options_are_zero_and_old_options_are_removed(self):
        old = line_options(['LUK +12%', '공격력 +14'], 299)
        self.assertAlmostEqual(self.model.gain(old, old), 0)
        self.assertLess(self.model.gain(old, {}), 0)
        self.assertLess(self.model.gain(old, {'luk_pct': 15}), self.model.gain({}, {'luk_pct': 15}))

    def test_compound_stats_match_shared_index_not_sum(self):
        c = self.c
        raised = replace(c, base_stat=c.base_stat + 20, stat_pct=c.stat_pct + .06,
                         final_main=c.final_main + 20 * c.stat_pct + (c.base_stat + 20) * .06,
                         attack=c.attack + 14 * c.attack_multiplier, crit_damage=c.crit_damage + 3)
        result = self.model.gain({}, {'luk': 20, 'luk_pct': 6, 'attack_power': 14, 'crit': 3})
        expected = 100 * (boss_index(raised, 100, 500, .02) / boss_index(c, 100, 500, .02) - 1)
        self.assertAlmostEqual(result, expected, places=10)
        summed = sum(self.model.gain({}, part) for part in ({'luk': 20}, {'luk_pct': 6}, {'attack_power': 14}, {'crit': 3}))
        self.assertGreater(result, summed)

    def test_level_option_and_crit_rate_loss(self):
        self.assertEqual(line_options(['캐릭터 기준 9레벨 당 LUK +2'], 298), {'luk': 66})
        model = ReplacementModel(replace(self.c, crit_rate=100))
        self.assertLess(model.gain({'rate': 10}, {}), 0)
        self.assertAlmostEqual(model.gain({}, {'rate': 10}), 0)

    def test_shared_index_uses_buffed_attack_and_crit(self):
        c = self.c
        rate = min(100, c.crit_rate) / 100
        expected = c.stat_attack * c.attack_effective * (1 + (POTENTIAL.buff.damage_pct + POTENTIAL.buff.boss_damage_pct) / 100)
        expected *= 1 + rate * (.35 + c.crit_damage_effective / 100)
        self.assertAlmostEqual(boss_index(c), expected)

    def test_target_uses_conditional_damage_reward(self):
        dist = {10: .8, 20: .2}
        # At 10+: expected damage = .8*1+.2*5=1.8; cost 1. At 20+: damage5/cost5=1.
        best = best_target(dist, 0, 1, rewards={10: .8, 20: 1})
        self.assertEqual(best['target'], 10)
        self.assertAlmostEqual(best['reward'], 1.8)
        self.assertIsNone(best_target(dist, 0, 1, rewards={10: -1, 20: -.2}))

    def test_flame_moments_equal_bruteforce_and_preserve_probabilities(self):
        c = self.c
        config = {'options': ['luk', 'dex', 'all_stat', 'attack_power', 'armor'],
                  'lines': 2, 'tiers': [2, 3], 'tier_odds': [.3, .7]}
        before = {'luk': 15, 'all_stat': 2, 'attack_power': 3, 'dex': 4}
        with patch.dict(SPEC_UP.flame.raw, config):
            moments = distribution(200, '나이트로드', 'luk', c)
            actual = rewards(moments, before, c)
            old = flame_distribution(200, '나이트로드', 'luk', c)
            brute = {}
            for keys in itertools.combinations(config['options'], 2):
                for tiers in itertools.product(range(2), repeat=2):
                    prob = math.prod(config['tier_odds'][i] for i in tiers) / 10
                    option, grade = {}, 0
                    for key, t in zip(keys, tiers):
                        part = _flame_option(key, config['tiers'][t] + SPEC_UP.flame.boss_bonus, 200)
                        grade = round(grade + round(calculate_item_score(part, '나이트로드', 'luk', c), 1), 1)
                        for k, v in part.items():
                            option[k] = option.get(k, 0) + v
                    brute[grade] = brute.get(grade, 0) + prob * self.model.gain(flame_options(before), flame_options(option))
            self.assertEqual(set(moments), set(old))
            for grade in old:
                self.assertAlmostEqual(moments[grade][0], old[grade], places=12)
                self.assertAlmostEqual(actual[grade], brute[grade], places=10)

    def test_potential_action_emits_direct_reward_and_precise_sort_key(self):
        item = {'potential_option_grade': '레전드리', 'potential_option_1': 'LUK +6%'}
        actions = potential_actions(item, 'potential', '신발', 200, self.c, 298, cap=33)
        self.assertTrue(actions)
        result = finish(actions[0])
        self.assertEqual(result['model'], 'direct')
        self.assertGreater(result['final_damage'], 0)
        self.assertAlmostEqual(result['damage_efficiency'], actions[0]['final_damage'] / (actions[0]['cost'] / SPEC_UP.ranking.unit))
