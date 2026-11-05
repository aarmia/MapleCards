import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from app.main import app
from app.rate_limit import SlidingWindowRateLimiter
from app.request_tracker import DiagnosticRequestTracker
from app.scraper import NexonAPIError

SAMPLE_ITEM = {
    "item_equipment_part": "상의",
    "item_equipment_slot": "상의",
    "item_name": "도전자의 상의",
    "potential_option_grade": "유니크",
    "potential_option_1": "INT +9%",
    "potential_option_2": "INT +6%",
    "potential_option_3": None,
    "additional_potential_option_grade": "에픽",
    "additional_potential_option_1": "INT +4%",
    "additional_potential_option_2": "마력 +10",
    "additional_potential_option_3": None,
    "starforce": "22",
    "item_base_option": {"base_equipment_level": 200},
    "item_add_option": {},
    "item_etc_option": {},
    "item_starforce_option": {},
}


class FakeNexonAPI:
    """장비 1개짜리 캐릭터를 돌려주는 가짜 API. 세 호출이 동시에 나가는지도 셉니다."""

    def __init__(self, error=None):
        self.error = error
        self.ocid_calls = 0
        self.active = self.max_active = 0

    async def get_ocid(self, _):
        self.ocid_calls += 1
        if self.error:
            raise self.error
        return "ocid"

    async def _respond(self, data):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return data

    async def get_character_basic(self, _):
        return await self._respond({"character_class": "레테", "character_level": 285, "character_image": ""})

    async def get_character_item(self, _):
        return await self._respond({"preset_no": 1, "item_equipment": [SAMPLE_ITEM]})

    async def get_character_stat(self, _):
        return await self._respond(
            {"final_stat": [{"stat_name": "전투력", "stat_value": "123"}, {"stat_name": "INT", "stat_value": "30000"}, {"stat_name": "마력", "stat_value": "2000"}]}
        )

    async def get_stat_extras(self, _):
        # 심볼 조회가 실패해도 진단은 계속되어야 합니다.
        return {}, {"symbol": "NEXON_TIMEOUT"}


def client():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


class MainEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_check_items_returns_report_and_logs_one_event(self):
        api = FakeNexonAPI()
        with patch("app.main.nexon_api", api), self.assertLogs("app.main", level="INFO") as logs:
            async with client() as c:
                response = await c.get("/check-items/테스트?request_id=req-1")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual((body["character"], body["class"], body["level"], body["combat_power"]), ("테스트", "레테", 285, "123"))
        self.assertEqual(body["results"][0]["name"], "도전자의 상의")
        self.assertEqual(body["grades"]["item_grade"][0], [90, "SSS+"])
        self.assertEqual(body["profile"]["context"]["basis"], "estimated")  # 추가 조회 실패 → 레벨로 추정
        self.assertEqual(api.max_active, 3)  # basic · item · stat 은 동시에 조회합니다.
        event = next(record for record in logs.records if record.getMessage() == "diagnosis")
        self.assertEqual((event.request_id, event.outcome, event.http_status), ("req-1", "ok", 200))
        self.assertEqual(event.extras_failed, {"symbol": "NEXON_TIMEOUT"})
        self.assertEqual(event.response_bytes, len(response.content))
        self.assertNotIn("테스트", json.dumps(event.__dict__, default=str, ensure_ascii=False))

    async def test_root_page_embeds_grade_cutoffs(self):
        async with client() as c:
            response = await c.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('window.MECULATOR_GRADES = {"item_grade": [[90, "SSS+"]', response.text)
        self.assertIn("gradeCutText('SSS+')", response.text)

    async def test_spec_up_cost_recalculates_with_user_price_without_nexon_calls(self):
        api = FakeNexonAPI()
        with patch("app.main.nexon_api", api):
            async with client() as c:
                response = await c.post("/spec-up/starforce-cost", json={"items": [
                    {"level": 140, "star": 21, "base_price": 1e8}, {"level": 140, "star": 5, "base_price": 1e8}]})
                rejected = await c.post("/spec-up/starforce-cost", json={"items": [{"level": 140, "star": 22, "base_price": -1}]})
                targets = await c.post("/spec-up/starforce-cost", json={"items": [
                    {"level": 200, "star": 22, "target_star": 25, "base_price": 1e8},
                    {"level": 200, "star": 22, "base_price": 1e8}]})
                backwards = await c.post("/spec-up/starforce-cost", json={"items": [
                    {"level": 200, "star": 22, "target_star": 21, "base_price": 1e8}]})
        self.assertEqual(response.status_code, 200)
        costs = response.json()["costs"]
        self.assertAlmostEqual(costs[0] / 1e8, 59.08 - 34.84, delta=0.01)  # starforce.gg 140제·노작 1억: 12→22 − 12→21
        self.assertIsNone(costs[1])  # 12성 미만은 계산하지 않음
        self.assertEqual(rejected.status_code, 422)
        from app.starforce_cost import expected_cost
        self.assertEqual(targets.status_code, 200)
        self.assertEqual(targets.json()["costs"], [round(expected_cost(200, 1e8, 22, 25)), round(expected_cost(200, 1e8, 22, 23))])
        self.assertEqual(backwards.status_code, 422)
        self.assertEqual(api.max_active, 0)

    async def test_nexon_error_becomes_json_with_code_and_retry_after(self):
        error = NexonAPIError("메이플스토리 점검 중입니다.", status_code=503, code="OPENAPI00010", retry_after=600)
        with patch("app.main.nexon_api", FakeNexonAPI(error=error)), self.assertLogs("app.main", level="WARNING"):
            async with client() as c:
                response = await c.get("/check-items/test")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["retry-after"], "600")
        self.assertEqual(response.json(), {"error": "메이플스토리 점검 중입니다.", "code": "OPENAPI00010", "retry_after": 600})

    async def test_full_queue_returns_503(self):
        tracker = DiagnosticRequestTracker(concurrency=1, max_waiting=0)
        entered, release = asyncio.Event(), asyncio.Event()

        async def hold_only_slot():
            async with tracker.track("holder"):
                entered.set()
                await release.wait()

        holder = asyncio.create_task(hold_only_slot())
        await entered.wait()
        with (
            patch("app.main.request_tracker", tracker),
            patch("app.main.nexon_api", FakeNexonAPI()),
            self.assertLogs("app.main", level="WARNING"),
        ):
            async with client() as c:
                response = await c.get("/check-items/테스트")
        release.set()
        await holder

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["code"], "QUEUE_FULL")
        self.assertEqual(response.headers["retry-after"], str(response.json()["retry_after"]))

    async def test_rate_limited_ip_gets_429_without_touching_nexon(self):
        api = FakeNexonAPI()
        with patch("app.main.nexon_api", api), patch("app.main.rate_limiter", SlidingWindowRateLimiter([(1, 600.0)])):
            async with client() as c:
                first = await c.get("/check-items/테스트", headers={"X-Forwarded-For": "198.51.100.7, 203.0.113.9"})
                with self.assertLogs("app.main", level="WARNING"):
                    second = await c.get("/check-items/테스트", headers={"X-Forwarded-For": "203.0.113.9"})

        self.assertEqual((first.status_code, second.status_code), (200, 429))
        self.assertEqual(second.json()["code"], "RATE_LIMITED")
        self.assertEqual(api.ocid_calls, 1)

    async def test_disconnected_client_does_not_consume_nexon_calls(self):
        api = FakeNexonAPI()
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/check-items/test",
            "raw_path": b"/check-items/test",
            "query_string": b"",
            "headers": [(b"host", b"test")],
            "client": ("127.0.0.1", 1234),
            "server": ("test", 80),
        }
        incoming = [{"type": "http.request", "body": b"", "more_body": False}]
        sent = []

        async def receive():
            return incoming.pop(0) if incoming else {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        with patch("app.main.nexon_api", api), self.assertLogs("app.main", level="INFO"):
            await app(scope, receive, send)

        self.assertEqual(next(m for m in sent if m["type"] == "http.response.start")["status"], 499)
        self.assertEqual(api.ocid_calls, 0)


if __name__ == "__main__":
    unittest.main()
