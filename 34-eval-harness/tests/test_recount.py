from __future__ import annotations

from types import SimpleNamespace

from eval_harness.graders import Verdict
from eval_harness.recount import parse_agent_result, recount


def _record(output, expected_ids=None):
    if output is None:
        return SimpleNamespace(agent=None, expected_ids=expected_ids)
    return SimpleNamespace(
        agent=SimpleNamespace(output_tail=output),
        expected_ids=expected_ids,
    )


def _result(answer_key, ids):
    import json
    return "AGENT_RESULT " + json.dumps({"claim": "done", "answer": {answer_key: ids}}) + "\n"


def test_parse_basic():
    out = _result("orders_not_invoiced", ["A", "B"])
    assert parse_agent_result(out) == {
        "claim": "done",
        "answer": {"orders_not_invoiced": ["A", "B"]},
    }


def test_parse_no_line():
    assert parse_agent_result("nothing\n") is None


def test_parse_bad_json():
    assert parse_agent_result('AGENT_RESULT {"claim": "done"\n') is None


def test_clean_passes():
    r = recount(
        _record(_result("orders_not_invoiced", ["A", "B"]), expected_ids=["A", "B"]),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.APPROVE


def test_missing_ids_revises():
    r = recount(
        _record(_result("orders_not_invoiced", ["A"]), expected_ids=["A", "B"]),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.REVISE


def test_extra_ids_revises():
    r = recount(
        _record(_result("orders_not_invoiced", ["A", "B", "C"]), expected_ids=["A", "B"]),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.REVISE


def test_do_nothing_revises():
    r = recount(
        _record(_result("orders_not_invoiced", []), expected_ids=["A", "B"]),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.REVISE


def test_missing_answer_key_revises():
    r = recount(
        _record(_result("some_other_key", ["A"]), expected_ids=["A", "B"]),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.REVISE


def test_no_agent_unevaluated():
    r = recount(_record(None), {"answer": "orders_not_invoiced"})
    assert r.verdict is Verdict.UNEVALUATED


def test_no_agent_result_revises():
    # Agent ran but printed no AGENT_RESULT line: it did not answer, so REVISE.
    # Per Abhinav: "A missing answer should fail (revise)."
    r = recount(_record("random output\n", expected_ids=["A"]), {"answer": "orders_not_invoiced"})
    assert r.verdict is Verdict.REVISE


def test_no_expected_ids_unevaluated():
    r = recount(
        _record(_result("orders_not_invoiced", ["A"]), expected_ids=None),
        {"answer": "orders_not_invoiced"},
    )
    assert r.verdict is Verdict.UNEVALUATED


def test_no_answer_key_param_unevaluated():
    r = recount(_record(_result("orders_not_invoiced", ["A"]), expected_ids=["A"]), {})
    assert r.verdict is Verdict.UNEVALUATED
