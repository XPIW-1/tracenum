"""Offline tests for the live-question guard: billing bounds, quota limits, access sessions.

    python -m unittest discover -s tests -v      (from the repo root; no models, no network)
"""
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))
os.environ.setdefault("ACCESS_PEPPER", "p" * 40)
os.environ.setdefault("SESSION_SECRET", "s" * 40)

import billing  # noqa: E402
import quota  # noqa: E402
import access  # noqa: E402


class BillingTest(unittest.TestCase):
    def test_reservation_bounds_any_split_of_the_token_budget(self):
        for prompt in range(0, billing.TOKEN_BUDGET + 1, 3900):
            cost = billing.call_cost(prompt, billing.TOKEN_BUDGET - prompt)
            self.assertLessEqual(cost, billing.RESERVE_PER_QUESTION)

    def test_unknown_usage_is_none(self):
        self.assertIsNone(billing.call_cost(None, 10))

    def test_cost_rounds_up(self):
        self.assertEqual(billing.call_cost(1, 0), 2)  # 2 元/M -> 2 micro-yuan per token


class QuotaTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.q = quota.Quota(Path(self.dir.name) / "q.sqlite3")

    def tearDown(self):
        self.dir.cleanup()

    def test_concurrency_one(self):
        self.assertTrue(self.q.reserve("a", "code1", "1.1.1.1").ok)
        d = self.q.reserve("b", "code2", "2.2.2.2")
        self.assertEqual((d.ok, d.reason), (False, "busy"))
        self.q.finish("a", 1000)
        self.assertTrue(self.q.reserve("b", "code2", "2.2.2.2").ok)

    def test_per_code_daily_limit(self):
        for i in range(quota.PER_CODE):
            self.assertTrue(self.q.reserve(f"r{i}", "code", f"10.0.0.{i}").ok)
            self.q.finish(f"r{i}", 10)
        d = self.q.reserve("x", "code", "10.0.0.99")
        self.assertEqual(d.reason, "per_code")

    def test_per_ip_daily_limit_across_codes(self):
        for i in range(quota.PER_IP):
            self.assertTrue(self.q.reserve(f"r{i}", f"code{i}", "9.9.9.9").ok)
            self.q.finish(f"r{i}", 10)
        self.assertEqual(self.q.reserve("x", "codeX", "9.9.9.9").reason, "per_ip")

    def test_refund_does_not_count(self):
        for i in range(quota.PER_CODE + 2):
            self.assertTrue(self.q.reserve(f"r{i}", "code", "1.2.3.4").ok)
            self.q.finish(f"r{i}", 0, refund=True)

    def test_site_limit(self):
        for i in range(quota.SITE):
            self.assertTrue(self.q.reserve(f"r{i}", f"c{i}", f"ip{i}").ok)
            self.q.finish(f"r{i}", 10)
        self.assertEqual(self.q.reserve("x", "cx", "ipx").reason, "site")

    def test_budget_counts_unknown_and_inflight(self):
        per = billing.RESERVE_PER_QUESTION
        n = billing.MONTHLY_CAP // per
        old_site, old_code, old_ip = quota.SITE, quota.PER_CODE, quota.PER_IP
        quota.SITE = quota.PER_CODE = quota.PER_IP = 10 ** 6
        try:
            for i in range(n):
                self.assertTrue(self.q.reserve(f"r{i}", f"c{i}", f"ip{i}").ok, i)
                self.q.finish(f"r{i}", None)  # usage unknown: full reservation stays
            self.assertEqual(self.q.reserve("x", "cx", "ipx").reason, "budget")
        finally:
            quota.SITE, quota.PER_CODE, quota.PER_IP = old_site, old_code, old_ip

    def test_long_running_live_request_keeps_its_slot(self):
        self.assertTrue(self.q.reserve("a", "c", "ip").ok)
        with quota.closing(self.q._connect()) as db:
            db.execute("UPDATE ledger SET created = created - ?", (quota.STALE_SECONDS * 3,))
        self.assertEqual(self.q.reserve("b", "c2", "ip2").reason, "busy")

    def test_restart_reclaims_slot_but_keeps_money(self):
        self.assertTrue(self.q.reserve("a", "c", "ip").ok)
        self.q = quota.Quota(self.q.path)  # process restart while "a" was running
        self.assertTrue(self.q.reserve("b", "c2", "ip2").ok)
        with quota.closing(self.q._connect()) as db:
            self.assertEqual(db.execute("SELECT status FROM ledger WHERE request_id='a'").fetchone()[0], "unknown")

    def test_stale_inflight_frees_slot_but_keeps_money(self):
        self.assertTrue(self.q.reserve("a", "c", "ip").ok)
        self.q.live.discard("a")  # e.g. written by another process
        with quota.closing(self.q._connect()) as db:
            db.execute("UPDATE ledger SET created = created - ?", (quota.STALE_SECONDS + 5,))
        self.assertTrue(self.q.reserve("b", "c2", "ip2").ok)
        with quota.closing(self.q._connect()) as db:
            self.assertEqual(db.execute("SELECT status FROM ledger WHERE request_id='a'").fetchone()[0], "unknown")

    def test_parallel_reservations_never_exceed_concurrency(self):
        results = []
        barrier = threading.Barrier(8)

        def go(i):
            barrier.wait()
            results.append(self.q.reserve(f"p{i}", f"c{i}", f"ip{i}").ok)
        threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(sum(results), quota.CONCURRENCY)


class AccessTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        access.DB_PATH = Path(self.dir.name) / "a.sqlite3"
        with access.closing(access._db()) as db:
            db.execute("INSERT INTO codes VALUES (?,?,?,?,0)",
                       (access.code_hash("good-code-123"), "test", time.time(), time.time() + 3600))
            db.execute("INSERT INTO codes VALUES (?,?,?,?,0)",
                       (access.code_hash("old-code-1234"), "old", time.time() - 7200, time.time() - 3600))
            db.commit()

    def tearDown(self):
        self.dir.cleanup()

    def test_valid_session_roundtrip(self):
        token = access.sign_session(access.code_hash("good-code-123"))
        self.assertEqual(access.read_session(token)[1]["label"], "test")

    def test_tampered_or_expired_or_revoked(self):
        token = access.sign_session(access.code_hash("good-code-123"))
        self.assertIsNone(access.read_session(token[:-1] + ("0" if token[-1] != "0" else "1")))
        self.assertIsNone(access.read_session(access.sign_session(access.code_hash("old-code-1234"))))
        self.assertIsNone(access.read_session(None))
        with access.closing(access._db()) as db:
            db.execute("UPDATE codes SET revoked=1")
            db.commit()
        self.assertIsNone(access.read_session(token))

    def test_unknown_code(self):
        self.assertIsNone(access.lookup(access.code_hash("nope-nope-nope")))


if __name__ == "__main__":
    unittest.main()
