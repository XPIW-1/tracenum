"""Rehearsal helper: run demo questions through the live API to fill the rerank cache.

Round-1 retrieval for a given question is deterministic, so after one rehearsal it
is served from tracenum/backend/cache/rerank in well under a second. Later rounds
depend on the model's rewritten query, which usually but not always repeats; each
extra rehearsal widens the cache.

    python prewarm.py                 # featured lab questions, all three strategies
    python prewarm.py --repeat 2      # rehearse twice
    python prewarm.py --only BYD-2025-003 WSI-2025-005
"""
import argparse
import json
import time
import urllib.request

API = "http://127.0.0.1:8765"
FEATURED = ["WSI-2025-005", "BYD-2025-002", "WSI-2025-008", "WSI-2025-007", "BYD-2025-010"]
LOOKUP = [("BYD", "2025年合并口径下的营业收入是多少？"), ("BYD", "2025年研发投入金额是多少，占营业收入的比例是多少？"),
          ("WSI", "2025年归属于上市公司股东的净利润是多少？"), ("WSI", "2025年经营活动产生的现金流量净额是多少？")]


def ask(company, question, strategies):
    body = json.dumps({"company": company, "question": question, "strategies": strategies}).encode("utf-8")
    request = urllib.request.Request(f"{API}/api/ask", data=body, headers={"Content-Type": "application/json"})
    finals = {}
    with urllib.request.urlopen(request, timeout=1800) as response:
        for raw in response:
            line = raw.decode("utf-8").strip()
            if line.startswith("data:"):
                event = json.loads(line[5:])
                if event["type"] == "final":
                    r = event["result"]
                    finals[event["strategy"]] = f"{r.get('rounds')}轮 {r.get('total_tokens')}t {r.get('stop_reason')} {'拒答' if r.get('refused') else ''}"
                elif event["type"] == "error":
                    finals[event["strategy"]] = "ERROR " + event["message"]
    return finals


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    while not json.load(urllib.request.urlopen(f"{API}/api/status"))["ready"]:
        time.sleep(3)
    questions = {q["question_id"]: q for q in json.load(urllib.request.urlopen(f"{API}/api/questions"))}
    targets = args.only or FEATURED
    for round_no in range(args.repeat):
        for qid in targets:
            q = questions[qid]
            started = time.time()
            print(f"[{round_no + 1}] {qid}", ask(q["company"], q["question"], ["S1", "S2", "S3"]),
                  f"{time.time() - started:.0f}s", flush=True)
        if not args.only:
            for company, text in LOOKUP:
                started = time.time()
                print(f"[{round_no + 1}] {company} {text}", ask(company, text, ["S3"]), f"{time.time() - started:.0f}s", flush=True)


if __name__ == "__main__":
    main()
