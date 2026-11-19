"""Build the owner's sign-off page: original PDF page images next to what the parser decided.

    python corpus/v3/signoff.py          # -> build/signoff/index.html (open locally)

~30 items chosen by risk, not at random: restated comparatives, parent/consolidated
switches, titles printed on the previous page, row-sequence continuations, investee
tables, section-default and not-applicable scopes, blocked tables, plus a few random
statement pages. The page writes the verdicts as JSON for the owner to paste back.
"""
from __future__ import annotations

import html
import json
import random
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO.parent
CORPUS = REPO / "build" / "corpus_v3"
OUT = REPO / "build" / "signoff"
REG = {f"{e['code']}_{e['report_year']}": e for e in
       json.loads((REPO / "corpus" / "registry.json").read_text(encoding="utf-8"))["reports"]}


def load(rid):
    d = CORPUS / f"{rid}_v3"
    segs = json.loads((d / "segments.json").read_text(encoding="utf-8"))
    segs = segs if isinstance(segs, list) else segs.get("segments", [])
    qc = json.loads((d / "qc.json").read_text(encoding="utf-8"))
    with (d / "chunks.jsonl").open(encoding="utf-8") as stream:
        chunks = [json.loads(line)["metadata"] for line in stream if line.strip()]
    return segs, qc, chunks


def seg(segs, stype, scope):
    return next((s for s in segs if s.get("statement_type") == stype and s.get("scope") == scope), None)


def cached_page(rid, directory, page):
    return json.loads((CORPUS / f"{rid}_v3" / directory / f"page_{page:04d}.json").read_bytes())


def describe_table(rid, page, table_id):
    tables = sorted(cached_page(rid, "cleaned", page)["tables"], key=lambda t: (t["bbox"][1], t["bbox"][0]))
    table = next(t for t in tables if t["table_id"] == table_id)
    primary = [t for t in tables if t["primary"]]
    order = primary if table["primary"] else tables
    index = order.index(table)
    position = (("上方" if index == 0 else "下方" if index == len(order) - 1 else "中部")
                if len(order) > 1 else "")
    marker = "第" if table["primary"] else "备选取表第"
    description = f"本页{position}{marker}{index + 1}张表（{table_id.rsplit(':', 1)[-1]}）"
    context = table["context"]
    if title := context.get("table_title"):
        description += f"「{title['text']}」"
    else:
        headers = table.get("header_rows") or table["rows"][:1]
        first_row = "｜".join(" ".join(cell.split()) for cell in headers[0]["cells"] if cell and cell.strip())
        if first_row:
            description += f"（表格开头「{first_row[:95]}」）"

    earlier_title = next((a for a in context["scope_evidence"]
                          if a["rule"] == "explicit_statement_title" and a["source_page"] < page), None)
    continuation = next((a for a in context["scope_evidence"]
                         if a.get("source_table_id") and a["source_page"] == page - 1), None)
    if continuation:
        previous = cached_page(rid, "cleaned", page - 1)
        source = next(t for t in previous["tables"] if t["table_id"] == continuation["source_table_id"])
        headers = source["header_rows"] or source.get("repeated_header_rows", [])
        header_page = page - 1 if source["header_rows"] else source.get("header_source_page")
        if headers and header_page:
            line_ids = {lid for row in headers for lid in row.get("source_line_ids", [])}
            original = cached_page(rid, "raw", header_page)["lines"]
            texts = [line["text"].strip() for line in original if line["line_id"] in line_ids]
            if not texts:
                texts = ["｜".join(" ".join(cell.split()) for cell in row["cells"] if cell) for row in headers]
            description += f"。第{page - 1}页续接源表"
            if header_page != page - 1:
                description += f"沿用第{header_page}页"
            description += f"表头「{' / '.join(texts)}」"
    if earlier_title:
        description += f"。第{earlier_title['source_page']}页报表标题原文「{earlier_title['text']}」"
    return description


