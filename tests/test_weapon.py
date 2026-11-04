import unittest

from app.analyzer import evaluate_equipment
from app.potential import reference_context
from app.scoring_config import POTENTIAL
from app.weapon import WeaponContext, best_lines_total, convert_weapon_line


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
        "item_starforce_option": {},
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
        self.assertFalse(any("보스" in line for line in POTENTIAL.weapon.best_lines.potential_emblem))

    def test_weapon_secondary_and_emblem_use_their_own_axes(self):
        weapon = weapon_item("무기", "한손검", soul_option="공격력 +3%", soul_potential_option_1="보스 몬스터 데미지 +2%")
        secondary = weapon_item("보조무기", "방패")
        emblem = weapon_item("엠블렘", "엠블렘")
        results = {r["slot"]: r for r in evaluate_equipment([weapon, secondary, emblem], "히어로", 285)}

        w = results["무기"]
        self.assertEqual([row["key"] for row in w["scoring_basis"]["breakdown"]], ["add", "pot", "pot_additional", "soul", "star"])
        self.assertGreater(w["detail"]["add"], 0)
        self.assertGreater(w["detail"]["soul"], 0)
        self.assertEqual(sorted({e["category"] for e in w["scoring_basis"]["recognized_options"]}), ["소울", "에디셔널", "윗잠재", "추가옵션"])
        self.assertAlmostEqual(w["total_score"], w["detail"]["add"] + w["detail"]["pot"] + w["detail"]["pot_additional"] + w["detail"]["soul"] + w["detail"]["star"], delta=0.3)
        for slot in ("보조무기", "엠블렘"):
            r = results[slot]
            self.assertEqual([row["key"] for row in r["scoring_basis"]["breakdown"]], ["pot", "pot_additional"])
            self.assertAlmostEqual(r["total_score"], r["detail"]["pot"] + r["detail"]["pot_additional"], delta=0.3)
            self.assertNotIn("add", r["detail"])
        self.assertIn("pot", w["scoring_basis"]["benchmarks"])
        self.assertLessEqual(results["엠블렘"]["scoring_basis"]["benchmarks"]["pot"], w["scoring_basis"]["benchmarks"]["pot"])


if __name__ == "__main__":
    unittest.main()
