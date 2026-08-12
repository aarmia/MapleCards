import unittest

import httpx

from app.scraper import NexonAPIError, NexonAPIHandler


class NexonAPIHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        handler = getattr(self, "handler", None)
        if handler is not None:
            await handler.close()

    def make_handler(self, callback, api_key="test-key"):
        transport = httpx.MockTransport(callback)
        client = httpx.AsyncClient(
            transport=transport,
            base_url="https://open.api.nexon.com/maplestory/v1",
        )
        self.handler = NexonAPIHandler(api_key=api_key, client=client)
        return self.handler

    async def test_reuses_one_client_for_multiple_requests(self):
        requested_paths = []

        def callback(request):
            requested_paths.append(request.url.path)
            if request.url.path.endswith("/id"):
                return httpx.Response(200, json={"ocid": "test-ocid"})
            return httpx.Response(200, json={"character_name": "테스트"})

        handler = self.make_handler(callback)
        original_client = handler.client

        ocid = await handler.get_ocid("테스트")
        basic = await handler.get_character_basic(ocid)

        self.assertEqual(ocid, "test-ocid")
        self.assertEqual(basic["character_name"], "테스트")
        self.assertIs(handler.client, original_client)
        self.assertEqual(len(requested_paths), 2)

    async def test_start_and_close_manage_client_lifecycle(self):
        self.handler = NexonAPIHandler(api_key="test-key")

        await self.handler.start()
        client = self.handler.client

        self.assertIsNotNone(client)
        self.assertFalse(client.is_closed)

        await self.handler.close()

        self.assertTrue(client.is_closed)
        self.assertIsNone(self.handler.client)

    async def test_missing_api_key_raises_service_unavailable(self):
        handler = self.make_handler(
            lambda _: httpx.Response(500),
            api_key="",
        )

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("테스트")

        self.assertEqual(context.exception.status_code, 503)

    async def test_invalid_character_is_mapped_to_not_found(self):
        handler = self.make_handler(
            lambda _: httpx.Response(
                400,
                json={"error": {"name": "OPENAPI00004"}},
            )
        )

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("없는캐릭터")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.message, "캐릭터를 찾을 수 없습니다.")

    async def test_rate_limit_is_preserved(self):
        handler = self.make_handler(lambda _: httpx.Response(429))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_basic("test-ocid")

        self.assertEqual(context.exception.status_code, 429)

    async def test_timeout_is_mapped_to_gateway_timeout(self):
        def callback(request):
            raise httpx.ReadTimeout("timed out", request=request)

        handler = self.make_handler(callback)

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_stat("test-ocid")

        self.assertEqual(context.exception.status_code, 504)


if __name__ == "__main__":
    unittest.main()
