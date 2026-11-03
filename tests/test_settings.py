import os
import unittest
from unittest.mock import patch

from app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_without_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.diagnosis_concurrency, 5)
        self.assertEqual(settings.queue_max_waiting, 40)
        self.assertEqual(settings.queue_wait_timeout, 8.0)
        self.assertEqual((settings.nexon_connect_timeout, settings.nexon_read_timeout), (3.0, 6.0))
        self.assertEqual((settings.log_format, settings.log_level), ("text", "INFO"))
        self.assertEqual(settings.unsupported_summary_interval, 3600.0)

    def test_log_format_defaults_to_json_on_render(self):
        with patch.dict(os.environ, {"RENDER": "true"}, clear=True):
            self.assertEqual(Settings.from_env().log_format, "json")
        with patch.dict(os.environ, {"RENDER": "true", "LOG_FORMAT": "Text", "LOG_LEVEL": "debug"}, clear=True):
            settings = Settings.from_env()
        self.assertEqual((settings.log_format, settings.log_level), ("text", "DEBUG"))

    def test_invalid_choice_falls_back_to_default(self):
        with patch.dict(os.environ, {"LOG_LEVEL": "LOUD"}, clear=True), self.assertLogs("app.settings", level="WARNING"):
            self.assertEqual(Settings.from_env().log_level, "INFO")

    def test_reads_values_from_environment(self):
        env = {"DIAGNOSIS_CONCURRENCY": "8", "QUEUE_MAX_WAITING": "0", "QUEUE_WAIT_TIMEOUT": "2.5"}
        with patch.dict(os.environ, env, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.diagnosis_concurrency, 8)
        self.assertEqual(settings.queue_max_waiting, 0)
        self.assertEqual(settings.queue_wait_timeout, 2.5)

    def test_invalid_or_out_of_range_values_fall_back_to_defaults(self):
        env = {"DIAGNOSIS_CONCURRENCY": "zero", "QUEUE_WAIT_TIMEOUT": "0", "NEXON_READ_TIMEOUT": " "}
        with patch.dict(os.environ, env, clear=True), self.assertLogs("app.settings", level="WARNING") as logs:
            settings = Settings.from_env()

        self.assertEqual(settings.diagnosis_concurrency, 5)
        self.assertEqual(settings.queue_wait_timeout, 8.0)
        self.assertEqual(settings.nexon_read_timeout, 6.0)
        self.assertEqual(len(logs.records), 2)


if __name__ == "__main__":
    unittest.main()
