import unittest

from app.combat_power import adjust_combat_power, combat_power
from app.crit_damage import boss_crit_damage


class CombatPowerTests(unittest.TestCase):
    def test_formula_matches_measured_character_within_one_percent(self):
        # 엔젤릭버스터 실측: DEX 121,178 · STR 19,014 · 공격력 15,944 · 보공 573% · 크뎀 170.9% → 전투력 1,467,545,513
        value = combat_power(121178, 19014, 15944, 573, 170.9)
        self.assertAlmostEqual(value / 1_467_545_513, 1.0, delta=0.01)

    def test_adjustment_takes_the_larger_of_absolute_and_scaled_projection(self):
        current = dict(main_stat=100000, sub_stat=10000, attack=20000, boss_damage_pct=500, crit_damage_pct=150)
        optimal = {**current, "main_stat": 110000, "boss_damage_pct": 520}
        low_api = combat_power(**current) * 0.9  # API 전투력이 스탯보다 낮게 잡힌 스냅샷 → 절대값
        self.assertEqual(adjust_combat_power(low_api, current, optimal, is_current=False)["adjusted"], round(combat_power(**optimal)))
        high_api = combat_power(**current) * 1.1  # API 전투력이 공식보다 큰 직업 → 비율 보정
        ratio = combat_power(**optimal) / combat_power(**current)
        self.assertEqual(adjust_combat_power(high_api, current, optimal, is_current=False)["adjusted"], round(high_api * ratio))
        self.assertEqual(adjust_combat_power(low_api, current, optimal, is_current=True)["adjusted"], low_api)
        high_api_lower_projection = combat_power(**optimal) * 1.1
        self.assertEqual(adjust_combat_power(high_api_lower_projection, optimal, current, is_current=False)["status"], "current")  # 두 추정 모두 API 아래면 그대로


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
