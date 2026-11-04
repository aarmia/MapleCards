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

        # 훈장·포켓은 기본 옵션을 빼고 추가옵션만 봅니다. 불멸의 유산 추옵 최대(182/49/7)가 100
        medal = {
            "item_equipment_part": "훈장", "item_equipment_slot": "훈장", "item_name": "불멸의 유산",
            "item_total_option": {"str": "192", "dex": "59", "attack_power": "10", "boss_damage": "10", "all_stat": "7"},
            "item_add_option": {"str": "182", "dex": "49", "all_stat": "7"}, "item_base_option": {},
        }
        medal_result = evaluate_equipment([medal], "히어로", 285)[0]
        self.assertEqual(medal_result["total_score"], 100.0)
        self.assertNotIn("보스 몬스터 데미지 +10%", [e["option"] for e in medal_result["scoring_basis"]["recognized_options"]])

        # 시드링은 종류 × 레벨, 예비 특수 반지 자리도 같은 규칙
        ring = {"item_equipment_part": "반지", "item_equipment_slot": "반지1", "item_name": "리스트레인트 링", "special_ring_level": 6, "item_base_option": {}, "item_add_option": {}}
        spare = {**ring, "item_equipment_slot": "예비 특수 반지", "item_name": "컨티뉴어스 링", "special_ring_level": 3}
        ring_results = {r["slot"]: r for r in evaluate_equipment([ring, spare], "히어로", 285)}
        self.assertEqual((ring_results["반지1"]["total_score"], ring_results["반지1"]["part"]), (100.0, "특수 반지"))
        self.assertEqual(ring_results["예비 특수 반지"]["total_score"], 50.0)

    def test_exceptional_upgrades_add_ten_points_each_on_accessories_and_medals(self):
        belt = {
            "item_equipment_part": "벨트", "item_equipment_slot": "벨트", "item_name": "몽환의 벨트", "potential_option_1": "STR +12%",
            "item_exceptional_option": {"str": "40", "exceptional_upgrade": 2}, "starforce": "22",
            "item_base_option": {"base_equipment_level": 200}, "item_add_option": {}, "item_etc_option": {}, "item_starforce_option": {},
        }
        medal = {
            "item_equipment_part": "훈장", "item_equipment_slot": "훈장", "item_name": "불멸의 유산",
            "item_add_option": {"str": "91"}, "item_exceptional_option": {"exceptional_upgrade": 3}, "item_base_option": {},
        }
        results = {r["part"]: r for r in evaluate_equipment([belt, medal], "히어로", 285)}
        self.assertEqual(results["벨트"]["detail"]["exceptional"], 20)
        self.assertEqual(results["벨트"]["scoring_basis"]["breakdown"][-1], {"key": "exceptional", "label": "익셉셔널", "raw_score": 2, "weighted_score": 20})
        self.assertAlmostEqual(results["벨트"]["total_score"], sum(results["벨트"]["detail"].values()), delta=0.3)
        self.assertEqual(results["훈장"]["detail"]["exceptional"], 30)
        self.assertAlmostEqual(results["훈장"]["total_score"], results["훈장"]["detail"]["add"] + 30, delta=0.1)
        # 강화가 없으면 축 자체가 보이지 않습니다
        plain = evaluate_equipment([{**belt, "item_exceptional_option": {"exceptional_upgrade": 0}}], "히어로", 285)[0]
        self.assertNotIn("exceptional", plain["detail"])
        self.assertNotIn("exceptional", [row["key"] for row in plain["scoring_basis"]["breakdown"]])

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
