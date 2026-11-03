import os
import unittest
from unittest.mock import patch

from app.settings import Settings


class SettingsTests(unittest.TestCase):
    def test_defaults_without_environment(self):
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings.from_env()

        self.assertEqual((settings.diagnosis_concurrency, settings.queue_max_waiting, settings.queue_wait_timeout), (5, 40, 8.0))
        self.assertEqual((settings.rate_limit_per_10min, settings.rate_limit_per_hour), (150, 600))
        self.assertEqual((settings.log_format, settings.log_level), ("text", "INFO"))

    def test_environment_overrides_and_invalid_values_fall_back(self):
        env = {
            "RENDER": "true",
            "DIAGNOSIS_CONCURRENCY": "8",
            "RATE_LIMIT_PER_HOUR": "0",
            "QUEUE_WAIT_TIMEOUT": "zero",
            "LOG_LEVEL": "debug",
        }
        with patch.dict(os.environ, env, clear=True), self.assertLogs("app.settings", level="WARNING"):
            settings = Settings.from_env()

        self.assertEqual((settings.diagnosis_concurrency, settings.rate_limit_per_hour), (8, 0))
        self.assertEqual(settings.queue_wait_timeout, 8.0)
        self.assertEqual((settings.log_format, settings.log_level), ("json", "DEBUG"))


if __name__ == "__main__":
    unittest.main()
