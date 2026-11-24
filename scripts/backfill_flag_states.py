"""Add flag_states to recordings made before pipeline.py emitted them.

Each check event is matched to the frozen run log of the same run (backend/runs/<stamp>_..._
<strategy>_<question>_*/events.jsonl, same round). The run log stores what the recording did not:
the fact ledger, the calculation audit and the reason codes. A recording is only changed when the
run log's feedback and missing items equal the recording's for every check, so a wrong match
aborts instead of writing.

Works on raw recordings (backend/recordings) and on exported ones (web/public/data/recordings),
since both carry the same check events.

    python scripts/backfill_flag_states.py --runs backend/runs file.json [...]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import check_flags  # noqa: E402


def run_checks(runs: Path, stamp: str, strategy: str, qid: str) -> dict[int, dict]:
    dirs = sorted(runs.glob(f"{stamp}*_{strategy}_{qid}_*"))
    if len(dirs) != 1:
        raise SystemExit(f"{stamp} {strategy} {qid}: expected one run log, found {len(dirs)}")
    rows = {}
    for line in (dirs[0] / "events.jsonl").read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("node") == "check" and "feedback" in row:
            if row["round"] in rows:
                raise SystemExit(f"{dirs[0].name}: two check rows for round {row['round']}")
            rows[row["round"]] = row
    return rows


def backfill(path: Path, runs: Path) -> int:
    record = json.loads(path.read_text(encoding="utf-8"))
    stamp = record["id"].split("_", 1)[0]
    logs: dict[str, dict[int, dict]] = {}
    changed = 0
    for item in record["events"]:
        event = item["event"]
        if event.get("type") != "node" or event.get("node") != "check":
            continue
        strategy = event["strategy"]
        if strategy not in logs:
            logs[strategy] = run_checks(runs, stamp, strategy, record["question_id"])
        row = logs[strategy].get(event["round"])
        if row is None:
            raise SystemExit(f"{path.name}: no run-log check for {strategy} round {event['round']}")
        if row["feedback"] != event["feedback"] or row.get("missing_items") != event.get("missing"):
            raise SystemExit(f"{path.name}: run log disagrees with the recording at {strategy} round {event['round']}")
        # apply_feedback sets SUPPORTED / UNSUPPORTED; an early return (out-of-corpus year, budget
        # stop) leaves the initial NOT_EVALUATED, and every lowered flag is then a rule result.
        ledger_ran = row.get("verification_status") in check_flags.LEDGER_STATUSES
        if ledger_ran and not ("fact_audit" in row and "calculation_audit" in row):
            raise SystemExit(f"{path.name}: run log has no ledger audit at {strategy} round {event['round']}")
        rule = check_flags.rule_feedback_from_log(row["feedback"], row.get("feedback_reason_codes") if ledger_ran else None,
                                                  row.get("missing_items"))
        facts, calc = check_flags.statuses_from_state(row) if rule else ([], None)
        states = check_flags.flag_states(row["feedback"], rule, facts, calc)
        status = row.get("verification_status") if rule else None
        if event.get("flag_states") != states or event.get("verification_status") != status:
            event["flag_states"], event["verification_status"] = states, status
            changed += 1
    if changed:
        path.write_text(json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
                        encoding="utf-8", newline="\n")
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=Path, required=True, help="backend/runs folder with the frozen run logs")
    parser.add_argument("recordings", type=Path, nargs="+")
    args = parser.parse_args()
    for path in args.recordings:
        if path.name == "index.json":
            continue
        print(f"{path.name}: {backfill(path, args.runs)} check events updated")


if __name__ == "__main__":
    main()
