import unittest

from app.analyzer import evaluate_equipment
from app.calculator import weapon_starforce_option
from app.potential import reference_context
from app.scoring_config import POTENTIAL
from app.weapon import WeaponContext, best_lines_total, convert_weapon_line, weapon_add_value


def weapon_item(slot, part, **fields):
    item = {
        "item_equipment_slot": slot,
        "item_equipment_part": part,
        "item_name": f"테스트 {part}",
        "potential_option_grade": "레전드리",
        "potential_option_1": "공격력 +13%",
        "potential_option_2": "보스 몬스터 데미지 +40%",
        "potential_option_3": "몬스터 방어율 무시 +40%",
        "additional_potential_option_1": "공격력 +12%",
        "additional_potential_option_2": "STR +9%",
        "starforce": "22",
        "item_base_option": {"base_equipment_level": 200, "attack_power": "400"},
        "item_add_option": {"attack_power": "150", "boss_damage": "6"},
        "item_etc_option": {},
        "item_starforce_option": {"str": "145", "dex": "145", "attack_power": "250"},  # 200제 순수 공 400 의 22성 (무기 규칙)
    }
    item.update(fields)
    return item


class WeaponConversionTests(unittest.TestCase):
    def setUp(self):
        self.context = WeaponContext(reference_context("히어로", "str"), damage=100.0, boss_damage=300.0, ignore_defense=95.0)

    def test_lines_convert_to_attack_percent_units(self):
        c, buff = self.context, POTENTIAL.weapon.buff
        unit = 0.01 / (c.stats.attack_multiplier_effective + buff.attack_pct_extra / 100)
        boss = (0.01 / (1 + (100 + 300 + buff.boss_damage_extra) / 100)) / unit
        cases = {
            "공격력 +12%": 12.0,
            "마력 +12%": None,  # STR 직업에게 마력은 다른 직업 옵션
            "보스 몬스터 데미지 +40%": 40 * boss,
            "데미지 +10%": 10 * boss,
            "공격력 +30": 30 / (c.stats.attack / c.stats.attack_multiplier + POTENTIAL.buff.attack_flat) / unit,
            "STR +12%": 12 * c.stat_pct_unit,  # 유효 스탯 12% → 공% 단위
        }
        for option, expected in cases.items():
            with self.subTest(option=option):
                converted = convert_weapon_line(option, c, 285)
                if expected is None:
                    self.assertIsNone(converted)
                else:
                    self.assertAlmostEqual(converted[0], expected, places=9)
        # 방무: 보스 방어율 380% 에서 남은 방어율이 (1 − 0.4) 배가 되는 효과. 방무가 높을수록 값이 작아집니다
        low = WeaponContext(c.stats, damage=100.0, boss_damage=300.0, ignore_defense=80.0)
        self.assertGreater(convert_weapon_line("몬스터 방어율 무시 +40%", low, 285)[0], convert_weapon_line("몬스터 방어율 무시 +40%", c, 285)[0])
        self.assertGreater(c.ied_value(40), 0)
        # 엠블렘 기준점은 보공 줄이 없어 무기보다 작습니다
        self.assertLessEqual(best_lines_total("potential", "emblem", c, 285), best_lines_total("potential", "weapon", c, 285))
        # 데스티니 표(공 13%)는 제네시스·보조·엠블렘 표(공 12%)보다 기준점이 높습니다
        self.assertGreater(best_lines_total("potential", "weapon", c, 285, "destiny"), best_lines_total("potential", "weapon", c, 285, "default"))

    def test_flame_all_stat_is_a_percentage(self):
        # 불꽃 올스탯은 % (API all_stat 5 = 올스탯 5%). 고정 올스탯으로 읽으면 주스탯 몇 점보다도 작아집니다.
        _, entries = weapon_add_value({"all_stat": "5", "str": "60"}, self.context, 280)
        values = {entry["option"]: entry["value"] for entry in entries}
        self.assertAlmostEqual(values["올스탯 +5%"], round(convert_weapon_line("올스탯 +5%", self.context, 280)[0], 2))
        self.assertGreater(values["올스탯 +5%"], values["STR +60"])

    def test_weapon_starforce_matches_real_items(self):
        # 1~15성 내림(순수 공/마 ÷ 50) + 1, 16~22성 무기 표. 실제 API 스타포스 값과 같아야 합니다.
        self.assertEqual(weapon_starforce_option(200, 22, 390, "luk", ("DEX",))["attack_power"], 246)  # 제네시스 대거 (318 + 작 72)
        self.assertEqual(weapon_starforce_option(250, 22, 517, "int", ("LUK",))["magic_power"], 310)  # 데스티니 스태프 (445 + 72)
        self.assertEqual(weapon_starforce_option(160, 18, 286, "str", ("DEX",)), {"str": 79, "dex": 79, "attack_power": 135})  # 도전자의 폭검
        # 공격력이 낮은 일반 보조무기(아스트라)는 방어구 표를 따르고 23성 이상도 계산됩니다
        self.assertEqual(weapon_starforce_option(200, 23, 7, "str", ("DEX",), weapon_like=False)["attack_power"], 127)
        self.assertIsNone(weapon_starforce_option(250, 23, 517, "int", ("LUK",)))  # 무기 23성 이상은 로그 가산

    def test_weapon_secondary_and_emblem_use_their_own_axes(self):
        weapon = weapon_item("무기", "한손검", soul_option="공격력 +3%", soul_potential_option_1="보스 몬스터 데미지 +2%")
        secondary = weapon_item("보조무기", "방패", item_base_option={"base_equipment_level": 200, "attack_power": "7"},
                                item_starforce_option={"str": "145", "dex": "145", "attack_power": "106"})  # 일반 보조무기 = 방어구 표
        emblem = weapon_item("엠블렘", "엠블렘")
        results = {r["slot"]: r for r in evaluate_equipment([weapon, secondary, emblem], "히어로", 285)}

        w = results["무기"]
        self.assertEqual([row["key"] for row in w["scoring_basis"]["breakdown"]], ["base", "add", "star", "pot", "pot_additional", "soul"])
        self.assertGreater(w["detail"]["add"], 0)
        self.assertGreater(w["detail"]["soul"], 0)
        self.assertEqual(sorted({e["category"] for e in w["scoring_basis"]["recognized_options"]}), ["소울", "에디셔널", "윗잠재", "추가옵션"])
        self.assertAlmostEqual(w["total_score"], sum(w["detail"].values()), delta=0.3)
        # 보조무기는 스타포스(22성 = 만점) + 윗잠·에디, 엠블렘은 윗잠·에디만
        secondary_result = results["보조무기"]
        self.assertEqual([row["key"] for row in secondary_result["scoring_basis"]["breakdown"] if row["key"] != "base"], ["star", "pot", "pot_additional"])
        self.assertEqual(secondary_result["detail"]["star"], secondary_result["bar_max"]["star"])  # 22성 · 200제 기준
        emblem_result = results["엠블렘"]
        self.assertEqual([row["key"] for row in emblem_result["scoring_basis"]["breakdown"] if row["key"] != "base"], ["pot", "pot_additional"])
        self.assertAlmostEqual(emblem_result["total_score"], sum(emblem_result["detail"].values()), delta=0.3)
        for slot in ("보조무기", "엠블렘"):
            self.assertNotIn("add", results[slot]["detail"])
        self.assertIn("pot", w["scoring_basis"]["benchmarks"])
        self.assertLessEqual(results["엠블렘"]["scoring_basis"]["benchmarks"]["pot"], w["scoring_basis"]["benchmarks"]["pot"])



