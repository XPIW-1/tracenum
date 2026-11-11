"""Stream one question through the API and print compact events with timings.

python smoke.py BYD S1 "比亚迪2025年营业收入是多少？"
"""
import json
import sys
import time
import urllib.request

company, strategies, question = sys.argv[1], sys.argv[2].split(","), sys.argv[3]
for _ in range(120):
    status = json.load(urllib.request.urlopen("http://127.0.0.1:8765/api/status"))
    if status["ready"] or status["error"]:
        break
    time.sleep(2)
print("status", status)
body = json.dumps({"company": company, "question": question, "strategies": strategies}).encode()
request = urllib.request.Request("http://127.0.0.1:8765/api/ask", data=body,
                                 headers={"Content-Type": "application/json"})
start = time.time()
with urllib.request.urlopen(request, timeout=900) as response:
    for raw in response:
        line = raw.decode("utf-8").strip()
        if not line.startswith("data:"):
            continue
        event = json.loads(line[5:])
        t = f"{time.time() - start:6.1f}s"
        kind = event["type"]
        if kind == "node":
            extra = {k: event.get(k) for k in ("selected_pages", "stop_reason", "feedback", "total_tokens", "query")
                     if event.get(k) is not None}
            print(t, event["strategy"], "node", event["node"], "r", event["round"], f"{event['latency']}s",
                  json.dumps(extra, ensure_ascii=False)[:300])
        elif kind == "final":
            r = event["result"]
            print(t, event["strategy"], "FINAL", r.get("stop_reason"), r.get("total_tokens"), "cited", r.get("cited_pages"))
            print("   numbers", json.dumps(r.get("numbers"), ensure_ascii=False)[:500])
            print("   answer", (r.get("answer") or "")[:600].replace("\n", " "))
        elif kind == "error":
            print(t, event["strategy"], "ERROR", event["message"], event.get("trace"))
        elif kind != "node_start":
            print(t, kind, json.dumps(event, ensure_ascii=False)[:300])
