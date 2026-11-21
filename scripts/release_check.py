"""Release gate: everything a release depends on, checked in one place. Exit 1 on any FAIL.

    python scripts/release_check.py [--eval eval/results/<file>.json] [--python <env python>]

Checks (each prints PASS / FAIL / WARN with the reason):
  tests      all Python test files (with --python: the full model environment, so
             test_api_live runs too) and the web JS tests
  corpus     corpus/v3/lint.py: the chunk-text contract holds for every report
  index      every Chroma collection was built from the current chunks (sha256 in the
             collection metadata, read straight from chroma.sqlite3)
  featured   every featured v3 recording has provenance, was made from the current chunks,
             has no blocking answer-check finding, and its visible answer (as the web app
             folds it) contains no system text
  eval       the numeric eval rows were all produced from the current chunks; reports the
             original and strict grading and visible system text
  git        working tree clean and HEAD on the remote (a release is reproducible)
  line-endings  deploy files that run on Linux are LF
  secrets    no key-like strings or private-key blocks in tracked files
On success, writes build/release_manifest.json: commit, chunks sha256 per report, index
check, eval summary and model fingerprints seen.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "eval"))
CORPUS = ROOT / "build" / "corpus_v3"
DB = ROOT / "build" / "vector_v3"
RECORDINGS = ROOT / "backend" / "recordings"
DEFAULT_EVAL = ROOT / "eval" / "results" / "full431_latest.json"
SECRET = re.compile(r"sk-[A-Za-z0-9]{24,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|(?i:api[_-]?key)\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}")

results: list[tuple[str, str, str]] = []


def report(name: str, status: str, detail: str = "") -> None:
    results.append((name, status, detail))
    print(f"[{status}] {name}: {detail}", flush=True)


def run(cmd, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", **kw)


def registry_keys() -> list[str]:
    reports = json.loads((ROOT / "corpus" / "registry.json").read_text(encoding="utf-8"))["reports"]
    return [f"{e['code']}_{e['report_year']}" for e in reports if e.get("status") != "MISSING"]


def check_tests(python: str) -> None:
    env_note = "full environment" if python != sys.executable else "current interpreter (test_api_live may need --python)"
    modules = sorted(f"tests.{p.stem}" for p in (ROOT / "tests").glob("test_*.py"))
    out = run([python, "-m", "unittest", *modules],
              env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8", "HF_HUB_OFFLINE": "1"})
    tail = (out.stderr or out.stdout).strip().splitlines()[-1:] or ["no output"]
    summary = next((l for l in (out.stderr or "").splitlines() if l.startswith("Ran ")), "")
    report("tests/python", "PASS" if out.returncode == 0 else "FAIL", f"{summary} {tail[0]} ({env_note})")
    js = run(["node", "--test", "web/tests/answerRevision.test.js"])
    passed = re.search(r"# pass (\d+)|ℹ pass (\d+)", js.stdout)
    report("tests/web", "PASS" if js.returncode == 0 else "FAIL",
           f"{(passed[1] or passed[2]) if passed else '?'} passed")


def check_corpus() -> None:
    out = run([sys.executable, "corpus/v3/lint.py"], env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"})
    try:
        summary = json.loads(out.stdout)
    except json.JSONDecodeError:
        report("corpus", "FAIL", f"lint did not run: {out.stderr[-300:]}")
        return
    report("corpus", "PASS" if out.returncode == 0 else "FAIL",
           f"{summary['reports']} reports, {summary['chunks']} chunks, violations {summary['violations'] or 'none'}")


def check_index(current: dict[str, str]) -> dict:
    path = DB / "chroma.sqlite3"
    if not path.is_file():
        report("index", "FAIL", f"{path} missing")
        return {}
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    rows = con.execute("SELECT c.name, m.str_value FROM collections c JOIN collection_metadata m "
                       "ON m.collection_id = c.id WHERE m.key = 'chunks_sha256'").fetchall()
    con.close()
    built = {name.removeprefix("v3_"): sha for name, sha in rows}
    stale = [k for k in current if built.get(k) != current[k]]
    report("index", "FAIL" if stale else "PASS",
           f"{len(current) - len(stale)}/{len(current)} collections built from the current chunks"
           + (f"; stale: {', '.join(stale[:6])}{'…' if len(stale) > 6 else ''}" if stale else ""))
    return built


def visible(answers: list[str]) -> list[str]:
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(answers, f, ensure_ascii=False)
    out = run(["node", "web/scripts/visible_answers.mjs", f.name])
    Path(f.name).unlink(missing_ok=True)
    if out.returncode:
        raise RuntimeError(out.stderr[-300:])
    return json.loads(out.stdout)


def system_text(text: str) -> list[str]:
    import answer_check
    return [m[0] for p in (answer_check.SYSTEM_NOTE, answer_check.VERIFIER_TALK) for m in p.finditer(text)]


def check_featured(current: dict[str, str]) -> set[str]:
    import answer_check
    featured = json.loads((RECORDINGS / "featured_v3.json").read_text(encoding="utf-8"))
    prov_path = RECORDINGS / "provenance.json"
    prov = json.loads(prov_path.read_text(encoding="utf-8")) if prov_path.is_file() else {}
    problems, warnings, fingerprints, answers = [], [], set(), {}
    for key, rid in featured.items():
        entry = prov.get(rid)
        if not entry:
            problems.append(f"{key}: no provenance")
        else:
            fingerprints.update(entry.get("model_fingerprints", []))
            if entry.get("chunks_sha256") != current.get(key):
                problems.append(f"{key}: recorded from other chunks")
            if answer_check.blocking(entry.get("findings", [])):
                problems.append(f"{key}: blocking finding")
            warnings += [f"{key}: {f['code']} {f['detail']}" for f in entry.get("findings", []) if f["severity"] == "warn"
                         and f["code"] != "verifier_talk"]
        record = json.loads((RECORDINGS / f"{rid}.json").read_text(encoding="utf-8"))
        # the full answer is in the final event (summary.answer is cut to 240 characters)
        finals = [e["event"]["result"] for e in record.get("events", []) if e["event"].get("type") == "final"]
        answers[key] = (finals[-1].get("answer") if finals else "") or ""
    for key, text in zip(answers, visible(list(answers.values()))):
        if leaked := system_text(text):
            problems.append(f"{key}: visible system text {sorted(set(leaked))}")
    report("featured", "FAIL" if problems else "PASS",
           f"{len(featured)} recordings" + (f"; {'; '.join(problems[:8])}" if problems else ""))
    if warnings:
        report("featured/quotes", "WARN", "review: " + "; ".join(warnings[:6]))
    return fingerprints


def check_eval(path: Path, current: dict[str, str]) -> dict:
    if not path.is_file():
        report("eval", "FAIL", f"{path.relative_to(ROOT)} missing (run eval/run_numeric.py)")
        return {}
    rows = json.loads(path.read_text(encoding="utf-8"))
    stale = [r["id"] for r in rows if r.get("chunks_sha256") != current.get(f"{r['code']}_{r['report_year']}")]
    from strict_grade import strict_row
    gold = {q["id"]: q for q in json.loads((ROOT / "eval" / "numeric_gold.json").read_text(encoding="utf-8"))["questions"]}
    strict = Counter(strict_row(r, gold.get(r["id"], {}))["strict_verdict"] for r in rows)
    lenient = Counter(r["verdict"] for r in rows)
    leaks = sum(bool(system_text(v)) for v in visible([r.get("answer") or "" for r in rows]))
    fingerprints = sorted({fp for r in rows for fp in r.get("model_fingerprints", [])})
    summary = {"file": path.name, "n": len(rows), "lenient": dict(lenient), "strict": dict(strict),
               "visible_system_text": leaks, "model_fingerprints": fingerprints}
    status = "FAIL" if stale or leaks or lenient.get("error") else "PASS"
    report("eval", status, f"{len(rows)} rows; lenient {dict(lenient)}; strict {dict(strict)}; "
           f"visible system text {leaks}" + (f"; {len(stale)} rows from other chunks" if stale else ""))
    return summary


def check_git() -> dict:
    dirty = run(["git", "status", "--porcelain", "--untracked-files=no"]).stdout.strip()
    head = run(["git", "rev-parse", "HEAD"]).stdout.strip()
    remote = run(["git", "branch", "-r", "--contains", head]).stdout.strip()
    report("git", "FAIL" if dirty or not remote else "PASS",
           f"HEAD {head[:8]}" + ("; uncommitted changes" if dirty else "") + ("" if remote else "; not on any remote branch"))
    return {"commit": head, "dirty": bool(dirty), "on_remote": bool(remote)}


def check_line_endings() -> None:
    """Files that run on the Linux server must be LF: a CR after a Dockerfile line
    continuation breaks the build, and a CRLF .gitattributes had silently stopped
    enforcing this (git read "eol=lf" + CR as an unknown value)."""
    candidates = [*(ROOT / "deploy").iterdir(), ROOT / ".gitattributes"]
    files = [p for p in candidates if p.is_file() and (p.suffix in {"", ".sh", ".yaml"}
                                                         or p.name.startswith(("Caddyfile", "Dockerfile")))]
    bad = [p.name for p in files if b"\r" in p.read_bytes()]
    report("line-endings", "FAIL" if bad else "PASS",
           f"{len(files)} server files" + (f"; CRLF in {bad}" if bad else ""))


def check_secrets() -> None:
    files = run(["git", "ls-files"]).stdout.splitlines()
    hits = []
    for name in files:
        path = ROOT / name
        if path.suffix in {".pdf", ".png", ".jpg", ".woff2", ".sqlite3"} or not path.is_file() or path.stat().st_size > 5_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if SECRET.search(text):
            hits.append(name)
    report("secrets", "FAIL" if hits else "PASS", f"{len(files)} tracked files" + (f"; check {hits}" if hits else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--eval", type=Path, default=DEFAULT_EVAL)
    ap.add_argument("--python", default=sys.executable, help="interpreter with the full model environment")
    args = ap.parse_args()
    import provenance
    current = {k: provenance.chunks_sha256(k) for k in registry_keys()}
    check_tests(args.python)
    check_corpus()
    built = check_index(current)
    fingerprints = check_featured(current)
    eval_summary = check_eval(args.eval if args.eval.is_absolute() else ROOT / args.eval, current)
    git = check_git()
    check_line_endings()
    check_secrets()
    failed =[n for n, s, _ in results if s == "FAIL"]
    if not failed:
        manifest = {"git": git, "chunks_sha256": current, "index_matches": built == current,
                    "eval": eval_summary,
                    "model_fingerprints": sorted(fingerprints | set(eval_summary.get("model_fingerprints", []))),
                    "lint_sha256": hashlib.sha256((ROOT / "corpus" / "v3" / "lint.py").read_bytes()).hexdigest()}
        (ROOT / "build" / "release_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                                                             encoding="utf-8")
    print(f"\n{'RELEASE BLOCKED: ' + ', '.join(failed) if failed else 'ALL CHECKS PASSED'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
