import copy
import unittest
from dataclasses import replace

from app.equip_value import _flat, attach_values, equivalent
from app.potential import reference_context
from app.preset_optimizer import boss_index
from app.set_projection import DAWN, DAWN_TABLE


class EquipValueTests(unittest.TestCase):
    def fixture(self, two=False):
        ring = {"item_name": "여명의 가디언 엔젤 링", "item_equipment_slot": "반지1",
                "potential_option_1": "LUK +12%", "item_total_option": dict(luk=0, str=0, dex=0, attack_power=0)}
        items = [ring]
        if two:
            items.append({"item_name": "데이브레이크 펜던트", "item_equipment_slot": "펜던트",
                          "item_total_option": dict(luk=0, dex=0, attack_power=0)})
        ctx = replace(reference_context("나이트워커", "luk"), base_stat=9000, stat_pct=10, exempt_stat=30000,
                      final_main=120000, final_sub=12000, attack=15000, attack_multiplier=3,
                      sources={"sub_base_estimate": 4000, "sub_multiplier": 3, "sub_exempt": 0})
        opt = {"method": "saved_presets_boss_stat_index", "damage": 50, "boss_damage": 500, "ignore_defense": 98}
        data = {"set_effect": [{"set_name": DAWN, "total_set_count": len(items),
                                "set_option_full": DAWN_TABLE, "set_effect_info": DAWN_TABLE[:int(two)]}]}
        return items, {"context": ctx, "optimization": opt}, data

    def value(self, items, profile, data):
        row = {"slot": "반지1", "is_wse": False, "is_special": False, "total_score": 42, "score_ratio": 83}
        attach_values([row], items, items, profile, 298, data)
        self.assertEqual((row["total_score"], row["score_ratio"]), (42, 83))
        return row["equip_value"]

    def test_twelve_percent_only_is_twelve_and_no_threshold_has_zero_set_gain(self):
        v = self.value(*self.fixture())
        self.assertEqual((v["item"], v["set"], v["total"]), (12, 0, 12))

    def test_same_loadout_has_zero_equivalent_gain(self):
        _, profile, _ = self.fixture()
        state = (profile["context"], 50, 500, .02)
        self.assertEqual(equivalent(state, boss_index(*state)), 0)

    def test_threshold_and_unknown_set(self):
        items, profile, data = self.fixture(two=True)
        v = self.value(items, profile, data)
        self.assertAlmostEqual(v["item"], 12)
        self.assertGreater(v["set"], 0)
        self.assertAlmostEqual(v["total"], v["item"] + v["set"], places=1)
        unknown = self.value(items, profile, None)
        self.assertEqual((unknown["status"], unknown["item"], unknown["total"]), ("partial", 12, None))

    def test_exceptional_is_counted_once(self):
        item = {key: {"luk": 20} for key in ("item_base_option", "item_add_option", "item_etc_option", "item_starforce_option")}
        item["item_exceptional_option"] = {"luk": 5}
        for total in (80, 85):
            item["item_total_option"] = {"luk": total}
            self.assertEqual(_flat(item, "luk"), 85)

    def test_identical_item_swap_gains_nothing(self):
        items, profile, data = self.fixture(two=True)
        before = self.value(items, profile, data)
        swapped = [copy.deepcopy(items[0]), items[1]]
        after = self.value(swapped, profile, data)
        self.assertEqual(after, before)

    def test_swap_within_same_set_tier_keeps_set_share(self):
        items, profile, data = self.fixture(two=True)
        before = self.value(items, profile, data)
        weaker = {**items[0], "potential_option_1": "LUK +9%"}
        after = self.value([weaker, items[1]], profile, data)
        self.assertAlmostEqual(after["set"] - before["set"], 0, places=2)
        self.assertAlmostEqual(before["item"] - after["item"], 3, places=2)

    def test_item_outside_any_set_has_zero_set_share(self):
        items, profile, data = self.fixture()
        items[0]["item_name"] = "세트 없는 반지"
        data["set_effect"][0]["total_set_count"] = 0
        v = self.value(items, profile, data)
        self.assertEqual((v["status"], v["item"], v["set"], v["total"]), ("estimated", 12, 0, 12))

    def test_item_outside_known_sets_is_partial_when_api_has_unregistered_set(self):
        items, profile, data = self.fixture()
        items[0]["item_name"] = "세트 없는 반지"
        data["set_effect"] = [{"set_name": "미등록 세트", "total_set_count": 1, "set_option_full": [], "set_effect_info": []}]
        v = self.value(items, profile, data)
        self.assertEqual((v["status"], v["item"], v["set"]), ("partial", 12, None))

    def multi_sub_fixture(self):
        items, profile, data = self.fixture()
        parts = {"STR": {"base": 2000, "mult": 1.5, "exempt": 0}, "DEX": {"base": 3000, "mult": 2, "exempt": 100}}
        profile["context"] = replace(profile["context"], sub_names=("STR", "DEX"), final_sub=2000 * 1.5 + 3000 * 2 + 100,
                                     sources={"subs": parts})
        return items, profile, data

    def test_multi_sub_class_main_stat_line_is_exact(self):
        v = self.value(*self.multi_sub_fixture())
        self.assertEqual((v["status"], v["item"], v["set"], v["total"]), ("estimated", 12, 0, 12))

    def test_multi_sub_class_counts_each_sub_stat(self):
        items, profile, data = self.multi_sub_fixture()
        items[0]["item_total_option"] = dict(luk=0, str=100, dex=100, attack_power=0)
        with_subs = self.value(items, profile, data)["item"]
        items[0]["item_total_option"] = dict(luk=0, str=100, dex=0, attack_power=0)
        str_only = self.value(items, profile, data)["item"]
        self.assertGreater(with_subs, str_only)
        self.assertGreater(str_only, 12)
        # DEX 의 배율(2)이 STR(1.5)보다 커서 같은 깡스탯이라도 DEX 몫이 더 커야 합니다.
        self.assertGreater(with_subs - str_only, str_only - 12)

    def test_multi_sub_class_without_reconstruction_is_unavailable(self):
        items, profile, data = self.multi_sub_fixture()
        profile["context"] = replace(profile["context"], sources={})
        self.assertEqual(self.value(items, profile, data)["status"], "unavailable")

    def test_single_sub_class_reports_no_reason(self):
        v = self.value(*self.fixture())
        self.assertEqual((v["status"], v["reason"]), ("estimated", ""))
