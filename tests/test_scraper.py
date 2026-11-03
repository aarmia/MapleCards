import unittest

import httpx

from app.scraper import NexonAPIError, NexonAPIHandler, parse_error_code


def nexon_error(status, code):
    return httpx.Response(status, json={"error": {"name": code, "message": "x"}})


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
        self.assertEqual(client.timeout.connect, 3.0)
        self.assertEqual(client.timeout.read, 6.0)

        await self.handler.close()

        self.assertTrue(client.is_closed)
        self.assertIsNone(self.handler.client)

    async def test_missing_api_key_raises_service_unavailable(self):
        handler = self.make_handler(lambda _: httpx.Response(500), api_key="")

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("테스트")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "NEXON_KEY_MISSING")

    async def test_invalid_character_is_mapped_to_not_found(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00004"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("없는캐릭터")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.code, "CHARACTER_NOT_FOUND")
        self.assertEqual(context.exception.message, "캐릭터를 찾을 수 없습니다.")

    async def test_unreadable_400_on_id_is_treated_as_missing_character(self):
        handler = self.make_handler(lambda _: httpx.Response(400, text="Bad Request"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("없는캐릭터")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.code, "CHARACTER_NOT_FOUND")

    async def test_invalid_key_is_not_reported_as_missing_character(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00005"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("테스트")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "OPENAPI00005")
        self.assertIn("키", context.exception.message)
        self.assertEqual(context.exception.upstream_status, 400)

    async def test_game_maintenance_maps_to_service_unavailable_with_retry_after(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00010"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_ocid("테스트")

        self.assertEqual(context.exception.status_code, 503)
        self.assertEqual(context.exception.code, "OPENAPI00010")
        self.assertEqual(context.exception.retry_after, 600)
        self.assertIn("점검", context.exception.message)

    async def test_quota_exceeded_maps_to_429_with_retry_after(self):
        handler = self.make_handler(lambda _: nexon_error(429, "OPENAPI00007"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_item("test-ocid")

        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(context.exception.code, "OPENAPI00007")
        self.assertEqual(context.exception.retry_after, 10)

    async def test_invalid_identifier_on_character_endpoint(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00003"))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_basic("stale-ocid")

        self.assertEqual(context.exception.status_code, 404)
        self.assertEqual(context.exception.code, "OPENAPI00003")

    async def test_rate_limit_without_body_falls_back_to_status(self):
        handler = self.make_handler(lambda _: httpx.Response(429))

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_basic("test-ocid")

        self.assertEqual(context.exception.status_code, 429)
        self.assertEqual(context.exception.code, "NEXON_HTTP_429")
        self.assertEqual(context.exception.retry_after, 5)

    async def test_timeout_is_mapped_to_gateway_timeout(self):
        def callback(request):
            raise httpx.ReadTimeout("timed out", request=request)

        handler = self.make_handler(callback)

        with self.assertRaises(NexonAPIError) as context:
            await handler.get_character_stat("test-ocid")

        self.assertEqual(context.exception.status_code, 504)
        self.assertEqual(context.exception.code, "NEXON_TIMEOUT")

    async def test_operator_alert_is_logged_once_per_interval(self):
        handler = self.make_handler(lambda _: nexon_error(400, "OPENAPI00005"))

        with self.assertLogs("app.scraper", level="CRITICAL") as logs:
            for _ in range(3):
                with self.assertRaises(NexonAPIError):
                    await handler.get_character_basic("test-ocid")

        self.assertEqual(len(logs.records), 1)
        self.assertEqual(logs.records[0].nexon_code, "OPENAPI00005")


class ParseErrorCodeTests(unittest.TestCase):
    def test_reads_code_from_error_body(self):
        self.assertEqual(parse_error_code(nexon_error(400, "OPENAPI00009")), "OPENAPI00009")

    def test_returns_none_for_non_json_or_unexpected_shape(self):
        self.assertIsNone(parse_error_code(httpx.Response(400, text="<html>")))
        self.assertIsNone(parse_error_code(httpx.Response(400, json={"error": "string"})))
        self.assertIsNone(parse_error_code(httpx.Response(400, json=["list"])))


if __name__ == "__main__":
    unittest.main()
