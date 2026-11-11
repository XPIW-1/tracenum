"""TraceNum API: live, streamed answers over the frozen formal-v1 pipeline.

Run:  python -m uvicorn server:app --port 8765   (from tracenum/backend)
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import pipeline as P

app = FastAPI(title="TraceNum API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class AskBody(BaseModel):
    company: str
    question: str
    strategies: list[str] = ["S3"]


@app.on_event("startup")
def warm():
    # Load models in the background so the first question does not pay for it.
    def load():
        try:
            P.PIPELINE.load()
        except Exception as exc:  # surfaced through /api/status
            P.PIPELINE.error = f"{type(exc).__name__}: {exc}"
    threading.Thread(target=load, daemon=True).start()


@app.get("/api/status")
def status():
    return {"ready": P.PIPELINE.ready, "error": P.PIPELINE.error, "model": P.FREEZE["model"]["alias"],
            "budget": P.FREEZE["budget"]["token_budget_per_run"]}


@app.get("/api/corpora")
def corpora():
    return [{"code": code, **{k: v for k, v in info.items() if k != "pdf_path"}} for code, info in P.CORPORA.items()]


@app.get("/api/questions")
def questions():
    out = []
    for q in P.QUESTIONS:
        out.append({"question_id": q["question_id"], "company": P.COMPANY_TO_CODE[q["company"]],
                    "question": q["question"], "question_type": q["question_type"],
                    "metric": q.get("metric"), "boundary": q.get("boundary_question", False),
                    "results": P.PAPER.get(q["question_id"], {})})
    return out


@app.get("/api/pdf/{code}")
def pdf(code: str):
    if code not in P.CORPORA:
        raise HTTPException(404)
    return FileResponse(P.CORPORA[code]["pdf_path"], media_type="application/pdf")


class ExportBody(BaseModel):
    items: list[dict]


@app.post("/api/export")
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


@app.post("/api/ask")
async def ask(body: AskBody):
    if body.company not in P.CORPORA:
        raise HTTPException(400, "unknown company")
    strategies = [s for s in body.strategies if s in P.STRATEGIES] or ["S3"]
    question, paper = P.build_question(body.company, body.question)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def push(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    tape = Tape(body.company, question, strategies)

    async def stream():
        yield sse(tape.add({"type": "meta", "question": question["question"], "question_id": question["question_id"],
                            "paper": paper, "strategies": strategies, "budget": P.FREEZE["budget"]["token_budget_per_run"],
                            "company": body.company}))
        later = P.out_of_corpus_year(body.company, body.question, question)
        if later:
            info = P.CORPORA[body.company]
            for s in strategies:
                yield sse({"type": "final", "strategy": s, "boundary": True, "result": {
                    "answer": "", "refused": True, "boundary_year": later, "corpus_year": info["year"],
                    "cited_pages": [], "numbers": [], "evidence": [], "suggested_pages": [],
                    "missing": [f"问题涉及 {later} 年，当前语料只覆盖 {info['company']} {info['year']} 年年报"],
                    "stop_reason": "语料边界外（未调用模型）", "total_tokens": 0, "model_calls": 0, "rounds": 0,
                    "budget": P.FREEZE["budget"]["token_budget_per_run"]}})
            yield sse({"type": "done"})
            return

        def worker(strategy):
            emit = lambda e: push({**e, "strategy": strategy})
            started = time.perf_counter()
            emit({"type": "start"})
            try:
                final = P.PIPELINE.run(question, strategy, emit)
                result = P.summarize_final(final, body.company)
                result["elapsed"] = round(time.perf_counter() - started, 1)
                emit({"type": "final", "result": result})
            except Exception as exc:
                emit({"type": "error", "message": f"{type(exc).__name__}: {exc}",
                      "trace": traceback.format_exc()[-800:]})
            finally:
                push({"type": "_worker_done"})

        if not P.PIPELINE.ready:
            yield sse({"type": "loading", "message": "正在加载检索模型…"})
        for s in strategies:
            threading.Thread(target=worker, args=(s,), daemon=True).start()
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


@app.get("/api/recordings")
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


@app.post("/api/recordings/{rid}/feature")
def feature(rid: str):
    record = _recording(rid)
    featured = _featured()
    featured[record["question_id"]] = rid
    RECORDINGS.mkdir(parents=True, exist_ok=True)
    FEATURED.write_text(json.dumps(featured, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"ok": True}


@app.get("/api/replay/{rid}")
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
