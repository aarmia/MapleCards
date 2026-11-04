import unittest
from datetime import datetime

from app.analyzer import build_report, evaluate_equipment
from app.potential import reference_context
from app.special import KST, best_total, option_lines, title_expired, title_lines
from app.weapon import WeaponContext


class SpecialPartTests(unittest.TestCase):
    def test_fixed_options_score_against_the_best_item_for_the_character(self):
        context = WeaponContext(reference_context("히어로", "str"), damage=100.0, boss_damage=300.0, ignore_defense=95.0)
        badge = {
            "item_equipment_part": "뱃지", "item_equipment_slot": "뱃지", "item_name": "창세의 뱃지",
            "item_total_option": {"str": "15", "dex": "15", "int": "15", "luk": "15", "attack_power": "10", "magic_power": "10", "speed": "10", "jump": "10"},
            "item_base_option": {}, "item_add_option": {},
        }
        self.assertEqual(option_lines(badge["item_total_option"], "공격력"), ["STR +15", "DEX +15", "INT +15", "LUK +15", "공격력 +10"])

        result = evaluate_equipment([badge], "히어로", 285)[0]
        self.assertTrue(result["is_special"])
        self.assertEqual(result["total_score"], 100.0)  # 창세의 뱃지가 뱃지의 최상 기준이라 100
        self.assertEqual([row["key"] for row in result["scoring_basis"]["breakdown"]], ["special"])
        self.assertEqual(result["scoring_basis"]["benchmarks"]["special"], best_total("뱃지", context, 285))
        self.assertEqual([e["reason"] for e in result["scoring_basis"]["unrecognized_options"]], ["other_stat", "other_stat"])  # INT · LUK

    def test_title_options_are_parsed_and_expired_titles_score_zero(self):
        description = "깊은 어둠이 걸어온 마법의 주문\n\n보스 몬스터 데미지 +30%\n몬스터 방어율 무시+30%\n일반 몬스터 데미지 +30%\n추가 경험치 +100%\n공격력/마력+30\n옵션 적용 기간: 90일"
        self.assertEqual(title_lines(description, "마력"), ["마력 +30", "보스 몬스터 데미지 +30%", "몬스터 방어율 무시 +30%"])
        self.assertTrue(title_expired({"date_option_expire": "2026-01-01T00:00+09:00"}, now=datetime(2026, 6, 1, tzinfo=KST)))
        self.assertFalse(title_expired({"date_option_expire": None}))

        basic = {"character_class": "비숍", "character_level": 285}
        stats = {"final_stat": [{"stat_name": "INT", "stat_value": "40000"}, {"stat_name": "마력", "stat_value": "3000"}]}
        title = {"title_name": "BLACK SPELL", "title_description": description, "date_option_expire": "2099-01-01T00:00+09:00"}
        report = build_report("x", basic, {"item_equipment": [], "title": title}, stats)
        item = next(r for r in report["results"] if r["part"] == "칭호")
        self.assertEqual(item["total_score"], 100.0)  # 보공 30 · 방무 30 · 공/마 30 이 칭호의 최상 기준
        self.assertEqual(sorted(e["option"] for e in item["scoring_basis"]["recognized_options"]), ["마력 +30", "몬스터 방어율 무시 +30%", "보스 몬스터 데미지 +30%"])

        expired = build_report("x", basic, {"item_equipment": [], "title": {**title, "date_option_expire": "2020-01-01T00:00+09:00"}}, stats)
        item = next(r for r in expired["results"] if r["part"] == "칭호")
        self.assertEqual(item["total_score"], 0)
        self.assertIn("만료", item["guide"])


if __name__ == "__main__":
    unittest.main()
