"""Corpus contract check: run before building the index; any violation stops the build.

Why: the frozen generation prompt presents every retrieved chunk under the heading
【年报原文】, so the model treats all of a chunk's text as annual-report text. A chunk may
therefore contain only
  1. a location line            【公司 YYYY年年报｜PDF物理页N】
  2. quoted source lines        <标签>（第N页原文）：<text>   -- 标签 is 表题/口径依据/单位 (a
                                单位 line must name a unit); <text> must occur verbatim
                                (whitespace-insensitive) in the RAW extraction of page N
  3. system notes               〔系统注：非年报原文〕...      -- the only place for anything
                                the system inferred; one fixed marker the answer check
                                (backend/answer_check.py) can recognise
  4. table structure markers    表头（重复供定位）： / 本片原始行序：
  5. source body                table rows whose every cell occurs on the chunk's source
                                pages (a cell's lines are joined with the layout glyph ↵),
                                or text that occurs verbatim on its page.
Unknown values are left out, never written as a placeholder.

    python corpus/v3/lint.py                 # all reports in build/corpus_v3
    python corpus/v3/lint.py 002594_2023_v3  # one report
Exit status 1 when any report has a violation. A JSON summary goes to stdout.
"""
from __future__ import annotations

import json
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
BUILD = REPO / "build" / "corpus_v3"

SYSTEM_MARK = "〔系统注：非年报原文〕"
LOCATION = re.compile(r"【(?P<company>[^｜】]+) (?P<year>\d{4})年年报｜PDF物理页(?P<page>\d+)】")
QUOTE = re.compile(r"(?P<label>[^（）：]{1,10})（第(?P<page>\d+)页原文）：(?P<text>.+)")
CELL_BREAK = "↵"  # in-cell line break written by the table renderer; layout only
MARKERS = {"表头（重复供定位）：", "本片原始行序："}
# Placeholders only the system writes: banned in every model-visible line.
BANNED = re.compile(r"待核验|章节推定|TODO")
# Internal English values: banned outside the source body (a report's own English text,
# e.g. an A+H filing, may contain "other" or "None").
BANNED_OUTSIDE_BODY = re.compile(r"\b(?:unknown|other|None|null|nan|NaN|undefined|True|False)\b")
# The only labels a quoted source line may carry, and what its text must contain.
LABELS = {"表题": None, "口径依据": None, "单位": re.compile(r"元")}
PRIMARY = {"资产负债表", "利润表", "现金流量表", "所有者权益变动表"}
STATEMENT_TYPES = PRIMARY | {"other"}
UNITS = {"元", "千元", "万元", "百万元", "亿元", "美元", "港元"}  # parse.UNIT can print USD/HKD
AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?")


def norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


class Pages:
    def __init__(self, directory: Path):
        self.directory = directory
        self._cache: dict[int, str] = {}

    def text(self, page: int) -> str:
        """Reference text of a page from the RAW extraction only (pdfplumber text and the
        cells of its raw table finds), never from the cleaned output being checked."""
        if page not in self._cache:
            path = self.directory / "raw" / f"page_{page:04d}.json"
            if not path.exists():
                self._cache[page] = ""
            else:
                data = json.loads(path.read_text(encoding="utf-8"))
                cells = "\x00".join(c or "" for key in ("tables", "recovery_tables") for t in data.get(key) or []
                                    for r in t["rows"] for c in r["cells"])
                lines = "\x00".join(line.get("text") or "" for line in data.get("lines") or [])
                self._cache[page] = norm(data.get("text", "")) + "\x00" + norm(lines) + "\x00" + norm(cells)
        return self._cache[page]

    def contains(self, pages, fragment: str) -> bool:
        fragment = norm(fragment)
        return not fragment or any(fragment in self.text(p) for p in pages)


def source_pages(record: dict) -> set[int]:
    meta = record["metadata"]
    pages = {int(meta["page"])}
    anchors = meta.get("anchors") or []
    if isinstance(anchors, str):  # older builds stored a repr
        pages.update(int(p) for p in re.findall(r"'source_page': (\d+)", anchors))
    else:
        pages.update(int(a["source_page"]) for a in anchors if a.get("source_page"))
    return pages


