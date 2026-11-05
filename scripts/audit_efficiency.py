"""Live reference comparison; prints Markdown only, never credentials or raw API data.

Run: python -m scripts.audit_efficiency  (실제 이름은 .env MECULATOR_SAMPLE_CHARACTERS, scripts/sample_characters.py)
The old-buff column uses the SAME API snapshot, with preset optimization rerun.
"""
import asyncio
import hashlib
import json
from datetime import datetime, timezone
from unittest.mock import patch

from app.calculator import get_main_stat
from app.character_profile import build_character_profile
from app.potential import convert_line
from app.preset_optimizer import optimize_presets
from app.preset_projection import active_items
from app.scoring_config import POTENTIAL
from app.scraper import NexonAPIHandler
from scripts.sample_characters import character_name

REFERENCES = {
    "2고스펙": ("LUK", "DEX", [12.03, 8, 7.04, 6.81, 6, 5.67, 4.16, 4.01, 3.64, 3.52, 2.13, 1.92, 1.08, .81, .50, .25, .15, .14]),
    "1랭커": ("LUK", "DEX", [12.56, 8, 7.44, 6.84, 6, 5.70, 2.96, 4.19, 2.59, 3.72, 2.25, 2.03, 1.12, .84, .49, .25, .15, .13]),
    "5세나": ("DEX", "STR", [11.93, 8, 7.30, 6.91, 6, 5.76, 3.64, 3.98, 3.19, 3.65, 2.21, 1.99, 1.21, .91, .61, .31, .19, .17]),
}


def options(main, sub):
    return ["크리티컬 데미지 +3%", f"{main} +8%", f"캐릭터 기준 9레벨 당 {main} +2",
            "올스탯 +6%", f"{main} +6%", "올스탯 +5%", "공격력 +16", "크리티컬 데미지 +1%",
            "공격력 +14", f"캐릭터 기준 9레벨 당 {main} +1", f"{main} +20", f"{main} +18",
            f"{sub} +8%", f"{sub} +6%", f"캐릭터 기준 9레벨 당 {sub} +2",
            f"캐릭터 기준 9레벨 당 {sub} +1", f"{sub} +20", f"{sub} +18"]


def evaluate(basic, stats, items, extras, lines):
    cls, level = basic["character_class"], basic["character_level"]
    main = get_main_stat(cls, stats)
    ctx = build_character_profile(cls, main, level, active_items(items["item_equipment"]), stats, extras)["context"]
    optimized = optimize_presets(cls, main, level, items, stats, extras, ctx)
    if optimized is None:
        raise ValueError("Saved preset optimization unavailable")
    _, _, ctx, info = optimized
    return [convert_line(line, ctx, level)[0] for line in lines], ctx, info


async def main():
    print("# 효율 참조표 대조\n\n조회 UTC: " + datetime.now(timezone.utc).isoformat())
    print("\n참조: 사용자가 제공한 과거 스크린샷. 현재 API와 촬영 시점이 달라 순수 수식 오차로 단정할 수 없음.")
    print("직전 버프 열: 동일 API 입력에서 올스탯 0, 공/마 355, 크뎀 54로 복원 후 최적화 재실행. 나머지 코드는 현재 버전.")
    handler = NexonAPIHandler()
    try:
        for name, (main_stat, sub, refs) in REFERENCES.items():  # name = 익명 라벨
            ocid = await handler.get_ocid(character_name(name))
            basic, stats, items, (extras, failures) = await asyncio.gather(
                handler.get_character_basic(ocid), handler.get_character_stat(ocid),
                handler.get_character_item(ocid), handler.get_stat_extras(ocid))
            lines = options(main_stat, sub)
            values, ctx, info = evaluate(basic, stats, items, extras, lines)
            with patch.dict(POTENTIAL.buff.raw, {"all_stat_flat": 0, "attack_flat": 355, "crit_damage_pct": 54}):
                previous, _, old_info = evaluate(basic, stats, items, extras, lines)
            digest = hashlib.sha256(json.dumps([basic, stats, items, extras], sort_keys=True).encode()).hexdigest()[:16]
            print(f"\n## {name}\n\n레벨 {basic['character_level']} / {basic['character_class']} / 입력 해시 {digest}")
            print(f"\n추가 조회 실패: {json.dumps(failures, ensure_ascii=False)}")
            print(f"\n선택 조합: {info['selected']} / 직전 버프 조합: {old_info['selected']}")
            from dataclasses import asdict
            snapshot = asdict(ctx)
            snapshot["sources"] = {}
            print(f"\n환산 회귀 입력: {json.dumps(snapshot, ensure_ascii=False)}")
            print(f"\n세트 보정: {ctx.sources.get('set_projection_status')} / 제약: {info.get('limitations')}")
            print("\n| 옵션 | 참조 | 직전 버프 | 현재 | 차이(%p) |\n|---|---:|---:|---:|---:|")
            for line, ref, old, now in zip(lines, refs, previous, values):
                print(f"| {line} | {ref:.2f} | {old:.2f} | {now:.2f} | {now-ref:+.2f} |")
            print(f"\n평균 절대 차이: {sum(abs(v-r) for v,r in zip(values,refs))/len(refs):.4f}%p")
    finally:
        await handler.close()


if __name__ == "__main__":
    asyncio.run(main())
