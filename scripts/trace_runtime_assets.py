"""Record every project file the live pipeline reads during one real S3 run.

The result is the evidence behind deploy/assets.lock.json: the Docker image must
contain exactly these frozen inputs (plus code), nothing more.

    python scripts/trace_runtime_assets.py WSI-2025-005 > trace.json

It makes one real DeepSeek call path (S3) for a featured question whose rerank
scores are already cached, so it is fast and cheap.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
ROOT = BACKEND.parents[1]  # D:\Finance-RAG-Experiment
SKIP = (BACKEND / "runs", BACKEND / "cache", BACKEND / "recordings")

seen: dict[str, str] = {}


def hook(event, args):
    if event != "open" or not args or not isinstance(args[0], (str, bytes, Path)):
        return
    raw = args[0].decode() if isinstance(args[0], bytes) else str(args[0])
    mode = args[1] if len(args) > 1 and isinstance(args[1], str) else "r"
    try:
        path = Path(raw).resolve()
    except (OSError, ValueError):
        return
    if ROOT not in path.parents or any(s == path or s in path.parents for s in SKIP):
        return
    if "__pycache__" in path.parts or path.suffix == ".pyc":
        return
    if any(flag in mode for flag in "wax+"):
        seen.setdefault(str(path), "write")
    else:
        seen.setdefault(str(path), "read")


def main():
    qid = sys.argv[1] if len(sys.argv) > 1 else "WSI-2025-005"
    sys.addaudithook(hook)
    sys.path.insert(0, str(BACKEND))
    import pipeline as P

    question = next(q for q in P.QUESTIONS if q["question_id"] == qid)
    events = []
    final = P.PIPELINE.run(question, "S3", events.append)
    summary = P.summarize_final(final, P.COMPANY_TO_CODE[question["company"]])
    files = []
    for name, kind in sorted(seen.items()):
        path = Path(name)
        if kind != "read" or not path.is_file():
            continue
        files.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size,
                      "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    # Directories read through sqlite/hnsw (Chroma) do not always surface as "open".
    print(json.dumps({"question_id": qid, "cited_pages": summary.get("cited_pages"),
                      "stop_reason": summary.get("stop_reason"), "files": files}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