def pick():
    rng = random.Random(20261119)
    items = []

    @lru_cache(maxsize=None)
    def report(rid):
        return load(rid)

    def add(rid, page, table_id, kind, claim, ask):
        claim = f"{describe_table(rid, page, table_id)}。{claim}"
        items.append({"id": f"{rid}:p{page}:{len(items)}", "report": rid, "page": int(page), "kind": kind,
                      "table_id": table_id, "claim": claim, "ask": ask})

    def add_statement(rid, s, kind, claim, ask):
        table = s["tables"][0]
        add(rid, table["page"], table["table_id"], kind, claim, ask)

    # 1. Restated comparatives (2024 reports of BYD, Great Wall, CATL)
    for rid in ("002594_2024", "601633_2024", "300750_2024"):
        segs, _, _ = report(rid)
        s = seg(segs, "利润表", "合并")
        add_statement(rid, s, "追溯调整", f"合并利润表，单位 {s.get('unit')}，含 2023 年比较列。",
                      "这张表是合并利润表吗？2023 年比较列是否为调整后的数字（与 2023 年报原数不同）？")
    # 2. Parent-company statements (scope switch), one per company family
    for rid in ("002594_2025", "002466_2025", "002812_2024", "301358_2025", "688005_2023", "300014_2024"):
        segs, _, _ = report(rid)
        s = seg(segs, "现金流量表", "母公司") or seg(segs, "资产负债表", "母公司")
        add_statement(rid, s, "母公司口径", f"{s['statement_type']}，口径为母公司。",
                      "这张表是母公司（或「公司」）报表吗？")
    # 3. Title printed on the previous page
    catl, _, _ = report("300750_2023")
    add_statement("300750_2023", seg(catl, "利润表", "合并"), "标题在上一页", "合并利润表正文。",
                  "这张表是上一页标题所指的合并利润表吗？")
    # 4. Row-sequence continuations
    conts = []
    for rid in sorted(REG):
        _, qc, _ = report(rid)
        conts += [(rid, c) for c in qc.get("accepted_continuations", []) if c.get("rule") == "row_sequence"]
    for rid, c in rng.sample(conts, min(5, len(conts))):
        add(rid, c["page"], c["table_id"], "跨页续表",
            f"按行序识别为第{c['source_page']}页报表的续表。",
            "这张表和上一页续接源表的口径、单位一致吗？")
    # 5. Investee, section-default 合并, not-applicable, blocked
    pools = {"被投资主体": [], "章节推定合并": [], "不适用": [], "未知已阻断": []}
    for rid in sorted(REG):
        _, _, chunks = report(rid)
        for m in chunks:
            if m.get("type") != "table":
                continue
            if m.get("scope") == "被投资主体" and m.get("entity"):
                pools["被投资主体"].append((rid, m))
            elif m.get("scope") == "合并" and m.get("scope_source") == "section_heading":
                pools["章节推定合并"].append((rid, m))
            elif m.get("scope") == "不适用":
                pools["不适用"].append((rid, m))
            elif m.get("scope") == "unknown" and m.get("scope_unknown_reason") != "fragment_without_scope":
                pools["未知已阻断"].append((rid, m))
    asks = {"被投资主体": "这张表是该被投资单位（子公司/合营/联营企业）的数字，而不是本集团合并数吗？主体名称对吗？",
            "章节推定合并": "这张表的数字是集团合并口径吗？",
            "不适用": "这张表与合并/母公司口径无关（如股东、薪酬、担保），可以按表名直接引用吗？",
            "未知已阻断": "这张表的口径确实无法从页面判断（所以禁止直接答数）吗？如果能判断，是什么口径？"}
    for kind, n in (("被投资主体", 3), ("章节推定合并", 4), ("不适用", 3), ("未知已阻断", 3)):
        seen = set()
        for rid, m in rng.sample(pools[kind], len(pools[kind])):
            if (rid, m["page"]) in seen:
                continue
            seen.add((rid, m["page"]))
            extra = f"，主体={m.get('entity')}" if kind == "被投资主体" else ""
            add(rid, m["page"], m["table_id"], kind,
                f"口径为 {m.get('scope')}（来源 {m.get('scope_source') or m.get('scope_unknown_reason')}）{extra}。",
                asks[kind])
            if len(seen) >= n:
                break
    # 6. Opening-balance column ("YYYY年1月1日")
    catl_balance = seg(catl, "资产负债表", "合并")
    table = next(t for t in catl_balance["tables"] if t["page"] == 116)
    opening = next(p for p in table["period_columns"] if p.get("period") == "opening")
    add("300750_2023", table["page"], table["table_id"], "期初余额列",
        f"资产负债表印刷表头「{opening['printed_label']}」，绑定为「{opening['label']}」。",
        "比较列对应本年期初吗？报告有明确说明该列已调整或重述吗？")
    return items


