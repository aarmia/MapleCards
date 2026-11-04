import unittest

from app.combat_power import adjust_combat_power, combat_power
from app.crit_damage import boss_crit_damage, growth_crit_damage
from app.analyzer import _power_estimate_limit


class CombatPowerTests(unittest.TestCase):
    def test_growth_crit_excludes_skills_without_changing_boss_basis(self):
        extras = {"link_skill": {"character_link_skill": [{"skill_effect": "크리티컬 데미지 6% 증가"}]},
                  "guild": {"guild_noblesse_skill": [{"skill_effect": "크리티컬 데미지 30% 증가"}]}}
        gear = [{"potential_option_1": "크리티컬 데미지 +8%"}]
        self.assertEqual(growth_crit_damage("칼리", gear, extras)[0], 8)
        self.assertGreater(boss_crit_damage("칼리", gear, extras)[0], 8)

    def test_unverified_normalization_does_not_publish_estimate(self):
        gear = [{"item_equipment_slot": "무기", "item_name": "차크람", "starforce": "22"}]
        self.assertIsNone(_power_estimate_limit("칼리", gear, gear))
        self.assertTrue(_power_estimate_limit("칼리", gear, [{**gear[0], "starforce": "23"}]))
        self.assertTrue(_power_estimate_limit("제로", gear, gear))
        self.assertTrue(_power_estimate_limit("칼리", [], [{"item_equipment_slot": "보조무기", "item_name": "아스트라 아워글라스"}]))

    def test_active_sources_not_alternative_or_saved_copies(self):
        line = "크리티컬 데미지 +3%"
        core = [{"sub_stat_name_1": "크리티컬 데미지 증가", "sub_stat_level_1": 4}]
        union = {"union_raider_stat": [line], "union_state_stat": [line], "union_occupied_stat": [line]}
        extras = {"union_raider": union, "hexa_stat": {"character_hexa_stat_core_2": core,
                  "character_hexa_stat_core_3": core, "preset_hexa_stat_core_2": core}}
        _, parts = boss_crit_damage("", [{"soul_option": line, "soul_potential_option_2": line}], extras)
        self.assertEqual((parts["gear"], parts["union"], parts["hexa_stat"]), (6, 6, 2.8))
        union["union_state_stat"] = []
        self.assertEqual(boss_crit_damage("", [], extras)[1]["union"], 3)
        del union["union_state_stat"]
        self.assertEqual(boss_crit_damage("", [], extras)[1]["union"], 6)

    def test_formula_matches_measured_character_within_one_percent(self):
        # 엔젤릭버스터 실측: DEX 121,178 · STR 19,014 · 공격력 15,944 · 보공 573% · 크뎀 170.9% → 전투력 1,467,545,513
        value = combat_power(121178, 19014, 15944, 573, 170.9)
        self.assertAlmostEqual(value / 1_467_545_513, 1.0, delta=0.01)

    def test_adjustment_scales_api_for_both_directions_of_baseline_error(self):
        current = dict(main_stat=100000, sub_stat=10000, attack=20000, boss_damage_pct=500, crit_damage_pct=150)
        optimal = {**current, "main_stat": 110000, "boss_damage_pct": 520}
        low_api = combat_power(**current) * 0.9
        high_api = combat_power(**current) * 1.1
        ratio = combat_power(**optimal) / combat_power(**current)
        self.assertEqual(adjust_combat_power(low_api, current, optimal, is_current=False)["adjusted"], round(low_api * ratio))
        self.assertEqual(adjust_combat_power(high_api, current, optimal, is_current=False)["adjusted"], round(high_api * ratio))
        self.assertEqual(adjust_combat_power(low_api, current, optimal, is_current=True)["adjusted"], low_api)
        high_api_lower_projection = combat_power(**optimal) * 1.1
        self.assertEqual(adjust_combat_power(high_api_lower_projection, optimal, current, is_current=False)["status"], "current")  # 두 추정 모두 API 아래면 그대로

    def test_sena_snapshot_small_preset_change_does_not_replace_api_with_absolute_power(self):
        # Preserve the audit snapshot's two formula outputs exactly (unit stat factor).
        current = dict(main_stat=25, sub_stat=0, attack=1620916961, boss_damage_pct=0, crit_damage_pct=0)
        optimal = {**current, "attack": 1622314957}
        result = adjust_combat_power(1467545513, current, optimal, is_current=False)
        self.assertEqual((result["adjusted"], result["ratio"], result["status"]), (1467545513, 1, "current"))

    def test_half_percent_boundary(self):
        current = dict(main_stat=25, sub_stat=0, attack=1000, boss_damage_pct=0, crit_damage_pct=0)
        for attack, expected in ((990, 8000), (1000, 8000), (1005, 8000), (1006, 8048)):
            with self.subTest(attack=attack):
                self.assertEqual(adjust_combat_power(8000, current, {**current, "attack": attack}, False)["adjusted"], expected)


    def test_boss_crit_damage_sums_sources_and_skips_duplicate_sharp_eyes(self):
        extras = {
            "hyper_stat": {"use_preset_no": "1", "hyper_stat_preset_1": [{"stat_type": "크리티컬 데미지", "stat_level": 14, "stat_increase": "크리티컬 데미지 14% 증가"}]},
            "hexa_stat": {"character_hexa_stat_core": [{"main_stat_name": "크리티컬 데미지 증가", "main_stat_level": 10,
                                                        "sub_stat_name_1": "크리티컬 데미지 증가", "sub_stat_level_1": 4}]},
            "union_champion": {"champion_badge_total_info": [{"stat": "크리티컬 데미지 15.00% 증가"}]},
            "guild": {"guild_noblesse_skill": [{"skill_effect": "30분 동안 크리티컬 데미지 30% 증가, 재사용 대기시간 5분"}]},
        }
        total, parts = boss_crit_damage("엔젤릭버스터", [{"potential_option_1": "크리티컬 데미지 +8%"}], extras)
        # 장비 8 + 하이퍼 14 + 헥사 7.0(메인 10) + 1.4(추가 4) + 챔피언 15 + 직업 패시브 21 + 소울 게이즈 17 + 노블레스 30 + 쓸만한 샤프 아이즈 8
        self.assertAlmostEqual(total, 121.4)
        self.assertEqual(boss_crit_damage("보우마스터", [], {})[1]["useful_sharp_eyes"], 0)  # 직업 샤프 아이즈와 중복되지 않습니다


if __name__ == "__main__":
    unittest.main()
