"""One real expansion question end to end (makes DeepSeek + reranker calls).

    python scripts/smoke_expansion.py 688005 2024 "2024年营业收入是多少？"
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import expansion as X  # noqa: E402
import pipeline as P  # noqa: E402

code, report_year, text = sys.argv[1], int(sys.argv[2]), sys.argv[3]
data_year = int(sys.argv[4]) if len(sys.argv) > 4 else None
P.PIPELINE.load()
info = X.route(code, report_year, data_year)
question = X.build_question(info, text)
final = X.run_expansion(P.PIPELINE.namespace, info, question)
summary = P.summarize_final(final, None) if False else None
pages = sorted({d.metadata.get("page") for d in final.get("evidence", [])})
print(json.dumps({"route": info["collection_name"], "rounds": final.get("retrieval_count"),
                  "tokens": final.get("total_tokens"), "stop": final.get("stop_reason"),
                  "evidence_pages": pages, "answer": final.get("draft_answer", "")[:400]}, ensure_ascii=False, indent=1))
