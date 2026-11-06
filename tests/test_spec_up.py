import re
import unittest
from dataclasses import replace
from unittest.mock import patch

from app.calculator import calculate_item_score, flame_best_add_option
from app.potential import reference_context
from app.scoring_config import POTENTIAL
from app.spec_up import (
    amplify_tries,
    attach_spec_up,
    best_target,
    burst_share,
    expected_resets,
    final_damage_per_pct,
    flame_distribution,
    flame_steps,
    listing_actions,
    potential_actions,
    potential_distribution,
    seed_ring_action,
    soul_action,
)
from app.weapon import WeaponContext


def str_pct(text):
    match = re.match(r"^(STR|올스탯) \+(\d+)%$", text)
    return float(match.group(2)) if match else 0.0


class SpecUpTests(unittest.TestCase):
    def test_challenger_items_get_listing_only_and_keep_scores(self):
        context = reference_context("나이트로드", "luk")
        for name in ("도전자의 모자", "도전자의 컨티뉴어스 링", "강화된 도전자의 장갑"):
            for source in ("row", "api"):
                with self.subTest(name=name, source=source):
                    row = {"slot": "모자", "name": name if source == "row" else "일반 모자",
                           "total_score": 42, "score_ratio": 80, "spec_up": [{"stale": True}]}
                    item = {"item_equipment_slot": "모자", "item_name": name if source == "api" else "일반 모자"}
                    with patch("app.spec_up.starforce_action") as star, patch("app.spec_up.potential_actions") as potential, \
                            patch("app.spec_up.flame_distribution") as flame, patch("app.spec_up.seed_ring_action") as ring,                             patch("app.spec_up.listing_actions", return_value=[]) as listing:
                        report = attach_spec_up([row], [item], context, "나이트로드", "luk", 298)
                        for action in (star, potential, flame, ring):
                            action.assert_not_called()  # 강화 추천은 만들지 않고
                        listing.assert_called_once()  # 매물 교체만 봅니다
                    self.assertEqual(row["spec_up"], [])
                    self.assertEqual((row["total_score"], row["score_ratio"]), (42, 80))
                    self.assertEqual(report["count"], 0)

    def test_assumptions_follow_config_and_are_in_report(self):
        context = reference_context("나이트로드", "luk")
        with patch.dict(POTENTIAL.raw, {"cooldown_per_second": 7.5}):
            report = attach_spec_up([], [], context, "나이트로드", "luk", 280)
        self.assertTrue(report["estimate"])
        notes = " ".join(report["assumptions"])
        for token in ("주스탯 7.5%", "비실측", "실제 DPS", "평균"):
            self.assertIn(token, notes)

    def test_official_odds_match_mapletiptip_examples(self):
        # mapletiptip.com 200제 모자 예시: 윗잠 STR 21%↑ 2.5584% · 30%↑ 0.1426%, 에디 STR 14%↑ 0.2973% (올스탯% 포함)
        for kind, target, chance in (("potential", 21, 0.025584), ("potential", 30, 0.001426), ("additional", 14, 0.002973)):
            dist = potential_distribution(kind, "모자", 200, None, 0, value_of=str_pct)
            self.assertAlmostEqual(sum(p for v, p in dist.items() if v >= target), chance, places=6)
        # 천장 포함 평균 재설정(올림): 윗잠 6·23·56회, 에디 33·80·112회
        resets = [expected_resets(p, n) for p, n in ((0.15, 10), (0.035, 42), (0.014, 107), (0.02381, 62), (0.009804, 152), (0.007, 214))]
        self.assertEqual([int(-(-r // 1)) for r in resets], [6, 23, 56, 33, 80, 112])

    def test_hat_additional_splits_main_stat_and_cooldown_cases(self):
        context = reference_context("나이트로드", "luk")
        hat = {"additional_potential_option_grade": "레전드리", "additional_potential_option_1": "LUK +9%",
               "additional_potential_option_2": "LUK +7%", "additional_potential_option_3": "최대 HP +9%"}
        actions = potential_actions(hat, "additional", "모자", 250, context, 280, cap=23.0)
        self.assertEqual([a["variant"] for a in actions], ["main", "cooldown"])
        self.assertNotIn("재사용", actions[0]["combo"])  # 주스탯만: 대표 구성에 쿨감 없음
        self.assertIn("재사용 대기시간", actions[1]["combo"])  # 쿨감 1초 + 주스탯
        shown = float(actions[1]["label"].split("환산 ")[1].split("%")[0])
        self.assertLess(shown, 23.0 - 11.0 + 1e-9)  # 표시 목표는 쿨감 1초(11%) 몫을 뺀 주스탯%
        self.assertGreater(actions[1]["chance"], actions[0]["chance"])
        shoes = potential_actions({**hat}, "additional", "신발", 250, context, 280, cap=23.0)
        self.assertEqual([a["variant"] for a in shoes], ["all"])  # 모자 외에는 나누지 않음

    def test_final_damage_matches_stat_attack_ratio(self):
        # 유효 스탯 1% = 최종 주스탯 + a'/100 → 최종 데미지 증가 = 스탯 공격력 증가 비율
        context = reference_context("나이트로드", "luk")
        raised = replace(context, final_main=context.final_main + context.base_stat_effective / 100)
        self.assertAlmostEqual(final_damage_per_pct(context), 100 * (raised.stat_attack / context.stat_attack - 1), places=9)

    def test_seed_ring_levels_value_their_burst_window(self):
        context = reference_context("나이트로드", "luk")
        # 리레 4→5: 늘어난 5초(15→20초 비중 차이)에 공/마 68% / 5→6: 20초 구간 공/마 68→85%
        s15, s20 = burst_share("나이트로드")  # 실측 전 직업은 default
        r45 = seed_ring_action("리스트레인트 링", 4, "나이트로드", context, 0.0, 5)
        base = context.attack_multiplier_effective * 100 + 12  # 컨티 5레벨 공/마 12%
        # 지금 레벨의 전체 딜(1 + 구간 비중 × 공/마 ÷ 배율) 대비 증가
        self.assertAlmostEqual(r45["gain"] * final_damage_per_pct(context), 100 * ((1 + s20 * 68 / base) / (1 + s15 * 68 / base) - 1), places=6)
        self.assertEqual(r45["material"], {"name": "생명의 연마석", "count": 10, "meso": 50e8})  # 비용은 연마석 시세로 화면이 계산
        r56 = seed_ring_action("리스트레인트 링", 5, "나이트로드", context, 0.0, 5)
        self.assertAlmostEqual(r56["gain"] * final_damage_per_pct(context), 100 * ((1 + s20 * 85 / base) / (1 + s20 * 68 / base) - 1), places=6)
        self.assertIsNone(seed_ring_action("리스트레인트 링", 6, "나이트로드", context, 0.0, 5))  # 만렙
        c56 = seed_ring_action("컨티뉴어스 링", 5, "처음 보는 직업", context, 100.0, 5)  # 표에 없는 직업은 평균
        self.assertEqual(c56["material"], {"name": "신념의 연마석", "count": 20, "meso": 200e8})
        self.assertGreater(c56["gain"], 0)

    def test_best_target_trades_gain_against_cost(self):
        dist = {0.0: 0.5, 10.0: 0.4, 30.0: 0.1}
        best = best_target(dist, current=5.0, cost_per_try=1.0)
        # 10↑: 이득 (0.4·10 + 0.1·30)/0.5 − 5 = 9, 비용 2 → 4.5 / 30↑: 이득 25, 비용 10 → 2.5
        self.assertEqual((best["target"], best["gain"], best["cost"]), (10.0, 9.0, 2.0))
        # 등급업 직후 첫 결과도 성공일 수 있음: 추가 재설정 (1 − P) / P
        self.assertEqual(best_target(dist, 5.0, 1.0, setup_cost=3.0, first_try_free=True)["cost"], 4.0)
        # 목표 범위(현재 + 최소 상승폭 ~ 만점): 10 은 최소 상승폭 미달이라 30 만 후보, 만점이 20 이면 후보 없음
        self.assertEqual(best_target(dist, 5.0, 1.0, min_target=11.0)["target"], 30.0)
        self.assertIsNone(best_target(dist, 5.0, 1.0, min_target=11.0, max_target=20.0))

    def test_flame_steps_start_at_plus_7_5_then_every_5(self):
        dist = {100.0: 0.5, 110.0: 0.3, 120.0: 0.15, 130.0: 0.05}
        steps = flame_steps(dist, current=100.0, cost_per_try=1.0)
        self.assertEqual([step["target"] for step in steps], [107.5, 112.5, 117.5, 122.5, 127.5])
        self.assertAlmostEqual(steps[0]["chance"], 0.5)                          # 110 · 120 · 130
        self.assertAlmostEqual(steps[0]["gain"], (110 * .3 + 120 * .15 + 130 * .05) / .5 - 100)
        self.assertAlmostEqual(steps[2]["chance"], 0.2)                          # 117.5 이상 = 120 · 130
        for step in steps:
            self.assertAlmostEqual(step["tries"], 1 / step["chance"])
            self.assertAlmostEqual(step["cost"], 1.0 / step["chance"])

    def test_flame_distribution_tops_out_at_best_flame(self):
        context = reference_context("나이트로드", "luk")
        dist = flame_distribution(250, "나이트로드", "luk", context)
        self.assertAlmostEqual(sum(dist.values()), 1.0, places=9)
        best = calculate_item_score(flame_best_add_option(250, "나이트로드", "luk", context.sub_names, context), "나이트로드", "luk", context)
        self.assertAlmostEqual(max(dist), round(best, 1), places=1)

    def test_soul_amplify_next_stage(self):
        # 기대 시도 수: 게이지(실패당 확률 증가)와 확정을 반영한 값 (hoonjaelab 9.18회 · 19.11회와 같음)
        self.assertAlmostEqual(amplify_tries(0.05, 0.01, 25), 9.18, places=2)
        self.assertAlmostEqual(amplify_tries(0.015, 0.003, 50), 19.11, places=2)
        context = reference_context("나이트로드", "luk")
        weapon = WeaponContext(stats=context, ignore_defense=90)
        item = {"soul_name": "위대한 데미안의 소울 적용", "soul_potential_grade": "레전드리", "soul_potential_amplified_grade": 2,
                "soul_potential_option_1": "공격력 +4%", "soul_potential_option_2": "공격력 +3%",
                "soul_potential_option_3": "크리티컬 확률 +4%"}
        action = soul_action(item, 200, context, weapon, 280, 0.0)
        # 공식 표의 다음 단계 값: 공% 는 정비례(4→6, 3→4.5), 크확은 아님(4→5)
        self.assertTrue(action["detail"].endswith("공격력 +6% · 공격력 +4.5% · 크리티컬 확률 +5%"))
        self.assertEqual(action["material"]["name"], "3단계 소울 에테르")
        self.assertAlmostEqual(action["material"]["meso"], action["material"]["count"] * 1_750_000_000)
        self.assertGreater(action["final_damage"], 0)
        # 0단계: 레전드리 공% 두 줄을 가정하고, 미라클 등급업 + 두 줄 재설정 메소를 증폭 메소에 더함
        fresh = soul_action({"soul_name": "위대한 데미안의 소울 적용"}, 200, context, weapon, 280, 0.0)
        self.assertTrue(fresh["detail"].startswith("가정: 레전드리 공격력 +2% · 공격력 +1.5%"))
        self.assertGreater(fresh["material"]["meso"], fresh["material"]["count"] * 500_000_000 + 100 * 10**8)
        self.assertIsNone(soul_action({**item, "soul_potential_amplified_grade": 4}, 200, context, weapon, 280, 0.0))
        self.assertIsNone(soul_action({**item, "soul_name": "데미안의 소울 적용"}, 200, context, weapon, 280, 0.0))
        self.assertIsNone(soul_action(item, 160, context, weapon, 280, 0.0))

    def test_listing_grades_rise_and_carry_the_crafting_cost(self):
        from app.analyzer import evaluate_equipment
        item = {"item_equipment_part": "모자", "item_equipment_slot": "모자", "item_name": "에테르넬 시프반다나", "starforce": "17",
                "item_base_option": {"base_equipment_level": 250, "luk": "80", "dex": "80"}, "potential_option_grade": "레전드리",
                "potential_option_1": "LUK +13%", "item_add_option": {"luk": "84"}, "item_etc_option": {}, "item_starforce_option": {},
                "scroll_upgrade": "0", "scroll_upgradeable_count": "12", "scroll_resilience_count": "0"}
        context = reference_context("나이트로드", "luk")
        row = evaluate_equipment([item], "나이트로드", 285, profile={"context": context})[0]
        actions = listing_actions(row, item, context, "나이트로드", "luk", 285, {})
        self.assertTrue(actions)
        self.assertEqual([a["grade"] for a in actions], sorted(a["grade"] for a in actions))
        self.assertEqual([a["final_damage"] for a in actions], sorted(a["final_damage"] for a in actions))  # 높은 등급일수록 큼
        self.assertTrue(all(a["listing_fixed"] > 0 and a["star"] == 0 for a in actions))  # 큐브·불꽃 기댓값 (노작·스타포스는 화면)
        self.assertEqual(listing_actions({**row, "total_score": 10_000}, item, context, "나이트로드", "luk", 285, {}), [])  # 더 좋은 장비면 없음
        # 메큘레이터 지수가 지금 지수의 min_index_gain_pct % 이상 오를 때만 추천
        small = {"total": 100.0, "rows": {"모자": row}, "context": context}
        huge = {**small, "total": 1_000_000.0}
        self.assertTrue(all(a["index_gain"] > 0 for a in listing_actions(row, item, context, "나이트로드", "luk", 285, {}, small)))
        self.assertEqual(listing_actions(row, item, context, "나이트로드", "luk", 285, {}, huge), [])

    def test_upgrade_paths_bundles_caps_and_badge(self):
        from app.analyzer import evaluate_equipment
        from app.spec_up import bundle_actions
        from app.weapon import WeaponContext
        job, context = "아크메이지(불,독)", reference_context("아크메이지(불,독)", "int")
        weapon = WeaponContext(context)

        def item(slot, part, name, level=200, **extra):
            return {"item_equipment_part": part, "item_equipment_slot": slot, "item_name": name, "starforce": "17",
                    "item_base_option": {"base_equipment_level": level}, "item_add_option": {}, "item_etc_option": {},
                    "item_starforce_option": {}, "scroll_upgrade": "2", **extra}

        def actions(it, items=None):
            row = evaluate_equipment([it], job, 285, profile={"context": context})[0]
            return row, listing_actions(row, it, context, job, "int", 285, {}, weapon=weapon, items=items or [it])

        # 앱솔랩스는 상위 계열을 한 칸씩 (아케인셰이드·에테르넬 모두)
        _, absolab = actions(item("어깨장식", "어깨장식", "앱솔랩스 메이지숄더", 160))
        names = {a["price_item"]["name"] for a in absolab if a["axis"] == "상위 교체"}
        self.assertEqual(names, {"아케인셰이드 메이지숄더", "에테르넬 메이지숄더"})
        # 아케인셰이드 → 에테르넬은 한 칸 교체 없이 묶음만, 한벌옷은 에테르넬 상의 + 하의
        shoulder, suit = item("어깨장식", "어깨장식", "아케인셰이드 메이지숄더"), item("상의", "한벌옷", "아케인셰이드 메이지슈트")
        row, arcane = actions(shoulder)
        self.assertFalse([a for a in arcane if a["axis"] == "상위 교체"])
        rows = evaluate_equipment([shoulder, suit], job, 285, profile={"context": context})
        bundles = [action for _, action in bundle_actions(rows, [shoulder, suit], context, job, "int", 285, {}, None, weapon)]
        self.assertTrue(bundles)
        self.assertTrue(all(a["bundle"] and a["slots"] == ["상의", "어깨장식"] and a["pieces"] == 3 for a in bundles))
        self.assertIn("에테르넬 세트(마법사) 0→3", bundles[0]["detail"])
        # 장신구: 여명 → 칠흑 (같은 부위), 광휘는 순서 체인으로만. 여명 같은 아이템 매물은 5등급까지
        dawn = item("반지1", "반지", "여명의 가디언 엔젤 링", 160)
        _, ring = actions(dawn)
        self.assertEqual({a["price_item"]["name"] for a in ring if a["axis"] == "상위 교체"}, {"거대한 공포"})
        self.assertTrue(all(a["grade"] <= 5 for a in ring if a["axis"] == "매물 교체"))
        pitched = [{"item_name": name, "item_equipment_slot": f"칸{i}"} for i, name in enumerate(
            ["루즈 컨트롤 머신 마크", "마력이 깃든 안대", "커맨더 포스 이어링", "몽환의 벨트", "저주받은 황의 마도서",
             "미트라의 분노 : 마법사", "컴플리트 언더컨트롤", "창세의 뱃지", "고통의 근원", "거대한 공포"])]
        _, ring = actions(dawn, [dawn, *pitched])
        self.assertFalse([a for a in ring if a["axis"] == "상위 교체"])  # 칠흑 10세트여도 광휘는 radiant_chain_actions 로만
        # 거대한 공포는 최종 아이템: 같은 아이템의 상위 매물만
        horror = item("반지2", "반지", "거대한 공포", 200)
        _, final = actions(horror, [horror, *pitched[:9]])
        self.assertTrue(final and all(a["axis"] == "매물 교체" for a in final))
        # 플라즈마 하트는 같은 아이템 매물 없이 컴플리트 언더컨트롤로만
        _, heart = actions(item("기계 심장", "기계 심장", "플라즈마 하트", 130))
        self.assertTrue(heart)
        self.assertEqual({(a["axis"], a["price_item"]["name"]) for a in heart}, {("상위 교체", "컴플리트 언더컨트롤")})
        self.assertTrue(all(a["grade"] <= 3 for a in heart))  # 기계 심장은 3단계까지 (하트 전용 주문서)
        # 뱃지 → 창세의 뱃지 (뱃지 옵션 + 칠흑 세트 +1), 비용은 뱃지 시세
        from app.spec_up import special_replace_action
        badge = {"item_equipment_part": "뱃지", "item_equipment_slot": "뱃지", "item_name": "크리스탈 웬투스 뱃지",
                 "item_total_option": {"int": "10", "magic_power": "5"}, "item_base_option": {"base_equipment_level": 130}}
        badge_row = evaluate_equipment([badge], job, 285, profile={"context": context, "weapon": weapon})[0]
        action = special_replace_action(badge_row, badge, [badge], context, weapon, 285, job, "int")
        self.assertEqual((action["price_item"]["name"], action["listing_fixed"]), ("창세의 뱃지", 0.0))
        self.assertIn("칠흑의 보스 세트 0→1", action["detail"])
        # 훈장 → 불멸의 유산 (기본 옵션 + 추옵 205급, 1단계). 칠흑 조건·순서는 radiant_chain_actions
        medal = {"item_equipment_part": "훈장", "item_equipment_slot": "훈장", "item_name": "THE RED",
                 "item_add_option": {}, "item_base_option": {"base_equipment_level": 0, "int": "5"}}
        medal_row = evaluate_equipment([medal], job, 285, profile={"context": context, "weapon": weapon})[0]
        action = special_replace_action(medal_row, medal, [medal, *pitched], context, weapon, 285, job, "int")
        self.assertEqual(action["price_item"]["name"], "불멸의 유산")
        self.assertIn("추옵 205급", action["detail"])
        self.assertIn("광휘의 보스 세트 0→1", action["detail"])
    def test_radiant_chain_follows_the_fixed_order(self):
        from app.analyzer import evaluate_equipment
        from app.spec_up import radiant_chain_actions
        from app.weapon import WeaponContext
        job, context = "아크메이지(불,독)", reference_context("아크메이지(불,독)", "int")
        weapon = WeaponContext(context)

        def item(slot, part, name, level):
            return {"item_equipment_part": part, "item_equipment_slot": slot, "item_name": name, "starforce": "17",
                    "item_base_option": {"base_equipment_level": level}, "item_add_option": {}, "item_etc_option": {},
                    "item_starforce_option": {}, "scroll_upgrade": "2"}
        worn = [item("훈장", "훈장", "THE RED", 0), item("반지1", "반지", "마이스터링", 140),
                item("반지2", "반지", "여명의 가디언 엔젤 링", 160), item("펜던트", "펜던트", "고통의 근원", 160),
                item("펜던트2", "펜던트", "데이브레이크 펜던트", 140), item("눈장식", "눈장식", "마력이 깃든 안대", 160),
                item("얼굴장식", "얼굴장식", "루즈 컨트롤 머신 마크", 160)]
        filler = [{"item_name": name, "item_equipment_slot": f"칸{i}"} for i, name in enumerate(
            ["커맨더 포스 이어링", "몽환의 벨트", "저주받은 황의 마도서", "미트라의 분노 : 마법사", "컴플리트 언더컨트롤", "창세의 뱃지", "거대한 공포"])]
        rows = evaluate_equipment(worn, job, 285, profile={"context": context, "weapon": weapon})

        def chain(items):
            return [action for _, action in radiant_chain_actions(rows, items, context, job, "int", 285, {}, None, weapon)]
        steps = chain([*worn, *filler])
        # 불멸의 유산 → 마이스터링 칸(세트 안 깨짐) → 남은 반지 + 여명 펜던트(칠흑 고통의 근원은 유지) → 원혼 → 원죄
        self.assertEqual([(a["chain"], a["slots"]) for a in steps],
                         [(1, ["훈장"]), (2, ["반지1"]), (3, ["반지2", "펜던트2"]), (4, ["눈장식"]), (5, ["얼굴장식"])])
        self.assertEqual(steps[0]["price_item"]["name"], "불멸의 유산")
        self.assertEqual([p["name"] for p in steps[2]["price_items"]], ["황홀한 악몽", "죽음의 맹세"])
        self.assertTrue(all(a["grade"] == 1 and a["target_star"] == 18 for a in steps[1:]))  # 광휘는 1단계 매물로만
        self.assertEqual(chain([*worn, *filler[:-1]]), [])  # 칠흑 9세트면 광휘 순서 없음


if __name__ == "__main__":
    unittest.main()
