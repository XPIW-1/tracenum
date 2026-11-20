"""Assemble the Docker build context for the live backend, without touching v2/.

    python deploy/package_runtime.py                 # -> build/runtime (paths for /app inside the image)
    python deploy/package_runtime.py --prefix D:/tmp/rt --out D:/tmp/rt   # local dry run on Windows

Layout of the output mirrors the project root, so backend/pipeline.py (ROOT = parents[2])
finds v2/ next to tracenum/ exactly as on the development machine:

    <out>/v2/...                       only the locked frozen files (deploy/assets.lock.json)
    <out>/formal_reports_2025/*.pdf    byte-identical originals
    <out>/deepseek_workspace/...       blind-scored paper results (read by the methodology page)
    <out>/tracenum/backend/*.py        backend code (no runs/, cache/, recordings/)
    <out>/hf/                          HuggingFace cache with only bge-base-zh-v1.5 (offline load)
    <out>/Dockerfile, requirements-runtime.txt

The only derived file is v2/config/dataset_manifest_v1.draft.json: its two Windows
`local_pdf_path` values are rewritten to <prefix>/formal_reports_2025/<name>.pdf. Every
other byte is copied unchanged, and the script asserts that the derived manifest differs
from the original in those two fields only. Both hashes are written to <out>/RUNTIME.json.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[1]
ROOT = REPO.parent
LOCK = REPO / "deploy" / "assets.lock.json"
MANIFEST_REL = "v2/config/dataset_manifest_v1.draft.json"
HF_MODEL = "models--BAAI--bge-base-zh-v1.5"
HF_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
BACKEND_FILES = ["pipeline.py", "server.py", "rerank.py", "access.py", "quota.py", "billing.py",
                 "review_recordings.py", "expansion.py"]  # the last one grades the post-deploy smoke test


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(REPO / "build" / "runtime"))
    parser.add_argument("--prefix", default="/app", help="absolute project root inside the target system")
    parser.add_argument("--no-model", action="store_true", help="skip copying the embedding model")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    marker = out / ".tracenum-runtime"
    # Only ever delete a folder this script created before; never a source tree.
    protected = {ROOT.resolve(), REPO.resolve(), *(p.resolve() for p in (ROOT / "v2", REPO / "backend", REPO / "web"))}
    if out in protected or any(out in p.parents for p in protected):
        sys.exit(f"refusing to use {out} as output: it is (or contains) a source folder")
    if out.exists():
        if any(out.iterdir()) and not marker.is_file():
            sys.exit(f"refusing to overwrite {out}: not empty and not created by this script")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    marker.write_text("created by deploy/package_runtime.py\n", encoding="utf-8")

    sys.path.insert(0, str(REPO / "scripts"))
    from lock_assets import file_digest
    lock = json.loads(LOCK.read_text(encoding="utf-8"))
    for item in lock["files"]:
        src = ROOT / item["path"]
        if file_digest(src)[0] != item["sha256"]:
            sys.exit(f"asset changed since lock: {item['path']} (run scripts/lock_assets.py --check)")
        dst = out / item["path"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)

    # Derived manifest: only local_pdf_path changes.
    original_bytes = (ROOT / MANIFEST_REL).read_bytes()
    records = json.loads(original_bytes.decode("utf-8"))
    derived = json.loads(original_bytes.decode("utf-8"))
    for record in derived:
        name = Path(record["local_pdf_path"].replace("\\", "/")).name
        record["local_pdf_path"] = str(PurePosixPath(args.prefix) / "formal_reports_2025" / name) \
            if args.prefix.startswith("/") else str(Path(args.prefix) / "formal_reports_2025" / name)
    for before, after in zip(records, derived):
        changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
        if changed - {"local_pdf_path"}:
            sys.exit(f"derived manifest changed more than local_pdf_path: {changed}")
    derived_bytes = (json.dumps(derived, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (out / MANIFEST_REL).write_bytes(derived_bytes)
    for record in derived:  # the PDFs the derived manifest points at must be the frozen ones
        pdf = out / "formal_reports_2025" / Path(record["local_pdf_path"]).name
        if sha(pdf) != record["pdf_sha256"]:
            sys.exit(f"pdf hash mismatch after copy: {pdf}")

    backend = out / "tracenum" / "backend"
    backend.mkdir(parents=True)
    for name in BACKEND_FILES:
        if (REPO / "backend" / name).is_file():
            shutil.copy2(REPO / "backend" / name, backend / name)

    # Post-deploy smoke test (scripts/e2e_featured.py): the featured list and the paper's Gold
    # answers for grading. Neither is served; the Gold file is checked against the freeze.
    (out / "tracenum" / "scripts").mkdir(parents=True)
    shutil.copy2(REPO / "scripts" / "e2e_featured.py", out / "tracenum" / "scripts" / "e2e_featured.py")
    shutil.copy2(REPO / "scripts" / "memtest_v3.py", out / "tracenum" / "scripts" / "memtest_v3.py")
    # v3 corpus: the registry routes (code, report year); the Chroma index itself lives on the
    # data volume (TRACENUM_V3_DB), uploaded separately, not baked into the image.
    (out / "tracenum" / "corpus").mkdir(parents=True)
    shutil.copy2(REPO / "corpus" / "registry.json", out / "tracenum" / "corpus" / "registry.json")
    (out / "tracenum" / "eval").mkdir(parents=True)
    shutil.copy2(REPO / "eval" / "numeric_pilot24.json", out / "tracenum" / "eval" / "numeric_pilot24.json")
    (out / "tracenum" / "web" / "public" / "data").mkdir(parents=True)
    shutil.copy2(REPO / "web/public/data/showcase.json", out / "tracenum/web/public/data/showcase.json")
    freeze = json.loads((ROOT / "v2/config/formal_freeze_v1.json").read_text(encoding="utf-8"))
    gold = ROOT / "v2" / freeze["gold"]["path"]
    if sha(gold) != freeze["gold"]["sha256"]:
        sys.exit("gold answers differ from the freeze")
    (out / "v2" / gold.relative_to(ROOT / "v2")).parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(gold, out / "v2" / gold.relative_to(ROOT / "v2"))

    if not args.no_model:
        model = HF_CACHE / HF_MODEL
        revision = (model / "refs" / "main").read_text().strip()
        target = out / "hf" / "hub" / HF_MODEL
        shutil.copytree(model / "snapshots" / revision, target / "snapshots" / revision)
        (target / "refs").mkdir(parents=True)
        (target / "refs" / "main").write_text(revision)

    for name in ("Dockerfile", "requirements-runtime.txt", ".dockerignore"):
        if (REPO / "deploy" / name).is_file():
            shutil.copy2(REPO / "deploy" / name, out / name)

    # v2/src/observability.py stamps every run with `git rev-parse HEAD` and
    # `git status --porcelain` of v2/. The image has no repository, so deploy/git-shim.sh
    # answers those two calls with the values recorded here from the real checkout.
    import subprocess
    v2 = ROOT / "v2"
    commit = subprocess.check_output(["git", "-C", str(v2), "rev-parse", "HEAD"], text=True).strip()
    dirty = bool(subprocess.check_output(["git", "-C", str(v2), "status", "--porcelain"], text=True).strip())
    (out / "v2" / ".git-snapshot").write_text(f"{commit}\n{'dirty' if dirty else 'clean'}\n", encoding="utf-8")
    shim = REPO / "deploy" / "git-shim.sh"
    if shim.is_file():
        shutil.copy2(shim, out / "git-shim.sh")

    (out / "RUNTIME.json").write_text(json.dumps({
        "prefix": args.prefix,
        "v2_git_commit": commit, "v2_git_dirty": dirty,
        "manifest_original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "manifest_derived_sha256": hashlib.sha256(derived_bytes).hexdigest(),
        "manifest_changed_fields": ["local_pdf_path"],
        "assets_locked": len(lock["files"]),
    }, indent=1) + "\n", encoding="utf-8")
    size = sum(p.stat().st_size for p in out.rglob("*") if p.is_file())
    print(f"runtime context: {out} ({size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
