import copy
import unittest
from dataclasses import replace

from app.character_profile import build_stat_context
from app.potential import reference_context
from app.preset_optimizer import boss_index, effects, group_candidates, optimize_presets


class PresetOptimizerTests(unittest.TestCase):
    def test_current_effects_are_not_added_twice_and_percent_preserves_base(self):
        stats = {"final_stat": [{"stat_name": k, "stat_value": str(v)} for k, v in {
            "STR": 40000, "DEX": 5000, "공격력": 3000, "데미지": 15,
            "보스 몬스터 데미지": 20, "방어율 무시": 99, "크리티컬 확률": 100,
            "크리티컬 데미지": 100}.items()]}
        item = {"item_name": "same", "item_total_option": {"str": "100", "dex": "50", "attack_power": "10"}}
        data = {"preset_no": 1, "item_equipment": [item], "item_equipment_preset_1": [item]}
        skills = [{"skill_effect": "데미지 15% 증가"}]
        extras = {"symbol": {"symbol": []}, "link_skill": {
            "character_link_skill": skills, "character_link_skill_preset_1": skills}}
        current = build_stat_context("히어로", "str", 298, [item], stats, extras)
        unchanged = optimize_presets("히어로", "str", 298, data, stats, extras, current)
        self.assertEqual(unchanged[3]["relative_index"], 1)
        self.assertEqual(unchanged[2].final_main, current.final_main)
        self.assertEqual(unchanged[2].attack, current.attack)
        extras["link_skill"]["character_link_skill_preset_3"] = skills + [{"skill_effect": "모든 능력치 15% 증가"}]
        changed = optimize_presets("히어로", "str", 298, data, stats, extras, current)
        self.assertEqual(changed[3]["selected"]["link"], "3")
        self.assertEqual(changed[2].base_stat, current.base_stat)
        self.assertAlmostEqual(changed[2].stat_pct, current.stat_pct + .15)
        self.assertAlmostEqual(changed[2].final_main, current.final_main + current.base_stat * .15)

    def test_parser_does_not_count_conditional_or_hunting_damage(self):
        c = reference_context("히어로", "str")
        result = effects(["일반 몬스터 공격 시 데미지 50% 증가", "10초 동안 데미지 60% 증가",
                          "공격 시 20%의 확률로 데미지 20% 증가", "보스 몬스터 공격 시 데미지 20% 증가",
                          "크리티컬 데미지 20.00% 증가", "공격력과 마력 15 증가"], c)
        self.assertEqual(result["damage"], 0)
        self.assertEqual(result["boss"], 20)
        self.assertEqual(result["crit"], 20)
        self.assertEqual(result["attack"], 15)
        self.assertEqual(len(result["ignored"]), 3)

    def test_crit_rate_caps_and_ied_multiplies(self):
        c = reference_context("히어로", "str")
        self.assertEqual(boss_index(replace(c, crit_rate=100), 0, 0, .01),
                         boss_index(replace(c, crit_rate=150), 0, 0, .01))
        result = effects(["방어율 무시 30% 증가", "방어율 무시 40% 증가"], c)
        self.assertAlmostEqual(result["ied"], .42)

    def test_saved_combination_beats_hunting_and_does_not_mutate_inputs(self):
        stats = {"final_stat": [{"stat_name": key, "stat_value": str(value)} for key, value in {
            "STR": 40000, "DEX": 5000, "공격력": 3000, "데미지": 0,
            "보스 몬스터 데미지": 0, "방어율 무시": 99, "크리티컬 확률": 100,
            "크리티컬 데미지": 100}.items()]}
        item = {"item_name": "same", "item_equipment_slot": "모자", "item_total_option": {"str": "100", "dex": "100", "attack_power": "10"}}
        items = {"preset_no": 1, "item_equipment": [item], "item_equipment_preset_1": [item]}

        def row(text):
            return {"stat_level": 1, "stat_increase": text}

        def skill(text):
            return {"skill_effect": text}

        def ability(text):
            return {"ability_info": [{"ability_value": text}]}

        extras = {"symbol": {"symbol": []},
            "hyper_stat": {"use_preset_no": "1", "hyper_stat_preset_1": [row("일반 몬스터 공격 시 데미지 100% 증가")],
                           "hyper_stat_preset_2": [row("보스 몬스터 공격 시 데미지 40% 증가")]},
            "union_raider": {"union_state_stat": ["획득 경험치 10% 증가"], "union_state_stat_preset": [
                {"preset_no": 1, "union_state_stat": ["획득 경험치 10% 증가"]},
                {"preset_no": 10, "union_state_stat": ["크리티컬 데미지 20% 증가"]}]},
            "link_skill": {"character_link_skill": [skill("획득 경험치 15% 증가")],
                "character_link_skill_preset_1": [skill("획득 경험치 15% 증가")],
                "character_link_skill_preset_3": [skill("보스 몬스터 공격 시 데미지 20% 증가")]},
            "ability": {**ability("아이템 드롭률 20% 증가"), "ability_preset_1": ability("아이템 드롭률 20% 증가"),
                        "ability_preset_2": ability("공격력 30 증가")}}
        before = copy.deepcopy(extras)
        current = build_stat_context("히어로", "str", 298, [item], stats, extras)
        result = optimize_presets("히어로", "str", 298, items, stats, extras, current)
        self.assertEqual(result[3]["selected"], {"equipment": 1, "hyper_stat": "2", "union": "10", "link": "3", "ability": "2"})
        self.assertEqual(result[3]["combinations_evaluated"], 16)
        self.assertGreater(result[3]["relative_index"], 1)
        self.assertEqual(result[2].crit_damage, 120)
        self.assertEqual(result[2].attack, 3030)
        self.assertEqual(result[2].base_stat, current.base_stat)
        self.assertEqual(extras, before)

    def test_empty_saved_slots_stay_current(self):
        groups = group_candidates({})
        self.assertTrue(all(not saved for _, _, _, saved in groups))

    def test_missing_boss_stats_do_not_claim_optimization(self):
        self.assertIsNone(optimize_presets("히어로", "str", 298, {}, {}, {}, reference_context("히어로", "str")))

    def test_effects_track_each_sub_stat_for_multi_sub_class(self):
        context = reference_context("섀도어", "luk")
        result = effects(["STR +30", "DEX +5%", "올스탯 +10"], context)
        self.assertEqual(result["subs"]["STR"], {"flat": 40, "exempt": 0, "pct": 0})
        self.assertEqual(result["subs"]["DEX"], {"flat": 10, "exempt": 0, "pct": 5})
        self.assertEqual(effects(["STR +30"], context, exempt=True)["subs"]["STR"]["exempt"], 30)
        self.assertNotIn("subs", effects(["DEX +30"], reference_context("나이트워커", "luk")))

    def test_multi_sub_profile_reconstructs_each_sub_stat_exactly(self):
        stats = {"final_stat": [{"stat_name": k, "stat_value": str(v)} for k, v in {
            "LUK": 40000, "STR": 4000, "DEX": 6000, "공격력": 3000, "크리티컬 확률": 100, "크리티컬 데미지": 100}.items()]}
        item = {"item_name": "x", "item_total_option": {"luk": "100", "str": "50", "dex": "50", "attack_power": "10"},
                "item_add_option": {"all_stat": "6"}, "potential_option_1": "DEX +12%"}
        context = build_stat_context("섀도어", "luk", 290, [item], stats, {})
        parts = context.sources["subs"]
        self.assertEqual((parts["STR"]["mult"], parts["DEX"]["mult"]), (1.06, 1.18))
        for label, final in (("STR", 4000), ("DEX", 6000)):
            p = parts[label]
            self.assertAlmostEqual(p["base"] * p["mult"] + p["exempt"], final)

