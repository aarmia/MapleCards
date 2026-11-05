import unittest
from dataclasses import replace

from app.potential import reference_context
from app.preset_projection import flat_delta, flat_potential, project_context


class PresetProjectionTests(unittest.TestCase):
    def test_flat_options_do_not_include_percent_or_duplicate_item_total(self):
        item = {"item_total_option": {"luk": "100"},
                "potential_option_1": "LUK +12%", "potential_option_2": "LUK +20",
                "additional_potential_option_1": "캐릭터 기준 9레벨 당 LUK +2",
                "additional_potential_option_2": "올스탯 +5"}
        self.assertEqual(flat_potential([item], "LUK", 298), 91)
        delta = flat_delta([{"item_total_option": {"luk": "80"}}], [item], "luk", "LUK", 298)
        self.assertEqual(delta["total"], 111)

    def test_reserve_ring_is_excluded(self):
        item = {"item_equipment_slot": "예비 특수 반지", "potential_option_1": "LUK +100"}
        self.assertEqual(flat_potential([item], "LUK", 298), 0)

    def test_projection_updates_attack_substat_and_preserves_identical_stats(self):
        current = replace(reference_context("나이트워커", "luk"), base_stat=8000,
                          attack=10000, attack_multiplier=2, final_sub=9000,
                          sources={"sub_base_estimate": 4000, "sub_exempt": 1000, "sub_multiplier": 2})
        selected = replace(current, attack_multiplier=3, stat_pct=10,
                           sources={"sub_multiplier": 3})
        a = [{"item_name": "a", "item_total_option": {"luk": "100", "dex": "50", "attack_power": "20"}}]
        b = [{"item_name": "b", "item_total_option": {"luk": "200", "dex": "70", "attack_power": "30"},
              "additional_potential_option_1": "공격력 +14"}]
        result = project_context(current, selected, a, b, 298)
        self.assertEqual(result.base_stat, 8100)
        self.assertEqual(result.attack, (5000 + 24) * 3)
        self.assertEqual(result.final_sub, 4020 * 3 + 1000)
        self.assertEqual(result.sources["sub_base_estimate"], 4020)
        self.assertEqual(result.sources["set_projection_status"], "unresolved")
        same = project_context(current, current, a, a, 298)
        self.assertEqual(same.final_main, current.final_main)
        self.assertEqual(same.attack, current.attack)
        self.assertEqual(same.sources["preset_projection"], "equipped")
        same_id = project_context(current, selected, a, b, 298, same_preset=True)
        self.assertEqual(same_id.attack, current.attack)
        self.assertEqual(same_id.final_sub, current.final_sub)

    def test_projection_updates_each_sub_stat_of_multi_sub_class(self):
        parts = {"STR": {"base": 1000, "mult": 2, "exempt": 100}, "DEX": {"base": 2000, "mult": 1.5, "exempt": 50}}
        current = replace(reference_context("섀도어", "luk"), base_stat=8000, attack=10000, attack_multiplier=2,
                          final_sub=1000 * 2 + 100 + 2000 * 1.5 + 50, sources={"subs": parts})
        selected = replace(current, sources={"subs": {"STR": {**parts["STR"], "mult": 3}, "DEX": {**parts["DEX"], "mult": 2}}})
        a = [{"item_name": "a", "item_total_option": {"luk": "0", "str": "10", "dex": "20", "attack_power": "0"}}]
        b = [{"item_name": "b", "item_total_option": {"luk": "0", "str": "40", "dex": "20", "attack_power": "0"},
              "potential_option_1": "올스탯 +5"}]
        result = project_context(current, selected, a, b, 298)
        self.assertEqual(result.sources["subs"]["STR"], {"base": 1035, "mult": 3, "exempt": 100})
        self.assertEqual(result.sources["subs"]["DEX"], {"base": 2005, "mult": 2, "exempt": 50})
        self.assertEqual(result.final_sub, 1035 * 3 + 100 + 2005 * 2 + 50)
        self.assertNotIn("sub_projection_unmodeled", result.sources)

    def test_missing_totals_are_not_treated_as_zero_equipment(self):
        delta = flat_delta([{}], [{"item_total_option": {"luk": "100"}}], "luk", "LUK", 298)
        self.assertEqual(delta["total"], 0)
        self.assertFalse(delta["item_totals_complete"])
