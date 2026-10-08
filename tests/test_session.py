import unittest

from sharecovex.session import (FAILURES, IDLE, LIFETIME, LOCKOUT, MAX_LOCKOUT, MAX_SESSIONS, WINDOW,
                                LoginLimiter, SessionStore)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class SessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.sessions = SessionStore(self.clock)

    def test_a_token_names_its_administrator_and_nothing_else_does(self):
        token = self.sessions.create("admin")
        self.assertGreaterEqual(len(token), 40)
        self.assertNotEqual(token, self.sessions.create("admin"))
        self.assertEqual(self.sessions.user(token), "admin")
        for wrong in (None, "", token[:-1], token + "x", 7):
            self.assertIsNone(self.sessions.user(wrong))

    def test_a_session_ends_when_idle_and_use_keeps_it_alive(self):
        token = self.sessions.create("admin")
        for _ in range(3):
            self.clock.now += IDLE - 1
            self.assertEqual(self.sessions.user(token), "admin")
        self.clock.now += IDLE + 1
        self.assertIsNone(self.sessions.user(token))
        self.assertEqual(self.sessions.sessions, {})

    def test_a_session_ends_after_its_lifetime_whatever_its_activity(self):
        token = self.sessions.create("admin")
        elapsed = 0
        while elapsed + IDLE // 2 <= LIFETIME:
            self.clock.now += IDLE // 2
            elapsed += IDLE // 2
            self.assertEqual(self.sessions.user(token), "admin")
        self.clock.now += IDLE // 2
        self.assertIsNone(self.sessions.user(token))

    def test_signing_out_and_a_changed_password_end_sessions(self):
        first, second, other = (self.sessions.create(name) for name in ("admin", "admin", "pedro"))
        self.sessions.end(first)
        self.sessions.end("unknown")
        self.assertIsNone(self.sessions.user(first))
        self.assertEqual(self.sessions.user(second), "admin")
        self.sessions.end_user("admin")
        self.assertIsNone(self.sessions.user(second))
        self.assertEqual(self.sessions.user(other), "pedro")

    def test_the_number_of_sessions_is_bounded(self):
        oldest = self.sessions.create("admin")
        for _ in range(MAX_SESSIONS):
            self.clock.now += 1
            self.sessions.create("admin")
        self.assertEqual(len(self.sessions.sessions), MAX_SESSIONS)
        self.assertIsNone(self.sessions.user(oldest))


class LoginLimiterTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.limiter = LoginLimiter(self.clock)

    def fail(self, address="10.0.0.9", times=FAILURES):
        for _ in range(times):
            self.limiter.failed(address)

    def test_repeated_failures_block_only_that_address(self):
        self.fail(times=FAILURES - 1)
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), 0)
        self.fail(times=1)
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), LOCKOUT)
        self.assertEqual(self.limiter.retry_after("10.0.0.10"), 0)
        self.clock.now += LOCKOUT - 0.5
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), 1)
        self.clock.now += 0.5
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), 0)

    def test_each_new_block_lasts_longer_up_to_a_ceiling(self):
        waits = []
        for _ in range(7):
            self.fail()
            waits.append(self.limiter.retry_after("10.0.0.9"))
            self.clock.now += waits[-1]
        self.assertEqual(waits[:4], [LOCKOUT, LOCKOUT * 2, LOCKOUT * 4, LOCKOUT * 8])
        self.assertEqual(waits[-1], MAX_LOCKOUT)

    def test_old_failures_do_not_count_and_a_good_sign_in_clears_them(self):
        self.fail(times=FAILURES - 1)
        self.clock.now += WINDOW + 1
        self.fail(times=1)
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), 0)
        self.fail(times=FAILURES - 2)
        self.limiter.succeeded("10.0.0.9")
        self.fail(times=FAILURES - 1)
        self.assertEqual(self.limiter.retry_after("10.0.0.9"), 0)


if __name__ == "__main__":
    unittest.main()
