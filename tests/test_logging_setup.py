import io
import json
import logging
import unittest
from collections import Counter

from app.analyzer import UnsupportedOptionRegistry
from app.logging_setup import JsonFormatter, TextFormatter, configure_logging


def make_record(message="diagnosis", level=logging.INFO, extra=None, exc_info=None):
    record = logging.LogRecord("app.main", level, "main.py", 10, message, (), exc_info)
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


class FormatterTests(unittest.TestCase):
    def test_json_formatter_emits_one_line_with_extra_fields(self):
        record = make_record(extra={"request_id": "r1", "total_ms": 12, "unsupported_top": [{"option": "STR +6%"}]})

        line = JsonFormatter().format(record)
        payload = json.loads(line)

        self.assertNotIn("\n", line)
        self.assertEqual(payload["event"], "diagnosis")
        self.assertEqual((payload["level"], payload["logger"]), ("INFO", "app.main"))
        self.assertEqual((payload["request_id"], payload["total_ms"]), ("r1", 12))
        self.assertEqual(payload["unsupported_top"][0]["option"], "STR +6%")
        self.assertTrue(payload["ts"].endswith("+00:00"))
        self.assertIsInstance(payload["pid"], int)
        self.assertNotIn("args", payload)

    def test_json_formatter_includes_exception_text(self):
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = make_record("failed", logging.ERROR, exc_info=sys.exc_info())

        payload = json.loads(JsonFormatter().format(record))
        self.assertIn("ValueError: boom", payload["exception"])

    def test_text_formatter_appends_key_values(self):
        record = make_record(extra={"outcome": "ok", "item_count": 23})

        line = TextFormatter("%(levelname)s %(name)s: %(message)s").format(record)

        self.assertEqual(line, 'INFO app.main: diagnosis outcome="ok" item_count=23')


class ConfigureLoggingTests(unittest.TestCase):
    def setUp(self):
        root = logging.getLogger()
        self._saved = (list(root.handlers), root.level)
        self._access = logging.getLogger("uvicorn.access")
        self._access_saved = (self._access.disabled, self._access.propagate)

    def tearDown(self):
        root = logging.getLogger()
        for handler in list(root.handlers):
            root.removeHandler(handler)
        for handler in self._saved[0]:
            root.addHandler(handler)
        root.setLevel(self._saved[1])
        self._access.disabled, self._access.propagate = self._access_saved

    def test_root_gets_single_json_handler_and_access_log_is_disabled(self):
        stream = io.StringIO()

        configure_logging("json", "INFO", stream=stream)
        logging.getLogger("app.test").info("hello", extra={"request_id": "r2"})
        logging.getLogger("uvicorn.access").info("GET /request-status/x")

        lines = [line for line in stream.getvalue().splitlines() if line]
        self.assertEqual(len(logging.getLogger().handlers), 1)
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["request_id"], "r2")
        self.assertTrue(logging.getLogger("uvicorn.access").disabled)

    def test_text_format_is_available_for_local_development(self):
        stream = io.StringIO()

        configure_logging("text", "WARNING", stream=stream)
        logging.getLogger("app.test").info("hidden")
        logging.getLogger("app.test").warning("shown", extra={"code": "QUEUE_FULL"})

        output = stream.getvalue()
        self.assertNotIn("hidden", output)
        self.assertIn('WARNING app.test: shown code="QUEUE_FULL"', output)


class UnsupportedOptionRegistryTests(unittest.TestCase):
    def test_warns_once_per_new_option_and_keeps_counting(self):
        registry = UnsupportedOptionRegistry()
        collector = Counter()

        with self.assertLogs("app.analyzer", level="WARNING") as logs:
            registry.record("앱솔랩스 슈즈", "potential", "STR +6%", collector)
            registry.record("아케인셰이드 슈즈", "potential", "STR +6%", collector)
            registry.record("앱솔랩스 슈즈", "additional_potential", "최대 HP +180", collector)

        self.assertEqual(len(logs.records), 2)
        self.assertEqual(logs.records[0].getMessage(), "unsupported_option_first_seen")
        self.assertEqual((logs.records[0].item, logs.records[0].option), ("앱솔랩스 슈즈", "STR +6%"))
        self.assertEqual(collector[("potential", "STR +6%")], 2)

        summary = registry.summary(top=1)
        self.assertEqual((summary["total"], summary["distinct"]), (3, 2))
        self.assertEqual(summary["top"], [{"type": "potential", "option": "STR +6%", "count": 2}])

    def test_reset_forgets_seen_options(self):
        registry = UnsupportedOptionRegistry()
        with self.assertLogs("app.analyzer", level="WARNING"):
            registry.record("x", "potential", "DEX +9%")
        registry.reset()
        with self.assertLogs("app.analyzer", level="WARNING") as logs:
            registry.record("x", "potential", "DEX +9%")
        self.assertEqual(len(logs.records), 1)
        self.assertEqual(registry.summary()["total"], 1)


if __name__ == "__main__":
    unittest.main()
