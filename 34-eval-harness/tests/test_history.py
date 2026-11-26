"""Score history and the "what got worse" report (plan: "Given two runs, when a task passed before and fails
now, then the regression report lists it with the reason").

History is one JSON line per task per run. The report compares each agent's latest run with its previous
run on the same system. An infrastructure row (nothing was judged) is never called a regression or a fix.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_harness.history import (
    HistoryLine,
    add_results,
    compare,
    load_history,
    render_report,
)


def results_file(tmp_path: Path, name: str, rows: list[tuple[str, bool, str]]) -> Path:
    path = tmp_path / name
    tasks = [
        {"id": tid, "title": f"title {tid}", "passed": ok, "score": 1.0 if ok else 0.0, "evidence": ev}
        for tid, ok, ev in rows
    ]
    path.write_text(json.dumps({"tasks": tasks, "summary": "x"}))
    return path


PASS = "outcome=approve; recount=approve"
FAIL = "outcome=revise; recount=revise (missing SO-0007)"
INFRA = "infrastructure: agent exited with code 2: usage error"


def test_adding_a_results_file_writes_one_line_per_task(tmp_path: Path) -> None:
    res = results_file(tmp_path, "a.json", [("keystone:t1", True, PASS), ("keystone:t2", False, FAIL)])
    hist = tmp_path / "history.jsonl"
    added = add_results(hist, res, run="run-1", agent="real", recorded_at="2026-11-27T10:00:00")
    assert added == 2
    lines = load_history(hist)
    assert [(ln.task, ln.instance, ln.status) for ln in lines] == [
        ("t1", "keystone", "pass"),
        ("t2", "keystone", "fail"),
    ]
    assert lines[1].evidence == FAIL


def test_an_infrastructure_row_is_stored_as_infra_not_fail(tmp_path: Path) -> None:
    res = results_file(tmp_path, "a.json", [("suryodaya:t1", False, INFRA)])
    hist = tmp_path / "history.jsonl"
    add_results(hist, res, run="run-1", agent="real", recorded_at="2026-11-27T10:00:00")
    assert load_history(hist)[0].status == "infra"


def test_adding_the_same_run_twice_is_refused(tmp_path: Path) -> None:
    res = results_file(tmp_path, "a.json", [("keystone:t1", True, PASS)])
    hist = tmp_path / "history.jsonl"
    add_results(hist, res, run="run-1", agent="real", recorded_at="2026-11-27T10:00:00")
    with pytest.raises(ValueError, match="already in the history"):
        add_results(hist, res, run="run-1", agent="real", recorded_at="2026-11-27T11:00:00")
    assert len(load_history(hist)) == 1


def line(
    run: str, when: str, task: str, status: str, agent: str = "real", inst: str = "keystone"
) -> HistoryLine:
    evidence = {"pass": PASS, "fail": FAIL, "infra": INFRA}[status]
    return HistoryLine(
        recorded_at=when,
        run=run,
        agent=agent,
        instance=inst,
        task=task,
        title=task,
        status=status,
        score=1.0 if status == "pass" else 0.0,
        evidence=evidence,
    )


def test_pass_then_fail_is_a_regression_with_its_reason() -> None:
    hist = [line("r1", "2026-11-26", "t1", "pass"), line("r2", "2026-11-27", "t1", "fail")]
    [group] = compare(hist)
    assert (group.agent, group.instance, group.before_run, group.after_run) == (
        "real",
        "keystone",
        "r1",
        "r2",
    )
    assert [(c.task, c.kind, c.evidence) for c in group.changes] == [("t1", "worse", FAIL)]


def test_fail_then_pass_is_a_fix() -> None:
    hist = [line("r1", "2026-11-26", "t1", "fail"), line("r2", "2026-11-27", "t1", "pass")]
    assert [c.kind for c in compare(hist)[0].changes] == ["better"]


@pytest.mark.parametrize(("before", "after"), [("pass", "infra"), ("infra", "fail"), ("infra", "pass")])
def test_infrastructure_on_either_side_is_not_judged(before: str, after: str) -> None:
    hist = [line("r1", "2026-11-26", "t1", before), line("r2", "2026-11-27", "t1", after)]
    assert [c.kind for c in compare(hist)[0].changes] == ["not_judged"]


def test_unchanged_tasks_are_not_listed_but_are_counted() -> None:
    hist = [
        line("r1", "2026-11-26", "t1", "pass"),
        line("r1", "2026-11-26", "t2", "fail"),
        line("r2", "2026-11-27", "t1", "pass"),
        line("r2", "2026-11-27", "t2", "fail"),
    ]
    [group] = compare(hist)
    assert group.changes == []
    assert (group.same_pass, group.same_fail) == (1, 1)


def test_new_and_dropped_tasks_are_listed() -> None:
    hist = [line("r1", "2026-11-26", "old", "pass"), line("r2", "2026-11-27", "new", "fail")]
    assert sorted((c.task, c.kind) for c in compare(hist)[0].changes) == [("new", "new"), ("old", "dropped")]


def test_each_agent_and_system_is_compared_only_with_itself() -> None:
    hist = [
        line("r1", "2026-11-26", "t1", "pass", agent="real", inst="keystone"),
        line("r2", "2026-11-27", "t1", "fail", agent="nothing", inst="keystone"),
        line("r3", "2026-11-27", "t1", "fail", agent="real", inst="suryodaya"),
    ]
    assert compare(hist) == []  # no agent+system pair has two runs yet


def test_only_the_latest_two_runs_are_compared() -> None:
    hist = [
        line("r1", "2026-11-25", "t1", "fail"),
        line("r2", "2026-11-26", "t1", "pass"),
        line("r3", "2026-11-27", "t1", "fail"),
    ]
    [group] = compare(hist)
    assert (group.before_run, group.after_run, group.changes[0].kind) == ("r2", "r3", "worse")


def test_the_report_leads_with_what_got_worse() -> None:
    hist = [
        line("r1", "2026-11-26", "t1", "pass"),
        line("r1", "2026-11-26", "t2", "fail"),
        line("r2", "2026-11-27", "t1", "fail"),
        line("r2", "2026-11-27", "t2", "pass"),
    ]
    text = render_report(compare(hist))
    assert text.index("Worse") < text.index("Better")
    assert "t1" in text and "missing SO-0007" in text
    assert "real on keystone: passed 1 before, 1 now" in text


def test_an_empty_report_says_there_is_nothing_to_compare() -> None:
    assert "nothing to compare" in render_report([])