class WeaponAttackTierTests(unittest.TestCase):
    # 데스티니 무기 1추~5추(= 7~3단계) 공/마 추옵. 출처: https://matsu1207.tistory.com/587
    # 표에는 기본 공/마가 없어서, 250제 7단계 비율(0.72)로 1추에서 기본 공/마를 되돌려 씁니다.
    TABLE = {
        "한손검": [257, 201, 152, 111, 76], "두손검": [268, 209, 158, 115, 79], "폴암": [240, 187, 142, 103, 71],
        "건틀렛 리볼버": [201, 157, 119, 87, 59], "완드": [315, 246, 186, 136, 93], "스태프": [320, 249, 189, 139, 94],
        "활": [251, 196, 148, 108, 74], "아대": [136, 106, 81, 59, 40], "건": [196, 153, 116, 85, 58],
        "핸드캐논": [275, 214, 162, 118, 81],
    }

    def test_every_destiny_row_maps_back_to_its_tier(self):
        from app.weapon import weapon_attack_tier
        for name, values in self.TABLE.items():
            item = {"item_base_option": {"base_equipment_level": 250, "attack_power": values[0] / 0.72}}
            for tier, amount in zip((7, 6, 5, 4, 3), values):
                self.assertEqual(weapon_attack_tier(item, "공격력", amount), tier, f"{name} {amount}")

    def test_destiny_dagger_seen_in_live_data_is_tier_seven(self):
        from app.weapon import weapon_attack_tier
        item = {"item_base_option": {"base_equipment_level": 250, "attack_power": "349"}}
        self.assertEqual(weapon_attack_tier(item, "공격력", 251), 7)
        self.assertIsNone(weapon_attack_tier(item, "공격력", 20))   # 1~2단계 영역은 자료가 없어 판별하지 않음
        self.assertIsNone(weapon_attack_tier({"item_base_option": {}}, "공격력", 251))


    def test_every_table_value_maps_to_its_tier_by_name_and_part(self):
        from app.scoring_config import POTENTIAL
        from app.weapon import weapon_attack_tier
        checked = 0
        for set_name, table in POTENTIAL.weapon.flame_attack.raw.items():
            for part, values in table.items():
                # 기본 공격력을 일부러 비워 비율 판별로 빠지지 않게 하고, 표 조회만 검사합니다.
                item = {"item_name": f"{set_name} 무기", "item_equipment_part": part, "item_base_option": {}}
                for tier, amount in zip((7, 6, 5, 4, 3), values):
                    self.assertEqual(weapon_attack_tier(item, "공격력", amount), tier, f"{set_name} {part} {amount}")
                    checked += 1
        self.assertGreater(checked, 300)

    def test_genesis_lapis_uses_its_own_table_and_api_part_spacing_is_ignored(self):
        from app.weapon import weapon_attack_tier
        lapis = {"item_name": "제네시스 라피스", "item_equipment_part": "대검", "item_base_option": {}}
        self.assertEqual(weapon_attack_tier(lapis, "공격력", 46), 4)   # 비율로는 3단계 근처라 틀리게 나오는 값
        dual = {"item_name": "제네시스 듀얼보우건", "item_equipment_part": "듀얼 보우건", "item_base_option": {}}
        self.assertEqual(weapon_attack_tier(dual, "공격력", 153), 6)


if __name__ == "__main__":
    unittest.main()
