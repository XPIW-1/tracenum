"""Daily limits, one-at-a-time execution and the monthly money cap, in one SQLite file.

Every live question goes through reserve() -> (calls) -> finish(). reserve() runs in a
single IMMEDIATE transaction, so two requests can never both pass a limit.

Limits (env, defaults agreed with the owner):
  LIVE_PER_CODE_DAILY=3  LIVE_PER_IP_DAILY=3  LIVE_SITE_DAILY=30  LIVE_CONCURRENCY=1
  LIVE_MONTHLY_CAP_YUAN=45 (billing.MONTHLY_CAP)
Days and months follow Beijing time.

Money: reserve() books billing.RESERVE_PER_QUESTION. finish() replaces it with the
settled cost when usage for every call is known; otherwise the full reservation stays
on the ledger as "unknown" (it still counts against the cap until reconciled by hand).
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import billing

BEIJING = timezone(timedelta(hours=8))
STALE_SECONDS = 20 * 60  # an in-flight slot older than this is from a crashed process


def _env_int(name, default):
    return int(os.environ.get(name, default))


PER_CODE = _env_int("LIVE_PER_CODE_DAILY", 3)
PER_IP = _env_int("LIVE_PER_IP_DAILY", 3)
SITE = _env_int("LIVE_SITE_DAILY", 30)
CONCURRENCY = _env_int("LIVE_CONCURRENCY", 1)

SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
  request_id TEXT PRIMARY KEY, created REAL NOT NULL, day TEXT NOT NULL, month TEXT NOT NULL,
  code_hash TEXT NOT NULL, ip TEXT NOT NULL, status TEXT NOT NULL,      -- inflight | settled | unknown | rejected
  reserved INTEGER NOT NULL, cost INTEGER, finished REAL, note TEXT
);
CREATE INDEX IF NOT EXISTS ledger_day ON ledger(day);
CREATE INDEX IF NOT EXISTS ledger_month ON ledger(month);
"""


@dataclass
class Decision:
    ok: bool
    reason: str = ""          # machine code shown to the client: per_code, per_ip, site, busy, budget
    message: str = ""         # Chinese text for the page


MESSAGES = {
    "per_code": "这个专属链接今天的 3 次实时提问已用完，明天会恢复。下面是预先运行的真实结果。",
    "per_ip": "当前网络今天的实时提问次数已用完，明天会恢复。",
    "site": "今天全站的实时提问额度已用完，明天会恢复。",
    "busy": "另一位访客的问题正在运行（实时问答一次只跑一个），请一两分钟后再试。",
    "budget": "本月实时问答的费用额度已用完，实时提问暂停；精选题与回放不受影响。",
}


class Quota:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        with closing(self._connect()) as db:
            db.executescript(SCHEMA)

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _now():
        now = datetime.now(BEIJING)
        return time.time(), now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def _spent(self, db, month):
        """Money committed this month: settled costs + every open or unknown reservation."""
        row = db.execute("SELECT COALESCE(SUM(CASE WHEN status='settled' THEN cost ELSE reserved END), 0) "
                         "FROM ledger WHERE month=? AND status IN ('inflight','settled','unknown')", (month,)).fetchone()
        return row[0]

    def reserve(self, request_id: str, code_hash: str, ip: str) -> Decision:
        stamp, day, month = self._now()
        with self.lock, closing(self._connect()) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # A crashed worker leaves "inflight"; free the slot but keep the money as unknown.
                db.execute("UPDATE ledger SET status='unknown', note='stale inflight' "
                           "WHERE status='inflight' AND created < ?", (stamp - STALE_SECONDS,))
                count = lambda where, *a: db.execute(
                    f"SELECT COUNT(*) FROM ledger WHERE day=? AND status!='rejected' AND {where}", (day, *a)).fetchone()[0]
                reason = None
                if db.execute("SELECT COUNT(*) FROM ledger WHERE status='inflight'").fetchone()[0] >= CONCURRENCY:
                    reason = "busy"
                elif count("code_hash=?", code_hash) >= PER_CODE:
                    reason = "per_code"
                elif count("ip=?", ip) >= PER_IP:
                    reason = "per_ip"
                elif count("1=1") >= SITE:
                    reason = "site"
                elif self._spent(db, month) + billing.RESERVE_PER_QUESTION > billing.MONTHLY_CAP:
                    reason = "budget"
                if reason:
                    db.execute("ROLLBACK")
                    return Decision(False, reason, MESSAGES[reason])
                db.execute("INSERT INTO ledger(request_id, created, day, month, code_hash, ip, status, reserved) "
                           "VALUES (?,?,?,?,?,?,'inflight',?)",
                           (request_id, stamp, day, month, code_hash, ip, billing.RESERVE_PER_QUESTION))
                db.execute("COMMIT")
                return Decision(True)
            except BaseException:
                db.execute("ROLLBACK")
                raise

    def finish(self, request_id: str, cost: int | None, note: str = "", *, refund: bool = False):
        """cost=None keeps the reservation as 'unknown'. refund=True: nothing was spent (no model call)
        and the attempt does not count against daily limits."""
        stamp, _, _ = self._now()
        with self.lock, closing(self._connect()) as db:
            if refund:
                db.execute("UPDATE ledger SET status='rejected', cost=0, finished=?, note=? WHERE request_id=?",
                           (stamp, note or "no model call", request_id))
            elif cost is None:
                db.execute("UPDATE ledger SET status='unknown', finished=?, note=? WHERE request_id=?",
                           (stamp, note or "usage unknown", request_id))
            else:
                db.execute("UPDATE ledger SET status='settled', cost=?, finished=?, note=? WHERE request_id=?",
                           (cost, stamp, note, request_id))

    def snapshot(self, code_hash: str | None, ip: str) -> dict:
        _, day, month = self._now()
        with self.lock, closing(self._connect()) as db:
            count = lambda where, *a: db.execute(
                f"SELECT COUNT(*) FROM ledger WHERE day=? AND status!='rejected' AND {where}", (day, *a)).fetchone()[0]
            spent = self._spent(db, month)
            return {
                "code_remaining": max(PER_CODE - count("code_hash=?", code_hash), 0) if code_hash else 0,
                "ip_remaining": max(PER_IP - count("ip=?", ip), 0),
                "site_remaining": max(SITE - count("1=1"), 0),
                "budget_ok": spent + billing.RESERVE_PER_QUESTION <= billing.MONTHLY_CAP,
                "busy": db.execute("SELECT COUNT(*) FROM ledger WHERE status='inflight'").fetchone()[0] >= CONCURRENCY,
            }
