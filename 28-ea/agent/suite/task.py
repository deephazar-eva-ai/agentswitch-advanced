"""What a task is, and what a task gets to work with.

A task has four parts (see TEST_SUITE_GUIDE.md, "Writing a task"):
    setup   puts the task's own test data on the platform (through ctx.seed_* helpers in seed.py);
    prompt  the request the agent gets with --prompt;
    check   reads the platform afterwards and returns a verdict;
    runs    how many times the agent runs between setup and check (1, or 2 for "running twice" tasks).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

KINDS = ("normal", "refusal")


@dataclass(frozen=True)
class Task:
    id: str                 # stable and unique, e.g. "ea_t09_injection_no_draft"; the runner adds "keystone:"
    title: str              # one line: what must happen
    kind: str               # "normal": the agent must do something; "refusal": the agent must change nothing
    prompt: Callable[["Context"], str]
    setup: Callable[["Context"], None]
    check: Callable[["Context", list], dict]
    runs: int = 1


class Context:
    """One task's view of the run: who we are, the seed client, and the test data this task created."""

    def __init__(self, account, seeder, run_id: str, task_id: str, threads=None):
        self.account = account
        self.reader = account.reader
        self.threads = threads              # ThreadReader: EmailThread.get / EmailThread.list only
        self.seeder = seeder
        self.run_id = run_id
        self.task_id = task_id
        self.seeds: dict[str, dict] = {}   # key -> the seeded email as read back right after it was created
        self.seed_threads: dict[str, dict] = {}  # key -> its thread as read back right after the email was added
        self.since: str | None = None      # platform time of the first seed; writes from then on are checked


def passed(evidence: str) -> dict:
    return {"passed": True, "score": 1.0, "evidence": evidence}


def failed(evidence: str, model_dependent: bool = False) -> dict:
    """`model_dependent`: this failure could come from the model not answering (a missing draft for a control
    email). If the agent's output says mail was not assessed, the runner reports infrastructure instead."""
    out = {"passed": False, "score": 0.0, "evidence": evidence}
    if model_dependent:
        out["model_dependent"] = True
    return out
