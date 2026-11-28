"""v1 --prompt refusal: send/book/delete/accept/replace/edit stop before any platform write."""
from __future__ import annotations

import io
import json
from contextlib import redirect_stderr, redirect_stdout

from ea_agent import refusal, runner
from fakes import FakeClient


def test_should_refuse_words():
    assert refusal.should_refuse("Send my draft reply now")
    assert refusal.should_refuse("Replace the two drafts with shorter text")
    assert refusal.should_refuse("Delete these two emails permanently")
    assert refusal.should_refuse("Book the meeting and send the invite")
    assert not refusal.should_refuse('Reply to the meeting request "X" with times that work for me.')
    assert not refusal.should_refuse("Draft a reply to the status request")
    assert not refusal.should_refuse(None)
    assert not refusal.should_refuse("")


def test_refuse_send_exits_zero_without_platform_or_model():
    buf = io.StringIO()
    calls = []

    def factory(system, base, token):
        calls.append((system, base, token))
        return FakeClient()

    with redirect_stdout(buf), redirect_stderr(buf):
        result, code = runner.run(
            {"prompt": 'Send my draft reply to "Brochure" now (draft id 1, original id 2).',
             "no_file": True, "no_llm": True, "system": ["keystone"]},
            {"EA_EMAIL": "me@example.test", "EA_PASSWORD_KEYSTONE": "pw"},
            False, client_factory=factory)
    assert code == 0
    assert result.get("refused") is True
    assert calls == []
    assert "Refused (v1):" in buf.getvalue()
    line = [ln for ln in buf.getvalue().splitlines() if ln.startswith("AGENT_RESULT ")][-1]
    assert json.loads(line[len("AGENT_RESULT "):])["claim"] == "refused"


def test_normal_prompt_still_connects():
    # A non-refusal prompt must not take the early-exit path (it will fail login with this env, which proves
    # we got past the refusal check and tried to run).
    buf = io.StringIO()
    with redirect_stdout(buf), redirect_stderr(buf):
        _, code = runner.run(
            {"prompt": 'Reply to the meeting request "X" (id 1) with times that work for me.',
             "no_file": True, "no_llm": True, "system": ["keystone"]},
            {"EA_EMAIL": "me@example.test"},  # no password → login failure path
            False, client_factory=lambda *a: FakeClient())
    assert code == 1
    assert "Refused:" not in buf.getvalue()
