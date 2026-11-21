"""Answer check (product layer, after the frozen pipeline): does the answer present system
text as annual-report text, or quote words the report does not contain?

The corpus contract (corpus/v3/lint.py) guarantees that a chunk's source part is
verbatim report text; this module uses exactly that part as the reference, so a quote
that only exists in a system note does NOT count as supported.

Findings (each {"code", "severity", "detail"}):
  system_note_leak  the answer repeats a corpus system note or placeholder (待核验, 系统注 …)
  verifier_talk     the answer uses the verifier's internal vocabulary (UNSUPPORTED …);
                    the web app folds those sentences into 查看修正过程
  english_internal_value  unknown / None / null in the answer (warn: may be report English)
  unsupported_quote a quoted phrase (“…” 「…」 ‘…’ "…") that occurs in no evidence source text
  page_not_in_evidence  [第N页] cited but no evidence chunk comes from page N

Severity: system_note_leak is "block" (a recording with it is not published; a live
answer gets a visible notice). The rest are "warn": verifier talk is folded by the web
app, and the model also puts its own wording, or a row plus a unit, in quotation marks,
so a human looks at quote warnings before publishing.

check() never edits the answer; callers decide (block a recording, flag a live answer).
"""
from __future__ import annotations

import re
import unicodedata

SYSTEM_MARK = "〔系统注：非年报原文〕"
LOCATION = re.compile(r"^【[^】]*PDF物理页\d+】$")
QUOTED_LINE = re.compile(r"^[^（）：]{1,10}（第\d+页原文）：(?P<text>.+)$")
STRUCTURE = {"表头（重复供定位）：", "本片原始行序：", "本片原始行序"}
# Corpus system text: header placeholders and the fixed wording of the parser's system notes
# (corpus/v3/parse.py render_table_header). check() also matches, dynamically, any longer
# phrase from the system notes in the evidence the model saw, so a new note wording is covered.
SYSTEM_NOTE = re.compile(r"系统注|非年报原文|待核验|章节推定|本表附近未找到单位说明|仅已绑定的行或列|"
                         r"页原文）|系统按所在章节判断|依据原文表题或章节|沿用前页表头|系统依据第\d+页|"
                         r"本表混合合并与母公司口径|无法绑定口径|不能用于数值回答|被投资主体按(?:本片行序|列)|"
                         r"\bnumeric_answer_allowed\b")
# English internal values: a warning only (a report's own English text may contain them).
ENGLISH_VALUE = re.compile(r"\b(?:unknown|None|null)\b")
NOTE_PIECE = re.compile(r"[；;：:（）()、，,]")
# The frozen pipeline shows the model its verifier's feedback and the model sometimes argues
# with it; the web app folds those sentences (web/src/answerRevision.js foldAnswer).
VERIFIER_TALK = re.compile(r"\b(?:UNSUPPORTED|SUPPORTED|verified_operands|NONE|calculation_ok|metric_match|"
                           r"time_match|retrieval_complete)\b|\boperation\s*[=:：]")
QUOTE = re.compile(r"[“「‘\"]([^”」’\"\n]{2,80})[”」’\"]")
PAGE = re.compile(r"第\s*(\d+)\s*页")


def norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or "")).replace("↵", "")


def source_text(chunk_text: str) -> str:
    """The verbatim report part of a chunk: table cells, body text and quoted source lines."""
    out = []
    for line in (chunk_text or "").split("\n"):
        if LOCATION.match(line) or line.startswith(SYSTEM_MARK) or line in STRUCTURE:
            continue
        quoted = QUOTED_LINE.match(line)
        if quoted:
            out.append(quoted["text"])
        elif line.startswith("|"):
            out.extend(cell for cell in line.strip("|").split("|"))
        else:
            out.append(line)
    return "\n".join(out)


def check(answer: str, evidence: list[tuple[int, str]]) -> list[dict]:
    """evidence: (page, chunk text) for every chunk the answer could draw on."""
    findings = []
    leaks = {match[0] for match in SYSTEM_NOTE.finditer(answer or "")}
    said = norm(answer)
    reference = "\x00".join(norm(source_text(text)) for _, text in evidence)
    for _, text in evidence:
        for line in text.split("\n"):
            if line.startswith(SYSTEM_MARK):
                for piece in NOTE_PIECE.split(line[len(SYSTEM_MARK):]):
                    piece = norm(piece)
                    if len(piece) >= 8 and piece in said and piece not in reference:
                        leaks.add(piece)
    for leak in sorted(leaks):
        findings.append({"code": "system_note_leak", "severity": "block", "detail": leak})
    for match in ENGLISH_VALUE.finditer(answer or ""):
        findings.append({"code": "english_internal_value", "severity": "warn", "detail": match[0]})
    for match in VERIFIER_TALK.finditer(answer or ""):
        findings.append({"code": "verifier_talk", "severity": "warn", "detail": match[0]})
    for match in QUOTE.finditer(answer or ""):
        phrase = norm(match[1])
        if re.search(r"[一-鿿]", phrase) and phrase not in reference:
            findings.append({"code": "unsupported_quote", "severity": "warn", "detail": match[1]})
    # Evidence pages include the pages of quoted source lines (e.g. a unit stated on p153).
    pages = {int(p) for p, _ in evidence} | {int(p) for _, text in evidence
                                             for p in re.findall(r"（第(\d+)页原文）", text)}
    for page in sorted({int(p) for p in PAGE.findall(answer or "")} - pages):
        findings.append({"code": "page_not_in_evidence", "severity": "warn", "detail": str(page)})
    return findings


def blocking(findings: list[dict]) -> bool:
    return any(f["severity"] == "block" for f in findings)


def check_state(final: dict) -> list[dict]:
    """Check a pipeline final state (draft_answer + evidence documents)."""
    evidence = [(int(d.metadata.get("page") or 0), d.page_content) for d in final.get("evidence") or []]
    return check(final.get("draft_answer") or "", evidence)


def split_context(text: str) -> list[tuple[int, str]]:
    """The frozen renderer prefixes each chunk with a [第N页] line."""
    parts = re.split(r"^\[第(\d+)页\]\n", text or "", flags=re.M)
    return [(int(parts[i]), parts[i + 1]) for i in range(1, len(parts) - 1, 2)]


def check_run_dir(run_dir) -> dict:
    """Check the final answer of a logged run against every context the model was shown."""
    import json
    from pathlib import Path
    run_dir = Path(run_dir)
    read = lambda name: [json.loads(l) for l in (run_dir / name).read_text(encoding="utf-8").splitlines() if l.strip()]
    answers = read("answers.jsonl")
    evidence = [chunk for c in read("context.jsonl") for chunk in split_context(c.get("text", ""))]
    answer = answers[-1].get("answer_text", "") if answers else ""
    return {"run": run_dir.name, "answer": answer, "findings": check(answer, evidence)}
