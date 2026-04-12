from fastapi import FastAPI, HTTPException, Response, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.responses import PlainTextResponse
import sys
import os
import asyncio
import math

# 현재 파일의 부모 폴더(app)를 경로에 추가
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.append(BASE_DIR)

try:
    from scraper import NexonAPIHandler
except ImportError:
    from app.scraper import NexonAPIHandler

from app.image_gen import CardGenerator

app = FastAPI()
nexon_api = NexonAPIHandler()
card_gen = CardGenerator()

# 서버 세마포어 설정 (동시 API 처리 인원을 5명으로 제한)
api_semaphore = asyncio.Semaphore(5)

# 경로 설정
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))
static_dir = os.path.join(BASE_DIR, "static")

if os.path.exists(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
else:
    print(f"⚠️ Warning: Static directory not found at {static_dir}")


# 파비콘 404 에러 방지 핸들러
@app.get("/favicon.ico", include_in_schema=False)
async def favicon():
    favicon_path = os.path.join(static_dir, "favicon.ico")
    if os.path.exists(favicon_path):
        return FileResponse(favicon_path)
    return Response(status_code=204)


@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return "User-agent: *\nAllow: /\nSitemap: https://meculator.onrender.com/sitemap.xml"

@app.get("/check-items/{character_name}")
async def check_items(character_name: str):
    # 세마포어를 사용하여 넥슨 API 호출 구간 보호
    async with api_semaphore:
        ocid = await nexon_api.get_ocid(character_name)
        if not ocid:
            return {"error": "캐릭터를 찾을 수 없습니다."}

        basic_info = await nexon_api.get_character_basic(ocid)
        item_data = await nexon_api.get_character_item(ocid)
        stat_data = await nexon_api.get_character_stat(ocid)

        if not basic_info or not item_data:
            return {"error": "데이터를 불러오는 데 실패했습니다."}

        char_class = basic_info.get("character_class")
        char_level = int(basic_info.get("character_level", 0))
        char_image = basic_info.get("character_image", "")

        combat_power = 0
        if stat_data and "final_stat" in stat_data:
            for stat in stat_data["final_stat"]:
                if stat.get("stat_name") == "전투력":
                    combat_power = stat.get("stat_value")
                    break

        best_preset_idx = nexon_api.get_best_preset(item_data, char_class, char_level)
        items = item_data.get(f"item_equipment_preset_{best_preset_idx}")
        if not items:
            items = item_data.get("item_equipment", [])

        evaluate_list = []

        # --- [헬퍼 함수 1: 추옵 점수 산출 로직] ---
        def get_advanced_add_score(actual_급수, level, part_name):
            no_add_slots = ["반지", "어깨장식", "기계 심장", "훈장", "뱃지", "포켓 아이템", "엠블렘", "보조무기", "무기"]
            if any(k in part_name for k in no_add_slots):
                return 100.0

            target_map = {250: 186, 200: 162, 160: 144, 150: 132, 140: 126, 135: 123, 130: 120}
            target = target_map.get(level)

            if target is None:
                if level >= 100:
                    target = (level * 0.6) + 42
                else:
                    return 100.0

            diff_percent = ((actual_급수 - target) / target) * 100.0
            score = 100.0
            rem = abs(diff_percent)

            if diff_percent > 0:
                mult = 0.5
                decay = 0.9
            else:
                mult = 1.2
                decay = 1.05

            while rem > 0:
                chunk = min(5.0, rem)
                if diff_percent > 0:
                    score += (chunk * mult)
                    mult *= decay
                else:
                    score -= (chunk * mult)
                    mult = min(mult * decay, 3.0)
                rem -= chunk

            return round(max(0, score), 2)

        # --- [헬퍼 함수 2: 공통 스타포스 점수 계산] ---
        def get_starforce_score(star, level):
            if level <= 0: return 0.0
            weight = 1.0 + (level - 200) / 600.0
            base22 = 100.0 * ((min(star, 22) / 22.0) ** 2) * weight
            return base22 if star <= 22 else base22 + (3.0 * math.log(star - 21))

        # --- [헬퍼 함수 3: 세분화된 가이드 생성] ---
        # 💡 [수정] 놀장강 판별 변수(is_noljang) 추가
        def get_dynamic_guide(scores, star_val, part_name, total_score, item_name, item_req_level, is_noljang=False):
            if is_noljang:
                return f"💎 [놀라운 장비 강화 아이템] 놀라운 장비 강화 아이템을 착용중입니다. 교체를 원하신다면 명백한 상위 아이템과 베이스의 아이템으로 교체하세요."

            target_hearts = ["리튬 하트", "페어리 하트", "티타늄 하트", "플라즈마 하트"]
            upgrade_hearts = ["리튬 하트", "페어리 하트", "티타늄 하트"]
            black_heart = ["블랙 하트"]
            if any(heart in item_name for heart in upgrade_hearts):
                if total_score >= 125:
                    return "🚨 [업그레이드 권장] 현재 하트는 성능 한계가 명확합니다. 플라즈마 하트로의 강화를 고려하세요."
            if any(heart in item_name for heart in target_hearts):
                if total_score >= 275:
                    return "🚨 [교체 권장] 현재 하트는 성능 한계가 명확합니다. 컴플리트 언더컨트롤로의 교체를 고려해야할 시기입니다."
            if any(heart in item_name for heart in black_heart):
                return "🚨 블랙 하트는 점수 환산을 지원하지 않습니다."

            # 타일런트(슈페리얼) 전용 가이드 로직
            if "타일런트" in item_name:
                if total_score >= 280:
                    return "✨ [교체 권장] 어느정도 완성된 슈페리얼 아이템입니다. 교체를 원하신다면 지금의 아이템보다 상위의 아이템으로 교체를 추천합니다."
                else:
                    return "⚠️ [교체 권장] 하나에서 두개 정도 부분이 아쉬운 슈페리얼 아이템입니다. 강화를 하시기보다는 동급이나 상위의 아이템으로 교체를 추천합니다."

            max_star_possible = 30
            if item_req_level < 128:
                max_star_possible = 15
            elif item_req_level < 138:
                max_star_possible = 20

            labels = ["추가옵션", "윗잠재", "에디셔널", "스타포스"]
            max_bench = [110, 108, 40, 105]

            eval_indices = [1, 2]
            if not any(k in part_name for k in ["반지", "어깨장식", "기계 심장", "보조무기", "엠블렘"]):
                eval_indices.append(0)
            if not any(k in part_name for k in ["보조무기", "엠블렘"]):
                eval_indices.append(3)

            ratios = [scores[i] / max_bench[i] for i in range(4)]

            # worst_label 및 next_label (차순위) 계산
            indexed_ratios = [(ratios[i], labels[i]) for i in eval_indices]
            indexed_ratios.sort(key=lambda x: x[0])
            worst_label = indexed_ratios[0][1]
            next_label = indexed_ratios[1][1] if len(indexed_ratios) > 1 else worst_label
            min_ratio = indexed_ratios[0][0]

            if total_score >= 350:
                if star_val >= 22 or star_val >= max_star_possible:
                    # 1. 추가옵션 체크 스킵 대상 확인
                    no_flame_items = ["숄더", "거대한 공포", "마이스터 링", "가디언", "근원의 속삭임", "황홀한 악몽", "컴플리트 언더컨트롤"]
                    is_no_flame_item = any(k in item_name for k in no_flame_items)

                    # 2. 추가옵션 체크
                    if not is_no_flame_item:
                        target_add = 103.5 if item_req_level <= 200 else 101
                        if scores[0] < target_add:
                            return "✨ [추가옵션 강화 / 전승] 추가옵션이 완성되지 않았습니다. 강화하거나, 전승을 고려하세요."

                    # 3. 잠재능력 및 에디셔널 체크
                    if scores[1] < 101:
                        return "✨ [잠재능력 강화 / 전승] 베이스는 완벽합니다. 잠재능력 수치를 극대화하기 위해 강화하거나, 전승을 고려하세요."
                    elif scores[2] <= 43:
                        return "✨ [에디/교체 권장] 베이스는 완벽하지만, 에디셔널을 강화하거나 상위 매물로 교체하세요"

                    return f"👑 [종결] 완벽한 장비입니다. {star_val + 1}성 도전 외엔 투자가 무의미합니다."

                return f"🔍 [미세조정] 종결급이나 '{worst_label}'이(가) 평균보다 낮습니다."

            elif total_score >= 300:
                if total_score >= 330:
                    if star_val >= 23:
                        return "✨ [종결 / 상위매물 교체] 23성 이상 도달한 장비입니다. 추가 강화보다는 이대로 사용하시거나, 상위 등급의 베이스 아이템 매물로 교체하는 것을 추천합니다."
                    target_star = 23 if item_req_level <= 160 else 22
                    if star_val < target_star and 3 in eval_indices:
                        return f"⭐ [스타포스 권장] 체급에 걸맞은 '{target_star}성' 달성이 시급합니다. 우선적으로 {star_val + 1}성 강화를 최우선하세요."
                    return f"💎 [준종결] 종결급 체급입니다. 마지막 퍼즐인 '{worst_label}' 수치 보완을 추천합니다."

                if 3 in eval_indices:
                    if star_val >= max_star_possible:
                        return f"✅ [강화 한계] 최대치까지 강화되었습니다. 이제 부족한 '{worst_label}'에 집중하세요."
                    if star_val <= 18:
                        target_star = 21 if max_star_possible >= 21 else max_star_possible
                        return f"⚔️ [단계적 강화] 베이스가 훌륭합니다. 우선 '{target_star}성 안착'을 목표로 강화를 추천합니다."
                    if star_val < 22:
                        if min_ratio < 0.25:
                            return f"🚨 [밸런스 보완] 스타포스보다 급한 것은 '{worst_label}'입니다. 밸런스를 먼저 맞춰주세요."
                        return "📈 [상위 강화] 22성 도전의 가치가 있는 베이스입니다. 22성 강화를 고려하세요."
                return f"🛠️ [강화 권장] 엘리트 장비입니다. 전체적인 밸런스를 위해 '{worst_label}'을 보완하세요."

            elif total_score >= 250:
                if star_val < 17 and 3 in eval_indices:
                    return "📦 [가성비 강화] 최소 17 ~ 18성 달성 후 잠재능력을 손보는 것이 효율적입니다."
                return f"📈 [효율 투자 / 교체] '{worst_label}'부터 차근차근 올리거나, 상위 아이템으로 교체를 추천합니다."

            elif total_score >= 175:
                is_limited = any(h in item_name for h in
                                 ["이터널 플레임 링", "어웨이크 링", "테네브리스 원정대 반지", "글로리온 링 : 슈프림"])

                if not is_limited and star_val < 17 and 3 in eval_indices:
                    return "📦 [가성비 정체] 베이스는 나쁘지 않으나 스타포스가 낮습니다. 17성 강화 시 점수가 대폭 상승합니다."

                if is_limited:
                    return "📈 [이벤트 아이템] 이벤트 아이템을 강화하기보다는, 무료 재화를 사용하거나 상위 아이템으로의 교체를 추천합니다."

                if scores[1] < 65:
                    if scores[2] < 12.5:
                        return "🔮 [잠재 / 에디 부족] 스타포스에 비해 잠재능력이 아쉽습니다. 더 높은 잠재능력을 위해 강화하거나 교체를 추천합니다."
                    else:
                        return "🔮 [잠재 부족] 스타포스에 비해 잠재능력이 아쉽습니다. 더 높은 잠재능력을 위해 강화하거나 교체를 추천합니다."

                if scores[2] < 12.5:
                    return "🔮 [에디 부족] 스타포스에 비해 에디셔널 잠재능력이 아쉽습니다. 더 높은 에디셔널 잠재능력을 위해 강화를 추천합니다."

                if scores[0] < 82.5 and 0 in eval_indices:
                    return "🌀 [베이스 부실] 추옵이 낮아 투자가 비효율적입니다. 추가옵션의 강화를 추천합니다."

                if is_limited:
                    return f"✅ [다음 단계 준비] 전반적인 밸런스가 좋습니다. 다음 단계로 넘어가기 위한 업그레이드나 교체를 준비하세요."
                return f"✅ [다음 단계 준비] 전반적인 밸런스가 좋습니다. 다음 단계로 넘어가기 위한 상위 아이템 교체나 {next_label} 강화를 준비하세요."

            else:
                return "🚨 [교체 시급] 현재 세팅에서 가장 효율이 떨어지는 부위입니다. 상위 아이템으로 교체를 추천합니다."

        # --- [헬퍼 함수 4: 특수 부위 가이드 생성] ---
        def get_special_part_guide(total_score, part_name, item_name):
            if "포켓 아이템" in part_name:
                if total_score >= 280: return "👑 포켓 부위 종결급 추옵입니다."
                if total_score >= 200: return "✅ 준수한 가성비 성배입니다."
                return "🚨 더 높은 급수의 성배(80급 이상)로 교체를 추천합니다."
            if "뱃지" in part_name:
                if any(k in item_name for k in ["창세", "칠요"]): return "👑 상위 티어 뱃지를 착용 중입니다."
                return "💡 칠요의 뱃지 혹은 창세의 뱃지로의 업그레이드 목표를 잡으세요."
            if "훈장" in part_name:
                if any(k in item_name for k in ["칠요", "카루타", "멸살"]): return "👑 종결급 훈장입니다."
                return "💡 더 높은 등급의 훈장 획득을 권장합니다."
            return "✅ 해당 부위는 표준 성능을 보여주고 있습니다."

        # --- [아이템 개별 평가 루프] ---
        for item in items:
            slot = item.get("item_equipment_slot", "")
            part = item.get("item_equipment_part", "")
            name = item.get("item_name", "")
            icon = item.get("item_icon", "")
            star = int(item.get("starforce", 0))
            item_req_level = int(item.get("item_base_option", {}).get("base_equipment_level", 0))

            is_weapon = any(k in slot for k in ["무기", "보조무기", "엠블렘"])
            is_special = any(k in part for k in ["훈장", "뱃지", "포켓 아이템", "칭호"])
            special_rings = ["리스트레인트", "컨티뉴어스", "웨폰퍼프"]

            # 공통: 모든 아이템에 일관되게 들어갈 완벽한 raw_options_dict 생성
            raw_options_dict = {
                "base": item.get("item_base_option"),
                "add": item.get("item_add_option"),
                "etc": item.get("item_etc_option"),
                "starforce": item.get("item_starforce_option"),
                "potential_grade": item.get("potential_option_grade"),
                "potential_options": [item.get("potential_option_1"), item.get("potential_option_2"),
                                      item.get("potential_option_3")],
                "additional_grade": item.get("additional_potential_option_grade"),
                "additional_options": [item.get("additional_potential_option_1"),
                                       item.get("additional_potential_option_2"),
                                       item.get("additional_potential_option_3")],
                "exceptional": item.get("item_exceptional_option")
            }

            # 1. 특수 부위 평가 로직 (훈장, 뱃지, 포켓 아이템, 칭호)
            if is_special:
                actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)

                if "포켓" in part:
                    total_item_score = actual_add_급수 * 2.0
                elif any(k in name for k in ["창세"]):
                    total_item_score = 280.0
                elif any(k in name for k in ["칠요"]):
                    total_item_score = 250.0
                elif any(k in name for k in ["불멸"]):
                    total_item_score = 320.0
                else:
                    total_item_score = 180.0

                guide_text = get_special_part_guide(total_item_score, part, name)

                evaluate_list.append({
                    "is_wse": True, "is_special": True, "is_noljang": False, "part": part, "name": name, "icon": icon, "star": 0,
                    "total_score": round(total_item_score, 2),
                    "guide": guide_text,
                    "detail": {"add": round(total_item_score, 1), "star": 0, "pot": 0, "pot_additional": 0},
                    "raw_options": raw_options_dict
                })
                continue  # 특수 부위는 아래 일반 장비 로직을 건너뜀

            # 💡 [정밀 판별] 비정상적 강화 수치 기반 놀장강 판별 로직 추가
            etc_ops = item.get("item_etc_option", {})
            star_ops = item.get("item_starforce_option", {})

            # 1. 주문서(etc) 수치 중 최대값 추출
            etc_stats_max = max(
                int(etc_ops.get("str", 0)), int(etc_ops.get("dex", 0)),
                int(etc_ops.get("int", 0)), int(etc_ops.get("luk", 0))
            )
            etc_atk_max = max(int(etc_ops.get("attack_power", 0)), int(etc_ops.get("magic_power", 0)))

            # 2. 스타포스 수치 중 최대값 추출
            star_stats_max = max(
                int(star_ops.get("str", 0)), int(star_ops.get("dex", 0)),
                int(star_ops.get("int", 0)), int(star_ops.get("luk", 0))
            )

            is_superior = "타일런트" in name
            is_noljang = False

            # 놀장강은 150제 이하 아이템에만 존재하며, 12성일 때 주문서(etc) 수치가 비정상적으로 높습니다.
            if 8 <= star <= 15 and not is_superior and item_req_level <= 150:
                # 일반적인 주문서(작)로는 150제 펜던트 등에서 공/마가 100(이미지의 +114) 근처까지 갈 수 없습니다.
                # 보통 놀장 12성은 etc_stats가 100 이상, etc_atk가 50~100 이상을 기록합니다.
                if etc_stats_max > 50 and etc_atk_max > 10:
                    is_noljang = True
                # 혹은 API에 따라 starforce_option에 수치가 아예 없고 etc에만 몰려있는 경우도 포함
                elif star_stats_max == 0 and (etc_stats_max > 30 or etc_atk_max > 15):
                    is_noljang = True

            # 2. 일반 장비 점수 산출 로직
            # 💡 놀장강일 경우 22성 급 점수 부여, 슈페리얼일 경우 보정치 적용
            if is_noljang:
                adv_star_score = get_starforce_score(22, item_req_level)
            elif is_superior:
                adv_star_score = get_starforce_score(star, item_req_level) * 3.0
            else:
                adv_star_score = get_starforce_score(star, item_req_level)

            if is_weapon:
                if any(k in slot for k in ["보조무기", "엠블렘"]):
                    add_score = 100.0
                    adv_star_score = 100.0
                else:
                    weapon_add_val = nexon_api.calculate_weapon_add_option_score(item, char_class)
                    add_score = weapon_add_val * 2.0

                weapon_pot_val = nexon_api.calculate_weapon_potential_score(item, "potential", char_class)
                pot_score = (weapon_pot_val * 3.3) if weapon_pot_val > 0 else 0
                weapon_eddy_val = nexon_api.calculate_weapon_potential_score(item, "additional_potential", char_class)
                eddy_score = (weapon_eddy_val * 2.5) if weapon_eddy_val > 0 else 0
                pot_val = weapon_pot_val
            else:
                actual_add_급수 = nexon_api.calculate_item_score(item.get("item_add_option", {}), char_class)
                add_score = get_advanced_add_score(actual_add_급수, item_req_level, part)
                pot_val = nexon_api.calculate_potential_score(item, "potential", char_class, char_level)
                pot_score = (pot_val * 3.3) if pot_val > 0 else 0
                eddy_val = nexon_api.calculate_potential_score(item, "additional_potential", char_class, char_level)
                eddy_score = (eddy_val * 2.5) if eddy_val > 0 else 0

            total_item_score = add_score + pot_score + eddy_score + adv_star_score

            # 시드링 계열 제외 후 일반 장비 리스트 추가
            if pot_val != -1 and not any(k in name for k in special_rings):
                # 💡 가이드 함수에 is_noljang 변수 전달
                guide_text = get_dynamic_guide([add_score, pot_score, eddy_score, adv_star_score], star, part,
                                               total_item_score, name, item_req_level, is_noljang)

                evaluate_list.append({
                    "is_wse": is_weapon, "is_special": False, "is_noljang": is_noljang, "slot": slot, "part": part, "name": name, "icon": icon, "star": star,
                    "total_score": round(total_item_score, 2),
                    "guide": guide_text,
                    "detail": {
                        "add": round(add_score, 1), "star": round(adv_star_score, 1),
                        "pot": round(pot_score, 1), "pot_additional": round(eddy_score, 1)
                    },
                    "raw_options": raw_options_dict
                })

        all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])

        # 총평 계산 (특수 부위 제외)
        total_scores = [item["total_score"] for item in evaluate_list if not item["is_special"]]
        avg_score = sum(total_scores) / len(total_scores) if total_scores else 0
        worst_item = next((item for item in all_sorted_results if not item.get("is_special")), None)

        # 💡 [수정] 무기 슬롯이면서 이름에 데스티니가 들어가는지 확인
        has_destiny_weapon = any(
            "데스티니" in item.get("name", "")
            for item in evaluate_list
        )

        if avg_score >= 380:
            rank, comment = "ETERNAL", "전 서버 최상위권 장비입니다. 이제 2차 초월의 영역입니다."
        elif avg_score >= 350:
            if has_destiny_weapon:
                rank, comment = "DESTINY", "이미 데스티니 무기를 쟁취한 훌륭한 스펙입니다!  부족한 부위를 다듬고 더 높은 곳으로의 성장을 준비하세요!"
            else:
                rank, comment = "DESTINY", "데스티니 초월에 충분히 도전할만한 스펙입니다.  당신의 가능성을 믿고 초월에 도전하세요"
        elif avg_score >= 330:
            if has_destiny_weapon:
                rank, comment = "DESTINY", "이미 데스티니 무기를 쟁취한 훌륭한 스펙입니다!  부족한 부위를 다듬고 더 높은 곳으로의 성장을 준비하세요!"
            else:
                rank, comment = "DESTINY", "데스티니 초월이 가시권에 들어왔습니다.  천천히 부족한 부위를 강화하고 데스티니 초월에 도전하세요"
        elif avg_score >= 280:
            rank, comment = "ASTRA", "본격적으로 아스트라 해방에 도전하세요.  2차 해방까지는 가성비의 영역으로 들어섰습니다"
        elif avg_score >= 220:
            rank, comment = "ASTRA", "아스트라 보조 해방을 위한 준비를 할 때 입니다.  천천히 아스트라 해방 준비에 도전하세요"
        elif avg_score >= 200:
            rank, comment = "GENESIS", "제네시스 해방을 위한 준비를 할 때 입니다.  해방 준비를 해 보세요"
        else:
            rank, comment = "EPIC", "성장 가능성이 큽니다. 낮은 점수 부위부터 교체해보세요."

        overall_review = {
            "rank": rank, "avg_score": round(avg_score, 1), "main_comment": comment,
            "priority_target": worst_item["name"] if worst_item else "없음",
            "next_step": f"'{worst_item['name']}' 부위의 보완이 가장 시급합니다." if worst_item else ""
        }

        all_sorted_results = sorted(evaluate_list, key=lambda x: x["total_score"])

        return {
            "character": character_name, "class": char_class, "level": char_level,
            "character_image": char_image, "combat_power": combat_power,
            "best_preset": best_preset_idx, "overall": overall_review,
            "results": all_sorted_results
        }