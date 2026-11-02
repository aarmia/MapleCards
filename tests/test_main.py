import asyncio
import json
import logging
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.main import APP_JS_VERSION, SITE_URL, app, check_items
from app.scraper import NexonAPIError


class ErroringNexonAPI:
    async def get_ocid(self, _):
        raise NexonAPIError("캐릭터를 찾을 수 없습니다.", status_code=404)


class ConcurrentNexonAPI:
    def __init__(self):
        self.active_requests = 0
        self.max_active_requests = 0

    async def get_ocid(self, _):
        return "test-ocid"

    async def _response(self, data):
        self.active_requests += 1
        self.max_active_requests = max(
            self.max_active_requests,
            self.active_requests,
        )
        await asyncio.sleep(0.01)
        self.active_requests -= 1
        return data

    async def get_character_basic(self, _):
        return await self._response(
            {
                "character_class": "나이트워커",
                "character_level": 297,
                "character_image": "",
            }
        )

    async def get_character_item(self, _):
        return await self._response({"item_equipment": []})

    async def get_character_stat(self, _):
        return await self._response({"final_stat": []})


class FixtureNexonAPI:
    """tests/fixtures/characters 의 실제 응답 샘플을 그대로 돌려주는 가짜 API."""

    def __init__(self, fixture):
        self.fixture = fixture

    async def get_ocid(self, _):
        return "fixture-ocid"

    async def get_character_basic(self, _):
        return self.fixture["basic"]

    async def get_character_item(self, _):
        return self.fixture["item_equipment"]

    async def get_character_stat(self, _):
        return self.fixture["stat"]


def load_first_fixture():
    fixture_dir = Path(__file__).parent / "fixtures" / "characters"
    paths = sorted(fixture_dir.glob("*.json"))
    if not paths:
        raise unittest.SkipTest("픽스처가 없습니다. scripts/fetch_fixtures.py 로 생성하세요.")
    return json.loads(paths[0].read_text(encoding="utf-8"))


class MainEndpointTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        # 픽스처 통합 테스트에서 미지원 옵션 경고가 대량으로 찍히지 않도록 잠시 로그 레벨을 올립니다.
        cls._analyzer_logger = logging.getLogger("app.analyzer")
        cls._previous_level = cls._analyzer_logger.level
        cls._analyzer_logger.setLevel(logging.ERROR)

    @classmethod
    def tearDownClass(cls):
        cls._analyzer_logger.setLevel(cls._previous_level)

    async def test_root_uses_versioned_app_script_and_disables_html_cache(self):
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            response = await client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-cache")
        self.assertIn(
            f'/static/js/app.js?v={APP_JS_VERSION}',
            response.text,
        )
        # 오류 UI는 서버가 보낸 메시지를 그대로 바인딩해야 합니다.
        self.assertIn('x-text="errorMessage"', response.text)
        # 가이드 태그는 서버가 분리해 내려주므로 프론트에서 정규식으로 다시 파싱하지 않습니다.
        self.assertNotIn("item.guide.match(", response.text)

    async def test_favicon_robots_and_sitemap_are_served(self):
        transport = httpx.ASGITransport(app=app)

        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://test",
        ) as client:
            favicon = await client.get("/favicon.ico")
            robots = await client.get("/robots.txt")
            sitemap = await client.get("/sitemap.xml")

        self.assertEqual(favicon.status_code, 200)
        self.assertTrue(favicon.headers["content-type"].startswith("image/"))
        self.assertEqual(robots.status_code, 200)
        self.assertIn(f"Sitemap: {SITE_URL}/sitemap.xml", robots.text)
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn(f"<loc>{SITE_URL}/</loc>", sitemap.text)

    async def test_nexon_error_uses_http_status_and_message(self):
        transport = httpx.ASGITransport(app=app)

        with patch("app.main.nexon_api", ErroringNexonAPI()):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/check-items/test")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            response.json(),
            {"error": "캐릭터를 찾을 수 없습니다."},
        )

    async def test_character_requests_run_concurrently(self):
        fake_api = ConcurrentNexonAPI()

        with patch("app.main.nexon_api", fake_api):
            result = await check_items("테스트")

        self.assertEqual(fake_api.max_active_requests, 3)
        self.assertEqual(result["character"], "테스트")

    async def test_check_items_builds_full_report_from_real_fixture(self):
        fixture = load_first_fixture()
        transport = httpx.ASGITransport(app=app)

        with patch("app.main.nexon_api", FixtureNexonAPI(fixture)):
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
            ) as client:
                response = await client.get("/check-items/테스트")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(
            set(body),
            {"character", "class", "level", "character_image", "combat_power", "best_preset", "overall", "results"},
        )
        self.assertEqual(body["class"], fixture["basic"]["character_class"])
        self.assertIn(body["best_preset"], (1, 2, 3))
        self.assertIn(body["overall"]["rank"], {"ETERNAL", "DESTINY", "ASTRA", "GENESIS", "EPIC"})
        self.assertTrue(body["results"])
        for item in body["results"]:
            self.assertTrue(item["guide_tag"])
            self.assertNotIn("[", item["guide_message"])
            self.assertIsInstance(item["total_score"], (int, float))


if __name__ == "__main__":
    unittest.main()
