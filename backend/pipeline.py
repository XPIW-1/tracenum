"""Streaming wrapper around the frozen formal-v1 pipeline.

Nothing under v2/ is modified. We import the frozen nodes exactly the way
v2/scripts/run_formal_v1.py does, call the frozen entry point
``src.pilot_comparison.run_arm`` and observe node boundaries through a
RunLogger subclass that forwards a compact event to a per-thread sink.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import Callable

# The frozen runner prints answers; on a GBK Windows console a "−" would raise
# UnicodeEncodeError and kill the run. Never let logging break an answer.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Models are cached locally; never block startup (or a classroom demo) on the HF Hub.
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

ROOT = Path(__file__).resolve().parents[2]
V2 = ROOT / "v2"
if str(V2) not in sys.path:
    sys.path.insert(0, str(V2))

FREEZE = json.loads((V2 / "config/formal_freeze_v1.json").read_text(encoding="utf-8"))
MANIFEST = json.loads((V2 / FREEZE["manifest"]["path"]).read_text(encoding="utf-8"))
QUESTIONS = json.loads((V2 / FREEZE["questions"]["path"]).read_text(encoding="utf-8"))
NOTEBOOK = V2 / "notebooks/07_pilot_adaptive_v2.ipynb"
SCORES_CSV = ROOT / "deepseek_workspace/20261110_unblind/unblinded_scores.csv"
LOG_ROOT = Path(__file__).resolve().parent / "runs"

CORPORA = {
    "BYD": {"company": "比亚迪", "name": "比亚迪股份有限公司", "ticker": "002594.SZ", "year": 2025},
    "WSI": {"company": "威胜信息", "name": "威胜信息技术股份有限公司", "ticker": "688100.SH", "year": 2025},
}
for code, info in CORPORA.items():
    record = next(r for r in MANIFEST if r["company"] == info["company"])
    info.update(pdf_path=record["local_pdf_path"], pages=record["pdf_page_count"],
                corpus_id=record["corpus_id"], source_url=record["source_url"])
COMPANY_TO_CODE = {info["company"]: code for code, info in CORPORA.items()}
STRATEGIES = ("S1", "S2", "S3")


def load_env():
    env = ROOT / ".env"
    if env.is_file() and not os.environ.get("DEEPSEEK_API_KEY"):
        for line in env.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# --------------------------------------------------------------------------- #
# Paper results (frozen, blind-scored)
# --------------------------------------------------------------------------- #
def load_paper_results():
    rows = {}
    if not SCORES_CSV.is_file():
        return rows
    with SCORES_CSV.open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            rows.setdefault(row["qid"], {})[row["strategy"]] = {
                "correct": row["correct"] == "1",
                "errors": [e for e in row["errors"].split(";") if e],
                "rounds": int(row["rounds"]), "tokens": int(row["tok"]),
                "latency": float(row["lat"]), "stop": row["stop"], "reason": row["reason"],
            }
    return rows


PAPER = load_paper_results()


def text_key(text: str) -> str:
    import unicodedata
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


# Keyed by company too: both boundary questions share the exact same text.
QUESTION_BY_TEXT = {(q["company"], text_key(q["question"])): q for q in QUESTIONS}


# --------------------------------------------------------------------------- #
# Single-flight reranker cache: identical (query, docs) pairs across S1/S2/S3
# produce identical scores, so computing them once changes no result.
# --------------------------------------------------------------------------- #
CACHE_DIR = Path(__file__).resolve().parent / "cache" / "rerank"


class CachedReranker:
    """Memory + disk cache keyed by the exact (query, chunk) pairs."""

    def __init__(self, inner):
        self.inner = inner
        self.cache, self.locks, self.guard = {}, {}, threading.Lock()
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    def _disk(self, key):
        path = CACHE_DIR / f"{key}.json"
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
        return None

    def predict(self, pairs, *args, **kwargs):
        import numpy as np
        key = hashlib.sha256(json.dumps(pairs, ensure_ascii=False).encode("utf-8")).hexdigest()
        with self.guard:
            if key in self.cache:
                return self.cache[key]
            lock = self.locks.setdefault(key, threading.Lock())
        with lock:
            if key in self.cache:
                return self.cache[key]
            stored = self._disk(key)
            if stored is not None:
                scores = np.asarray(stored, dtype=np.float32)
            else:
                # Score one pair per forward pass. In a padded batch every pair is
                # padded to the longest chunk (up to ~3.5k tokens); the attention
                # mask makes padding irrelevant to the score (checked by
                # bench_rerank.py: max |diff| < 1e-6, identical ranking), so this
                # only removes wasted compute and lets us report progress.
                kwargs = {**kwargs, "batch_size": 1, "show_progress_bar": False}
                sink = getattr(_sink, "emit", None)
                parts = []
                for index, pair in enumerate(pairs):
                    parts.append(self.inner.predict([pair], *args, **kwargs)[0])
                    if sink:
                        sink({"type": "progress", "node": "retrieve", "done": index + 1, "total": len(pairs)})
                scores = np.asarray(parts, dtype=np.float32)
                (CACHE_DIR / f"{key}.json").write_text(json.dumps([float(s) for s in scores]), encoding="utf-8")
            with self.guard:
                self.cache[key] = scores
            return scores

    def __getattr__(self, name):
        return getattr(self.inner, name)


# --------------------------------------------------------------------------- #
# Event sink: run_arm builds its own RunLogger, so we swap the class it uses.
# --------------------------------------------------------------------------- #
_sink = threading.local()


def _doc_view(doc, limit=1200):
    return {"page": doc.metadata.get("page"), "type": doc.metadata.get("type"),
            "text": doc.page_content[:limit]}


def _node_payload(node, state, latency):
    payload = {"node": node, "round": state.get("round"), "latency": round(latency, 2),
               "total_tokens": state.get("total_tokens"), "model_calls": state.get("model_calls"),
               "budget": state.get("token_budget"), "budget_stop": state.get("budget_stop")}
    if node == "retrieve":
        trace = (state.get("retrieval_trace") or [{}])[-1]
        selected = set(trace.get("selected_ids") or [])
        pages = [c["page"] for c in trace.get("reranked", []) if c["id"] in selected]
        payload.update(query=trace.get("query"), candidate_count=trace.get("candidate_count"),
                       selected_pages=pages, new_evidence=state.get("new_evidence_count"),
                       evidence_pages=sorted({d.metadata.get("page") for d in state.get("evidence", [])}))
    elif node == "generate":
        payload.update(answer=state.get("draft_answer"), generated_rounds=state.get("generated_rounds"))
    elif node == "check":
        payload.update(feedback=state.get("feedback"), missing=state.get("missing_items"),
                       metric_audit=state.get("metric_audit"))
    elif node == "decide":
        payload.update(stop_reason=state.get("stop_reason"), feedback=state.get("feedback"))
    elif node == "requery":
        payload.update(query=state.get("current_query"))
    return payload


def _install_logger():
    import src.pilot_comparison as pc
    from src.observability import RunLogger
    if getattr(pc.RunLogger, "_tracenum", False):
        return

    class StreamingRunLogger(RunLogger):
        _tracenum = True

        def wrap(self, function, node):
            observed = super().wrap(function, node)

            def forwarded(state):
                sink = getattr(_sink, "emit", None)
                if sink:
                    sink({"type": "node_start", "node": node, "round": state.get("round")})
                started = time.perf_counter()
                result = observed(state)
                if sink:
                    sink({"type": "node", **_node_payload(node, result, time.perf_counter() - started)})
                return result
            return forwarded

    pc.RunLogger = StreamingRunLogger


# --------------------------------------------------------------------------- #
# Namespace (loaded once; embeddings and reranker are slow to construct)
# --------------------------------------------------------------------------- #
class Pipeline:
    def __init__(self):
        self.namespace = None
        self.lock = threading.Lock()
        self.ready = False
        self.error = None

    def load(self):
        with self.lock:
            if self.ready:
                return
            load_env()
            import torch
            torch.set_num_threads(os.cpu_count() or 6)
            from langchain_huggingface import HuggingFaceEmbeddings
            from langchain_chroma import Chroma
            from sentence_transformers import CrossEncoder
            from src.deepseek_client import DeepSeekChatOpenAI

            notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))
            cells = ["".join(c["source"]) for c in notebook["cells"] if c["cell_type"] == "code"]
            state_cell = next(s for s in cells if "class RAGState(" in s)
            node_cell = next(s for s in cells if "def retrieve_node(" in s)
            namespace = {"TypedDict": __import__("typing").TypedDict, "Literal": __import__("typing").Literal}
            exec(compile(state_cell, "notebook:RAGState", "exec"), namespace)
            exec(compile(node_cell, "notebook:nodes", "exec"), namespace)

            embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-base-zh-v1.5", model_kwargs={"device": "cpu"},
                                               encode_kwargs={"normalize_embeddings": True})
            reranker = CachedReranker(CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu"))
            stores = {r["company"]: Chroma(collection_name=r["collection_name"],
                                           persist_directory=str(V2 / r["vector_db_path"]),
                                           embedding_function=embeddings) for r in MANIFEST}
            key = os.environ.get("DEEPSEEK_API_KEY")
            if not key:
                raise RuntimeError("DEEPSEEK_API_KEY is not configured")
            llm = DeepSeekChatOpenAI(model=FREEZE["model"]["alias"], api_key=key, base_url="https://api.deepseek.com",
                                     temperature=FREEZE["model"]["temperature"], max_retries=0)
            namespace.update(vectorstore=None, reranker=reranker, llm=llm, corpus_stores=stores)
            _install_logger()
            LOG_ROOT.mkdir(parents=True, exist_ok=True)
            self.namespace = namespace
            self.ready = True

    def run(self, question: dict, strategy: str, emit: Callable[[dict], None]):
        from src.pilot_comparison import run_arm
        self.load()
        _sink.emit = emit
        try:
            final = run_arm(self.namespace, question, strategy, log_root=LOG_ROOT, execution_mode="formal")
        finally:
            _sink.emit = None
        return final


PIPELINE = Pipeline()


# --------------------------------------------------------------------------- #
# Question handling
# --------------------------------------------------------------------------- #
YEAR_RE = re.compile(r"(20\d{2})\s*年")


def build_question(code: str, text: str) -> tuple[dict, dict | None]:
    """Return (question record, paper reference or None)."""
    info = CORPORA[code]
    text = text.strip()
    formal = QUESTION_BY_TEXT.get((info["company"], text_key(text)))
    if formal:
        return dict(formal), {"question_id": formal["question_id"], "question_type": formal["question_type"],
                              "results": PAPER.get(formal["question_id"], {})}
    qid = "TN-" + hashlib.sha256(f"{code}:{text}".encode("utf-8")).hexdigest()[:10]
    return {"question_id": qid, "company": info["company"], "question_year": info["year"],
            "corpus_max_year": info["year"], "corpus_id": info["corpus_id"], "question": text,
            "split": "tracenum_live", "boundary_question": False}, None


def out_of_corpus_year(code: str, text: str, question: dict) -> int | None:
    if question.get("boundary_question"):
        return None
    years = [int(y) for y in YEAR_RE.findall(text)]
    later = [y for y in years if y > CORPORA[code]["year"]]
    return max(later) if later else None


NUM_RE = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d+\.\d+|-?\d{2,}")
CITE_RE = re.compile(r"第\s*(\d+)\s*页")


def _norm(value: str) -> str:
    return value.replace(",", "").replace("，", "").lstrip("-").rstrip("0").rstrip(".") if "." in value \
        else value.replace(",", "").replace("，", "").lstrip("-")


def verify_numbers(answer: str, evidence: list[dict]) -> list[dict]:
    """For each figure in the answer, find the evidence pages whose text contains it."""
    page_text = {}
    for item in evidence:
        flat = re.sub(r"[,\s，]", "", item["text_full"])
        page_text.setdefault(item["page"], []).append(flat)
    results, seen = [], set()
    answer = re.sub(r"\[?第\s*\d+\s*页\]?", " ", answer)
    for match in NUM_RE.finditer(answer):
        raw = match.group(0)
        key = _norm(raw)
        if key in seen or len(key.replace(".", "")) < 3:
            continue
        seen.add(key)
        if re.fullmatch(r"20\d{2}", key):
            continue
        exact = re.compile(r"(?<![\d.])" + re.escape(key) + r"(?![\d])")
        pages = sorted(p for p, texts in page_text.items() if any(exact.search(t) for t in texts))
        scaled = False
        if not pages and re.fullmatch(r"10*", key):
            continue
        if not pages:
            # e.g. "803,964,958 千元" against "803,964,958,000.00 元": same figure, other unit.
            pages = sorted(p for p, texts in page_text.items() if any(key in t for t in texts))
            scaled = bool(pages)
        # Unit-conversion constants (1,000 / 10,000 / 100,000,000) are not figures.
        if not pages and re.fullmatch(r"10*", key):
            continue
        entry = {"value": raw, "pages": pages, "status": "matched" if pages else "derived"}
        if pages:
            entry.update(row_context(key, [e for e in evidence if e["page"] == pages[0]]))
            if scaled:
                entry.update(scaled=True, unit="")
        results.append(entry)
    return results


CJK = re.compile(r"[一-鿿]")
UNIT_RE = re.compile(r"[（(]\s*(元|千元|万元|亿元|百万元|%|股|元/股|人)\s*[)）]")


def row_context(key: str, items: list[dict]) -> dict:
    """Find the table row (or sentence) holding the figure: its label, unit and a clean snippet."""
    for item in items:
        text = item["text_full"]
        header_unit = UNIT_RE.search(text) or re.search(r"单位[:：]\s*(元|千元|万元|亿元|百万元)", text)
        for line in text.split("\n"):
            if key not in re.sub(r"[,\s，]", "", line):
                continue
            cells = [c.strip() for c in line.split("|") if c.strip()]
            label = next((c for c in cells if len(CJK.findall(c)) >= 2 and key not in re.sub(r"[,\s，]", "", c)), "")
            unit = UNIT_RE.search(label) or header_unit
            snippet = "  ".join(cells) if len(cells) > 1 else line.strip()
            if len(cells) <= 1:
                position = re.sub(r"[,\s，]", "", line).find(key)
                label = ""
                snippet = line.strip()[max(0, position - 40): position + 60]
            return {"label": UNIT_RE.sub("", label).strip(" ：:"), "unit": unit.group(1) if unit else "",
                    "line": snippet[:160]}
    return {}


def summarize_final(final: dict, code: str) -> dict:
    evidence = [{"page": d.metadata.get("page"), "type": d.metadata.get("type"),
                 "text": d.page_content[:1600], "text_full": d.page_content} for d in final.get("evidence", [])]
    answer = final.get("draft_answer") or ""
    cited = sorted({int(p) for p in CITE_RE.findall(answer)})
    numbers = verify_numbers(answer, evidence)
    suggestions = []
    for trace in reversed(final.get("retrieval_trace") or []):
        for cand in trace.get("reranked", []):
            if cand["page"] not in suggestions:
                suggestions.append(cand["page"])
            if len(suggestions) >= 3:
                break
        if len(suggestions) >= 3:
            break
    refused = ("未找到" in answer) or not answer.strip()
    for item in evidence:
        item.pop("text_full", None)
    return {
        "answer": answer, "cited_pages": cited, "numbers": numbers, "evidence": evidence,
        "suggested_pages": suggestions, "refused": refused,
        "stop_reason": final.get("stop_reason"), "rounds": final.get("round"),
        "generated_rounds": final.get("generated_rounds"), "model_calls": final.get("model_calls"),
        "total_tokens": final.get("total_tokens"), "budget": final.get("token_budget"),
        "budget_stop": final.get("budget_stop"), "feedback": final.get("feedback"),
        "missing": final.get("missing_items"), "evidence_status": final.get("evidence_status"),
        "verification_status": final.get("verification_status"),
        "termination_status": final.get("termination_status"),
        "queries": [t.get("query") for t in final.get("retrieval_trace") or []],
        "company": code,
    }
