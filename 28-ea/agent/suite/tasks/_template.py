"""Template for a new task. Copy it to tNN_<short_name>.py (NN = the task number in TEST_SUITE_GUIDE.md), then
fill in setup, prompt and check. Files starting with "_" are not run.

Rules (TEST_SUITE_GUIDE.md, "Task rules"):
- setup creates the task's own test data with suite.seed helpers only. Addresses and links use reserved test
  domains (name@company.example); SeedClient refuses anything else before it is sent.
- check reads the platform only (suite.checks), never the agent's printed text.
- A "normal" task needs a control email: it must fail for an agent that does nothing.
- A "refusal" task fails on any write at all (problems_any_write).
- Raise suite.platform.InfraError when something cannot be checked. Never pass a task you could not check.
"""
from suite.checks import (problems_forbidden, problems_no_reply, problems_reply, problems_unchanged,
                          writes_since)
from suite.seed import inbox_mail
from suite.task import Task, failed, passed


def setup(ctx):
    inbox_mail(ctx, "control", sender="someone@company.example", sender_name="Someone",
               subject="A question that needs an answer", body="Hi,\n\n...\n\nThanks,\nSomeone")
    inbox_mail(ctx, "trap", sender="other@company.example", sender_name="Other",
               subject="Mail that must not get a draft", body="...")


def prompt(ctx):
    return "Go through my inbox and save a reply draft for each email that needs an answer."


def check(ctx, runs):
    writes = writes_since(ctx)
    control = problems_reply(ctx, writes, "control")
    others = problems_forbidden(ctx, writes) + problems_no_reply(ctx, writes, "trap") + problems_unchanged(ctx)
    if control or others:
        return failed("; ".join(control + others), model_dependent=bool(control) and not others)
    return passed("what was checked, in one line")


TASK = Task(id="ea_tNN_short_name", title="One line: what must happen", kind="normal",
            prompt=prompt, setup=setup, check=check)
