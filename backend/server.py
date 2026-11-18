"""TraceNum API: live, streamed answers over the frozen formal-v1 pipeline.

Run:  python -m uvicorn server:app --port 8765   (from tracenum/backend)
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import traceback
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

import access
import billing
import pipeline as P
from quota import Quota

# DEV=1 on the laptop: the old local workbench (strategy lab live runs for all three
# strategies, recordings admin, server-side export). Production never sets it.
DEV = os.environ.get("TRACENUM_DEV") == "1"
# Live questions need both an explicit switch and a valid private-link session.
LIVE_ENABLED = os.environ.get("LIVE_ENABLED") == "1"
DATA_DIR = Path(os.environ.get("TRACENUM_DATA_DIR", Path(__file__).resolve().parent / "data"))
QUOTA = Quota(DATA_DIR / "quota.sqlite3")
MAX_QUESTION_CHARS = 200

app = FastAPI(title="TraceNum API", docs_url="/api/docs" if DEV else None, redoc_url=None, openapi_url=None if not DEV else "/openapi.json")
_origins = [o for o in os.environ.get("ALLOWED_ORIGINS", "").split(",") if o]
if DEV:
    _origins += ["http://127.0.0.1:5180", "http://localhost:5180"]
if _origins:  # production is same-origin behind Caddy; CORS only for listed origins
    app.add_middleware(CORSMiddleware, allow_origins=_origins, allow_credentials=True,
                       allow_methods=["GET", "POST", "DELETE"], allow_headers=["Content-Type"])


def dev_only():
    if not DEV:
        raise HTTPException(404)


def client_ip(request: Request) -> str:
    # uvicorn --proxy-headers already resolved X-Forwarded-For from Caddy.
    return request.client.host if request.client else "unknown"


def current_access(request: Request):
    return access.read_session(request.cookies.get(access.COOKIE))


class AskBody(BaseModel):
    company: str
    question: str = Field(max_length=MAX_QUESTION_CHARS * 2)
    strategies: list[str] = ["S3"]


@app.get("/api/health")
def health():
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Private access: session from a private link, résumé and contact details
# --------------------------------------------------------------------------- #
_attempts: dict[str, deque] = defaultdict(deque)


class CodeBody(BaseModel):
    code: str = Field(min_length=8, max_length=64)


@app.post("/api/access/session")
def open_session(body: CodeBody, request: Request):
    ip, now = client_ip(request), time.time()
    window = _attempts[ip]
    while window and window[0] < now - 3600:
        window.popleft()
    if len(window) >= 20:
        raise HTTPException(429, "尝试过于频繁，请稍后再试")
    window.append(now)
    hash_ = access.code_hash(body.code)
    info = access.lookup(hash_)
    if not info:
        raise HTTPException(403, "链接无效或已过期")
    response = JSONResponse({"ok": True, "label": info["label"]})
    response.set_cookie(value=access.sign_session(hash_), **access.cookie_kwargs())
    return response


@app.delete("/api/access/session")
def close_session():
    response = JSONResponse({"ok": True})
    kwargs = access.cookie_kwargs()
    response.delete_cookie(kwargs["key"], path="/", domain=kwargs["domain"])
    return response


@app.get("/api/session")
def session(request: Request):
    granted = current_access(request)
    snap = QUOTA.snapshot(granted[0] if granted else None, client_ip(request))
    reason = None
    if not LIVE_ENABLED:
        reason = "disabled"
    elif not granted:
        reason = "no_access"
    elif P.PIPELINE.error:
        reason = "unavailable"
    elif not snap["budget_ok"]:
        reason = "budget"
    return {"access": bool(granted), "live": reason is None, "reason": reason,
            "ready": P.PIPELINE.ready, "quota": {k: snap[k] for k in ("code_remaining", "site_remaining", "busy")}}


PRIVATE_DIR = DATA_DIR / "private"


@app.get("/private/resume.pdf")
def private_resume(request: Request):
    if not current_access(request):
        raise HTTPException(403)
    path = PRIVATE_DIR / "resume.pdf"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="application/pdf", headers={"Cache-Control": "no-store"},
                        filename="resume.pdf")


@app.get("/api/private/contact")
def private_contact(request: Request):
    if not current_access(request):
        raise HTTPException(403)
    path = PRIVATE_DIR / "contact.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    return JSONResponse(data, headers={"Cache-Control": "no-store"})


@app.on_event("startup")
def warm():
    # Load models in the background so the first question does not pay for it.
    # With live questions switched off nothing is loaded (saves ~1 GB on the server).
    if not (LIVE_ENABLED or DEV):
        return

    def load():
        try:
            P.PIPELINE.load()
        except Exception as exc:  # surfaced through /api/status
            P.PIPELINE.error = f"{type(exc).__name__}: {exc}"
    threading.Thread(target=load, daemon=True).start()


PAPER_FINGERPRINT = "e72d3c7f663d3e1383b00b3884cb4501"  # all 61 formal-v1 calls, 2026-10-26
_last_fingerprint = {"value": None, "at": None}


@app.get("/api/status")
def status():
    return {"ready": P.PIPELINE.ready, "error": "检索模型加载失败" if P.PIPELINE.error and not DEV else P.PIPELINE.error,
            "model": P.FREEZE["model"]["alias"], "budget": P.FREEZE["budget"]["token_budget_per_run"],
            "reranker": getattr(P.PIPELINE, "reranker_label", None),
            "fingerprint": {"paper": PAPER_FINGERPRINT, "last_seen": _last_fingerprint["value"],
                            "last_seen_at": _last_fingerprint["at"],
                            "matches_paper": _last_fingerprint["value"] == PAPER_FINGERPRINT
                            if _last_fingerprint["value"] else None}}


@app.get("/api/corpora", dependencies=[Depends(dev_only)])
def corpora():
    return [{"code": code, **{k: v for k, v in info.items() if k != "pdf_path"}} for code, info in P.CORPORA.items()]


@app.get("/api/questions", dependencies=[Depends(dev_only)])
def questions():
    out = []
    for q in P.QUESTIONS:
        out.append({"question_id": q["question_id"], "company": P.COMPANY_TO_CODE[q["company"]],
                    "question": q["question"], "question_type": q["question_type"],
                    "metric": q.get("metric"), "boundary": q.get("boundary_question", False),
                    "results": P.PAPER.get(q["question_id"], {})})
    return out


@app.get("/api/pdf/{code}", dependencies=[Depends(dev_only)])
def pdf(code: str):
    if code not in P.CORPORA:
        raise HTTPException(404)
    return FileResponse(P.CORPORA[code]["pdf_path"], media_type="application/pdf")


class ExportBody(BaseModel):
    items: list[dict]


@app.post("/api/export", dependencies=[Depends(dev_only)])
def export(body: ExportBody):
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from fastapi.responses import Response

    columns = [("指标", "metric", 22), ("数值", "value", 18), ("单位", "unit", 8), ("口径", "scope", 12),
               ("公司", "company", 10), ("年度", "year", 8), ("页码", "page", 8), ("核验状态", "status", 14),
               ("原文片段", "snippet", 60), ("提问", "question", 48), ("查询时间", "time", 20)]
    wb = Workbook()
    ws = wb.active
    ws.title = "底稿"
    ws.append([c[0] for c in columns])
    for cell in ws[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="172033")
    def as_number(text):
        raw = str(text).replace(",", "").replace("，", "").strip()
        negative = raw.startswith("-") or (raw.startswith("(") and raw.endswith(")"))
        raw = raw.strip("-()（）%")
        try:
            value = float(raw)
        except ValueError:
            return text
        return -value if negative else value

    for item in body.items:
        ws.append([as_number(item.get(key, "")) if key == "value" else item.get(key, "") for _, key, _ in columns])
        cell = ws.cell(ws.max_row, 2)
        if isinstance(cell.value, float):
            cell.number_format = "#,##0.00" if "." in str(item.get("value", "")) else "#,##0"
    for index, (_, _, width) in enumerate(columns, start=1):
        ws.column_dimensions[ws.cell(1, index).column_letter].width = width
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    ws.freeze_panes = "A2"
    note = wb.create_sheet("说明")
    note.append(["本底稿由溯数 TraceNum 导出。页码为年报 PDF 物理页；核验状态“原文一致”表示该数值在所引页的原文中逐字出现，“计算得出”表示由原文数值计算而来，需复核计算过程。"])
    buffer = BytesIO()
    wb.save(buffer)
    return Response(buffer.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": "attachment; filename=tracenum.xlsx"})


def sse(event: dict) -> bytes:
    return f"data: {json.dumps(event, ensure_ascii=False, default=str)}\n\n".encode("utf-8")


def refuse(status: int, reason: str, message: str):
    return JSONResponse({"reason": reason, "message": message}, status_code=status)


@app.post("/api/ask")
async def ask(body: AskBody, request: Request):
    if body.company not in P.CORPORA:
        raise HTTPException(400, "unknown company")
    text = " ".join(body.question.split())
    if not text or len(text) > MAX_QUESTION_CHARS:
        return refuse(400, "length", f"问题请控制在 {MAX_QUESTION_CHARS} 字以内")
    if DEV:
        granted, strategies = ("dev", {}), [s for s in body.strategies if s in P.STRATEGIES] or ["S3"]
    else:
        if not LIVE_ENABLED:
            return refuse(403, "disabled", "实时提问暂未开放，下面是预先运行的真实结果。")
        granted = current_access(request)
        if not granted:
            return refuse(403, "no_access", "自由提问仅对专属链接开放，下面是预先运行的真实结果。")
        strategies = ["S3"]  # live runs are S3 only; strategy comparison uses recordings
    request_id = uuid.uuid4().hex
    if not DEV:
        decision = QUOTA.reserve(request_id, granted[0], client_ip(request))
        if not decision.ok:
            return refuse(429, decision.reason, decision.message)
    ledger = {"cost": 0, "unknown": False, "calls": 0}

    def settle():
        if DEV:
            return
        if ledger["calls"] == 0 and not ledger["unknown"]:
            QUOTA.finish(request_id, 0, refund=True)
        else:
            QUOTA.finish(request_id, None if ledger["unknown"] else ledger["cost"],
                         f"{ledger['calls']} calls")

    # Everything that can fail or end the request happens before the response is
    # returned, so a reserved slot is always settled (by refund, or by the worker).
    try:
        question, paper = P.build_question(body.company, text)
        later = P.out_of_corpus_year(body.company, text, question)
    except Exception:
        settle()
        raise
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def push(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    tape = Tape(body.company, question, strategies)
    meta = {"type": "meta", "question": question["question"], "question_id": question["question_id"],
            "paper": paper, "strategies": strategies, "budget": P.FREEZE["budget"]["token_budget_per_run"],
            "company": body.company}

    if later:
        settle()  # no model call: refund, does not count against limits
        info = P.CORPORA[body.company]

        async def boundary_stream():
            yield sse(meta)
            for s in strategies:
                yield sse({"type": "final", "strategy": s, "boundary": True, "result": {
                    "answer": "", "refused": True, "boundary_year": later, "corpus_year": info["year"],
                    "cited_pages": [], "numbers": [], "evidence": [], "suggested_pages": [],
                    "missing": [f"问题涉及 {later} 年，当前语料只覆盖 {info['company']} {info['year']} 年年报"],
                    "stop_reason": "语料边界外（未调用模型）", "total_tokens": 0, "model_calls": 0, "rounds": 0,
                    "budget": P.FREEZE["budget"]["token_budget_per_run"]}})
            yield sse({"type": "done"})

        return StreamingResponse(boundary_stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    running = {"n": len(strategies)}
    guard = threading.Lock()

    def on_usage(event):
        with guard:
            ledger["calls"] += 1
            cost = billing.call_cost(event.get("prompt_tokens"), event.get("completion_tokens"))
            if cost is None:
                ledger["unknown"] = True
            else:
                ledger["cost"] += cost
        if event.get("system_fingerprint"):
            _last_fingerprint.update(value=event["system_fingerprint"],
                                     at=datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds"))

    def worker(strategy):
        def emit(e):
            if e.get("type") == "usage":
                on_usage(e)
                e = {"type": "usage", "system_fingerprint": e.get("system_fingerprint")}
            push({**e, "strategy": strategy})
        started = time.perf_counter()
        emit({"type": "start"})
        try:
            final = P.PIPELINE.run(question, strategy, emit)
            result = P.summarize_final(final, body.company)
            result["elapsed"] = round(time.perf_counter() - started, 1)
            emit({"type": "final", "result": result})
        except Exception as exc:
            with guard:
                ledger["unknown"] = ledger["unknown"] or ledger["calls"] > 0 or not P.PIPELINE.ready
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}" if DEV else "这次运行失败了，未产生答案。",
                  **({"trace": traceback.format_exc()[-800:]} if DEV else {})})
            print(f"[ask {request_id}] {type(exc).__name__}: {exc}", flush=True)
        finally:
            # Settle in the worker, not in the stream: the browser may disconnect while
            # the run keeps going (and keeps spending).
            with guard:
                running["n"] -= 1
                last = running["n"] == 0
            if last:
                settle()
            push({"type": "_worker_done"})

    loading = not P.PIPELINE.ready
    for s in strategies:
        threading.Thread(target=worker, args=(s,), daemon=True).start()

    async def stream():
        yield sse(tape.add(meta))
        if loading:
            yield sse({"type": "loading", "message": "正在加载检索模型…"})
        remaining = len(strategies)
        while remaining:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
            except asyncio.TimeoutError:
                yield b": keep-alive\n\n"
                continue
            if event["type"] == "_worker_done":
                remaining -= 1
                continue
            yield sse(tape.add(event))
        yield sse(tape.add({"type": "done"}))
        tape.save()

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# --------------------------------------------------------------------------- #
# Run recordings: every completed live run is kept so it can be replayed
# honestly (labelled with when it ran) when a live demo cannot wait or must
# not depend on one more nondeterministic run.
# --------------------------------------------------------------------------- #
RECORDINGS = Path(__file__).resolve().parent / "recordings"
FEATURED = RECORDINGS / "featured.json"


class Tape:
    def __init__(self, company, question, strategies):
        self.company, self.question, self.strategies = company, question, strategies
        self.started = time.monotonic()
        self.recorded_at = datetime.now(timezone(timedelta(hours=8)))
        self.events = []

    def add(self, event):
        self.events.append({"t": round(time.monotonic() - self.started, 3), "event": event})
        return event

    def save(self):
        finals = {e["event"]["strategy"]: e["event"]["result"] for e in self.events if e["event"]["type"] == "final"}
        errors = [e for e in self.events if e["event"]["type"] == "error"]
        if errors or len(finals) != len(self.strategies) or any(r.get("boundary_year") for r in finals.values()):
            return None
        RECORDINGS.mkdir(parents=True, exist_ok=True)
        rid = f"{self.recorded_at:%Y%m%dT%H%M%S}_{self.question['question_id']}_{''.join(self.strategies)}"
        record = {
            "id": rid, "recorded_at": self.recorded_at.isoformat(timespec="seconds"),
            "company": self.company, "question_id": self.question["question_id"],
            "question": self.question["question"], "strategies": self.strategies,
            "duration": self.events[-1]["t"] if self.events else 0,
            "summary": {s: {"rounds": r.get("rounds"), "tokens": r.get("total_tokens"), "refused": r.get("refused"),
                            "stop_reason": r.get("stop_reason"), "elapsed": r.get("elapsed"),
                            "answer": (r.get("answer") or "")[:240]} for s, r in finals.items()},
            "events": self.events,
        }
        (RECORDINGS / f"{rid}.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        return rid


def _featured():
    try:
        return json.loads(FEATURED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _recording(rid):
    path = RECORDINGS / f"{rid}.json"
    if "/" in rid or "\\" in rid or not path.is_file():
        raise HTTPException(404, "recording not found")
    return json.loads(path.read_text(encoding="utf-8"))


@app.get("/api/recordings", dependencies=[Depends(dev_only)])
def recordings(question_id: str | None = None):
    featured = _featured()
    out = []
    for path in sorted(RECORDINGS.glob("*.json"), reverse=True) if RECORDINGS.is_dir() else []:
        if path.name == FEATURED.name:
            continue
        record = json.loads(path.read_text(encoding="utf-8"))
        if question_id and record["question_id"] != question_id:
            continue
        out.append({k: v for k, v in record.items() if k != "events"}
                   | {"featured": featured.get(record["question_id"]) == record["id"]})
    return out


@app.post("/api/recordings/{rid}/feature", dependencies=[Depends(dev_only)])
def feature(rid: str):
    record = _recording(rid)
    featured = _featured()
    featured[record["question_id"]] = rid
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    FEATURED.write_text(json.dumps(featured, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


@app.get("/api/replay/{rid}", dependencies=[Depends(dev_only)])
async def replay(rid: str, speed: float = 4.0):
    record = _recording(rid)
    speed = min(max(speed, 1.0), 20.0)

    async def stream():
        last = 0.0
        for item in record["events"]:
            gap = (item["t"] - last) / speed
            last = item["t"]
            if gap > 0:
                await asyncio.sleep(min(gap, 4.0))
            event = dict(item["event"])
            if event["type"] == "meta":
                event["replay"] = {"id": rid, "recorded_at": record["recorded_at"], "speed": speed,
                                   "duration": record["duration"]}
            yield sse(event)

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
