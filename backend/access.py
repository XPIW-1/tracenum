"""Private access links: who may ask live questions and see the private résumé.

A link looks like  https://tracenum.<domain>/#k=<code>  (or the homepage with #k=).
The code sits in the URL fragment, which browsers never send to servers, Referers or
access logs. The page posts it once to /api/access/session, gets an HttpOnly cookie
for the parent domain, and removes the fragment from the address bar.

Codes are random, revocable and expire. Only an HMAC of each code is stored.

Admin CLI (run on the server inside the api container):
  python access.py add --label "某券商-研究所" --days 60
  python access.py list
  python access.py revoke <prefix-of-hash>
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import sys
import time
from contextlib import closing
from pathlib import Path

DB_PATH = Path(os.environ.get("TRACENUM_DATA_DIR", Path(__file__).resolve().parent / "data")) / "access.sqlite3"
COOKIE = "tn_access"
SESSION_DAYS = 30


def _secret(name: str) -> bytes:
    value = os.environ.get(name, "")
    if len(value) < 32:
        raise RuntimeError(f"{name} must be set to a random string of at least 32 characters")
    return value.encode("utf-8")


def code_hash(code: str) -> str:
    return hmac.new(_secret("ACCESS_PEPPER"), code.strip().encode("utf-8"), hashlib.sha256).hexdigest()


def _db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.execute("CREATE TABLE IF NOT EXISTS codes (hash TEXT PRIMARY KEY, label TEXT NOT NULL, created REAL NOT NULL, "
               "expires REAL NOT NULL, revoked INTEGER NOT NULL DEFAULT 0)")
    return db


def lookup(hash_: str) -> dict | None:
    with closing(_db()) as db:
        row = db.execute("SELECT label, expires, revoked FROM codes WHERE hash=?", (hash_,)).fetchone()
    if not row or row[2] or row[1] < time.time():
        return None
    return {"label": row[0], "expires": row[1]}


def sign_session(hash_: str) -> str:
    payload = base64.urlsafe_b64encode(json.dumps({"h": hash_, "exp": int(time.time()) + SESSION_DAYS * 86400})
                                       .encode()).decode().rstrip("=")
    mac = hmac.new(_secret("SESSION_SECRET"), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{mac}"


def read_session(token: str | None) -> tuple[str, dict] | None:
    """Return (code_hash, code_info) for a valid, unexpired session whose code is still active."""
    if not token or "." not in token:
        return None
    payload, mac = token.rsplit(".", 1)
    expected = hmac.new(_secret("SESSION_SECRET"), payload.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(mac, expected):
        return None
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (ValueError, json.JSONDecodeError):
        return None
    if data.get("exp", 0) < time.time():
        return None
    info = lookup(data.get("h", ""))
    return (data["h"], info) if info else None


def cookie_kwargs() -> dict:
    domain = os.environ.get("COOKIE_DOMAIN") or None  # e.g. "example.com" so both hosts share it
    return {"key": COOKIE, "httponly": True, "secure": os.environ.get("COOKIE_INSECURE") != "1",
            "samesite": "lax", "max_age": SESSION_DAYS * 86400, "domain": domain, "path": "/"}


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("--label", required=True)
    add.add_argument("--days", type=int, default=60)
    sub.add_parser("list")
    rev = sub.add_parser("revoke")
    rev.add_argument("prefix")
    args = parser.parse_args()
    with closing(_db()) as db:
        if args.cmd == "add":
            code = secrets.token_urlsafe(18)
            db.execute("INSERT INTO codes(hash, label, created, expires) VALUES (?,?,?,?)",
                       (code_hash(code), args.label, time.time(), time.time() + args.days * 86400))
            db.commit()
            domain = os.environ.get("COOKIE_DOMAIN", "<domain>")
            print(f"label:   {args.label}\nexpires: in {args.days} days\n"
                  f"link:    https://tracenum.{domain}/#k={code}\nhome:    https://{domain}/#k={code}\n"
                  "(the code is shown only once; it is not stored)")
        elif args.cmd == "list":
            for h, label, created, expires, revoked in db.execute("SELECT * FROM codes ORDER BY created"):
                state = "revoked" if revoked else ("expired" if expires < time.time() else "active")
                print(f"{h[:12]}  {state:<8} {time.strftime('%Y-%m-%d', time.localtime(expires))}  {label}")
        elif args.cmd == "revoke":
            matches = db.execute("SELECT hash FROM codes WHERE substr(hash, 1, ?) = ?",
                                 (len(args.prefix), args.prefix)).fetchall()
            if len(args.prefix) < 8 or len(matches) != 1:
                sys.exit(f"prefix must be >= 8 chars and match exactly one code (matched {len(matches)})")
            db.execute("UPDATE codes SET revoked=1 WHERE hash=?", (matches[0][0],))
            db.commit()
            print(f"revoked {matches[0][0][:12]}")


if __name__ == "__main__":
    main()
