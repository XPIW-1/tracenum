"""Rule-check tags: a validator gap must not be shown as a failure.

    python -m unittest tests.test_check_flags -v      (from the repo root; no models, no network)

Bug class this guards (2026-11): the frozen fact ledger cannot check some question shapes
(UNSUPPORTED) or cannot bind a number to a year column (UNKNOWN). The page showed those as red
「未通过」 on correct featured answers, e.g. BYD-2025-002 in all three rounds.
"""
import ast
import json
import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "backend"))

import check_flags as C  # noqa: E402

ALL_TRUE = {k: True for k in C.FLAGS}
RECORDINGS = REPO / "web" / "public" / "data" / "recordings"


def low(**flags):
    return {**ALL_TRUE, **flags}


class FlagStatesTest(unittest.TestCase):
    def test_all_pass(self):
        self.assertEqual(set(C.flag_states(ALL_TRUE, ALL_TRUE, ["VERIFIED"], "VERIFIED").values()), {"ok"})

    def test_ledger_gap_is_unverified(self):
        # BYD-2025-002 rounds 2-3: the rules passed, the ledger could not bind revenue (UNKNOWN)
        # and the question shape is outside its templates (UNSUPPORTED).
        final = low(retrieval_complete=False, calculation_ok=False, time_match=False)
        states = C.flag_states(final, ALL_TRUE, ["UNKNOWN"], "UNSUPPORTED")
        self.assertEqual(states, {"retrieval_complete": "unverified", "calculation_ok": "unverified",
                                  "time_match": "unverified", "metric_match": "ok"})

    def test_rule_failure_stays_bad(self):
        # Round 1: the answer said 未找到, so check_node itself lowered retrieval.
        final = low(retrieval_complete=False, calculation_ok=False, time_match=False)
        states = C.flag_states(final, low(retrieval_complete=False), ["UNKNOWN"], "UNSUPPORTED")
        self.assertEqual(states["retrieval_complete"], "bad")
        self.assertEqual(states["calculation_ok"], "unverified")

    def test_ledger_contradictions_are_bad(self):
        final = low(retrieval_complete=False, calculation_ok=False)
        self.assertEqual(C.flag_states(final, ALL_TRUE, ["CONFLICT"], "VERIFIED")["retrieval_complete"], "bad")
        self.assertEqual(C.flag_states(final, ALL_TRUE, ["FOUND_NOT_CITED"], None)["retrieval_complete"], "bad")
        self.assertEqual(C.flag_states(final, ALL_TRUE, ["VERIFIED"], "INCORRECT")["calculation_ok"], "bad")

    def test_without_ledger_every_false_is_bad(self):
        # Out-of-corpus year: check_node returns before the ledger runs.
        self.assertEqual(C.flag_states(low(time_match=False))["time_match"], "bad")

    def test_rule_feedback_from_log(self):
        rule = C.rule_feedback_from_log(low(retrieval_complete=False, calculation_ok=False),
                                        ["check_retrieval_complete", "fact_unknown"],
                                        ["答案未展示计算过程。请先列出加数再写结果"])
        self.assertEqual(rule, low(retrieval_complete=False, calculation_ok=False))
        self.assertIsNone(C.rule_feedback_from_log(ALL_TRUE, None, []))

    def test_rule_wording_matches_frozen_check_node(self):
        # rule_feedback_from_log reads check_node's calculation messages by their wording.
        # The frozen notebook is not in this repo; the recordings carry the messages it wrote.
        seen = " ".join(m for e in recording_events() if e.get("node") == "check" for m in e.get("missing") or [])
        for prefix in C.RULE_CALC_MISSING:
            self.assertIn(prefix, seen)


class LiveHookTest(unittest.TestCase):
    def fake_ledger(self, state):
        state["feedback"].update(retrieval_complete=False, time_match=False, calculation_ok=False)
        state["fact_audit"] = [{"evidence_status": "UNKNOWN"}]
        state["calculation_audit"] = {"status": "UNSUPPORTED"}
        state["verification_status"] = "UNSUPPORTED"
        return state

    def test_snapshot_then_payload(self):
        apply = C.snapshot_rule_feedback(self.fake_ledger)
        state = apply({"round": 2, "feedback": dict(ALL_TRUE)})
        out = C.payload(state)
        self.assertEqual(out["verification_status"], "UNSUPPORTED")
        self.assertEqual(set(out["flag_states"].values()), {"unverified", "ok"})

    def test_stale_snapshot_is_not_used(self):
        state = C.snapshot_rule_feedback(self.fake_ledger)({"round": 1, "feedback": dict(ALL_TRUE)})
        state.update(round=2, feedback=low(time_match=False))  # round 2 returned before the ledger
        self.assertEqual(C.payload(state), {"flag_states": C.flag_states(low(time_match=False)),
                                            "verification_status": None})


def recording_events():
    for path in sorted(RECORDINGS.glob("2*.json")):
        for item in json.loads(path.read_text(encoding="utf-8"))["events"]:
            yield item["event"]


class FeaturedRecordingsTest(unittest.TestCase):
    """Release gate on the public featured recordings."""

    def records(self):
        index = json.loads((RECORDINGS / "index.json").read_text(encoding="utf-8"))
        self.assertTrue(index)
        for entry in index:
            yield json.loads((RECORDINGS / f"{entry['id']}.json").read_text(encoding="utf-8"))

    def test_every_check_has_consistent_flag_states(self):
        for record in self.records():
            for item in record["events"]:
                e = item["event"]
                if e.get("type") == "node" and e.get("node") == "check":
                    states = e.get("flag_states")
                    self.assertIsNotNone(states, f"{record['id']} {e['strategy']} r{e['round']}")
                    for key in C.FLAGS:
                        self.assertIn(states[key], C.STATES)
                        self.assertEqual(states[key] == "ok", bool(e["feedback"][key]),
                                         f"{record['id']} {e['strategy']} r{e['round']} {key}")

    def test_answered_featured_result_ends_without_red(self):
        # A featured answer that the last check marks as a rule failure must not be published.
        for record in self.records():
            for strategy, summary in record["summary"].items():
                checks = [i["event"] for i in record["events"]
                          if i["event"].get("node") == "check" and i["event"].get("strategy") == strategy
                          and "flag_states" in i["event"]]
                if summary.get("refused") or not checks:
                    continue
                last = max(checks, key=lambda e: e["round"])
                bad = [k for k, v in last["flag_states"].items() if v == "bad"]
                self.assertEqual(bad, [], f"{record['question_id']} {strategy} round {last['round']}")


class PackagingTest(unittest.TestCase):
    def test_every_local_backend_import_is_packaged(self):
        # deploy/package_runtime.py copies an explicit list; a module left out breaks the server
        # at import time only after deployment.
        source = (REPO / "deploy" / "package_runtime.py").read_text(encoding="utf-8")
        listed = set(ast.literal_eval(re.search(r"BACKEND_FILES = (\[.*?\])", source, re.S).group(1)))
        local = {p.name for p in (REPO / "backend").glob("*.py")}
        pending, seen = ["server.py"], set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            tree = ast.parse((REPO / "backend" / name).read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                mods = ([a.name for a in node.names] if isinstance(node, ast.Import)
                        else [node.module] if isinstance(node, ast.ImportFrom) and node.module else [])
                pending += [f"{m.split('.')[0]}.py" for m in mods if f"{m.split('.')[0]}.py" in local]
        self.assertEqual(seen - listed, set())


if __name__ == "__main__":
    unittest.main()
