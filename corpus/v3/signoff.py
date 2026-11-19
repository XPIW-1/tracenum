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
    chunks = [json.loads(l)["metadata"] | {"_text": json.loads(l)["text"]}
              for l in (d / "chunks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    return segs, qc, chunks


def seg(segs, stype, scope):
    return next((s for s in segs if s.get("statement_type") == stype and s.get("scope") == scope), None)


def pick():
    rng = random.Random(20261119)
    items = []

    def add(rid, page, kind, claim, ask):
        items.append({"id": f"{rid}:p{page}:{len(items)}", "report": rid, "page": int(page), "kind": kind,
                      "claim": claim, "ask": ask})

    # 1. Restated comparatives (2024 reports of BYD, Great Wall, CATL)
    for rid in ("002594_2024", "601633_2024", "300750_2024"):
        segs, _, _ = load(rid)
        s = seg(segs, "利润表", "合并")
        add(rid, s["start_page"], "追溯调整", f"合并利润表（第{s['start_page']}–{s['end_page']}页），单位 {s.get('unit')}，"
            f"含 2023 年比较列", "这一页是合并利润表吗？2023 年比较列是否为调整后的数字（与 2023 年报原数不同）？")
    # 2. Parent-company statements (scope switch), one per company family
    for rid in ("002594_2025", "002466_2025", "002812_2024", "301358_2025", "688005_2023", "300014_2024"):
        segs, _, _ = load(rid)
        s = seg(segs, "现金流量表", "母公司") or seg(segs, "资产负债表", "母公司")
        add(rid, s["start_page"], "母公司口径", f"{s['statement_type']}，口径=母公司（第{s['start_page']}–{s['end_page']}页）",
            "这一页确实是母公司（或「公司」）报表，而不是合并报表吗？")
    # 3. Title printed on the previous page
    add("300750_2023", 120, "标题在上一页", "合并利润表正文从第120页开始，标题在第119页末尾",
        "第120页的表格是否就是第119页末尾标题所指的合并利润表？")
    # 4. Row-sequence continuations
    conts = []
    for rid in sorted(REG):
        _, qc, _ = load(rid)
        conts += [(rid, c) for c in qc.get("accepted_continuations", []) if c.get("rule") == "row_sequence"]
    for rid, c in rng.sample(conts, min(5, len(conts))):
        add(rid, c["page"], "跨页续表", f"第{c['page']}页的表格被判定为第{c['source_page']}页报表的续表（规则：行序衔接）",
            "这一页的表格确实是上一页同一张报表的延续（口径、单位都相同）吗？")
    # 5. Investee, section-default 合并, not-applicable, blocked
    pools = {"被投资主体": [], "章节推定合并": [], "不适用": [], "未知已阻断": []}
    for rid in sorted(REG):
        _, _, chunks = load(rid)
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
            add(rid, m["page"], kind, f"口径={m.get('scope')}（来源 {m.get('scope_source') or m.get('scope_unknown_reason')}）{extra}；"
                f"表格开头：{m['_text'][:90]}", asks[kind])
            if len(seen) >= n:
                break
    # 6. Opening-balance column ("YYYY年1月1日")
    add("300750_2023", 116, "期初调整列", "资产负债表比较列表头为「2023年1月1日」（调整后期初），而非 2022年12月31日",
        "比较列是否为调整后的 2023 年初余额？与 2022 年报的年末数是否不同？")
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
