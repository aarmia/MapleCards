import unittest

from app.rate_limit import RateLimitedError, SlidingWindowRateLimiter, client_ip, hash_ip


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SlidingWindowRateLimiterTests(unittest.TestCase):
    def test_allows_up_to_the_limit_then_returns_retry_after(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter([(3, 60.0)], clock=clock)

        for _ in range(3):
            self.assertIsNone(limiter.check("ip-a"))
        self.assertEqual(limiter.check("ip-a"), 60)

        clock.now += 30
        self.assertEqual(limiter.check("ip-a"), 30)
        clock.now += 30.5
        self.assertIsNone(limiter.check("ip-a"))

    def test_rejected_attempts_are_not_counted(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter([(1, 10.0)], clock=clock)

        self.assertIsNone(limiter.check("ip-a"))
        for _ in range(5):
            self.assertEqual(limiter.check("ip-a"), 10)
        clock.now += 10.1
        self.assertIsNone(limiter.check("ip-a"))

    def test_keys_are_independent(self):
        limiter = SlidingWindowRateLimiter([(1, 60.0)], clock=FakeClock())

        self.assertIsNone(limiter.check("ip-a"))
        self.assertIsNone(limiter.check("ip-b"))
        self.assertEqual(limiter.check("ip-a"), 60)

    def test_longest_window_wins_when_both_limits_apply(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter([(2, 10.0), (3, 100.0)], clock=clock)

        self.assertIsNone(limiter.check("ip"))
        self.assertIsNone(limiter.check("ip"))
        self.assertEqual(limiter.check("ip"), 10)
        clock.now += 10.1
        self.assertIsNone(limiter.check("ip"))
        # 10초 창은 비었지만 100초 창이 3건으로 차서 막힙니다.
        self.assertEqual(limiter.check("ip"), 90)

    def test_zero_or_missing_limits_disable_the_limiter(self):
        limiter = SlidingWindowRateLimiter([(0, 60.0), (0, 3600.0)])
        self.assertFalse(limiter.enabled)
        for _ in range(1000):
            self.assertIsNone(limiter.check("ip"))

    def test_cleanup_drops_idle_keys(self):
        clock = FakeClock()
        limiter = SlidingWindowRateLimiter([(100, 10.0)], clock=clock)
        limiter.CLEANUP_EVERY = 5

        for index in range(4):
            limiter.check(f"ip-{index}")
        clock.now += 11
        limiter.check("ip-new")

        self.assertEqual(limiter.tracked_keys(), 1)


class ClientIpTests(unittest.TestCase):
    def test_prefers_cloudflare_headers(self):
        headers = {"cf-connecting-ip": "203.0.113.5", "x-forwarded-for": "198.51.100.1, 203.0.113.5"}
        self.assertEqual(client_ip(headers, "10.0.0.1"), ("203.0.113.5", "cf-connecting-ip"))

    def test_uses_rightmost_forwarded_entry_by_default(self):
        headers = {"x-forwarded-for": "198.51.100.1, 203.0.113.5"}
        self.assertEqual(client_ip(headers, "10.0.0.1"), ("203.0.113.5", "x-forwarded-for[last]"))
        self.assertEqual(client_ip(headers, "10.0.0.1", xff_position="first"), ("198.51.100.1", "x-forwarded-for[first]"))

    def test_falls_back_to_socket_address(self):
        self.assertEqual(client_ip({}, "10.0.0.1"), ("10.0.0.1", "socket"))
        self.assertEqual(client_ip({"x-forwarded-for": " , "}, None), ("unknown", "socket"))

    def test_hash_is_stable_and_not_the_ip(self):
        self.assertEqual(hash_ip("203.0.113.5"), hash_ip("203.0.113.5"))
        self.assertNotIn("203", hash_ip("203.0.113.5"))
        self.assertEqual(len(hash_ip("203.0.113.5")), 12)


class RateLimitedErrorTests(unittest.TestCase):
    def test_payload_carries_code_and_retry_after(self):
        error = RateLimitedError(42)
        self.assertEqual(error.status_code, 429)
        self.assertEqual(error.to_payload(), {"error": error.message, "code": "RATE_LIMITED", "retry_after": 42})
        self.assertIn("42초", error.message)


if __name__ == "__main__":
    unittest.main()
