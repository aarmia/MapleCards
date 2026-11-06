"""아이템 카탈로그·세트 효과 표 수집 (스펙업 상위 아이템 교체용).

랭킹의 여러 구간에서 캐릭터를 골라 착용·프리셋 장비의 기본 옵션과 주문서 횟수, 세트 효과 전체 표를 모읍니다.
캐릭터 이름은 저장하지 않고 아이템·세트 수치만 app/data/catalog_items.json · catalog_sets.json 에 씁니다.
기존 파일이 있으면 합칩니다(관리자가 손으로 넣은 항목은 지우지 않음).

사용: python scripts/collect_catalog.py [--pages 1,50,200,600,1200] [--per-page 12]
API 호출 수 ≈ 랭킹 페이지 수 + 캐릭터 수 × 3 (ocid · 장비 · 세트 효과).
"""
import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from app.scraper import NexonAPIHandler  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "app" / "data"
ITEMS, SETS = DATA / "catalog_items.json", DATA / "catalog_sets.json"
KST = timezone(timedelta(hours=9))


def nonzero(option: dict) -> dict:
    out = {}
    for key, value in (option or {}).items():
        if key == "base_equipment_level":
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number:
            out[key] = int(number) if number == int(number) else number
    return out


def item_row(item: dict) -> dict:
    slots = sum(int(item.get(key) or 0) for key in ("scroll_upgrade", "scroll_upgradeable_count", "scroll_resilience_count"))
    return {"part": item.get("item_equipment_part"), "slot": item.get("item_equipment_slot"),
            "level": int((item.get("item_base_option") or {}).get("base_equipment_level") or 0),
            "slots": slots, "base": nonzero(item.get("item_base_option")),
            "tradable": item.get("cuttable_count") not in (None, "0", 0)}


async def main(pages: list[int], per_page: int) -> None:
    api = NexonAPIHandler()
    date = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    items = json.loads(ITEMS.read_text(encoding="utf-8")) if ITEMS.exists() else {}
    sets = json.loads(SETS.read_text(encoding="utf-8")) if SETS.exists() else {}
    calls = characters = 0
    for page in pages:
        ranking = (await api._request("/ranking/overall", {"date": date, "page": str(page)})).get("ranking", [])
        calls += 1
        step = max(1, len(ranking) // per_page)
        for entry in ranking[::step][:per_page]:
            try:
                ocid = await api.get_ocid(entry["character_name"])
                equipment = await api.get_character_item(ocid)
                set_effect = await api._request("/character/set-effect", {"ocid": ocid})
            except Exception as error:  # 한 캐릭터 실패는 건너뜁니다
                print("skip:", type(error).__name__)
                continue
            calls += 3
            characters += 1
            pool = list(equipment.get("item_equipment") or [])
            for index in (1, 2, 3):
                pool += equipment.get(f"item_equipment_preset_{index}") or []
            for item in pool:
                name = item.get("item_name")
                if not name or not item.get("item_base_option"):
                    continue
                row = item_row(item)
                old = items.get(name)
                # 같은 이름은 기본 옵션이 같아야 합니다. 주문서 횟수는 관측한 최댓값(실패·복구로 줄어든 값 제외)
                if old is None or (old.get("source") != "manual" and row["slots"] > old.get("slots", 0)):
                    items[name] = {**row, "source": "api"}
            for entry_set in set_effect.get("set_effect") or []:
                full = entry_set.get("set_option_full") or []
                name = entry_set.get("set_name")
                if name and len(full) > len((sets.get(name) or {}).get("options", [])) and (sets.get(name) or {}).get("source") != "manual":
                    sets[name] = {"options": [[int(row["set_count"]), row["set_option"]] for row in full], "source": "api"}
        print(f"page {page}: 누적 캐릭터 {characters} · 아이템 {len(items)} · 세트 {len(sets)}")
    ITEMS.write_text(json.dumps(dict(sorted(items.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    SETS.write_text(json.dumps(dict(sorted(sets.items())), ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"API 호출 약 {calls}회 · 캐릭터 {characters} · 아이템 {len(items)} · 세트 {len(sets)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", default="1,50,200,600,1200")
    parser.add_argument("--per-page", type=int, default=12)
    args = parser.parse_args()
    asyncio.run(main([int(p) for p in args.pages.split(",")], args.per_page))
