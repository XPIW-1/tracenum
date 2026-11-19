"""Offline TraceNum corpus extraction and chunking."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from collections import Counter
from contextlib import ExitStack
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REGISTRY = ROOT / "corpus" / "registry.json"
DEFAULT_OUT = ROOT / "build" / "corpus_v3"
RAW_SCHEMA = "raw-page-v3"
VERSION = "tracenum-v3"
UNIT = re.compile(r"(?:单位[:：]|报表(?:的)?单位为[:：]?)(人民币)?(亿元|万元|千元|美元|港元|元)(?![/／])")
AMOUNT = re.compile(r"^[（(]?[-−－]?\d{1,3}(?:,\d{3})*(?:\.\d{2})[）)]?$|^[（(]?[-−－]?\d+\.\d{2}[）)]?$")
STATEMENTS = ("资产负债表", "利润表", "现金流量表", "所有者权益变动表")
STATEMENT = re.compile("|".join((*STATEMENTS, "股东权益变动表", "股东权益变动")))
NUMBERING = r"(?:[一二三四五六七八九十\d]+[、.．]|[（(][一二三四五六七八九十\d]+[)）])"
TITLE_NUMBERING = re.compile(rf"^(?:{NUMBERING}|[（(][a-z][)）])[.．]?")
TITLE = re.compile(
    rf"^(?:{NUMBERING})?(?P<scope>合并及(?:母)?公司|合并|母公司|公司)?"
    rf"(?P<type>{STATEMENT.pattern})(?P<continued>[（(]续(?:表)?[)）]|[-—－]续(?:表)?|续表)?$"
)
UNIT_ONLY = re.compile(r"人民币(亿元|万元|千元|元)")
SECTION = re.compile(
    rf"^(?:第[一二三四五六七八九十\d]+[章节]|{NUMBERING}|"
    r"审计报告|财务报表附注|财务报表项目注释|公司基本情况|财务报告|财务报表)$"
)
NUMBERED_HEADING = re.compile(rf"^(?:第[一二三四五六七八九十\d]+[章节]|{NUMBERING})[^\d\s].*")
CHAPTER = re.compile(r"^第[一二三四五六七八九十百\d]+[章节](?P<title>[^.．…\d]{1,45})$")
NOTE_HEADING = re.compile(
    rf"^(?:{NUMBERING})?(?P<scope>合并|母公司|公司)财务报表"
    r"(?:(?:主要)?项目(?:注释|附注)|附注)(?:[（(]续[)）]|[-—－]续)?$"
)
OUTLINE_ITEM = re.compile(r"^(?P<number>[一二三四五六七八九十百]+)[、.．][^：:。…\d]{1,45}$")
PAREN_OUTLINE_ITEM = re.compile(r"^[（(](?P<number>[一二三四五六七八九十百]+)[)）][^：:。…\d]{1,45}$")
NOTE_PEER_TITLE = re.compile(rf"^(?:{NUMBERING})?(?:研发支出|合并范围(?:的)?变更|在其他主体中的权益|政府补助|"
                             r"分部信息|与金融工具相关的风险|公允价值.{0,4}披露|关联方及关联交易|股份支付|"
                             r"承诺及或有事项|资产负债表日后事项|其他重要事项|补充资料)$")
METRICS_HEADING = re.compile(rf"^(?:{NUMBERING})?(?:近[一二三四五六七八九十两]+年)?主要会计数据和财务指标$")
METRICS_DEFAULT = re.compile(
    rf"^(?:{NUMBERING})?(?:(?:\d{{4}}年)?(?:度)?分季度主要财务(?:指标|数据)|"
    r"非经常性损益项目(?:及|和)金额|(?:近[一二三四五六七八九十两]+年)?主要会计数据和财务指标)$"
)
DISCLOSURE_CHAPTER = re.compile(r"^(?:公司治理|股份变动及股东情况|重要事项|环境[和与、]社会责任|债券相关情况)")
DISCLOSURE_TITLE = re.compile(
    r"^(?:现聘任的会计师事务所|行业排放标准及.+情况|公司股票简况|"
    r"公司信息|公司基本情况|基本情况简介|联系人和联系方式|信息披露及备置地点|其他有关资料|"
    r"员工情况|利润分配(?:情况|方案)?|(?:公司)?(?:对外)?担保情况|对(?:外)?担保|诉讼(?:事项|情况)|"
    r"委托理财(?:情况)?|募集资金(?:使用情况)?|税收优惠|.*所得税税率纳税主体的[，,]披露情况说明|"
    r"公司报告期内董事[、监事和及高级管理人员]+报酬情况|"
    r"本报告期利润分配及资本公积金转增股本(?:情况|预案)|"
    r"(?:报告期内)?(?:全部有效的)?员工持股计划情况|"
    r"报告期内董事[、监事和及高级管理人员]+在员工持股计划中的持股情况|"
    r"(?:公司|本公司)(?:及其?子公司)?(?:对外)?担保情况(?:表)?|"
    r"内部控制缺陷认定标准|(?:财务|非财务)报告内部控制缺陷认定标准)$"
)
DISCLOSURE_SUBSECTION = re.compile(
    r"^(?:公司信息|公司基本情况|基本情况简介|公司股票简况|联系人和联系方式|信息披露及备置地点|其他有关资料|"
    r"(?:公司)?员工情况|利润分配(?:及资本公积金转增股本)?(?:情况|方案)?|"
    r"董事(?:[、和及]监事)?[、和及]高级管理人员(?:的)?情况|(?:重大)?担保(?:情况)?|"
    r"(?:重大)?诉讼[、和及仲裁]*(?:事项|情况)?|委托理财(?:情况)?|募集资金(?:使用情况)?|"
    r"聘任[、与]?解聘会计师事务所情况)$"
)
TAX_HEADING = re.compile(rf"^(?:{NUMBERING})?主要税种(?:及|和)税率$")
FINANCIAL_DISCLOSURE_TITLE = re.compile(
    r"^(?:关键管理人员(?:报酬|薪酬)|独立非执行董事(?:薪酬|酬金)|"
    r"执行董事[、及和]非执行董事[、及和]监事|董事(?:[、及和]监事)?(?:报酬|薪酬)|"
    r"(?:本集团本年度)?薪酬最高的前[五十](?:位|名雇员)|薪酬介于下列区间的雇员数量|"
    r"关联担保情况|关联方(?:提供的)?担保|本公司作为(?:被)?担保方)$"
)
INVESTEE_NOTE = re.compile(rf"^(?:{NUMBERING})?在其他主体中的权益$")
INVESTEE_SUMMARY = re.compile(r"(?:子公司|合营企业|联营企业|公司).*?(?:主要|汇总|简要)财务信息$")
ENTITY_NAME = re.compile(
    r"^[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9·（）()&.\-]{0,75}?"
    r"(?:有限责任公司|有限公司|公司|集团|Limited|LLC|Ltd\.?)"
    r"(?=$|[（(:：]|\s+[-(\d]|(?:的)?(?:主要|简要)财务信息)", re.I
)
EXPLICIT_SCOPE_LABEL = re.compile(rf"(?:合并|母公司)(?:{STATEMENT.pattern})(?!附注)")
METRICS_EVIDENCE = re.compile(r"归属于(?:上市公司|母公司)(?:股东|所有者)")
SCOPE_KEYWORD = re.compile(r"母公司|合并")
HEADER_YEAR = re.compile(r"((?:19|20)\d{2})(?:年(?:度|\d{1,2}月(?:\d{1,2}日)?)?|[-/.]\d{1,2}[-/.]\d{1,2})?")
PRINTED_YEAR = re.compile(r"(?:19|20)\d{2}年(?:度|\d{1,2}月\d{1,2}日)?")
RELATIVE_PERIOD = {"期末余额", "期初余额", "期末数", "期初数", "年末余额", "年初余额", "本期金额",
                   "上期金额", "本年金额", "上年金额", "本期发生额", "上期发生额", "本期数", "上期数"}
NUMBER = re.compile(r"[-−－+]?\d+(?:\.\d+)?|[（(]?[-−－+]?\d{1,3}(?:,\d{3})+(?:\.\d+)?[）)]?")


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def compact(value):
    return re.sub(r"\s+", "", value)


def extract_table(table, tid, method):
    rows = table.extract(x_tolerance=3, y_tolerance=3)
    return {"table_id": tid, "method": method, "bbox": list(table.bbox),
            "rows": [{"row_id": f"{tid}:r{i:04d}", "cells": cells,
                      "cell_bboxes": [list(c) if c else None for c in table.rows[i].cells]}
                     for i, cells in enumerate(rows)]}


def cell_glyphs(chars, bbox):
    if bbox is None:
        return []
    x0, top, x1, bottom = bbox
    return [c for c in chars if x0 - .2 <= (c["x0"] + c["x1"]) / 2 <= x1 + .2
            and top - .2 <= (c["top"] + c["bottom"]) / 2 <= bottom + .2]


def clean_cell(value, bbox, chars):
    """Only join numeric wraps supported by the glyphs of the SAME physical cell.

    A canonical two-decimal amount and all original nonspace glyphs are required.
    Two complete amounts on separate lines are never joined. Unsupported wraps
    are retained verbatim; no zeros, signs, decimal points or labels are invented.
    """
    if value is None:
        return None, None
    if "\n" not in value:
        return value, None
    parts = [s.strip() for s in value.splitlines() if s.strip()]
    joined = "".join(parts)
    # The PDF drawing order must exactly confirm every character; no pattern-only correction.
    numeric_parts = all(re.fullmatch(r"[\d,().（）\-−－]+", p) for p in parts)
    if numeric_parts and len(parts) > 1 and AMOUNT.fullmatch(joined) and not all(AMOUNT.fullmatch(p) for p in parts):
        glyphs = cell_glyphs(chars, bbox)
        evidence = "".join(c["text"] for c in glyphs if not c["text"].isspace())
        if evidence == joined and len({round(c["top"] / 3) for c in glyphs}) >= 2:
            return joined, {"rule": "same_cell_glyph_numeric_wrap", "before": value, "after": joined,
                            "basis": "exact original PDF glyph sequence within the same cell, canonical amount, >=2 baselines",
                            "glyphs": glyphs, "bbox": bbox}
    # Join Chinese label wraps only within an existing cell, retaining all glyphs.
    if all(not re.search(r"\d", p) for p in parts) and all(re.search(r"[\u4e00-\u9fff]", p) for p in parts):
        return joined, {"rule": "same_cell_label_wrap", "before": value, "after": joined,
                        "basis": "original physical cell boundary; nonspace characters unchanged", "bbox": bbox}
    return value, {"rule": "unresolved_multiline_cell", "before": value, "after": value, "bbox": bbox,
                   "basis": "insufficient exact glyph or financial-format evidence; preserved verbatim"}


def text_spans(text, size=800, overlap=150):
    if not 0 <= overlap < size:
        raise ValueError("Invalid chunk window")
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            split = text.rfind("\n", start + size // 2, end)
            if split > start:
                end = split + 1
        yield start, end, text[start:end]
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def table_groups(rows, size=10):
    # Every row, including empty/null/label-only/two-number rows, participates.
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def readable_cell(value):
    return "" if value is None else value.replace("\n", " ↵ ").replace("|", "¦")


def table_render(rows):
    return "\n".join("| " + " | ".join(readable_cell(v) for v in r["cells"]) + " |" for r in rows)


def report_id(report):
    return f"{report['code']}_{report['report_year']}"


def registry():
    reports = json.loads(REGISTRY.read_text(encoding="utf-8"))["reports"]
    return {report_id(report): report for report in reports}


def pdf_path(report):
    # The current registry uses local_path; also accept the documented field.
    return ROOT.parent / (report.get("local_pdf_path") or report["local_path"])


def verified_pdf(report):
    path = pdf_path(report)
    if digest(path) != report["sha256"]:
        raise ValueError(f"{report_id(report)} PDF SHA-256 does not match registry")
    return path


GEOMETRY = {"vertical_strategy": "text", "horizontal_strategy": "text",
            "min_words_vertical": 3, "text_x_tolerance": 2, "text_y_tolerance": 3}


def extract_page(page, report):
    number = page.page_number
    source_id = f"{report_id(report)}_v3:{report['sha256'][:16]}"
    raw_id = f"{source_id}:p{number:04d}"
    raw = {"schema": RAW_SCHEMA, "source_id": source_id, "raw_id": raw_id,
           "pdf_sha256": report["sha256"], "page": number, "width": page.width,
           "height": page.height, "text": "", "chars": [], "lines": [], "tables": [],
           "recovery_tables": [], "recovery_trigger": None, "image_count": 0, "errors": []}
    try:
        raw["text"] = page.extract_text() or ""
        raw["chars"] = [{k: c.get(k) for k in ("text", "x0", "x1", "top", "bottom", "size", "fontname")}
                        for c in page.chars]
        raw["lines"] = [{"line_id": f"{raw_id}:l{i:04d}", "text": line["text"],
                         "bbox": [line[k] for k in ("x0", "top", "x1", "bottom")]}
                        for i, line in enumerate(page.extract_text_lines())]
        raw["image_count"] = len(page.images)
    except (ValueError, TypeError, RuntimeError, KeyError) as exc:
        raw["errors"].append(f"text: {type(exc).__name__}: {exc}")
    if not raw["text"].strip():
        raw["errors"].append("EMPTY_TEXT_REQUIRES_VISUAL_CHECK")
    try:
        found = page.find_tables()
        raw["tables"] = [extract_table(t, f"{raw_id}:t{i:03d}", "pdfplumber_lines")
                         for i, t in enumerate(found)]
        widths = [len(t.columns) for t in found]
        numeric_lines = sum(bool(re.search(r"\d[\d,]*\.\d+|\d{1,3}(?:,\d{3})+", l["text"]))
                            for l in raw["lines"])
        if widths and max(widths) <= 1:
            raw["recovery_trigger"] = "single_column_fragments"
        elif not found and numeric_lines >= 5:
            raw["recovery_trigger"] = "numeric_layout_without_grid"
        if raw["recovery_trigger"]:
            raw["recovery_settings"] = GEOMETRY
            raw["recovery_tables"] = [extract_table(t, f"{raw_id}:g{i:03d}", "pdfplumber_text_geometry")
                                      for i, t in enumerate(page.find_tables(GEOMETRY))]
    except (ValueError, TypeError, RuntimeError, KeyError) as exc:
        raw["errors"].append(f"table: {type(exc).__name__}: {exc}")
    return raw


def raw_pages(report, out, limit=None, *, cached_only=False):
    """Validate the source even when reusing immutable extraction records."""
    from pypdf import PdfReader

    path = verified_pdf(report)
    reader = PdfReader(path, strict=True)
    if reader.is_encrypted or len(reader.pages) != report["pages"]:
        raise ValueError(f"{report_id(report)} PDF page count or encryption check failed")
    folder = out / "raw"
    if cached_only:
        for number in range(1, (limit or report["pages"]) + 1):
            dest = folder / f"page_{number:04d}.json"
            if not dest.is_file():
                raise FileNotFoundError(f"Missing cached raw page: {dest}")
    folder.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        pdf = None
        for number in range(1, (limit or len(reader.pages)) + 1):
            dest = folder / f"page_{number:04d}.json"
            if dest.exists():
                raw = json.loads(dest.read_text(encoding="utf-8"))
                if (raw["schema"], raw["pdf_sha256"], raw["page"]) != (RAW_SCHEMA, report["sha256"], number):
                    raise ValueError(f"Cached raw page identity mismatch: {dest}")
            else:
                if cached_only:
                    raise FileNotFoundError(f"Missing cached raw page: {dest}")
                if pdf is None:
                    import pdfplumber

                    pdf = stack.enter_context(pdfplumber.open(path))
                page = pdf.pages[number - 1]
                try:
                    raw = extract_page(page, report)
                finally:
                    page.close()
                content = json.dumps(raw, ensure_ascii=False, allow_nan=False) + "\n"
                with dest.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
            yield raw


def line_anchor(page, line, rule):
    return {"source_page": page, "line_id": line["line_id"], "text": line["text"],
            "bbox": line["bbox"], "rule": rule}


def outline_number(match):
    if not match:
        return None
    digits = {char: number for number, char in enumerate("一二三四五六七八九", 1)}
    tens, sep, ones = match["number"].partition("十")
    return digits.get(tens, 1) * 10 + digits.get(ones, 0) if sep else digits.get(tens)


def heading_text(value):
    value = TITLE_NUMBERING.sub("", value).lstrip("#")
    return re.sub(r"(?:[（(]续(?:表)?[)）]|[-—－]续(?:表)?|[:：])$", "", value)


def table_title_line(line):
    value = compact(line["text"])
    label = heading_text(value)
    if (DISCLOSURE_TITLE.fullmatch(label) or FINANCIAL_DISCLOSURE_TITLE.fullmatch(label)
            or INVESTEE_SUMMARY.search(label)):
        return True
    dated_label = re.sub(r"(?:19|20)\d{2}年度?", "", label)
    return (TITLE_NUMBERING.match(value) and 2 <= len(label) <= 65 and
            not re.search(r"\d|[。；，,]|适用|年度报告|单位[:：]", dated_label) and
            not re.fullmatch(r"(?:第.+[章节]|续(?:表)?|合计)", label))


def table_lead(table, lines, top, earliest):
    if table.get("method") == "pdfplumber_text_geometry":
        # Recovery can include the page heading and prose before the real grid.
        # A title must still be above the first extracted numeric body row.
        body_tops = [box[1] for row in table["rows"] if is_body(row) and
                     any(is_amount(cell) and not re.fullmatch(r"[（(]\d+[)）]", compact(cell or ""))
                         for cell in row["cells"])
                     for box in row.get("cell_bboxes", []) if box]
        if body_tops:
            top = max(top, min(body_tops))
    return [line for line in lines if earliest < line["bbox"][1] <= top + 1.5]


def investee_entity(page, table, title, lead):
    names = {}
    for row in table["rows"]:
        for cell in row["cells"]:
            if (match := ENTITY_NAME.match(compact(cell or ""))) and match[0] not in {"本公司", "该公司", "子公司", "母公司"}:
                names[match[0]] = {"rule": "investee_name_cell", "source_page": page,
                                   "row_id": row["row_id"], "text": cell}
    if (match := ENTITY_NAME.match(heading_text(compact(title["text"])))) and not match[0].endswith("子公司"):
        names[match[0]] = title
    if not names and any("上述子公司" in compact(line["text"]) for line in lead):
        for line in lead:
            if match := ENTITY_NAME.match(line["text"].strip()):
                names[match[0]] = line_anchor(page, line, "referenced_subsidiary_name")
    if len(names) == 1:
        return next(iter(names.items()))
    return None, None


class DocumentOutline:
    def __init__(self):
        self.chapter = self.note = self.metrics = None
        self.note_numbering = None
        self.note_number = self.metrics_number = None
        self.note_boundary_in_table = False
        self.boundary = None
        self.metrics_default = self.disclosure = self.disclosure_title = None
        self.disclosure_chapter = False
        self.tax = self.investee_note = self.investee_summary = None

    def advance(self, page, line, *, inside_table=False):
        value = compact(line["text"])
        label = heading_text(value)
        if chapter := CHAPTER.fullmatch(value):
            self.chapter = self.boundary = line_anchor(page, line, "chapter_heading")
            self.note = self.metrics = None
            self.note_numbering = None
            self.note_number = self.metrics_number = None
            self.note_boundary_in_table = False
            self.metrics_default = self.disclosure = self.disclosure_title = None
            self.tax = self.investee_note = self.investee_summary = None
            self.disclosure_chapter = bool(DISCLOSURE_CHAPTER.match(chapter["title"]))
            if self.disclosure_chapter:
                self.disclosure = self.chapter
        elif match := NOTE_HEADING.fullmatch(value):
            scope = "合并" if match["scope"] == "合并" else "母公司"
            self.note = (scope, line_anchor(page, line, "scoped_note_heading"))
            self.note_boundary_in_table = False
            self.note_numbering = PAREN_OUTLINE_ITEM if value.startswith(("(", "（")) else OUTLINE_ITEM
            self.note_number = outline_number(self.note_numbering.fullmatch(value))
            self.metrics = None
        elif METRICS_HEADING.fullmatch(value):
            self.metrics = line_anchor(page, line, "metrics_section_heading")
            self.metrics_number = outline_number(OUTLINE_ITEM.fullmatch(value))
        else:
            # A child list can restart at 一. A following peer must advance
            # the enclosing notes' numbering and keep its delimiter style.
            peer = outline_number(self.note_numbering.fullmatch(value)) if self.note_numbering else None
            named_peer = NOTE_PEER_TITLE.fullmatch(value)
            if (peer is not None and self.note_number is not None and peer > self.note_number
                    and (named_peer or (self.note and peer == self.note_number + 1 and not inside_table))):
                self.note = None
                self.note_number = peer
                self.boundary = line_anchor(page, line, "note_section_boundary")
            elif self.note and self.note_number is None and named_peer:
                self.note = None
                self.boundary = line_anchor(page, line, "note_section_boundary")
            elif (self.note and self.note_number is not None and peer == self.note_number + 1
                  and inside_table):
                # A numbered body row cannot end the note. An alternate view
                # overlapping this boundary still needs independent evidence.
                self.note_boundary_in_table = True
            if self.metrics and OUTLINE_ITEM.fullmatch(value):
                peer = outline_number(OUTLINE_ITEM.fullmatch(value))
                if self.metrics_number is None or (peer is not None and peer > self.metrics_number):
                    self.metrics = None

        if INVESTEE_NOTE.fullmatch(label):
            self.investee_note = line_anchor(page, line, "investee_note_heading")
            self.investee_summary = None
        elif self.investee_note and NOTE_PEER_TITLE.fullmatch(label):
            self.investee_note = self.investee_summary = None
        if self.investee_note and table_title_line(line):
            if INVESTEE_SUMMARY.search(label):
                self.investee_summary = line_anchor(page, line, "investee_summary_heading")
            elif label != value:
                self.investee_summary = None
        if self.chapter and "财务报告" in self.chapter["text"]:
            if label == "税项":
                self.tax = line_anchor(page, line, "tax_note_heading")
            elif self.tax and (NOTE_HEADING.fullmatch(value) or NOTE_PEER_TITLE.fullmatch(label) or
                               (re.match(rf"^{NUMBERING}", value) and label in
                                {"合并财务报表项目注释", "合并财务报表项目附注", "财务报表项目注释",
                                 "合并财务报表主要项目注释", "重要会计政策及会计估计"})):
                self.tax = None
        if self.chapter and "公司简介和主要财务指标" in compact(self.chapter["text"]):
            if METRICS_DEFAULT.fullmatch(value):
                self.metrics_default = line_anchor(page, line, "metrics_table_heading")
            elif OUTLINE_ITEM.fullmatch(value):
                self.metrics_default = None
        if self.chapter and not self.disclosure_chapter and "财务报告" not in self.chapter["text"]:
            if DISCLOSURE_SUBSECTION.fullmatch(label) and TITLE_NUMBERING.match(value):
                self.disclosure = line_anchor(page, line, "disclosure_heading")
                self.disclosure_title = None
            elif self.disclosure and OUTLINE_ITEM.fullmatch(value):
                self.disclosure = self.disclosure_title = None
        if self.disclosure and not inside_table and table_title_line(line):
            self.disclosure_title = line_anchor(page, line, "table_title")

    def table_scope(self, page, table, lines):
        if self.note and self.note_boundary_in_table and not table["primary"]:
            return "unknown", "unknown", [], "fragment_without_scope"
        if self.note:
            scope, heading = self.note
            return scope, "section_heading", [heading], None
        if self.metrics:
            x0, top, x1, bottom = table["bbox"]
            line = next((line for line in lines if top - 1 <= line["bbox"][1] <= bottom
                         and line["bbox"][0] <= x1 and line["bbox"][2] >= x0
                         and METRICS_EVIDENCE.search(compact(line["text"]))), None)
            if line:
                return "合并", "section_heading", [self.metrics, line_anchor(page, line, "metrics_scope_evidence")], None
        if self.metrics_default and table["primary"]:
            return "合并", "section_default", [self.chapter, self.metrics_default], None
        if self.metrics:
            return "unknown", "unknown", [], "metrics_without_attribution"
        if self.chapter and "管理层讨论与分析" in compact(self.chapter["text"]):
            return "合并(章节推定)", "section_default", [self.chapter], None
        return "unknown", "unknown", [], "no_scoped_section" if self.boundary else "before_outline"

    def non_statement_scope(self, page, table, lead):
        row_text = ["".join(compact(cell or "") for cell in row["cells"]) for row in table["rows"]]
        summary = next((line_anchor(page, line, "investee_summary_heading") for line in reversed(lead)
                        if INVESTEE_SUMMARY.search(heading_text(compact(line["text"])))
                        and table_title_line(line)), self.investee_summary)
        if self.investee_note and summary:
            entity, name_anchor = investee_entity(page, table, summary, lead)
            evidence = [self.investee_note, summary]
            if name_anchor and name_anchor not in evidence:
                evidence.append(name_anchor)
            return {"scope": "被投资主体", "scope_source": "investee_summary", "scope_evidence": evidence,
                    "numeric_answer_allowed": entity is not None,
                    **({"entity": entity} if entity else {"entity_unknown": True})}

        tax_title = next((line for line in reversed(lead) if TAX_HEADING.fullmatch(compact(line["text"]))), None)
        tax_header = any("税率" in row and any(key in row for key in ("计税依据", "纳税主体名称", "优惠期间"))
                         for row in row_text)
        financial_title = next((line for line in reversed(lead)
                                if FINANCIAL_DISCLOSURE_TITLE.fullmatch(heading_text(compact(line["text"])))), None)
        if self.tax and (tax_title or tax_header):
            disclosure = self.tax
        elif financial_title and self.chapter and "财务报告" in self.chapter["text"]:
            disclosure = line_anchor(page, financial_title, "disclosure_heading")
        else:
            disclosure = self.disclosure
        if not disclosure:
            return {}
        # An accounting adjustment can appear in the important-matters
        # chapter with consolidated and parent labels outside formal statements.
        if any(EXPLICIT_SCOPE_LABEL.search(compact(line["text"])) for line in lead) or any(
                EXPLICIT_SCOPE_LABEL.search(row) for row in row_text):
            return {}
        title = next((line_anchor(page, line, "table_title") for line in reversed(lead)
                      if line is tax_title or table_title_line(line)), None)
        title = title or (self.disclosure_title if disclosure is self.disclosure else None) or disclosure
        if disclosure is self.disclosure and table["primary"] and title["source_page"] == page:
            self.disclosure_title = title
        evidence = [disclosure] if title == disclosure else [disclosure, title]
        return {"scope": "不适用", "scope_source": "disclosure_heading", "scope_evidence": evidence,
                "table_title": title, "numeric_answer_allowed": True}


def is_amount(cell):
    value = (cell or "").strip()
    # A PDF extract may split a value, but an unresolved wrap is never an amount.
    if (value.startswith("(") and value.endswith(")")) or (value.startswith("（") and value.endswith("）")):
        value = value[1:-1].strip()
    return bool(NUMBER.fullmatch(value))


def is_body(row):
    cells = [compact(cell or "") for cell in row["cells"]]
    return (any(re.search(r"[\u4e00-\u9fff]", cell) and not HEADER_YEAR.fullmatch(cell)
                for cell in cells)
            and any(is_amount(cell) and not HEADER_YEAR.fullmatch(compact(cell or "")) for cell in row["cells"]))


def amount_columns(rows, header_box):
    if not header_box:
        return set()
    matches = set()
    for row in rows:
        for column, box in enumerate(row.get("cell_bboxes", [])):
            if not box or not is_amount(row["cells"][column]):
                continue
            overlap = min(box[2], header_box[2]) - max(box[0], header_box[0])
            width = min(box[2] - box[0], header_box[2] - header_box[0])
            if width > 0 and overlap >= width * 0.9:
                matches.add(column)
    return matches


def header_info(table):
    rows = table["rows"]
    count = max((len(row["cells"]) for row in rows), default=0)
    first_body = next((i for i, row in enumerate(rows) if is_body(row)), len(rows))
    prefix = rows[:min(first_body, 12)]
    periods = [None] * count
    conflicts = set()
    signature = [None] * count
    period_sources = [None] * count
    header_rows = []
    for row in prefix:
        header = False
        cells = [compact(cell or "") for cell in row["cells"]]
        for printed_column, cell in enumerate(cells):
            column = printed_column
            year = HEADER_YEAR.fullmatch(cell)
            relative = cell in RELATIVE_PERIOD
            # A lone year in the leading label cell can be a table-wide caption.
            if (year or relative) and column > 0:
                boxes = row.get("cell_bboxes")
                printed_box = boxes[column] if boxes else None
                aligned = amount_columns(rows[first_body:], printed_box)
                if len(aligned) > 1:
                    # The printed label is retained, but its year is not assigned
                    # to any ambiguous subdivision of a merged header.
                    header = True
                    continue
                if aligned:
                    column = next(iter(aligned))
                new = (cell, int(year[1]) if year else None)
                if periods[column] and periods[column] != new:
                    conflicts.add(column)
                periods[column] = new
                period_sources[column] = {"row_id": row["row_id"], "printed_column": printed_column,
                                          "bbox": printed_box}
                signature[column] = cell
                header = True
            elif re.fullmatch(r"附注[一二三四五六七八九十]*|注释", cell):
                signature[column] = "附注"
                header = True
            elif cell in {"项目", "资产", "负债", "股东权益", "所有者权益", "负债和股东权益", "负债和所有者权益"}:
                signature[column] = "项目"
                header = True
            elif cell in {"股本", "所有者权益合计", "股东权益合计", "资本公积", "未分配利润", "盈余公积"}:
                signature[column] = cell
                header = True
        if header:
            header_rows.append(row)
    for column in conflicts:
        periods[column] = signature[column] = period_sources[column] = None
    return {"column_count": count, "header_signature": signature, "header_rows": header_rows,
            "period_columns": [{"column": i, "label": period[0], "data_year": period[1],
                                **period_sources[i]}
                               for i, period in enumerate(periods) if period],
            "data_years": [period[1] if period else None for period in periods],
            "first_body": first_body}


def title_scope(match):
    printed = match["scope"]
    if printed == "合并":
        return "合并"
    if printed in {"母公司", "公司"}:
        return "母公司"
    return "unknown"


def blockers(lines):
    blocked = []
    for line in lines:
        value = compact(line["text"])
        if (SCOPE_KEYWORD.search(value) or STATEMENT.search(value) or SECTION.fullmatch(value)
                or NUMBERED_HEADING.match(value) or NOTE_HEADING.fullmatch(value) or METRICS_HEADING.fullmatch(value)):
            blocked.append(line)
    return blocked


def row_label(row):
    for cell in row["cells"][:2]:
        if cell and re.search(r"[\u4e00-\u9fff]", cell):
            return compact(cell)
    return ""


SECTION_ORDER = {
    "资产负债表": ("流动资产", "非流动资产", "流动负债", "非流动负债", "所有者权益", "股东权益"),
    "现金流量表": ("经营活动产生的现金流量", "投资活动产生的现金流量", "筹资活动产生的现金流量",
                    "汇率变动对现金", "现金及现金等价物净增加额"),
}

# Printed statement template order. Optional items can be skipped by issuers.
ITEM_ORDER = {
    "资产负债表": """
        流动资产|货币资金|结算备付金|拆出资金|交易性金融资产|衍生金融资产|应收票据|应收账款|应收款项融资|
        预付款项|应收保费|应收分保账款|应收分保合同准备金|其他应收款|应收股利|买入返售金融资产|存货|
        合同资产|持有待售资产|一年内到期的非流动资产|其他流动资产|流动资产合计|
        非流动资产|发放贷款和垫款|债权投资|其他债权投资|长期应收款|长期股权投资|其他权益工具投资|
        其他非流动金融资产|投资性房地产|固定资产|在建工程|生产性生物资产|油气资产|使用权资产|无形资产|
        开发支出|商誉|长期待摊费用|递延所得税资产|其他非流动资产|非流动资产合计|资产总计/资产合计|
        流动负债|短期借款|向中央银行借款|拆入资金|交易性金融负债|衍生金融负债|应付票据|应付账款|
        预收款项|合同负债|卖出回购金融资产款|吸收存款及同业存放|代理买卖证券款|代理承销证券款|
        应付职工薪酬|应交税费|其他应付款|应付股利|应付手续费及佣金|应付分保账款|持有待售负债|
        一年内到期的非流动负债|其他流动负债|流动负债合计|非流动负债|保险合同准备金|长期借款|应付债券|
        租赁负债|长期应付款|长期应付职工薪酬|预计负债|递延收益|递延所得税负债|其他非流动负债|
        非流动负债合计|负债合计/负债总计|所有者权益/股东权益/所有者权益（或股东权益）/所有者权益(或股东权益)|
        实收资本（或股本）/实收资本(或股本)/股本|其他权益工具|资本公积|库存股|其他综合收益|专项储备|
        盈余公积|一般风险准备|未分配利润|少数股东权益|所有者权益合计/股东权益合计/所有者权益（或股东权益）合计|
        负债和所有者权益总计/负债和股东权益总计/负债和所有者权益（或股东权益）总计
    """,
    "现金流量表": """
        经营活动产生的现金流量|销售商品、提供劳务收到的现金|客户存款和同业存放款项净增加额|
        向中央银行借款净增加额|向其他金融机构拆入资金净增加额|收到原保险合同保费取得的现金|收到再保业务现金净额|
        保户储金及投资款净增加额|收取利息、手续费及佣金的现金|拆入资金净增加额|回购业务资金净增加额|
        代理买卖证券收到的现金净额|收到的税费返还|收到其他与经营活动有关的现金|经营活动现金流入小计|
        购买商品、接受劳务支付的现金|客户贷款及垫款净增加额|存放中央银行和同业款项净增加额|
        支付原保险合同赔付款项的现金|拆出资金净增加额|支付利息、手续费及佣金的现金|支付保单红利的现金|
        支付给职工及为职工支付的现金/支付给职工以及为职工支付的现金|支付的各项税费|支付其他与经营活动有关的现金|
        经营活动现金流出小计|经营活动产生的现金流量净额|投资活动产生的现金流量|收回投资收到的现金|
        取得投资收益收到的现金|处置固定资产、无形资产和其他长期资产收回的现金净额|
        处置子公司及其他营业单位收到的现金净额|收到其他与投资活动有关的现金|投资活动现金流入小计|
        购建固定资产、无形资产和其他长期资产支付的现金|投资支付的现金|质押贷款净增加额|
        取得子公司及其他营业单位支付的现金净额|支付其他与投资活动有关的现金|投资活动现金流出小计|
        投资活动产生的现金流量净额|筹资活动产生的现金流量|吸收投资收到的现金|取得借款收到的现金|
        收到其他与筹资活动有关的现金|筹资活动现金流入小计|偿还债务支付的现金|分配股利、利润或偿付利息支付的现金|
        支付其他与筹资活动有关的现金|筹资活动现金流出小计|筹资活动产生的现金流量净额|
        汇率变动对现金及现金等价物的影响|现金及现金等价物净增加额|期初现金及现金等价物余额|期末现金及现金等价物余额
    """,
    "利润表": """
        营业总收入/营业收入|营业总成本/营业成本|税金及附加|销售费用|管理费用|研发费用|财务费用|
        其他收益|投资收益|对联营企业和合营企业的投资收益|以摊余成本计量的金融资产终止确认收益|
        汇兑收益|净敞口套期收益|公允价值变动收益|信用减值损失|资产减值损失|资产处置收益|
        营业利润|营业外收入|营业外支出|利润总额|所得税费用|净利润|持续经营净利润|终止经营净利润|
        其他综合收益的税后净额|不能重分类进损益的其他综合收益|将重分类进损益的其他综合收益|
        外币财务报表折算差额|归属于少数股东的其他综合收益的税后净额|综合收益总额|
        归属于母公司所有者的综合收益总额|归属于少数股东的综合收益总额|每股收益|基本每股收益|稀释每股收益
    """,
    "所有者权益变动表": """
        上年年末余额/上年期末余额|会计政策变更|前期差错更正|本年年初余额/本年期初余额|
        本年增减变动金额/本期增减变动金额|综合收益总额|所有者投入和减少资本|利润分配|所有者权益内部结转|
        专项储备|其他|本年年末余额/本期期末余额
    """,
}
ITEM_RANKS = {statement: {label: rank for rank, variants in enumerate(compact(items).split("|"))
                          for label in variants.split("/")} for statement, items in ITEM_ORDER.items()}
ITEM_PREFIX = re.compile(rf"^(?:(?:{NUMBERING})|(?:加|减|其中)[:：])+")
ITEM_QUALIFIER = re.compile(r"[（(](?:损失|亏损|净亏损|亏损总额|元/股|减少以).*$")


def item_rank(rows, ranks):
    for row in rows:
        label = ITEM_QUALIFIER.sub("", ITEM_PREFIX.sub("", row_label(row))).rstrip("：:")
        if label in ranks:
            yield ranks[label], row


def row_sequence_evidence(previous, table, statement):
    ranks = ITEM_RANKS.get(statement, {})
    tail = list(item_rank(previous["rows"], ranks))
    # Skip ignorable header/qualifier rows, then require two ordered item
    # anchors close to the page break. Skipped optional items are permitted.
    head = list(item_rank(table["rows"][:8], ranks))[:2]
    if not tail or len(head) != 2 or not tail[-1][0] < head[0][0] < head[1][0]:
        return None
    prior = tail[-1][1]
    return {"from_row_id": prior["row_id"], "from_row": row_label(prior),
            "matched_items": [{"row_id": row["row_id"], "text": row_label(row)} for _, row in head]}


def aligned_period_columns(previous, table):
    previous_periods = previous["period_columns"]
    if not previous_periods:
        return None
    note_columns = {i for i, label in enumerate(table["header_signature"]) if label == "附注"}
    columns = set()
    for row in table["rows"][table["first_body"]:]:
        label = row_label(row)
        if not label:
            continue
        label_column = next(i for i, cell in enumerate(row["cells"][:2]) if compact(cell or "") == label)
        for column, cell in enumerate(row["cells"][label_column + 1:], label_column + 1):
            if column not in note_columns and is_amount(cell):
                columns.add(column)
    columns = sorted(columns)
    if len(columns) != len(previous_periods):
        return None
    current = table["period_columns"]
    if current and ([(p["column"], p["label"]) for p in current] !=
                    [(i, old["label"]) for i, old in zip(columns, previous_periods)]):
        return None
    return columns


def sequence_evidence(previous, table, statement):
    """A known next subsection can substantiate a headerless continuation."""
    order = SECTION_ORDER.get(statement, ())
    if not order:
        return None

    def mark(row):
        label = re.sub(rf"^{NUMBERING}", "", row_label(row)).rstrip("：:")
        return next((i for i, heading in enumerate(order) if label == heading), None)

    prior = [(marker, row) for row in previous["rows"] if (marker := mark(row)) is not None]
    initial = [row for row in table["rows"] if row_label(row)
               and compact(row_label(row)) not in {"项目", "资产", "负债和股东权益", "负债和所有者权益"}]
    if not prior or not initial or mark(initial[0]) != prior[-1][0] + 1:
        return None
    return {"from_row_id": prior[-1][1]["row_id"], "from_row": row_label(prior[-1][1]),
            "to_row_id": initial[0]["row_id"], "to_row": row_label(initial[0])}


def header_matches(previous, current):
    # The first label heading can change from assets to liabilities. An optional
    # note label can disappear, but explicit amount-period positions must agree.
    left = previous["header_signature"]
    right = current["header_signature"]
    matching = [(a, b) for a, b in zip(left, right) if a not in (None, "项目", "附注")
                or b not in (None, "项目", "附注")]
    if not matching or not all(a == b for a, b in matching):
        return False
    return all(a == b or {a, b} <= {None, "项目"} for a, b in zip(left, right))


def period_conflict(previous, current):
    old = {p["column"]: p["label"] for p in previous["period_columns"]}
    return any(p["column"] in old and p["label"] != old[p["column"]] for p in current["period_columns"])


def printed_unit(line):
    text = compact(line["text"])
    match = UNIT.search(text)
    if match:
        return match[2]
    bare = UNIT_ONLY.fullmatch(text)
    return bare[1] if bare else None


def carry_printed_periods(table, previous, previous_page):
    """Use an actual earlier header only when its column bounds still bind."""
    if table["period_columns"]:
        return
    periods = [{**p, "source_page": p.get("source_page", previous_page), "rule": "aligned_earlier_printed_header"}
               for p in previous["period_columns"]
               if amount_columns(table["rows"], p["bbox"]) == {p["column"]}]
    table["period_columns"] = periods
    for period in periods:
        table["data_years"][period["column"]] = period["data_year"]


def bind_balance_date(table, page, lines, paired_periods=()):
    periods = table["period_columns"]
    if [p["label"] for p in periods] not in (["期末余额", "期初余额"], ["年末余额", "年初余额"],
                                               ["期末数", "期初数"]):
        return
    dates = [(line, re.fullmatch(r"((?:19|20)\d{2})年12月31日", compact(line["text"]))) for line in lines]
    dated = [(line, int(match[1])) for line, match in dates if match]
    if len(dated) == 1:
        line, year = dated[0]
        evidence = line_anchor(page, line, "annual_balance_sheet_date")
    elif not dated and [p["label"] for p in paired_periods] == [p["label"] for p in periods]:
        evidence = paired_periods[0].get("date_evidence")
        if not evidence:
            return
        year = paired_periods[0]["data_year"]
        evidence = {**evidence, "rule": "paired_balance_sheet_date"}
    else:
        return
    for period, data_year in zip(periods, (year, year - 1)):
        period.update(data_year=data_year, date_evidence=evidence)
        table["data_years"][period["column"]] = data_year


def carry_sequence_periods(table, previous, previous_page, columns):
    if table["period_columns"]:
        for current, old in zip(table["period_columns"], previous["period_columns"]):
            if current["data_year"] is None and old.get("date_evidence"):
                current.update(data_year=old["data_year"], date_evidence=old["date_evidence"])
    else:
        table["period_columns"] = [
            {**old, "source_page": old.get("source_page", previous_page), "column": column,
             "rule": "row_sequence_amount_order"}
            for column, old in zip(columns, previous["period_columns"])
        ]
    for period in table["period_columns"]:
        table["data_years"][period["column"]] = period["data_year"]


def logical_table_top(table):
    if table.get("method") != "pdfplumber_text_geometry":
        return table["bbox"][1]
    first_body = table["first_body"]
    rows = table["header_rows"] or table["rows"][first_body:first_body + 1]
    return min((box[1] for row in rows[:1] for box in row["cell_bboxes"] if box),
               default=table["bbox"][1])


def resolve_segments(pages):
    """Yield pages with table contexts, keeping only the immediately prior table."""
    segments = []
    prior = None
    section_unit = None
    outline = DocumentOutline()
    for raw, cleaned in pages:
        page = raw["page"]
        lines = sorted(raw["lines"], key=lambda line: (line["bbox"][1], line["bbox"][0]))
        active = None
        earliest = -1
        next_line = 0
        for table in cleaned["tables"]:
            table.update(header_info(table))
        ruled_tables = [t["bbox"] for t in cleaned["tables"] if t["primary"] and
                        t.get("method") == "pdfplumber_lines"]
        outline_lines = [(line, any(box[1] + 1.5 < line["bbox"][1] < box[3] for box in ruled_tables))
                         for line in lines]
        for table in sorted(cleaned["tables"], key=logical_table_top):
            info = table
            top = logical_table_top(table)
            while next_line < len(lines) and lines[next_line]["bbox"][1] <= top + 1.5:
                line, inside_table = outline_lines[next_line]
                outline.advance(page, line, inside_table=inside_table)
                next_line += 1
            scope, scope_source, scope_evidence, scope_reason = outline.table_scope(page, table, lines)
            context = {"statement_type": "other", "scope": scope, "scope_source": scope_source, "unit": None,
                       "segment_id": None, "scope_evidence": scope_evidence, "anchors": list(scope_evidence),
                       "numeric_answer_allowed": scope != "unknown"}
            if scope_reason:
                context.update(scope_unknown_reason=scope_reason, outline_heading=outline.boundary)
            table["context"] = context
            if not table["primary"]:
                continue
            if category := outline.non_statement_scope(page, table, table_lead(table, lines, top, earliest)):
                context.pop("scope_unknown_reason", None)
                context.pop("outline_heading", None)
                context.update(category, anchors=list(category["scope_evidence"]))
            before = [line for line in lines if line["bbox"][3] <= top + 1.5]
            # Geometric recovery may place the printed title inside its frame.
            for line in lines:
                if top - 1.5 <= line["bbox"][1] <= top + 3 and TITLE.fullmatch(compact(line["text"])):
                    if line not in before:
                        before.append(line)
            before.sort(key=lambda line: line["bbox"][1])
            local = [line for line in before if line["bbox"][1] > earliest]
            titled = [(line, TITLE.fullmatch(compact(line["text"]))) for line in local]
            title = next(((line, match) for line, match in reversed(titled) if match), None)
            new = None
            evidence = []
            unit_lines = [(line, printed_unit(line)) for line in local]
            for line, unit in unit_lines:
                if unit and "报表" in compact(line["text"]) and "单位为" in compact(line["text"]):
                    section_unit = {**line_anchor(page, line, "financial_section_unit"), "value": unit}
            if section_unit and any((SECTION.fullmatch(compact(line["text"])) or
                                     NUMBERED_HEADING.match(compact(line["text"])))
                                    and ("报表附注" in line["text"] or "报表项目注释" in line["text"])
                                    for line in before):
                section_unit = None
            units = [(line, unit) for line, unit in unit_lines if unit and
                     (not title or line["bbox"][1] >= title[0]["bbox"][1])]
            unit_anchor = None
            if units:
                line, unit = units[-1]
                unit_anchor = {**line_anchor(page, line, "printed_unit"), "value": unit}
            explicit_unit = unit_anchor["value"] if unit_anchor else None
            context["unit"] = explicit_unit

            if title:
                line, match = title
                intervening = [l for l in blockers(before) if l["bbox"][1] > line["bbox"][1]]
                if not intervening:
                    statement = match["type"]
                    if statement.startswith("股东权益"):
                        statement = "所有者权益变动表"
                    scope = title_scope(match)
                    evidence = [line_anchor(page, line, "explicit_statement_title")]
                    if statement == "资产负债表":
                        paired = active or prior
                        paired_periods = ()
                        if (scope == "母公司" and paired and paired[0] == page and
                                (paired[2]["statement_type"], paired[2]["scope"]) == ("资产负债表", "合并")):
                            paired_periods = paired[1]["period_columns"]
                        bind_balance_date(table, page, [l for l in local if l["bbox"][1] > line["bbox"][1]],
                                          paired_periods)
                    if not unit_anchor:
                        unit_anchor = section_unit
                    new = {"segment_id": f"{raw['source_id']}:s{len(segments) + 1:03d}",
                           "statement_type": statement, "scope": scope,
                           "explicit_title_continued": bool(match["continued"]),
                           "unit": unit_anchor["value"] if unit_anchor else None,
                           "period_columns": info["period_columns"],
                           "start_page": page, "end_page": page,
                           "header_signature": info["header_signature"],
                           "evidence": evidence + ([unit_anchor] if unit_anchor else []),
                           "tables": []}
                    segments.append(new)
            elif prior:
                old, prev_table, prev_segment = prior
                blocked_by = blockers(before)
                if page == old + 1 and earliest == -1:
                    same_header = header_matches(prev_table, table)
                    sequence = sequence_evidence(prev_table, table, prev_segment["statement_type"])
                    classic = (info["column_count"] == prev_table["column_count"] and
                               not period_conflict(prev_table, table) and (same_header or sequence))
                    row_sequence = row_sequence_evidence(prev_table, table, prev_segment["statement_type"])
                    columns = aligned_period_columns(prev_table, table) if row_sequence else None
                    gates = {
                        "preceding_heading_or_keyword": bool(blocked_by),
                        "unit_change_or_unconfirmed": bool(explicit_unit and explicit_unit != prev_segment["unit"]),
                        "header_and_sequence_unconfirmed": not (classic or columns),
                    }
                    rejected = [reason for reason, failed in gates.items() if failed]
                    if rejected:
                        if context["scope"] == "unknown":
                            context["scope_unknown_reason"] = "continuation_unconfirmed"
                        if not classic and not columns:
                            if info["column_count"] != prev_table["column_count"]:
                                rejected.append("column_count_mismatch")
                            if period_conflict(prev_table, table):
                                rejected.append("period_conflict")
                        table["blocked_continuation"] = {
                            "source_page": old, "source_table_id": prev_table["table_id"],
                            "table_id": table["table_id"], "reasons": rejected,
                            "preceding_markers": [line_anchor(page, line, "continuation_blocker")
                                                  for line in blocked_by],
                            "column_counts": [prev_table["column_count"], info["column_count"]],
                            "header_signatures": [prev_table["header_signature"], info["header_signature"]],
                            "row_sequence": row_sequence,
                        }
                    else:
                        new = prev_segment
                        rule = ("row_sequence" if not classic else "adjacent_page_matching_header" if same_header
                                else "adjacent_page_row_sequence")
                        evidence = [{"rule": rule,
                                     "source_page": old, "page": page, "source_table_id": prev_table["table_id"],
                                     "table_id": table["table_id"], "column_count": info["column_count"],
                                     "source_header_signature": prev_table["header_signature"],
                                     "header_signature": info["header_signature"],
                                     "source_header_row_ids": [r["row_id"] for r in prev_table["header_rows"]],
                                     "header_row_ids": [r["row_id"] for r in info["header_rows"]],
                                     "row_sequence": row_sequence if rule == "row_sequence" else sequence,
                                     "amount_columns": columns if rule == "row_sequence" else None,
                                     "previous_unit": prev_segment["unit"],
                                     "printed_unit": explicit_unit, "preceding_line_ids": [l["line_id"] for l in before],
                                     "blockers": []}]
                        new["evidence"].extend(evidence)
                        if unit_anchor:
                            new["evidence"].append(unit_anchor)
                        if not info["header_rows"]:
                            table["repeated_header_rows"] = prev_table.get("repeated_header_rows",
                                                                           prev_table["header_rows"])
                            table["header_source_page"] = prev_table.get("header_source_page", old)
                            if rule != "row_sequence":
                                carry_printed_periods(table, prev_table, old)
                        if rule == "row_sequence":
                            carry_sequence_periods(table, prev_table, old, columns)
                        elif table["period_columns"] and any(p.get("date_evidence") for p in prev_table["period_columns"]):
                            current_columns = aligned_period_columns(prev_table, table)
                            if current_columns:
                                carry_sequence_periods(table, prev_table, old, current_columns)
            earliest = table["bbox"][3]
            active = None
            if new:
                new["end_page"] = page
                new["tables"].append({"page": page, "table_id": table["table_id"],
                                      "period_columns": table["period_columns"]})
                scope_evidence = [new["evidence"][0], *evidence] if new["start_page"] != page else evidence
                context.pop("scope_unknown_reason", None)
                context.pop("outline_heading", None)
                for field in ("table_title", "entity", "entity_unknown"):
                    context.pop(field, None)
                context.update(statement_type=new["statement_type"], scope=new["scope"], unit=new["unit"],
                               scope_source="statement_title" if new["scope"] != "unknown" else "unknown",
                               segment_id=new["segment_id"], scope_evidence=scope_evidence,
                               anchors=scope_evidence + [a for a in new["evidence"] if "value" in a],
                               numeric_answer_allowed=new["scope"] != "unknown")
                if new["scope"] == "unknown":
                    context["scope_unknown_reason"] = "statement_title_without_unique_scope"
                active = (page, table, new)
            elif unit_anchor:
                context["anchors"].append(unit_anchor)
        trailing_lines = [line for line in lines if line["bbox"][1] > earliest + 2]
        has_later_heading = any(TITLE.fullmatch(compact(line["text"])) or
                                SECTION.fullmatch(compact(line["text"])) for line in trailing_lines)
        prior = None if has_later_heading else active
        for line, inside_table in outline_lines[next_line:]:
            outline.advance(page, line, inside_table=inside_table)
        scope_table_fragments(cleaned["tables"])
        yield raw, cleaned, segments


def contains(outer, inner):
    return all(a <= b + 2 for a, b in zip(outer[:2], inner[:2])) and all(
        a >= b - 2 for a, b in zip(outer[2:], inner[2:]))


def scope_table_fragments(tables):
    statements = [t for t in tables if t["primary"] and t["context"]["segment_id"]]
    for fragment in tables:
        if fragment["primary"]:
            continue
        context = fragment["context"]
        matches = [table for table in statements if contains(table["bbox"], fragment["bbox"])]
        if len(matches) == 1:
            parent = matches[0]
            source = parent["context"]
            context.pop("scope_unknown_reason", None)
            context.pop("outline_heading", None)
            context.update(scope=source["scope"], scope_source=source["scope_source"],
                           scope_evidence=source["scope_evidence"],
                           numeric_answer_allowed=source["numeric_answer_allowed"],
                           anchors=[*source["anchors"], {"rule": "contained_statement_fragment",
                                                        "table_id": parent["table_id"]}])
            if source["scope"] == "unknown":
                context["scope_unknown_reason"] = source["scope_unknown_reason"]
        elif context["scope"] == "unknown" and context["scope_unknown_reason"] in {"before_outline", "no_scoped_section"}:
            context["scope_unknown_reason"] = "fragment_without_scope"


def glyph_line(raw, line):
    glyphs = [glyph for glyph in cell_glyphs(raw["chars"], line["bbox"])
              if glyph["text"] and not glyph["text"].isspace()]
    return sorted(glyphs, key=lambda glyph: glyph["x0"])


def glyph_box(glyphs):
    return box_union([[g["x0"], g["top"], g["x1"], g["bottom"]] for g in glyphs])


VISIBLE_AMOUNT = r"(?:[（(]?[-−－+]?\d[\d,]*(?:\.\d+)?(?:\s*[）)])?|[-—－])"
AMOUNT_PAIR = re.compile(rf"\s({VISIBLE_AMOUNT})\s+({VISIBLE_AMOUNT})\s*$")


def line_amount_bands(raw, region, header, anchors):
    """Find two recurring visual amount columns when grid fragments are absent."""
    pairs = []
    for line in region:
        match = AMOUNT_PAIR.search(line["text"])
        if line["bbox"][1] <= header["bbox"][1] or not match:
            continue
        if not re.search(r"[\u4e00-\u9fff]", line["text"][:match.start()]):
            continue
        chars = glyph_line(raw, line)
        if "".join(g["text"] for g in chars) != compact(line["text"]) or any(len(g["text"]) != 1 for g in chars):
            continue
        boxes = [glyph_box(chars[len(compact(line["text"][:match.start(i)])):
                                 len(compact(line["text"][:match.end(i)]))]) for i in (1, 2)]
        if all(boxes) and all(abs(b[2] - a[2]) <= 20 for b, a in zip(boxes, anchors)):
            pairs.append((line, boxes))
    if len(pairs) < 3:
        return None
    columns = list(zip(*(boxes for _, boxes in pairs)))
    bands = [(min(a[0], *(b[0] for b in col)), max(a[2], *(b[2] for b in col)))
             for a, col in zip(anchors, columns)]
    return bands, pairs[-1][0]["bbox"][1]


def recover_period_grid(raw, table):
    """Repair geometry when the same printed years each anchor a physical amount band.

    This is a second, independently traceable view of the raw text geometry.
    Keep the default fragments and original text-strategy table alongside it.
    """
    fragments = [fragment for fragment in raw["tables"]
                 if fragment["rows"] and len(fragment["rows"][0]["cells"]) == 1]
    x0, top, x1, bottom = table["bbox"]
    region = [line for line in raw["lines"] if top - 2 <= line["bbox"][1] <= bottom + 1
              and line["bbox"][0] < x1 and line["bbox"][2] > x0 - 25]
    headers = [line for line in region if len(list(PRINTED_YEAR.finditer(compact(line["text"])))) == 2]
    if len(headers) != 1:
        return None
    header = headers[0]
    chars = glyph_line(raw, header)
    printed = "".join(g["text"] for g in chars)
    if printed != compact(header["text"]) or any(len(g["text"]) != 1 for g in chars):
        return None
    tokens = list(PRINTED_YEAR.finditer(printed))
    if not re.fullmatch(r"(?:项目|资产|负债|股东权益|所有者权益|负债和(?:股东|所有者)权益)?"
                        r"(?:附注[一二三四五六七八九十]*)?", printed[:tokens[0].start()]):
        return None
    if printed[tokens[0].end():tokens[1].start()] or printed[tokens[1].end():]:
        return None
    anchors = [glyph_box(chars[token.start():token.end()]) for token in tokens]
    bands = []
    for anchor in anchors:
        matches = [fragment["bbox"] for fragment in fragments if contains(fragment["bbox"], anchor)]
        if len(matches) != 1:
            break
        band = matches[0]
        # Confirm that this is a numeric band with other amount fragments aligned
        # at its right edge on the same page.
        peers = [f for f in fragments if abs(f["bbox"][2] - band[2]) < 2
                 and f["bbox"][1] <= bottom + 2 and f["bbox"][3] >= header["bbox"][1]]
        if not any(is_amount(cell) for peer in peers for row in peer["rows"]
                   for cell in row["cells"] if cell):
            break
        bands.append((min(f["bbox"][0] for f in peers), max(f["bbox"][2] for f in peers)))
    if len(bands) != 2:
        aligned = line_amount_bands(raw, region, header, anchors)
        if not aligned:
            return None
        bands, bottom = aligned
    if bands[0][1] >= bands[1][0]:
        return None
    note = re.search(r"附注[一二三四五六七八九十]*", printed[:tokens[0].start()])
    left = [bands[0][0] - 0.25, bands[1][0] - 0.25]
    if note:
        left.insert(0, chars[note.start()]["x0"] - 10)
    recovered = []
    table_id = table["table_id"] + ":period_grid"
    for line in region:
        if not header["bbox"][1] <= line["bbox"][1] <= bottom + 1:
            continue
        chars = glyph_line(raw, line)
        if "".join(g["text"] for g in chars) != compact(line["text"]):
            return None
        columns = [[] for _ in range(len(left) + 1)]
        for char in chars:
            midpoint = (char["x0"] + char["x1"]) / 2
            columns[sum(midpoint >= edge for edge in left)].append(char)
        cells = ["".join(char["text"] for char in group) for group in columns]
        recovered.append({"row_id": f"{table_id}:r{len(recovered):04d}", "cells": cells,
                          "cell_bboxes": [glyph_box(group) for group in columns],
                          "source_rows": [], "source_line_ids": [line["line_id"]], "repairs": []})
    if not recovered or recovered[0]["cells"][-2:] != [token[0] for token in tokens]:
        return None
    # Reject geometries that do not recover several label/value rows.
    if sum(bool(row["cells"][0]) and any(is_amount(value) for value in row["cells"][-2:])
           for row in recovered[1:]) < 3:
        return None
    bbox = box_union([b for row in recovered for b in row["cell_bboxes"] if b])
    return {"table_id": table_id, "raw_table_id": table["table_id"], "bbox": bbox,
            "method": "pdfplumber_glyph_period_grid", "rows": recovered, "primary": True,
            "geometry_evidence": {"header_line_id": header["line_id"], "period_bboxes": anchors,
                                  "column_x_boundaries": left},
            "raw_variant": "recovery_tables"}


def clean_page(raw):
    tables = []
    issues = [{"page": raw["page"], "code": "PAGE_EXTRACTION_REQUIRES_REVIEW", "detail": error}
              for error in raw["errors"]]
    if raw["image_count"]:
        issues.append({"page": raw["page"], "code": "RASTER_CONTENT_NOT_OCR_VERIFIED",
                       "image_count": raw["image_count"]})
    if raw["recovery_trigger"]:
        issues.append({"page": raw["page"], "code": "GEOMETRY_RECOVERY_REQUIRES_REVIEW",
                       "detail": raw["recovery_trigger"]})
    for variant in ("tables", "recovery_tables"):
        for table in raw[variant]:
            rows = []
            for row in table["rows"]:
                cells, repairs = [], []
                for column, value in enumerate(row["cells"]):
                    after, change = clean_cell(value, row["cell_bboxes"][column], raw["chars"])
                    cells.append(after)
                    if change:
                        change.update(column=column)
                        if change["rule"] == "unresolved_multiline_cell":
                            issues.append({"page": raw["page"], "code": "MULTILINE_CELL_UNRESOLVED",
                                           "table_id": table["table_id"], "row_id": row["row_id"], **change})
                        else:
                            repairs.append(change)
                rows.append({**row, "cells": cells, "repairs": repairs,
                             "source_rows": [row["row_id"]]})
            columns = max((len(row["cells"]) for row in rows), default=0)
            usable = columns >= 2 and any(is_body(row) for row in rows)
            candidate = {**table, "rows": rows, "raw_variant": variant, "primary": usable}
            aligned = recover_period_grid(raw, table) if variant == "recovery_tables" and usable else None
            if aligned:
                candidate["primary"] = False
                tables.append(aligned)
            tables.append(candidate)
    # Retain alternative geometries and fragments in the trace. Only the fuller
    # representation participates in scope/continuation when it contains a fragment.
    for table in tables:
        if table["primary"] and table["raw_variant"] == "tables":
            if any(alt["primary"] and alt["raw_variant"] == "recovery_tables"
                   and contains(alt["bbox"], table["bbox"]) for alt in tables):
                table["primary"] = False
    tables.sort(key=lambda table: (table["bbox"][1], table["bbox"][0], table["table_id"]))
    text_parts, offset, lines = [], 0, []
    for line in raw["lines"]:
        text_parts.append(line["text"])
        end = offset + len(line["text"])
        lines.append({**line, "start": offset, "end": end})
        offset = end + 1
    return {"schema": "cleaned-page-v3", "page": raw["page"], "raw_id": raw["raw_id"],
            "raw_ref": f"raw/page_{raw['page']:04d}.json",
            "verbatim_text": raw["text"], "text": "\n".join(text_parts) if lines else raw["text"],
            "text_lines": lines, "tables": tables, "issues": issues}


def box_union(boxes):
    if not boxes:
        return None
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def make_chunks(raw, cleaned, report):
    page = raw["page"]
    base = {"code": report["code"], "company": report["company"], "report_year": report["report_year"],
            "corpus_id": f"{report_id(report)}_v3", "source_id": raw["source_id"], "page": page,
            "pdf_sha256": raw["pdf_sha256"],
            "pdf_path": report.get("local_pdf_path") or report["local_path"],
            "raw_ref": cleaned["raw_ref"], "cleaned_ref": f"cleaned/page_{page:04d}.json",
            "status": "candidate_pending_manual_review", "index_ready": False}

    def record(kind, text, metadata, origin):
        origin_json = json.dumps([VERSION, origin, text], ensure_ascii=False, sort_keys=True)
        key = hashlib.sha256(origin_json.encode("utf-8")).hexdigest()[:20]
        chunk_id = f"{base['corpus_id']}:p{page:04d}:{kind}:{key}"
        return {"id": chunk_id, "text": text, "origin": origin,
                "metadata": {**base, **metadata, "type": kind, "chunk_id": chunk_id}}

    for table in cleaned["tables"]:
        ctx = table["context"]
        headers = table.get("repeated_header_rows", table["header_rows"])
        raw_table_id = table.get("raw_table_id", table["table_id"])
        anchors = [*ctx["anchors"], {"rule": "table_geometry", "source_page": page,
                                    "bbox": table["bbox"], "raw_table_id": raw_table_id}]
        if headers:
            anchors.append({"rule": "printed_table_header", "source_page": table.get("header_source_page", page),
                            "row_ids": [row["row_id"] for row in headers],
                            "line_ids": [line for row in headers for line in row.get("source_line_ids", [])]})
        for rows in table_groups(table["rows"]):
            origin = {"table_id": table["table_id"], "raw_variant": table["raw_variant"],
                      "raw_table_id": raw_table_id,
                      "row_ids": [row["row_id"] for row in rows],
                      "header_row_ids": [row["row_id"] for row in headers],
                      "source_line_ids": [line for row in rows for line in row.get("source_line_ids", [])]}
            text = (f"【{base['company']} {base['report_year']}年年报｜PDF物理页{page}】\n"
                    f"报表类型：{ctx['statement_type']}；财务口径：{ctx['scope']}；单位：{ctx['unit'] or '待核验'}\n")
            if title := ctx.get("table_title"):
                text += f"引用表题（PDF物理页{title['source_page']}）：{title['text']}\n"
            if entity := ctx.get("entity"):
                text += f"被投资主体：{entity}\n"
            if headers:
                text += "表头（重复供定位）：\n" + table_render(headers) + "\n"
            text += "本片原始行序：\n" + table_render(rows)
            metadata = {**ctx, **origin, "anchors": anchors,
                        "bbox": table["bbox"], "data_years": table["data_years"],
                        "period_columns": table["period_columns"], "extraction_method": table["method"],
                        "context": ctx}
            yield record("table", text, metadata, origin)
    for start, end, text in text_spans(cleaned["text"]):
        lines = [line for line in cleaned["text_lines"] if line["start"] < end and line["end"] > start]
        origin = {"text_start": start, "text_end": end,
                  "source_line_ids": [line["line_id"] for line in lines]}
        metadata = {"statement_type": "other", "scope": "unknown", "unit": None, "segment_id": None,
                    "table_id": None, "row_ids": [], "data_years": [], "scope_evidence": [],
                    "bbox": box_union([line["bbox"] for line in lines]) or [0, 0, raw["width"], raw["height"]],
                    "numeric_answer_allowed": False,
                    "anchors": [line_anchor(page, line, "raw_text_line") for line in lines]}
        yield record("text", text, metadata, origin)


def parse_report(report, output, pages=None, baseline=None, *, cached_only=False):
    if pages is not None and not 1 <= pages <= report["pages"]:
        raise ValueError(f"--pages must be between 1 and {report['pages']} for {report_id(report)}")
    output = output.resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("--out must be inside the TraceNum workspace")
    out = output / f"{report_id(report)}_v3"
    target_pages = pages if pages is not None else report["pages"]
    existing_qc = out / "qc.json"
    round2_baseline = None
    if existing_qc.is_file():
        previous_qc = json.loads(existing_qc.read_text(encoding="utf-8"))
        if target_pages < previous_qc["pages_processed"]:
            raise ValueError(f"Use a different --out for the smaller subset of {report_id(report)}")
        if baseline is None:
            baseline = previous_qc.get("round1_baseline")
        round2_baseline = round2_snapshot(out, previous_qc)
    start = time.perf_counter()
    raw_count = 0
    chunks = Counter()
    summaries = []
    segments = []
    # Hash/page validation runs at the first iteration, before derived writes.
    raw_stream = raw_pages(report, out, pages, cached_only=cached_only)
    with ExitStack() as stack:
        stack.callback(raw_stream.close)
        for raw, cleaned, segments in resolve_segments((raw, clean_page(raw)) for raw in raw_stream):
            if not raw_count:
                streams = {name: stack.enter_context((out / f"{name}.jsonl").open("w", encoding="utf-8", newline="\n"))
                           for name in ("chunks", "trace", "issues")}
            raw_count += 1

            def emit(name, value):
                streams[name].write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")

            cleaned["raw_sha256"] = digest(out / cleaned["raw_ref"])
            for table in cleaned["tables"]:
                table["chunk_count"] = len(table_groups(table["rows"]))
                if table["context"]["scope"] == "unknown" and table["primary"]:
                    cleaned["issues"].append({"page": raw["page"], "code": "TABLE_SCOPE_NOT_ESTABLISHED",
                                              "table_id": table["table_id"],
                                              "reason": table["context"]["scope_unknown_reason"]})
                if table.get("blocked_continuation"):
                    cleaned["issues"].append({"page": raw["page"], "code": "CONTINUATION_REJECTED",
                                              **table["blocked_continuation"]})
            for issue in cleaned["issues"]:
                emit("issues", issue)
            write_json(out / "cleaned" / f"page_{raw['page']:04d}.json", cleaned)
            for chunk in make_chunks(raw, cleaned, report):
                m = chunk["metadata"]
                chunks[m["type"]] += 1
                emit("chunks", chunk)
                emit("trace", {"chunk_id": chunk["id"], "page": m["page"], "type": m["type"],
                               "raw_id": raw["raw_id"], "raw_ref": m["raw_ref"], "cleaned_ref": m["cleaned_ref"],
                               "origin": chunk["origin"], "context_provenance": m["anchors"]})
            summaries.append(summarize_page(raw, cleaned))
    duration = time.perf_counter() - start
    write_json(out / "segments.json", segments)
    result = report_qc(report, summaries, segments, chunks, duration)
    if baseline is not None:
        result["round1_baseline"] = baseline
    if round2_baseline is not None:
        result["round2_baseline"] = round2_baseline
    write_json(out / "qc.json", result)
    return result


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--ids", required=True, help="Comma-separated code_year identifiers")
    cli.add_argument("--out", type=Path, default=DEFAULT_OUT)
    cli.add_argument("--pages", type=int, help="Process the first N physical pages of each input")
    cli.add_argument("--baseline", type=Path, help="Previous pilot metrics keyed by report ID")
    cli.add_argument("--cached-only", action="store_true", help="Require all raw pages to be cached")
    args = cli.parse_args()
    reports = registry()
    ids = list(dict.fromkeys(item.strip() for item in args.ids.split(",")))
    if any(identifier not in reports for identifier in ids):
        cli.error(f"unknown report id(s): {', '.join(i for i in ids if i not in reports)}")
    baseline = json.loads(args.baseline.read_text(encoding="utf-8")) if args.baseline else {}
    results = []
    for identifier in ids:
        result = parse_report(reports[identifier], args.out, args.pages, baseline.get(identifier),
                              cached_only=args.cached_only)
        results.append(result)
        print(f"{identifier}: {result['pages_processed']} pages, {result['runtime_seconds']:.2f}s", flush=True)
    write_pilot(args.out / "QC_PILOT.md", results)


if __package__:
    from .qc import report_qc, round2_snapshot, summarize_page, write_pilot
else:
    from qc import report_qc, round2_snapshot, summarize_page, write_pilot


if __name__ == "__main__":
    main()


