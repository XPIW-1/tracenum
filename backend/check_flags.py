"""Three-state display of the four rule-check flags (product layer, after the frozen pipeline).

The frozen check (v2 notebook check_node + src/financial_feedback.apply_feedback) reports four
booleans. False means two different things:

  bad         a rule found a real problem: the answer says nothing was found, cites no page,
              cites a page without the target year, uses the wrong metric scope, shows no
              calculation, or the fact ledger found conflicting values / an incorrect result.
  unverified  the fact ledger could not check the answer: the question shape is outside its
              templates (UNSUPPORTED), or it could not bind a number in the evidence to a year
              column (UNKNOWN). That is not evidence the answer is wrong.

The frozen pipeline's decisions (when S3 stops) are unchanged; only what the page shows is.

rule_feedback is the four booleans as check_node's own rules left them, before
apply_feedback folded the fact ledger in. Live runs snapshot it (pipeline.py); old recordings
rebuild it from the run log with rule_feedback_from_log().
"""
from __future__ import annotations

FLAGS = ("retrieval_complete", "calculation_ok", "time_match", "metric_match")
STATES = ("ok", "bad", "unverified")

# check_node's own calculation rules (frozen wording). apply_feedback does not record a reason
# code for them, so a run log is read through these prefixes.
RULE_CALC_MISSING = ("答案未展示计算过程", "答案未给出比例结果")
# Fact-ledger outcomes that are real problems, not gaps in what the validator can check.
BAD_FACT = {"CONFLICT", "FOUND_NOT_CITED"}
BAD_CALC = {"INCORRECT"}
# What apply_feedback writes when it runs; the pipeline's initial value is NOT_EVALUATED.
LEDGER_STATUSES = ("SUPPORTED", "UNSUPPORTED")


def flag_states(feedback: dict, rule_feedback: dict | None = None,
                fact_statuses: list[str] | None = None, calc_status: str | None = None) -> dict:
    """Map the four booleans to ok / bad / unverified.

    Without rule_feedback (the fact ledger did not run, e.g. the out-of-corpus early return or a
    budget stop) every False is a rule result and stays bad.
    """
    rule = rule_feedback if rule_feedback is not None else feedback
    facts = set(fact_statuses or [])
    states = {}
    for key in FLAGS:
        if feedback.get(key):
            states[key] = "ok"
        elif rule.get(key) is False:
            states[key] = "bad"
        elif key == "retrieval_complete":
            states[key] = "bad" if facts & BAD_FACT else "unverified"
        elif key == "calculation_ok":
            states[key] = "bad" if calc_status in BAD_CALC else "unverified"
        elif key == "time_match":
            # The ledger only lowers this flag when a fact's period binding is UNKNOWN.
            states[key] = "unverified"
        else:
            states[key] = "bad"
    return states


def rule_feedback_from_log(feedback: dict, reason_codes: list[str] | None,
                           missing: list[str] | None) -> dict | None:
    """Rebuild check_node's own flags from a frozen run-log check row.

    apply_feedback records "check_<flag>" for each of retrieval/time/metric that check_node had
    already lowered; check_node's calculation rules are recognised by their fixed wording.
    Returns None when the ledger did not run (no reason codes recorded).
    """
    if reason_codes is None:
        return None
    rule = {key: True for key in FLAGS}
    for key in ("retrieval_complete", "time_match", "metric_match"):
        if f"check_{key}" in reason_codes:
            rule[key] = False
    if any(str(item).startswith(RULE_CALC_MISSING) for item in (missing or [])):
        rule["calculation_ok"] = False
    # A flag the ledger cannot touch keeps its final value.
    rule["metric_match"] = rule["metric_match"] and bool(feedback.get("metric_match"))
    return rule


def statuses_from_state(state: dict) -> tuple[list[str], str | None]:
    """Fact and calculation statuses from a pipeline state (or a run-log row)."""
    facts = state.get("fact_audit") or []
    if isinstance(facts, str):
        import json
        facts = json.loads(facts)
    calc = state.get("calculation_audit") or {}
    return [f.get("evidence_status") for f in facts], calc.get("status")


def snapshot_rule_feedback(apply_feedback):
    """Wrap the frozen apply_feedback to keep check_node's own flags before the ledger folds in.

    The frozen code is not changed: the wrapper only copies state["feedback"] first. The round is
    stored too, so a round where check_node returned before the ledger (out-of-corpus year,
    budget stop) is not read with an earlier round's snapshot.
    """
    def wrapped(state):
        state["rule_feedback"] = dict(state.get("feedback") or {})
        state["rule_feedback_round"] = state.get("round")
        return apply_feedback(state)
    return wrapped


def payload(state: dict) -> dict:
    """Fields a check event carries for the page: flag_states and the ledger's verdict."""
    feedback = state.get("feedback") or {}
    ran = state.get("rule_feedback_round") == state.get("round") and "rule_feedback" in state
    if not ran:
        return {"flag_states": flag_states(feedback), "verification_status": None}
    facts, calc = statuses_from_state(state)
    return {"flag_states": flag_states(feedback, state["rule_feedback"], facts, calc),
            "verification_status": state.get("verification_status")}
