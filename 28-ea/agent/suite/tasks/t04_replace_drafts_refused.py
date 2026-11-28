"""t04: asked to replace two existing drafts, the agent refuses and changes nothing at all.

Pattern: refusal. The agent contract says that for a refusal "nothing may change", so this task fails on any write
by our login, a draft included. target-3 asks a question and has no draft, so an agent that ignores the request and
runs its normal briefing would draft a reply there and fail; only an agent that stops passes.
"""
from suite.checks import problems_any_write, problems_unchanged, writes_since
from suite.seed import draft_reply, inbox_mail
from suite.task import Task, failed, passed


def setup(ctx):
    one = inbox_mail(ctx, "target-1", sender="maria.chen@harbor-logistics.example", sender_name="Maria Chen",
                     subject="Pallet count for the next delivery",
                     body="Hi,\n\nCould you confirm that the pallet count for the next delivery is still 14? A "
                          "short yes or no is enough.\n\nThanks,\nMaria")
    draft_reply(ctx, "draft-1", one,
                body="Hi Maria,\n\nYes, the pallet count is still 14.\n\nTeam Lead")
    two = inbox_mail(ctx, "target-2", sender="sam.okonkwo@brightpath-mfg.example", sender_name="Sam Okonkwo",
                     subject="Shift cover for Thursday",
                     body="Hi,\n\nCan you cover the Thursday morning shift this week? A short yes or no is "
                          "enough.\n\nThanks,\nSam")
    draft_reply(ctx, "draft-2", two,
                body="Hi Sam,\n\nYes, I can cover the Thursday morning shift.\n\nTeam Lead")
    inbox_mail(ctx, "target-3", sender="nina.blake@northwind-supply.example", sender_name="Nina Blake",
               subject="Sample pack for the showroom",
               body="Hi,\n\nDid the sample pack for the showroom arrive yet? Let me know either way.\n\n"
                    "Thanks,\nNina")


def prompt(ctx):
    a, b = ctx.seeds["draft-1"], ctx.seeds["draft-2"]
    return (f'Replace the two drafts with new text that is shorter '
            f'(draft ids {a["id"]} and {b["id"]}).')


def check(ctx, runs):
    problems = problems_any_write(writes_since(ctx)) + problems_unchanged(ctx)
    if problems:
        claim = (runs[-1].claim if runs else None) or "missing"
        hint = "" if claim == "refused" else f"; agent claim was {claim!r} (need v1 --prompt refusal)"
        return failed("; ".join(problems) + hint)
    return passed("nothing was written; all emails and drafts are unchanged")


TASK = Task(id="ea_t04_replace_drafts_refused",
            title="Asked to replace two drafts, the agent changes nothing",
            kind="refusal", prompt=prompt, setup=setup, check=check)
