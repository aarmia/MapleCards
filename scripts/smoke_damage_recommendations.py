"""Read-only live smoke test; no raw API snapshots, credentials or character names are printed.

Run: python -m scripts.smoke_damage_recommendations  (실제 이름은 .env MECULATOR_SAMPLE_CHARACTERS)
"""
import asyncio
import json
import time

from app.analyzer import build_report
from app.scraper import NexonAPIHandler
from scripts.sample_characters import LABELS, character_name


async def main():
    handler = NexonAPIHandler()
    try:
        for name in LABELS:  # 출력은 익명 라벨, 조회만 실제 이름 (.env)
            ocid = await handler.get_ocid(character_name(name))
            basic, stats, items, (extras, failures) = await asyncio.gather(
                handler.get_character_basic(ocid), handler.get_character_stat(ocid),
                handler.get_character_item(ocid), handler.get_stat_extras(ocid))
            started = time.perf_counter()
            report = build_report(name, basic, items, stats, extras=extras)
            json.dumps(report, allow_nan=False)
            actions = [a for row in report['results'] for a in row['spec_up']]
            # 스타포스·시드링 비용은 시세로 화면이 계산하므로 서버 응답에는 고정 비용 행동(잠재·추옵)만 cost 가 있습니다
            assert all(a['final_damage'] > 0 and (a['axis'] in ('스타포스', '시드링') or a['cost'] > 0) for a in actions)
            top = sorted(actions, key=lambda a: a['final_damage'], reverse=True)
            print(json.dumps({'name': name, 'analysis_seconds': round(time.perf_counter()-started, 3),
                              'failures': failures, 'actions': len(actions),
                              'models': sorted(set(a['model'] for a in actions)),
                              'top': [{k: a.get(k) for k in ('axis', 'label', 'final_damage')} for a in top[:3]]}, ensure_ascii=False), flush=True)
    finally:
        await handler.close()


if __name__ == '__main__':
    asyncio.run(main())
