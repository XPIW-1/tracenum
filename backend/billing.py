"""Money for live questions, in integer micro-yuan (1 元 = 1_000_000).

Prices are DeepSeek's published peak prices for deepseek-flash (per million tokens,
checked 2026-11-18 at api-docs.deepseek.com/zh-cn/quick_start/pricing): input on a
cache miss 2 元, output 8 元. We always bill input at the cache-miss price and use the
peak tier, so the ledger can only overstate, never understate, what DeepSeek charges.
The reranker (SiliconFlow bge-reranker-v2-m3) has no official price we could confirm
(2026-11-21: third-party listings say free on the China site, other hosts charge about
US$0.01 per million tokens). It is billed at a ceiling of 0.01 元 per request (one request
reranks at most ~40 chunks, ~20k tokens: ~0.0015 元 at the paid rate), so the ledger can
only overstate. Reconcile against the SiliconFlow bill and lower it once the price is known.
"""
from __future__ import annotations

import os

MICRO = 1_000_000
INPUT_PER_M = int(float(os.environ.get("PRICE_INPUT_YUAN_PER_M", "2")) * MICRO)
OUTPUT_PER_M = int(float(os.environ.get("PRICE_OUTPUT_YUAN_PER_M", "8")) * MICRO)
RERANK_PER_REQUEST = int(float(os.environ.get("PRICE_RERANK_YUAN_PER_REQUEST", "0.01")) * MICRO)
MONTHLY_CAP = int(float(os.environ.get("LIVE_MONTHLY_CAP_YUAN", "45")) * MICRO)

# Upper bound for one S3 question: the frozen pipeline stops at 39,000 tokens in total
# (input + output, enforced before every call by v2/src/budget_control.py). Billing all of
# them at the output price bounds the cost no matter how they split. Up to three
# retrieval rounds each call the reranker once.
TOKEN_BUDGET = 39_000
RESERVE_PER_QUESTION = TOKEN_BUDGET * OUTPUT_PER_M // 1_000_000 + 3 * RERANK_PER_REQUEST


def call_cost(prompt_tokens: int | None, completion_tokens: int | None) -> int | None:
    """Cost of one DeepSeek call; None when usage is unknown (caller keeps the reservation)."""
    if prompt_tokens is None or completion_tokens is None:
        return None
    return -(-(prompt_tokens * INPUT_PER_M + completion_tokens * OUTPUT_PER_M) // 1_000_000)  # round up


def yuan(micro: int) -> str:
    return f"{micro / MICRO:.4f} 元"
