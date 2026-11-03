import io
import json
import logging
import unittest

from app.logging_setup import JsonFormatter, configure_logging


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        root, access, httpx_logger = logging.getLogger(), logging.getLogger("uvicorn.access"), logging.getLogger("httpx")
        self._saved = (list(root.handlers), root.level, access.disabled, access.propagate, httpx_logger.level)

    def tearDown(self):
        root, access, httpx_logger = logging.getLogger(), logging.getLogger("uvicorn.access"), logging.getLogger("httpx")
        root.handlers[:], level, access.disabled, access.propagate, httpx_level = self._saved
        root.setLevel(level)
        httpx_logger.setLevel(httpx_level)

    def test_json_formatter_emits_one_line_with_extra_fields(self):
        record = logging.LogRecord("app.main", logging.INFO, "main.py", 1, "diagnosis", (), None)
        record.request_id, record.total_ms, record.color_message = "r1", 12, "uvicorn internal"

        payload = json.loads(JsonFormatter().format(record))

        self.assertEqual((payload["event"], payload["level"], payload["logger"]), ("diagnosis", "INFO", "app.main"))
        self.assertEqual((payload["request_id"], payload["total_ms"]), ("r1", 12))
        self.assertNotIn("color_message", payload)

    def test_configure_logging_silences_access_and_httpx_logs(self):
        stream = io.StringIO()
        configure_logging("json", "INFO", stream=stream)

        logging.getLogger("app.test").info("kept", extra={"code": "QUEUE_FULL"})
        logging.getLogger("uvicorn.access").info("GET /request-status/x")
        logging.getLogger("httpx").info("HTTP Request: GET .../id?character_name=x")

        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line]
        self.assertEqual([(line["event"], line["code"]) for line in lines], [("kept", "QUEUE_FULL")])
        self.assertEqual(len(logging.getLogger().handlers), 1)


if __name__ == "__main__":
    unittest.main()
