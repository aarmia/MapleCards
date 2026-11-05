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
            assert all(a['final_damage'] > 0 and a['cost'] > 0 for a in actions)
            top = report['spec_up']['top']
            assert all(not (a['axis'] == b['axis'] == '스타포스' and a['slot'] == b['slot']) for a, b in zip(top, top[1:]))
            print(json.dumps({'name': name, 'analysis_seconds': round(time.perf_counter()-started, 3),
                              'failures': failures, 'actions': len(actions),
                              'models': sorted(set(a['model'] for a in actions)),
                              'top': [{k: a[k] for k in ('name', 'axis', 'label', 'final_damage', 'damage_efficiency')} for a in top[:3]]}, ensure_ascii=False), flush=True)
    finally:
        await handler.close()


if __name__ == '__main__':
    asyncio.run(main())
