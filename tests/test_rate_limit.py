import unittest

from app.rate_limit import SlidingWindowRateLimiter, client_ip


class RateLimitTests(unittest.TestCase):
    def test_limit_then_retry_after_then_recovery(self):
        now = [1000.0]
        limiter = SlidingWindowRateLimiter([(2, 60.0)], clock=lambda: now[0])

        self.assertIsNone(limiter.check("ip"))
        self.assertIsNone(limiter.check("ip"))
        self.assertEqual(limiter.check("ip"), 60)
        self.assertIsNone(limiter.check("other-ip"))

        now[0] += 60.5
        # 거부된 시도는 세지 않으므로 창이 지나면 바로 풀립니다.
        self.assertIsNone(limiter.check("ip"))

    def test_client_ip_prefers_trusted_headers_then_rightmost_forwarded_entry(self):
        forwarded = {"x-forwarded-for": "198.51.100.1, 203.0.113.5"}

        self.assertEqual(client_ip({"cf-connecting-ip": "203.0.113.5", **forwarded}, "10.0.0.1"), ("203.0.113.5", "cf-connecting-ip"))
        self.assertEqual(client_ip(forwarded, "10.0.0.1"), ("203.0.113.5", "x-forwarded-for[last]"))
        self.assertEqual(client_ip(forwarded, "10.0.0.1", "first"), ("198.51.100.1", "x-forwarded-for[first]"))
        self.assertEqual(client_ip({}, "10.0.0.1"), ("10.0.0.1", "socket"))


if __name__ == "__main__":
    unittest.main()
