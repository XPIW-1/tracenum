"""Owner recheck after round 5: a small sample of the tables the new rules changed.

    python corpus/v3/signoff_round2.py   # -> build/signoff_round2/index.html
"""
import json
import random
from pathlib import Path

import signoff as S

S.OUT = S.REPO / "build" / "signoff_round2"


def pick():
    rng = random.Random(20261120)
    pools = {"group_wording": [], "multi_entity": [], "opening_column": [], "supplement": [], "guarantee": []}
    for rid in sorted(S.REG):
        segs, _, chunks = S.load(rid)
        for m in chunks:
            if m.get("type") != "table":
                continue
            if m.get("scope_source") == "group_wording":
                pools["group_wording"].append((rid, m))
            if m.get("entity_by_row"):
                pools["multi_entity"].append((rid, m))
            if "补充资料" in str(m.get("scope_evidence") or "") and m.get("scope") == "合并":
                pools["supplement"].append((rid, m))
            if "担保" in str(m.get("table_title") or "") and m.get("scope") == "不适用":
                pools["guarantee"].append((rid, m))
        for s in segs:
            if s.get("statement_type") == "资产负债表" and s.get("scope") == "合并" and rid.endswith("2023"):
                labels = [c.get("label", "") for c in s.get("period_columns", [])]
                if any("1月1日" in l for l in labels):
                    pools["opening_column"].append((rid, {"page": s["start_page"], "labels": labels}))
    asks = {
        "group_wording": ("附注集团措辞", "表格上方或标题出现「本集团」等字样，这张表的数字是集团合并口径吗？"),
        "multi_entity": ("多主体按行绑定", "表中每一行的数字是否属于各自标注的那家被投资单位？"),
        "opening_column": ("期初列", "比较列「XXXX年1月1日」现在被视为上一年末的数字（未写调整/重述就不视为调整）。这一页是否如此？"),
        "supplement": ("补充资料", "补充资料中的这张表（非经常性损益/净资产收益率/每股收益）是集团合并口径吗？"),
        "guarantee": ("担保披露", "这张担保表与合并/母公司口径无关，可以按表名直接引用吗？"),
    }
    quota = {"group_wording": 4, "multi_entity": 1, "opening_column": 1, "supplement": 1, "guarantee": 1}
    items = []
    for kind, n in quota.items():
        seen = set()
        for rid, m in rng.sample(pools[kind], len(pools[kind])):
            if (rid, m["page"]) in seen:
                continue
            seen.add((rid, m["page"]))
            label, ask = asks[kind]
            if kind == "opening_column":
                claim = f"合并资产负债表比较列表头：{', '.join(m['labels'])}"
            elif kind == "multi_entity":
                claim = f"表格 {str(m.get('table_id'))[-5:]} 按行绑定主体：{', '.join(sorted(set(m['entity_by_row'].values())))}"
            else:
                ev = m.get("scope_evidence")
                ev = ev[0].get("text", "") if isinstance(ev, list) and ev and isinstance(ev[0], dict) else str(ev or "")
                claim = (f"表格 {str(m.get('table_id'))[-5:]}：口径={m.get('scope')}（依据：{ev[:60]}）"
                         f"{'；表题=' + str(m.get('table_title'))[:40] if m.get('table_title') else ''}")
            items.append({"id": f"{rid}:p{m['page']}:{len(items)}", "report": rid, "page": int(m["page"]),
                          "kind": label, "claim": claim, "ask": ask})
            if len(seen) >= n:
                break
    return items


if __name__ == "__main__":
    items = pick()
    S.render(items)
    print(f"{len(items)} items -> {S.OUT / 'index.html'}")
