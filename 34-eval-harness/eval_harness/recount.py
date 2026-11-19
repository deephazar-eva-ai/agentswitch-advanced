"""Recount grader: compares the agent's AGENT_RESULT answer to expected ids from the trial record."""

from __future__ import annotations

from typing import Any

from eval_harness.trial import TrialRecord


def parse_agent_result(output: str) -> dict[str, Any] | None:
    """Find the last AGENT_RESULT line in the agent output and parse its JSON."""
    import json
    for line in reversed(output.splitlines()):
        stripped = line.strip()
        if stripped.startswith("AGENT_RESULT"):
            payload = stripped[len("AGENT_RESULT"):].strip()
            try:
                return json.loads(payload)
            except json.JSONDecodeError:
                return None
    return None


def recount(record: TrialRecord, params: dict[str, Any]):
    from eval_harness.graders import GraderResult, Verdict
    name = "recount"

    if record.agent is None or not getattr(record.agent, "output_tail", None):
        return GraderResult(name, Verdict.UNEVALUATED, 0.0, "no agent output")

    result = parse_agent_result(record.agent.output_tail)
    if result is None:
        return GraderResult(name, Verdict.REVISE, 0.0, "no AGENT_RESULT line")

    answer_key = params.get("answer")
    if not answer_key:
        return GraderResult(name, Verdict.UNEVALUATED, 0.0, "no answer key in params")

    answer = (result.get("answer") or {}).get(answer_key)
    if answer is None:
        return GraderResult(name, Verdict.REVISE, 0.0, f"missing answer: {answer_key}")

    found = {str(x) for x in answer}
    expected = {str(x) for x in (getattr(record, "expected_ids", None) or [])}

    if not expected:
        return GraderResult(name, Verdict.UNEVALUATED, 0.0, "no expected_ids in record")

    missing, extra = expected - found, found - expected
    if missing or extra:
        return GraderResult(
            name, Verdict.REVISE, 0.0,
            f"missing={sorted(missing)} extra={sorted(extra)}",
        )
    return GraderResult(name, Verdict.APPROVE, 1.0, f"ids match ({len(found)})")
