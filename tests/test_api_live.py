"""End-to-end API checks with a stub pipeline (no models, no network, no money).

    python -m unittest tests.test_api_live -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

TMP = tempfile.mkdtemp()
os.environ.update({"LIVE_ENABLED": "1", "TRACENUM_DATA_DIR": TMP, "ACCESS_PEPPER": "p" * 40,
                   "SESSION_SECRET": "s" * 40, "COOKIE_INSECURE": "1"})
os.environ.pop("TRACENUM_DEV", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from fastapi.testclient import TestClient  # noqa: E402

import access  # noqa: E402
import pipeline as P  # noqa: E402
import server  # noqa: E402


def fake_run(question, strategy, emit):
    emit({"type": "node", "node": "retrieve", "round": 1})
    emit({"type": "usage", "prompt_tokens": 1000, "completion_tokens": 500, "status": "completed",
          "system_fingerprint": "fp-test"})
    return {"fake": True}


class ApiLiveTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        P.PIPELINE.ready = True
        P.PIPELINE.run = fake_run
        P.summarize_final = lambda final, company: {"answer": "ok", "total_tokens": 1500}
        server.Tape.save = lambda self: None
        cls.code = "test-code-abcdef"
        with access.closing(access._db()) as db:
            db.execute("INSERT INTO codes VALUES (?,?,?,?,0)",
                       (access.code_hash(cls.code), "unit", time.time(), time.time() + 3600))
            db.commit()

    def client(self):
        return TestClient(server.app)

    def ask(self, c, text="比亚迪2025年营业收入是多少？", strategies=("S1", "S2", "S3")):
        return c.post("/api/ask", json={"company": "BYD", "question": text, "strategies": list(strategies)})

    def test_no_session_is_refused_and_dev_routes_hidden(self):
        c = self.client()
        r = self.ask(c)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["reason"], "no_access")
        for path in ("/api/recordings", "/api/questions", "/api/corpora", "/api/pdf/BYD", "/api/replay/x"):
            self.assertEqual(c.get(path).status_code, 404, path)
        self.assertEqual(c.post("/api/recordings/x/feature").status_code, 404)
        self.assertEqual(c.get("/private/resume.pdf").status_code, 403)

    def test_bad_code_rejected(self):
        self.assertEqual(self.client().post("/api/access/session", json={"code": "wrong-code-xyz"}).status_code, 403)

    def test_live_flow_forces_s3_and_settles_cost(self):
        c = self.client()
        r = c.post("/api/access/session", json={"code": self.code})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(c.get("/api/session").json()["live"])
        r = self.ask(c)
        self.assertEqual(r.status_code, 200)
        events = [json.loads(l[5:]) for l in r.text.splitlines() if l.startswith("data:")]
        self.assertEqual(events[0]["strategies"], ["S3"])
        self.assertTrue(all("prompt_tokens" not in e for e in events))  # usage details stay server-side
        self.assertEqual(events[-1]["type"], "done")
        time.sleep(0.2)
        with server.QUOTA.lock, access.closing(server.QUOTA._connect()) as db:
            status, cost = db.execute("SELECT status, cost FROM ledger ORDER BY created DESC").fetchone()
        self.assertEqual(status, "settled")
        self.assertEqual(cost, 1000 * 2 + 500 * 8)  # micro-yuan at 2/8 元 per M
        self.assertEqual(c.get("/api/status").json()["fingerprint"]["last_seen"], "fp-test")

    def test_boundary_year_is_free(self):
        c = self.client()
        c.post("/api/access/session", json={"code": self.code})
        before = c.get("/api/session").json()["quota"]["code_remaining"]
        r = self.ask(c, "比亚迪2026年营业收入是多少？")
        self.assertEqual(r.status_code, 200)
        self.assertIn("语料边界外", r.text)
        self.assertEqual(c.get("/api/session").json()["quota"]["code_remaining"], before)

    def test_question_length_limit(self):
        c = self.client()
        c.post("/api/access/session", json={"code": self.code})
        self.assertEqual(self.ask(c, "营" * 201).status_code, 400)


if __name__ == "__main__":
    unittest.main()
