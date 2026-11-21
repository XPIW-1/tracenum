"""What a result was produced from: corpus version, code version, model fingerprint.

Every recording and every eval row carries this, so the release check can tell a result
made from the current corpus and model from a stale one.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "build" / "corpus_v3"
sys.path.insert(0, str(ROOT / "backend"))


@lru_cache(maxsize=None)
def chunks_sha256(report_key: str) -> str | None:
    """sha256 of build/corpus_v3/<code>_<year>_v3/chunks.jsonl (what the index is built from)."""
    path = CORPUS / f"{report_key}_v3" / "chunks.jsonl"
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def git_commit() -> dict:
    run = lambda *a: subprocess.run(["git", *a], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain", "--untracked-files=no"))}


def run_record(run_dir) -> dict:
    """Model fingerprints and answer-check findings of one logged pipeline run."""
    import answer_check

    run_dir = Path(run_dir)
    fingerprints = set()
    calls = run_dir / "calls.jsonl"
    if calls.is_file():
        for line in calls.read_text(encoding="utf-8").splitlines():
            if line.strip() and (fp := json.loads(line).get("system_fingerprint")):
                fingerprints.add(fp)
    checked = answer_check.check_run_dir(run_dir)
    return {"run": run_dir.name, "model_fingerprints": sorted(fingerprints), "findings": checked["findings"]}
