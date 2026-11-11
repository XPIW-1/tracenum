"""List saved recordings with each strategy's answer checked against the paper's Gold.

A strategy "hits" when its final answer contains the Gold's key figure (or, for a
boundary question, when it declines). This is a quick screen for picking demo
recordings, not a replacement for the paper's blind scoring.

python review_recordings.py [--feature]   # --feature marks the best recording per question
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

import pipeline as P

RECORDINGS = Path(__file__).resolve().parent / "recordings"
GOLD = {g["question_id"]: g for g in json.loads((P.V2 / P.FREEZE["gold"]["path"]).read_text(encoding="utf-8"))}


def key_figures(text):
    return {n.replace(",", "") for n in re.findall(r"-?\d[\d,]*\.?\d*", text or "")}


def hit(qid, summary):
    gold = GOLD.get(qid)
    if not gold or summary is None:
        return None
    if not gold.get("answerable", True):
        return bool(summary.get("refused"))
    answer = summary.get("answer") or ""
    if summary.get("refused"):
        return False
    figures = key_figures(answer)
    wanted = {f.lstrip("-") for f in key_figures(gold.get("expected_answer", ""))}
    have = {f.lstrip("-") for f in figures}
    # Gold "0.4" should match an answer of "0.40".
    loose = {f.rstrip("0").rstrip(".") for f in have}
    return bool(wanted & have) or bool({w.rstrip("0").rstrip(".") for w in wanted} & loose)


def main():
    best = {}
    for path in sorted(RECORDINGS.glob("*_S1S2S3.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        qid = record["question_id"]
        # The summary keeps only 240 characters; judge on the full final answer.
        finals = {e["event"]["strategy"]: e["event"]["result"] for e in record["events"] if e["event"]["type"] == "final"}
        marks = {s: hit(qid, finals.get(s)) for s in ("S1", "S2", "S3")}
        paper = {s: P.PAPER.get(qid, {}).get(s, {}).get("correct") for s in ("S1", "S2", "S3")}
        agree = sum(marks[s] == paper[s] for s in marks)
        line = " ".join(f"{s}{'✓' if marks[s] else '✗'}" for s in marks)
        ref = " ".join(f"{s}{'✓' if paper[s] else '✗'}" for s in paper)
        print(f"{record['id']}  live: {line}   paper: {ref}   agree {agree}/3   {record['duration']:.0f}s")
        score = (agree, marks["S3"] is True, -record["duration"])
        if qid not in best or score > best[qid][0]:
            best[qid] = (score, record["id"])
    if "--feature" in sys.argv:
        for qid, (_, rid) in best.items():
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:8765/api/recordings/{rid}/feature", method="POST"))
            print("featured", qid, "->", rid)


if __name__ == "__main__":
    main()