def render(items):
    import pypdfium2 as pdfium
    (OUT / "img").mkdir(parents=True, exist_ok=True)
    docs = {}
    for it in items:
        rid = it["report"]
        if rid not in docs:
            docs[rid] = pdfium.PdfDocument(str(ROOT / REG[rid]["local_path"]))
        # Embedded as a JPEG data URI so the page works however it is opened (no relative paths).
        import base64, io
        buf = io.BytesIO()
        docs[rid][it["page"] - 1].render(scale=1.5).to_pil().convert("RGB").save(buf, "JPEG", quality=72)
        it["img"] = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
    cards = []
    for i, it in enumerate(items, 1):
        e = REG[it["report"]]
        cards.append(f"""<article class="card" data-id="{html.escape(it['id'])}">
<header><b>{i}. {html.escape(e['company'])} {e['report_year']} 年报 · PDF 第 {it['page']} 页</b><span class="tag">{html.escape(it['kind'])}</span></header>
<p class="claim"><b>解析器的判断：</b>{html.escape(it['claim'])}</p>
<p class="ask"><b>请确认：</b>{html.escape(it['ask'])}</p>
<div class="verdict"><label><input type="radio" name="v{i}" value="对"> 对</label>
<label><input type="radio" name="v{i}" value="错"> 错</label>
<label><input type="radio" name="v{i}" value="看不出"> 看不出</label>
<input class="note" placeholder="备注（错在哪里，可选）"></div>
<img loading="lazy" src="{it['img']}" alt="原页图片"></article>""")
    page = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>语料签核</title><style>
:root{{--bg:#f3f4f1;--paper:#fff;--ink:#172033;--muted:#5b6475;--rule:#d6dce4;--accent:#17835f}}
@media (prefers-color-scheme:dark){{:root{{--bg:#12161d;--paper:#1b212b;--ink:#e6eaf0;--muted:#a3adbb;--rule:#323b48;--accent:#4cc59a}}}}
body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 'Noto Sans SC','Microsoft YaHei',sans-serif}}
main{{max-width:980px;margin:0 auto;padding:24px 16px 80px}} h1{{font-size:24px}}
.card{{background:var(--paper);border:1px solid var(--rule);border-radius:8px;padding:16px;margin:18px 0}}
.card header{{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}} .tag{{color:var(--accent);font-size:13px}}
.claim,.ask{{margin:6px 0}} .verdict{{display:flex;flex-wrap:wrap;gap:14px;align-items:center;margin:8px 0}}
.note{{flex:1;min-width:200px;padding:4px 8px;border:1px solid var(--rule);border-radius:4px;background:var(--bg);color:var(--ink)}}
img{{width:100%;border:1px solid var(--rule);border-radius:4px;background:#fff}}
button{{font:inherit;padding:8px 18px;border-radius:6px;border:0;background:var(--ink);color:var(--paper);cursor:pointer}}
textarea{{width:100%;height:160px;margin-top:10px;font:13px monospace}}</style></head><body><main>
<h1>溯数 v3 语料签核（{len(items)} 页）</h1>
<p>每一项左边是解析器的判断，下面是年报原页图片（可放大浏览器查看细节）。只需选「对 / 错 / 看不出」，错的地方简单写一句。全部看完后点最下面的按钮，把生成的文字复制给我。</p>
{''.join(cards)}
<button id="go" type="button">生成签核结果</button><textarea id="out" readonly placeholder="点上面的按钮后，这里会出现结果，全选复制发给我"></textarea>
</main><script>
document.getElementById('go').onclick=()=>{{const r=[...document.querySelectorAll('.card')].map((c,i)=>{{
const v=c.querySelector('input[type=radio]:checked');return {{n:i+1,id:c.dataset.id,verdict:v?v.value:'未填',note:c.querySelector('.note').value}}}});
document.getElementById('out').value=JSON.stringify(r);}};</script></body></html>"""
    (OUT / "index.html").write_text(page, encoding="utf-8")
    (OUT / "items.json").write_text(json.dumps([{k: v for k, v in it.items() if k != "img"} for it in items],
                                               ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    items = pick()
    render(items)
    print(f"{len(items)} items -> {OUT / 'index.html'}")
