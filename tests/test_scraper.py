import unittest

import httpx

from app.scraper import NexonAPIError, NexonAPIHandler


def nexon_error(status, code):
    return httpx.Response(status, json={"error": {"name": code, "message": "x"}})


class NexonAPIHandlerTests(unittest.IsolatedAsyncioTestCase):
    def make_handler(self, callback):
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(callback), base_url="https://open.api.nexon.com/maplestory/v1"
        )
        self.addAsyncCleanup(client.aclose)
        return NexonAPIHandler(api_key="test-key", client=client)

    async def test_unknown_character_is_not_found(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00004"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("없는캐릭터")

        self.assertEqual((context.exception.status_code, context.exception.code), (404, "CHARACTER_NOT_FOUND"))

    async def test_invalid_key_is_not_reported_as_missing_character(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00005"))

        with self.assertLogs("app.scraper", level="CRITICAL"), self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("테스트")

        self.assertEqual((context.exception.status_code, context.exception.code), (503, "OPENAPI00005"))

    async def test_maintenance_carries_retry_after(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00010"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_basic("ocid")

        self.assertEqual((context.exception.status_code, context.exception.retry_after), (503, 600))
        self.assertIn("점검", context.exception.message)

    async def test_timeout_is_gateway_timeout(self):
        def callback(request):
            raise httpx.ReadTimeout("timed out", request=request)

        handler = self.make_handler(callback)

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_stat("ocid")

        self.assertEqual((context.exception.status_code, context.exception.code), (504, "NEXON_TIMEOUT"))


if __name__ == "__main__":
    unittest.main()
