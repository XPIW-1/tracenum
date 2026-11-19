"""Expansion questions: the paper's S3 graph over the v3 corpus, routed by (code, report year).

What is shared with the paper and what is not (stated on the methodology page):
  - same frozen LangGraph nodes (v2/notebooks/07), same S1/S2/S3 graph and stop rules,
    same 39,000-token budget, same embedding / reranker / generator stack;
  - different corpus (v3 parser, one Chroma collection per annual report) and a different
    router (this module) — so results are NOT the paper's experiment.

Routing: a question names one company (A-share code) and one annual report (report year).
An annual report holds the report year and the prior year as comparatives, so data years
report_year-1 .. report_year are answerable from it; anything else is a boundary refusal
without a model call. Nothing here guesses a company or a year.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "corpus" / "registry.json"
DB = Path(os.environ.get("TRACENUM_V3_DB") or REPO / "build" / "vector_v3")


class RouteError(ValueError):
    """The (company, report year, data year) triple cannot be answered from the v3 corpus."""


def load_registry() -> dict[tuple[str, int], dict]:
    reports = json.loads(REGISTRY.read_text(encoding="utf-8"))["reports"]
    return {(e["code"], int(e["report_year"])): e for e in reports if e.get("status") != "MISSING"}


REPORTS = load_registry()


def companies() -> list[dict]:
    out = {}
    for (code, year), e in sorted(REPORTS.items()):
        out.setdefault(code, {"code": code, "company": e["company"], "report_years": []})["report_years"].append(year)
    return list(out.values())


def route(code: str, report_year: int, data_year: int | None = None) -> dict:
    entry = REPORTS.get((code, int(report_year)))
    if entry is None:
        raise RouteError(f"语料中没有 {code} 的 {report_year} 年年报")
    data_year = int(report_year if data_year is None else data_year)
    if not report_year - 1 <= data_year <= report_year:
        raise RouteError(f"{report_year} 年年报只包含 {report_year - 1}–{report_year} 年数据；"
                         f"请改选 {data_year} 年年报")
    return {"code": code, "company": entry["company"], "report_year": int(report_year), "data_year": data_year,
            "corpus_id": f"{code}_{report_year}_v3", "collection_name": f"v3_{code}_{report_year}",
            "pdf_sha256": entry["sha256"], "corpus_max_year": int(report_year)}


_stores: dict[str, object] = {}


def store_for(route_info: dict, embeddings):
    """Open (once) and verify the collection for this annual report."""
    name = route_info["collection_name"]
    if name not in _stores:
        from langchain_chroma import Chroma
        store = Chroma(collection_name=name, persist_directory=str(DB), embedding_function=embeddings,
                       create_collection_if_not_exists=False)
        meta = store._collection.metadata or {}
        if meta.get("pdf_sha256") != route_info["pdf_sha256"] or meta.get("corpus_id") != route_info["corpus_id"]:
            raise RouteError(f"索引与登记的年报不一致：{name}")
        if store._collection.count() == 0:
            raise RouteError(f"索引为空：{name}")
        _stores[name] = store
    return _stores[name]


def build_question(route_info: dict, text: str) -> dict:
    digest = hashlib.sha256(f"{route_info['corpus_id']}|{text}".encode("utf-8")).hexdigest()[:10]
    return {"question_id": f"X-{route_info['code']}-{route_info['report_year']}-{digest}", "question": text,
            "company": route_info["company"], "question_year": route_info["data_year"],
            "corpus_max_year": route_info["corpus_max_year"], "split": "expansion"}


def run_expansion(namespace, route_info: dict, question: dict, strategy: str = "S3", *, log_root=None,
                  embeddings=None):
    """Mirror of v2/src/pilot_comparison.run_arm with our route instead of the frozen manifest route.

    The frozen retrieve node calls corpus_routing.retrieval_store(state, ...), which accepts
    a run-local store set in FORMAL_STORE whose collection name equals state.corpus_route.
    We provide exactly that, so the nodes themselves run unmodified.
    """
    from src.budget_control import (POLICY_VERSION, ANSWER_MAX_TOKENS, REQUERY_MAX_TOKENS, DEFAULT_TOKEN_BUDGET,
                                    ANSWER_MIN_OUTPUT_TOKENS, REQUERY_MIN_OUTPUT_TOKENS)
    from src.retrieval import RETRIEVAL_POLICY
    from src.metric_scope import CHECK_POLICY
    from src.observability import RunLogger
    from src.pilot_comparison import build_arm_app, COMPARISON_VERSION
    from src.corpus_routing import validate_question, FORMAL_STORE
    from src.runtime_status import update_status

    validate_question(question)
    store = store_for(route_info, embeddings or namespace["corpus_embeddings"])
    initial = {
        "original_question": question["question"], "current_query": question["question"],
        "company": question["company"], "question_year": question["question_year"],
        "corpus_max_year": route_info["corpus_max_year"],
        "evidence": [], "retrieved_ids": [], "new_evidence_count": 0,
        "draft_answer": "", "feedback": {}, "missing_items": [],
        "total_tokens": 0, "token_budget": DEFAULT_TOKEN_BUDGET, "model_calls": 0,
        "retrieval_count": 0, "round": 1, "max_rounds": {"S1": 1, "S2": 2, "S3": 3}[strategy],
        "stop_reason": "", "trajectory": [], "budget_stop": False,
        "budget_stop_detail": "", "usage_complete": True, "budget_overshoot_tokens": 0,
        "budget_safety_margin": 128, "min_output_tokens": 128,
        "generate_min_output_tokens": ANSWER_MIN_OUTPUT_TOKENS, "requery_min_output_tokens": REQUERY_MIN_OUTPUT_TOKENS,
        "generate_max_tokens": ANSWER_MAX_TOKENS, "requery_max_tokens": REQUERY_MAX_TOKENS,
        "api_calls": [], "generated_rounds": 0, "run_id": uuid4().hex,
        "budget_policy": POLICY_VERSION, "retrieval_policy": RETRIEVAL_POLICY,
        "retrieval_trace": [], "check_policy": CHECK_POLICY, "metric_audit": {},
        "financial_feedback_enabled": True, "fact_audit": [], "fact_history": [],
        "calculation_audit": {}, "calculation_history": [], "correction_feedback": {},
        "financial_contract": {}, "evidence_status": "evidence_insufficient",
        "correction_status": "not_demonstrated", "technical_status": "ok",
        "termination_status": "running", "total_latency": "UNKNOWN",
        "retrieval_latency": 0.0, "model_latency": 0.0,
        "corpus_route": {"company": route_info["company"], "corpus_id": route_info["corpus_id"],
                         "collection_name": route_info["collection_name"],
                         "corpus_max_year": route_info["corpus_max_year"], "router": "tracenum-expansion-v3"},
        "execution_mode": "formal",
        "verification_status": "NOT_EVALUATED", "feedback_reason_codes": [],
        "retrieval_needed": False, "revision_needed": False,
        "requery_failure": {}, "model_draft_answer": "", "refusal_source": "none",
    }
    logger = RunLogger(log_root or REPO / "backend" / "runs", group=strategy, qid=question["question_id"],
                       model=getattr(namespace.get("llm"), "model_name", "unknown-model"), dataset="expansion_v3")
    token = FORMAL_STORE.set(store)
    try:
        final = build_arm_app(namespace, strategy, logger=logger).invoke(initial)
    except Exception as exc:
        logger.finish(error=exc)
        raise
    finally:
        FORMAL_STORE.reset(token)
    update_status(final)
    logger.finish(final)
    final.update(strategy=strategy, question_id=question["question_id"], comparison_version=COMPARISON_VERSION,
                 expansion_route=route_info, observation_dir=str(logger.directory))
    return final
