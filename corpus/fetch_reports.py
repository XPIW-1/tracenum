"""Find and download A-share annual reports (Chinese full text) from cninfo, and register them.

    python corpus/fetch_reports.py --list        # query only: what would be downloaded
    python corpus/fetch_reports.py               # download missing PDFs, write corpus/registry.json

Rules (phase-2 plan): Chinese full-text annual report only; never the summary (摘要),
English version, or a cancelled notice. When a company re-published a report (更新后 /
修订版 / 更正后), the newest full text is used and the older one is recorded as
superseded, so the choice is visible. Anything ambiguous is flagged, not guessed.

PDFs go to <project>/corpus_v3/reports/ (not in git); the registry with URLs, dates,
SHA-256 and page counts is committed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent
OUT = ROOT / "corpus_v3" / "reports"
REGISTRY = REPO / "corpus" / "registry.json"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# Batch 1 (approved 2026-11-19). orgId from cninfo's public stock list.
COMPANIES = [
    ("002594", "比亚迪", "gshk0001211"), ("601633", "长城汽车", "gshk0002333"),
    ("300750", "宁德时代", "GD165627"), ("300014", "亿纬锂能", "9900008311"),
    ("301358", "湖南裕能", "9900048816"), ("688005", "容百科技", "9900038937"),
    ("001301", "尚太科技", "9900047767"), ("002709", "天赐材料", "9900022986"),
    ("002812", "恩捷股份", "9900026450"), ("002460", "赣锋锂业", "9900013787"),
    ("002466", "天齐锂业", "9900014189"), ("300124", "汇川技术", "9900012527"),
]
YEARS = (2023, 2024, 2025)
EXCLUDE = re.compile(r"摘要|英文|English|已取消|取消|提示性公告|问询|回复|说明|半年度|季度")
REVISED = re.compile(r"更新后|修订|更正后|修正后")


def query(code: str, org_id: str) -> list[dict]:
    column = "sse" if code.startswith("6") else "szse"
    rows, page = [], 1
    while True:
        form = urllib.parse.urlencode({
            "stock": f"{code},{org_id}", "tabName": "fulltext", "pageSize": 30, "pageNum": page,
            "column": column, "category": "category_ndbg_szsh", "plate": "", "seDate": "2024-01-01~2026-11-30",
            "searchkey": "", "secid": "", "sortName": "", "sortType": "", "isHLtitle": "true"}).encode()
        req = urllib.request.Request("http://www.cninfo.com.cn/new/hisAnnouncement/query", data=form,
                                     headers={**UA, "Content-Type": "application/x-www-form-urlencoded"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        rows += data.get("announcements") or []
        if not data.get("hasMore"):
            return rows
        page += 1
        time.sleep(1)


def pick(code: str, name: str, org_id: str) -> list[dict]:
    found = []
    for a in query(code, org_id):
        title = re.sub(r"<[^>]+>", "", a["announcementTitle"])
        m = re.search(r"(20\d\d)\s*年(?:年度)?报告|(20\d\d)\s*年年度报告", title)
        if not m or EXCLUDE.search(title):
            continue
        found.append({"year": int(m.group(1) or m.group(2)), "title": title, "id": a["announcementId"],
                      "date": time.strftime("%Y-%m-%d", time.localtime(a["announcementTime"] / 1000)),
                      "url": "http://static.cninfo.com.cn/" + a["adjunctUrl"]})
    out = []
    for year in YEARS:
        cands = sorted((f for f in found if f["year"] == year), key=lambda f: f["date"])
        if not cands:
            out.append({"code": code, "company": name, "report_year": year, "status": "MISSING"})
            continue
        chosen = cands[-1]
        out.append({"code": code, "company": name, "report_year": year, "title": chosen["title"],
                    "announcement_id": chosen["id"], "published": chosen["date"], "url": chosen["url"],
                    "revised": bool(REVISED.search(chosen["title"])),
                    "superseded": [{"title": c["title"], "published": c["date"], "url": c["url"]} for c in cands[:-1]],
                    "status": "REVIEW" if len(cands) > 1 else "OK"})
    return out


def pdf_pages(path: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(path)).pages)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args()
    entries = []
    for code, name, org in COMPANIES:
        entries += pick(code, name, org)
        time.sleep(1)
    for e in entries:
        flag = e["status"]
        print(f"{flag:<7} {e['code']} {e['company']:<6} {e['report_year']} {e.get('published', '')} "
              f"{e.get('title', '')}{'  [+' + str(len(e['superseded'])) + ' older]' if e.get('superseded') else ''}")
    if args.list:
        return
    OUT.mkdir(parents=True, exist_ok=True)
    for e in entries:
        if e["status"] == "MISSING":
            continue
        path = OUT / f"{e['code']}_{e['report_year']}.pdf"
        if not path.is_file():
            req = urllib.request.Request(e["url"], headers=UA)
            with urllib.request.urlopen(req, timeout=120) as r:
                data = r.read()
            if not data.startswith(b"%PDF"):
                sys.exit(f"not a PDF: {e['url']}")
            path.write_bytes(data)
            time.sleep(1.5)
        e.update(local_path=str(path.relative_to(ROOT)).replace("\\", "/"), bytes=path.stat().st_size,
                 sha256=hashlib.sha256(path.read_bytes()).hexdigest(), pages=pdf_pages(path))
    REGISTRY.write_text(json.dumps({"source": "cninfo.com.cn (A-share Chinese full-text annual reports)",
                                    "reports": entries}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    ok = sum(e["status"] != "MISSING" for e in entries)
    print(f"registered {ok}/{len(entries)}; total {sum(e.get('bytes', 0) for e in entries) / 1e6:.0f} MB")


if __name__ == "__main__":
    main()
