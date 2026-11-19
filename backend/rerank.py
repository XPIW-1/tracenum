"""Hosted reranker with the same predict(pairs) contract as sentence_transformers.CrossEncoder.

The frozen retrieval code (v2/src/retrieval.py) calls reranker.predict([[query, text], ...])
and only sorts by the returned scores, so any strictly comparable score works as long
as the *order* matches the paper's local BAAI/bge-reranker-v2-m3. Whether it does is
checked by scripts/compare_rerank.py before live questions are enabled.

Selected with TRACENUM_RERANKER=siliconflow (default: local CrossEncoder).
"""
from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request

import numpy as np

SILICONFLOW_URL = os.environ.get("SILICONFLOW_RERANK_URL", "https://api.siliconflow.cn/v1/rerank")
SILICONFLOW_MODEL = os.environ.get("SILICONFLOW_RERANK_MODEL", "BAAI/bge-reranker-v2-m3")


class RerankError(RuntimeError):
    """The hosted reranker failed; the caller must not fall back to a different model."""


class HostedReranker:
    """One HTTP request per predict() call: one query, all candidates, scores in input order."""

    def __init__(self, api_key: str, *, url: str = SILICONFLOW_URL, model: str = SILICONFLOW_MODEL,
                 timeout: float = 30.0, retries: int = 1, max_chunks_per_doc: int | None = None):
        if not api_key:
            raise RerankError("SILICONFLOW_API_KEY is not configured")
        self.api_key, self.url, self.model = api_key, url, model
        self.timeout, self.retries, self.max_chunks_per_doc = timeout, retries, max_chunks_per_doc
        self.last_meta: dict = {}

    def predict(self, pairs, *args, **kwargs):
        if not pairs:
            return np.asarray([], dtype=np.float32)
        queries = {query for query, _ in pairs}
        if len(queries) != 1:
            # retrieve_and_rerank always sends one query; refuse anything else rather than mix batches.
            raise RerankError("HostedReranker expects every pair to share one query")
        query = pairs[0][0]
        documents = [text for _, text in pairs]
        body = {"model": self.model, "query": query, "documents": documents,
                "top_n": len(documents), "return_documents": False}
        if self.max_chunks_per_doc is not None:
            body["max_chunks_per_doc"] = self.max_chunks_per_doc
        payload = self._post(body)
        results = payload.get("results")
        if not isinstance(results, list) or len(results) != len(documents):
            raise RerankError(f"reranker returned {len(results) if isinstance(results, list) else 'no'} results "
                              f"for {len(documents)} documents")
        scores = [None] * len(documents)
        for item in results:
            index, score = item.get("index"), item.get("relevance_score")
            if not isinstance(index, int) or not 0 <= index < len(documents) or scores[index] is not None:
                raise RerankError(f"bad or duplicate result index: {index!r}")
            if not isinstance(score, (int, float)) or not math.isfinite(score):
                raise RerankError(f"non-finite score for index {index}")
            scores[index] = float(score)
        self.last_meta = {"model": self.model, "id": payload.get("id"), "meta": payload.get("meta")}
        return np.asarray(scores, dtype=np.float32)

    def _post(self, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last = None
        for attempt in range(self.retries + 1):
            request = urllib.request.Request(self.url, data=data, method="POST", headers={
                "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last = RerankError(f"reranker HTTP {exc.code}: {exc.read()[:300].decode('utf-8', 'replace')}")
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last = RerankError(f"reranker unreachable: {exc}")
            if attempt < self.retries:
                time.sleep(1.5 * (attempt + 1))
        raise last


class CacheOnlyReranker:
    """Serves only scores the paper's local model already computed (backend/cache/rerank).

    Used for offline smoke tests and CI: CachedReranker answers every known (query, chunks)
    group from disk; anything new raises instead of silently using another model.
    """

    def predict(self, pairs, *args, **kwargs):
        raise RerankError("cache-only reranker: these candidates were never scored by the local model")


def build_reranker():
    """Return (inner_reranker, label). The label is recorded so caches never mix providers."""
    kind = os.environ.get("TRACENUM_RERANKER", "local").lower()
    if kind == "cache-only":
        return CacheOnlyReranker(), "local:BAAI/bge-reranker-v2-m3"
    if kind == "siliconflow":
        chunks = os.environ.get("SILICONFLOW_MAX_CHUNKS_PER_DOC")
        return HostedReranker(os.environ.get("SILICONFLOW_API_KEY", ""),
                              max_chunks_per_doc=int(chunks) if chunks else None), f"siliconflow:{SILICONFLOW_MODEL}"
    from sentence_transformers import CrossEncoder
    return CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu"), "local:BAAI/bge-reranker-v2-m3"