def check_record(record: dict, pages: Pages, report: dict) -> list[str]:
    problems = []
    text = record["text"]
    meta = record["metadata"]
    kind = meta.get("type") or record["id"].split(":")[2]
    for match in BANNED.finditer(text):
        problems.append(f"system word {match[0]!r}")
    lines = text.split("\n")
    body_from = 1 if lines and LOCATION.fullmatch(lines[0]) else 0
    for i, line in enumerate(lines):
        in_body = line.startswith("|") if kind == "table" else i >= body_from
        if not in_body:
            for match in BANNED_OUTSIDE_BODY.finditer(line):
                problems.append(f"system word {match[0]!r}")
    if meta.get("statement_type") not in STATEMENT_TYPES:
        problems.append(f"statement_type {meta.get('statement_type')!r} outside the closed set")
    if meta.get("unit") not in UNITS | {None}:
        problems.append(f"unit {meta.get('unit')!r} outside the closed set")
    own = source_pages(record)

    if kind != "table":
        lines = text.split("\n")
        body = lines
        if lines and LOCATION.fullmatch(lines[0]):
            body = lines[1:]
        if not pages.contains(own, "".join(body)):
            problems.append("text body not found verbatim on its page")
        return problems

    lines = text.split("\n")
    if not lines or not (loc := LOCATION.fullmatch(lines[0])):
        problems.append("first line is not the location line")
    elif loc["company"] != report["company"] or loc["year"] != str(report["report_year"]) \
            or int(loc["page"]) != int(meta["page"]):
        problems.append("location line does not match the record")
    in_rows = False
    for line in lines[1:]:
        if line in MARKERS:
            in_rows = True
            continue
        if line.startswith("|"):
            if not in_rows:
                problems.append("table row before a structure marker")
            for cell in line.strip("|").split("|"):
                for part in cell.split(CELL_BREAK):  # the parser's in-cell line-break glyph
                    if part.strip() and not pages.contains(own, part):
                        problems.append(f"cell not on source pages: {part.strip()[:30]!r}")
            continue
        in_rows = False
        if line.startswith(SYSTEM_MARK):
            continue
        quote = QUOTE.fullmatch(line)
        if not quote:
            problems.append(f"header line outside the grammar: {line[:40]!r}")
            continue
        if quote["label"] not in LABELS:
            problems.append(f"label outside the allowed set: {quote['label']!r}")
        elif LABELS[quote["label"]] and not LABELS[quote["label"]].search(quote["text"]):
            problems.append(f"{quote['label']} line without a unit: {quote['text'][:30]!r}")
        if not pages.contains({int(quote["page"])}, quote["text"]):
            problems.append(f"quoted text not on page {quote['page']}: {quote['text'][:30]!r}")
    return problems


def coverage(records: list[dict]) -> dict:
    """Share of primary-statement amount tables that carry a quoted unit line."""
    total = with_unit = 0
    for r in records:
        if r["metadata"].get("statement_type") in PRIMARY and r["id"].split(":")[2] == "table" \
                and AMOUNT.search(r["text"]):
            total += 1
            with_unit += bool(re.search(r"^单位（第\d+页原文）：", r["text"], re.M))
    return {"primary_amount_tables": total, "with_quoted_unit": with_unit}


def lint_report(directory: Path, registry: dict) -> dict:
    report = registry[directory.name.rsplit("_v3", 1)[0]]
    pages = Pages(directory)
    records = [json.loads(line) for line in (directory / "chunks.jsonl").open(encoding="utf-8")]
    kinds = Counter()
    examples = defaultdict(list)
    for record in records:
        for problem in check_record(record, pages, report):
            kind = re.sub(r"[:：].*", "", problem)
            kinds[kind] += 1
            if len(examples[kind]) < 2:
                examples[kind].append(f"{record['id']}: {problem}")
    cov = coverage(records)
    if cov["primary_amount_tables"] and cov["with_quoted_unit"] < cov["primary_amount_tables"]:
        kinds["primary amount table without a quoted unit"] += cov["primary_amount_tables"] - cov["with_quoted_unit"]
    return {"report": directory.name, "chunks": len(records), "violations": dict(kinds),
            "examples": dict(examples), "coverage": cov}


def main(argv: list[str]) -> int:
    reg = json.loads((REPO / "corpus" / "registry.json").read_text(encoding="utf-8"))["reports"]
    registry = {f"{e['code']}_{e['report_year']}": e for e in reg}
    dirs = [BUILD / name for name in argv] if argv else sorted(p for p in BUILD.iterdir()
                                                              if (p / "chunks.jsonl").exists())
    results = [lint_report(d, registry) for d in dirs]
    total = Counter()
    for r in results:
        total.update(r["violations"])
    summary = {"reports": len(results), "chunks": sum(r["chunks"] for r in results),
               "violations": dict(total), "failed_reports": [r["report"] for r in results if r["violations"]],
               "details": results}
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
