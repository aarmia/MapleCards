import copy
import unittest
from dataclasses import replace

from app.character_profile import build_stat_context
from app.potential import reference_context
from app.preset_optimizer import optimize_presets
from app.set_projection import DAWN, DAWN_TABLE, PITCHED, analyze_sets, apply_sets, count_sets, membership


def item(name, slot):
    return {"item_name": name, "item_equipment_slot": slot}


def pitched_fixture():
    before = [item("몽환의 벨트", "벨트"), item("저주받은 황의 마도서", "포켓 아이템"),
              item("컴플리트 언더컨트롤", "기계 심장"), item("창세의 뱃지", "뱃지"), item("미트라의 분노 : 도적", "엠블렘")]
    after = before + [item("루즈 컨트롤 머신 마크", "얼굴장식"), item("마력이 깃든 안대", "눈장식"),
                      item("커맨더 포스 이어링", "귀고리"), item("거대한 공포", "반지1"), item("고통의 근원", "펜던트")]
    table = []
    for n in range(2, 11):
        amount = 10 if n <= 3 else 20 if n == 10 else 15
        extra = "보스 몬스터 데미지 +10%" if n in (2, 5, 8, 10) else "몬스터 방어율 무시 +10%" if n in (3, 6) else "크리티컬 데미지 +5%"
        table.append({"set_count": n, "set_option": f"올스탯 +{amount}, 공격력 +{amount}, 마력 +{amount}, {extra}"})
    data = {"set_effect": [{"set_name": PITCHED, "total_set_count": 5,
                            "set_effect_info": table[:4], "set_option_full": table}]}
    return before, after, data


