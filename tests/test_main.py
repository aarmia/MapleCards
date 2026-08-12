import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.main import APP_JS_VERSION, app, check_items
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


class MainEndpointTests(unittest.IsolatedAsyncioTestCase):
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


if __name__ == "__main__":
    unittest.main()
