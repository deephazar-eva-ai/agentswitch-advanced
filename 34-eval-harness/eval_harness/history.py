"""Score history and the "what got worse" report.

History: one JSON line per task per run, appended from a results.json. It lives under runs/ (git-ignored),
because evidence can name customers and orders.

Report: for each agent on each system, the latest run is compared with the run before it. A task that
passed before and fails now is "worse", the reverse is "better". If either side is an infrastructure row
(nothing was judged), the change is "not_judged", never a regression or a fix.

Usage:
  python -m eval_harness.history add --results runs/pass-keystone.json --run pass-1127 --agent real
  python -m eval_harness.history report
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from eval_harness.results import ResultsFile

DEFAULT_HISTORY = Path("runs/history.jsonl")
INFRA_PREFIX = "infrastructure:"

Status = Literal["pass", "fail", "infra"]
Kind = Literal["worse", "better", "not_judged", "new", "dropped"]


class HistoryLine(BaseModel):
    recorded_at: str
    run: str
    agent: str
    instance: str
    task: str
    title: str
    status: Status
    score: float
    evidence: str


def _status(passed: bool, evidence: str) -> Status:
    if passed:
        return "pass"
    return "infra" if evidence.startswith(INFRA_PREFIX) else "fail"


def load_history(path: Path) -> list[HistoryLine]:
    if not path.exists():
        return []
    return [HistoryLine.model_validate_json(ln) for ln in path.read_text().splitlines() if ln.strip()]


def add_results(path: Path, results: Path, *, run: str, agent: str, recorded_at: str | None = None) -> int:
    """Append one line per task in `results`. Refuses a run+agent+system already in the history."""
    body = ResultsFile.model_validate_json(results.read_text())
    when = recorded_at or datetime.now().isoformat(timespec="seconds")
    new: list[HistoryLine] = []
    for row in body.tasks:
        instance, _, task = row.id.partition(":")
        new.append(
            HistoryLine(
                recorded_at=when,
                run=run,
                agent=agent,
                instance=instance,
                task=task,
                title=row.title,
                status=_status(row.passed, row.evidence),
                score=row.score,
                evidence=row.evidence,
            )
        )
    seen = {(h.run, h.agent, h.instance) for h in load_history(path)}
    clash = sorted({(n.run, n.agent, n.instance) for n in new} & seen)
    if clash:
        raise ValueError(f"run {clash[0][0]} for {clash[0][1]} on {clash[0][2]} is already in the history")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as out:
        out.writelines(n.model_dump_json() + "\n" for n in new)
    return len(new)


@dataclass(frozen=True)
class Change:
    task: str
    title: str
    kind: Kind
    before: Status | None
    after: Status | None
    evidence: str


@dataclass(frozen=True)
class Comparison:
    agent: str
    instance: str
    before_run: str
    after_run: str
    passed_before: int
    passed_after: int
    same_pass: int = 0
    same_fail: int = 0
    changes: list[Change] = field(default_factory=list)


def _kind(before: Status, after: Status) -> Kind | None:
    if before == after:
        return None
    if "infra" in (before, after):
        return "not_judged"
    return "worse" if before == "pass" else "better"


def _compare_pair(agent: str, instance: str, old: list[HistoryLine], cur: list[HistoryLine]) -> Comparison:
    olds, curs = {h.task: h for h in old}, {h.task: h for h in cur}
    changes: list[Change] = []
    same_pass = same_fail = 0
    for task in sorted(olds.keys() | curs.keys()):
        o, c = olds.get(task), curs.get(task)
        if o is None and c is not None:
            changes.append(Change(task, c.title, "new", None, c.status, c.evidence))
        elif c is None and o is not None:
            changes.append(Change(task, o.title, "dropped", o.status, None, o.evidence))
        elif o is not None and c is not None:
            kind = _kind(o.status, c.status)
            if kind is None:
                same_pass += c.status == "pass"
                same_fail += c.status != "pass"
            else:
                changes.append(Change(task, c.title, kind, o.status, c.status, c.evidence))
    return Comparison(
        agent=agent,
        instance=instance,
        before_run=old[0].run,
        after_run=cur[0].run,
        passed_before=sum(h.status == "pass" for h in old),
        passed_after=sum(h.status == "pass" for h in cur),
        same_pass=same_pass,
        same_fail=same_fail,
        changes=changes,
    )


def compare(history: Iterable[HistoryLine]) -> list[Comparison]:
    """Latest run vs the run before it, for every agent+system that has at least two runs."""
    runs: dict[tuple[str, str], dict[str, list[HistoryLine]]] = {}
    for h in history:  # dicts keep insertion order, so ties in time fall back to the order added
        runs.setdefault((h.agent, h.instance), {}).setdefault(h.run, []).append(h)
    out: list[Comparison] = []
    for (agent, instance), by_run in runs.items():
        ordered = sorted(by_run.values(), key=lambda lines: max(h.recorded_at for h in lines))
        if len(ordered) >= 2:
            out.append(_compare_pair(agent, instance, ordered[-2], ordered[-1]))
    return out


SECTIONS: list[tuple[Kind, str]] = [
    ("worse", "Worse (passed before, fails now)"),
    ("better", "Better (failed before, passes now)"),
    ("not_judged", "Not judged (infrastructure on one side)"),
    ("new", "New tasks"),
    ("dropped", "Tasks no longer in the run"),
]


def render_report(comparisons: list[Comparison]) -> str:
    if not comparisons:
        return "# What got worse\n\nnothing to compare yet: each agent needs two runs on the same system.\n"
    parts = ["# What got worse\n"]
    for c in comparisons:
        parts.append(
            f"\n## {c.agent} on {c.instance}: passed {c.passed_before} before, {c.passed_after} now\n"
            f"Runs: {c.before_run} then {c.after_run}. Unchanged: {c.same_pass} still pass, "
            f"{c.same_fail} still fail.\n"
        )
        for kind, heading in SECTIONS:
            rows = [ch for ch in c.changes if ch.kind == kind]
            if not rows:
                continue
            if kind in ("new", "dropped"):  # a suite change, not an agent change: names only
                parts.append(f"\n{heading}: {', '.join(ch.task for ch in rows)}\n")
                continue
            parts.append(f"\n### {heading}\n")
            parts.extend(f"- {ch.task}: {ch.title}\n  - why: {ch.evidence}\n" for ch in rows)
    return "".join(parts)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m eval_harness.history")
    parser.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
    sub = parser.add_subparsers(dest="command", required=True)
    add = sub.add_parser("add", help="append a results.json to the history")
    add.add_argument("--results", type=Path, required=True)
    add.add_argument("--run", required=True, help="a name for this run, e.g. pass-1127")
    add.add_argument("--agent", required=True, help="which agent ran, e.g. real, nothing, wrong")
    rep = sub.add_parser("report", help="print what got worse since each agent's previous run")
    rep.add_argument("--out", type=Path, help="also write the report to this file")
    args = parser.parse_args(argv)
    if args.command == "add":
        try:
            n = add_results(args.history, args.results, run=args.run, agent=args.agent)
        except ValueError as err:
            print(f"not added: {err}")
            return 1
        print(f"added {n} tasks to {args.history}")
        return 0
    text = render_report(compare(load_history(args.history)))
    print(text, end="")
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
