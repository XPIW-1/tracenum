"""Write deploy/assets.lock.json: every frozen file the live pipeline needs, with sha256.

The whitelist below was derived from scripts/trace_runtime_assets.py (one real S3 run
records every project file it opens) plus what the trace cannot see: the Chroma
directory (opened by native SQLite) and the second company's PDF/chunks.

    python scripts/lock_assets.py            # write the lock
    python scripts/lock_assets.py --check    # verify files still match the lock (CI / packaging)
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent  # the folder that holds tracenum/, v2/, formal_reports_2025/, ...
LOCK = REPO / "deploy" / "assets.lock.json"

V2_FILES = [
    "config/budget_limit.json", "config/budget_outputs.json", "config/dataset_manifest_v1.draft.json",
    "config/evidence_selection.json", "config/formal_freeze_v1.json", "config/input_tokenizer.json",
    "config/source_sidecar.json", "config/source_verification.json",
    "notebooks/07_pilot_adaptive_v2.ipynb",
    "questions/formal/model_questions_v1.draft.json",
    "tools/deepseek_tokenizer_v4/tokenizer.json",
]
V2_DIRS = ["src", "vector_db_formal_v1"]
OTHER = ["deepseek_workspace/20261110_unblind/unblinded_scores.csv"]


CHROMA_VOLATILE = {"acquire_write"}  # chromadb's own write-lock bookkeeping, touched on every open


def file_digest(path: Path) -> tuple[str, str]:
    """(digest, method). Chroma's SQLite file is rewritten by chromadb whenever it is opened
    (even for queries), so it is hashed by content: every table except CHROMA_VOLATILE,
    rows sorted. Every other file is hashed byte for byte."""
    if path.name == "chroma.sqlite3":
        import sqlite3
        db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            h = hashlib.sha256()
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
                if table in CHROMA_VOLATILE:
                    continue
                rows = sorted(repr(r) for r in db.execute(f'SELECT * FROM "{table}"'))
                h.update(table.encode() + b"|" + "\n".join(rows).encode("utf-8") + b"|")
            return h.hexdigest(), "sqlite-content-excluding-acquire_write"
        finally:
            db.close()
    return hashlib.sha256(path.read_bytes()).hexdigest(), "bytes"


def manifest_entries():
    freeze = json.loads((ROOT / "v2/config/formal_freeze_v1.json").read_text(encoding="utf-8"))
    return json.loads((ROOT / "v2" / freeze["manifest"]["path"]).read_text(encoding="utf-8"))


def collect() -> list[dict]:
    paths = [ROOT / "v2" / p for p in V2_FILES] + [ROOT / p for p in OTHER]
    for d in V2_DIRS:
        paths += [p for p in (ROOT / "v2" / d).rglob("*")
                  if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"]
    roles = {}
    for entry in manifest_entries():
        chunks = ROOT / "v2" / entry["chunks_path"]
        pdf = Path(entry["local_pdf_path"])
        paths += [chunks, pdf]
        roles[chunks.resolve()] = ("chunks", entry["chunks_sha256"])
        roles[pdf.resolve()] = ("pdf", entry["pdf_sha256"])
    out = []
    for path in sorted({p.resolve() for p in paths}):
        if not path.is_file():
            sys.exit(f"missing asset: {path}")
        digest, method = file_digest(path)
        role, expected = roles.get(path, (None, None))
        if expected and digest != expected:
            sys.exit(f"{role} hash differs from the frozen manifest: {path}")
        out.append({"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": digest,
                    **({"hash": method} if method != "bytes" else {}), **({"role": role} if role else {})})
    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = collect()
    if args.check:
        locked = {f["path"]: f["sha256"] for f in json.loads(LOCK.read_text(encoding="utf-8"))["files"]}
        current = {f["path"]: f["sha256"] for f in files}
        if locked != current:
            changed = sorted(set(locked.items()) ^ set(current.items()))
            sys.exit(f"assets differ from lock: {changed[:10]}")
        print(f"assets match lock ({len(files)} files)")
        return
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(json.dumps({
        "note": "Frozen inputs of the live pipeline. Paths are relative to the folder containing tracenum/ and v2/. "
                "Never edit v2/; regenerate with scripts/lock_assets.py.",
        "total_bytes": sum(f["bytes"] for f in files), "files": files}, ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8")
    print(f"wrote {LOCK.relative_to(REPO)}: {len(files)} files, {sum(f['bytes'] for f in files) / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