class SetProjectionTests(unittest.TestCase):
    def test_optimizer_includes_set_boss_crit_and_ied_exactly_once(self):
        before, after, data = pitched_fixture()
        for gear in after:
            gear["item_total_option"] = {"luk": "0", "dex": "0", "attack_power": "0"}
        stats = {"final_stat": [{"stat_name": key, "stat_value": str(value)} for key, value in {
            "LUK": 40000, "DEX": 5000, "공격력": 3000, "크리티컬 확률": 100,
            "크리티컬 데미지": 100, "데미지": 0, "보스 몬스터 데미지": 0, "방어율 무시": 99}.items()]}
        extras = {"symbol": {"symbol": []}, "set_effect": data}
        current = build_stat_context("나이트워커", "luk", 298, before, stats, extras)
        result = optimize_presets("나이트워커", "luk", 298,
            {"preset_no": 1, "item_equipment": before, "item_equipment_preset_1": before, "item_equipment_preset_2": after},
            stats, extras, current)
        self.assertEqual(result[0], 2)
        self.assertEqual(result[2].crit_damage, 110)
        self.assertEqual(result[2].attack, 3080)
        self.assertEqual(result[3]["boss_damage"], 20)
        self.assertAlmostEqual(result[3]["ignore_defense"], 99.1)

    def test_pitched_5_to_10_and_new_dawn_2(self):
        before, after, data = pitched_fixture()
        after += [item("여명의 가디언 엔젤 링", "반지2"), item("데이브레이크 펜던트", "펜던트2")]
        original = copy.deepcopy(data)
        report = analyze_sets(before, after, data)
        self.assertEqual(report["status"], "applied")
        self.assertTrue(all(x["count_matches"] and x["active_effects_match"] for x in report["validation"]))
        self.assertEqual(report["delta"]["luk"], 90)
        self.assertEqual(report["delta"]["attack"], 90)
        self.assertEqual(report["delta"]["boss"], 30)
        self.assertEqual(report["delta"]["crit"], 10)
        self.assertAlmostEqual(report["delta"]["ied_ratio"], .9)
        self.assertEqual(data, original)

    def test_same_equipment_never_double_counts_current_sets(self):
        before, _, data = pitched_fixture()
        report = analyze_sets(before, before, data)
        self.assertEqual(report["status"], "verified_unchanged")
        c = reference_context("나이트워커", "luk")
        projected = apply_sets(c, report)
        self.assertEqual(projected.base_stat, c.base_stat)
        self.assertEqual(projected.attack, c.attack)
        self.assertEqual(projected.crit_damage, c.crit_damage)

    def test_set_changes_recompute_stats_and_attack_once(self):
        before, after, data = pitched_fixture()
        report = analyze_sets(before, after, data)
        c = replace(reference_context("나이트워커", "luk"), base_stat=8000, stat_pct=10,
                    attack=10000, attack_multiplier=2, exempt_stat=30000,
                    sources={"sub_base_estimate": 4000, "sub_multiplier": 2, "sub_exempt": 600})
        p = apply_sets(c, report)
        self.assertEqual(p.base_stat, 8080)
        self.assertEqual(p.final_main, 110800)
        self.assertEqual(p.attack, 10160)
        self.assertEqual(p.final_sub, 8760)
        self.assertEqual(p.crit_damage, c.crit_damage + 10)

    def test_reverse_removes_ied_multiplicatively(self):
        before, after, data = pitched_fixture()
        data["set_effect"][0]["total_set_count"] = 10
        data["set_effect"][0]["set_effect_info"] = data["set_effect"][0]["set_option_full"]
        report = analyze_sets(after, before, data)
        self.assertEqual(report["delta"]["attack"], -80)
        self.assertAlmostEqual(report["delta"]["ied_ratio"], 1 / .9)

    def test_bad_count_and_bad_active_effect_skip_that_set_only(self):
        for mode in ("count", "effect"):
            before, after, data = pitched_fixture()
            if mode == "count":
                data["set_effect"][0]["total_set_count"] = 6
            else:
                data["set_effect"][0]["set_effect_info"] = []
            report = analyze_sets(before, after, data)
            self.assertEqual(report["status"], "unresolved")  # 바뀌는 세트가 칠흑 하나뿐이라 적용할 세트가 없습니다
            self.assertEqual([s["set"] for s in report["skipped"]], [PITCHED])
            self.assertEqual(report["delta"]["attack"], 0)

    def test_unlisted_item_counts_as_no_set_and_other_sets_still_apply(self):
        before, after, data = pitched_fixture()
        after += [item("알 수 없는 럭키 아이템", "모자")]
        self.assertEqual(membership(item("알 수 없는 럭키 아이템", "모자")), "")
        report = analyze_sets(before, after, data)
        self.assertEqual((report["status"], report["applied_sets"]), ("applied", [PITCHED]))

    def test_missing_api_or_missing_stage_blocks_correction(self):
        before, after, data = pitched_fixture()
        self.assertEqual(analyze_sets(before, after, None)["status"], "unresolved")
        data["set_effect"][0]["set_option_full"] = [x for x in data["set_effect"][0]["set_option_full"] if x["set_count"] != 6]
        report = analyze_sets(before, after, data)
        self.assertEqual(report["status"], "unresolved")
        self.assertTrue(any("단계 누락" in x for x in report["reasons"]))

    def test_armor_families_and_weapons_count_by_api_set_name(self):
        self.assertEqual(membership(item("아케인셰이드 아처후드", "상의")), "아케인셰이드 세트(궁수)")
        self.assertEqual(membership(item("앱솔랩스 파이렛코트", "상의")), "앱솔랩스 세트(해적)")
        self.assertEqual(membership(item("하이네스 레인저베레", "모자")), "루타비스 세트(궁수)")
        api = {"아케인셰이드 세트(궁수)", "에테르넬 세트(궁수)"}
        arcane = [item("아케인셰이드 아처후드", "상의"), item("아케인셰이드 아처햇", "모자"), item("아케인셰이드 아처글러브", "장갑")]
        self.assertEqual(count_sets(arcane + [item("아케인셰이드 보우", "무기")], api)["아케인셰이드 세트(궁수)"], 4)
        # 제네시스·데스티니 무기는 에테르넬 세트에 들어가고, 3개 이상 착용한 하위 세트에는 럭키 아이템으로 1개 더 셉니다
        counts = count_sets(arcane + [item("데스티니 보우", "무기")], api)
        self.assertEqual((counts["에테르넬 세트(궁수)"], counts["아케인셰이드 세트(궁수)"]), (1, 4))
        counts = count_sets(arcane[:2] + [item("제네시스 보우", "무기")], api)
        self.assertEqual((counts["에테르넬 세트(궁수)"], counts["아케인셰이드 세트(궁수)"]), (1, 2))  # 2개뿐이면 럭키 미적용
        self.assertEqual(count_sets([item("파프니르 윈드체이서", "무기")], api)["루타비스 세트(궁수)"], 0)  # API 세트명이 없으면 셀 수 없습니다

    def test_guardian_conversion_and_reserve_ring(self):
        self.assertNotEqual(membership(item("가디언 엔젤 링", "반지1")), membership(item("여명의 가디언 엔젤 링", "반지1")))
        before, after, data = pitched_fixture()
        after.append(item("거대한 공포", "예비 특수 반지"))
        report = analyze_sets(before, after, data)
        self.assertEqual(report["status"], "applied")
        self.assertEqual(report["changes"][0]["after"], 10)

    def test_api_table_has_priority_over_fallback(self):
        items = [item("여명의 가디언 엔젤 링", "반지1")]
        table = copy.deepcopy(DAWN_TABLE)
        table[0]["set_option"] = "올스탯 +11, 공격력 +12"
        data = {"set_effect": [{"set_name": DAWN, "total_set_count": 1, "set_effect_info": [], "set_option_full": table}]}
        report = analyze_sets(items, items + [item("데이브레이크 펜던트", "펜던트")], data)
        self.assertEqual(report["delta"]["luk"], 11)
        self.assertEqual(report["delta"]["attack"], 12)

    def test_unmodeled_set_skill_is_not_silently_ignored(self):
        before, after, data = pitched_fixture()
        data["set_effect"][0]["set_option_full"][-1]["set_option"] += ", [새로운 스킬] 사용 가능"
        report = analyze_sets(before, after, data)
        self.assertEqual(report["status"], "unresolved")
        self.assertEqual(report["delta"]["crit"], 0)
