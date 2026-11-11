"""List live runs of formal questions next to the paper's Gold answer and blind verdict.

python review_runs.py [QID ...]
"""
import json
import re
import sys
from pathlib import Path

import pipeline as P

GOLD = {g["question_id"]: g for g in json.loads((P.V2 / P.FREEZE["gold"]["path"]).read_text(encoding="utf-8"))}
RUNS = Path(__file__).resolve().parent / "runs"
wanted = set(sys.argv[1:])


def numbers(text):
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*\.?\d*", text or "") if len(n.replace(",", "")) >= 3}


rows = []
for run in sorted(RUNS.iterdir()):
    meta = json.loads((run / "run_meta.json").read_text(encoding="utf-8"))
    qid = meta["qid"]
    if qid not in GOLD or (wanted and qid not in wanted):
        continue
    answers = [json.loads(l) for l in (run / "answers.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    final = answers[-1]["answer_text"] if answers else ""
    rows.append((qid, meta["group"], meta.get("started_at", ""), len(answers), final))

for qid, group, started, rounds, final in sorted(rows):
    gold = GOLD[qid]
    expected = gold.get("expected_answer") or ""
    hit = numbers(expected) & numbers(final)
    paper = P.PAPER.get(qid, {}).get(group, {})
    print(f"== {qid} {group} {started[11:19]} answers={rounds} paper={'✓' if paper.get('correct') else '✗'}  gold-number-hit={sorted(hit)[:4]}")
    print("   GOLD:", expected[:160].replace("\n", " "))
    print("   LIVE:", final[:420].replace("\n", " "))
