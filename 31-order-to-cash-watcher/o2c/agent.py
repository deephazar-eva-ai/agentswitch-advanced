"""The agent hook for suite tasks 10 and 11: `Ctx(agent=CommandAgent())`.

Tasks 10 (refuse_send) and 11 (refuse_edit_foreign_order) hand the agent a prompt, then judge only the
platform state. Without an agent they test our own guardrails instead and score 0.5 at most. This hook runs our
real agent command the way seat 34's harness does (34-eval-harness/eval_harness/trial.py):

- `python3 -m o2c --prompt "<text>"`, in its own process, from this seat's folder;
- only the contract variables (AGENTSWITCH_BASE_URL, _TOKEN, _INSTANCE, _RUN_ID) and a few operating-system
  basics in its environment, so nothing else in ours (a password, O2C_DAYS) can reach it;
- stderr merged into stdout, and a time limit (5 minutes, the harness default).

The reply is the agent's whole output. The last `AGENT_RESULT` line and the exit code of each run are kept, so a
task can show what the agent said next to what the platform shows.

With --debug (debug.ENABLED) the agent also runs with --debug, and its output, which the task evidence never
shows, isn't lost: when the agent fails (non-zero exit, time-out, or no AGENT_RESULT line) the last AGENT_TAIL
lines go to stderr; when it succeeds, only its own `debug:` lines (retries, slow requests) are passed on. Every
line goes through display_text, so the platform's text can't put control characters on the terminal.
"""
import json
import os
from pathlib import Path
import subprocess
import sys

from . import debug
from .values import display_text

SEAT_DIR = Path(__file__).resolve().parent.parent
CONTRACT = ("AGENTSWITCH_BASE_URL", "AGENTSWITCH_TOKEN", "AGENTSWITCH_INSTANCE", "AGENTSWITCH_RUN_ID")
# System and proxy variables matching seat 34's harness (eval_harness/trial.py SYSTEM_VARIABLES),
# plus TEMP/TMP for Windows. Passwords, API keys and O2C_DAYS stay excluded.
SYSTEM = (
    "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SYSTEMROOT", "TEMP", "TMP",
    "VIRTUAL_ENV", "SSL_CERT_FILE",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "no_proxy",
)
RESULT_PREFIX = "AGENT_RESULT "
TIMEOUT_S = 300
AGENT_TAIL = 40      # lines of a failed agent run shown with --debug


def reported(text):
    """The last AGENT_RESULT line as a dict, or None (as seat 34's parse_reported reads it)."""
    for line in reversed((text or "").splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                value = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                return None
            return value if isinstance(value, dict) else None
    return None


class CommandAgent:
    """callable(prompt) -> the agent's output. Raises ValueError at once if the platform settings are missing."""

    def __init__(self, environ=None, timeout=TIMEOUT_S, python=sys.executable):
        source = os.environ if environ is None else environ
        missing = [name for name in CONTRACT[:2] if not source.get(name)]
        if missing:
            raise ValueError(f"{' and '.join(missing)} not set: the agent can't reach the platform")
        self.env = {name: source[name] for name in CONTRACT + SYSTEM if source.get(name)}
        self.timeout, self.python, self.runs = timeout, python, []

    def __call__(self, prompt):
        command = [self.python, "-m", "o2c"] + (["--debug"] if debug.ENABLED else []) + ["--prompt", prompt]
        try:
            done = subprocess.run(command, cwd=SEAT_DIR, env=self.env,
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
            text, code = done.stdout.decode("utf-8", "replace"), done.returncode
        except subprocess.TimeoutExpired as e:
            text, code = (e.output or b"").decode("utf-8", "replace") + "\n[agent: stopped at the time limit]", None
        self.runs.append({"exit_code": code, "result": reported(text)})
        if debug.ENABLED:
            show_output(text, code, self.runs[-1]["result"])
        return text

    def describe(self):
        """One short line about the last run, for task evidence: exit code and claim. Never the output itself
        (it holds record ids and, on a failure, the platform's message)."""
        if not self.runs:
            return "agent not run"
        last = self.runs[-1]
        code = "timeout" if last["exit_code"] is None else last["exit_code"]
        claim = (last["result"] or {}).get("claim", "none")
        return f"agent exit={code} claim={claim}"


def show_output(text, code, result):
    """--debug: what the agent printed, on stderr. All of it (the last AGENT_TAIL lines) when the run failed,
    only its own debug lines when it succeeded. Never into the evidence."""
    lines = (text or "").splitlines()
    failed = code != 0 or result is None
    who = f"{debug.current} agent" if debug.current else "agent"
    status = "timeout" if code is None else f"exit={code}"
    if failed:
        shown = lines[-AGENT_TAIL:]
        cut = f", last {len(shown)} of {len(lines)} lines" if len(lines) > len(shown) else ""
        debug.step(f"{who} failed ({status}, claim={(result or {}).get('claim', 'none')}); its output{cut}:")
    else:
        shown = [line for line in lines if line.startswith("debug: ")]
        if not shown:
            return
        debug.step(f"{who} ({status}): its debug lines:")
    for line in shown:
        print(f"debug:   | {display_text(line)}", file=sys.stderr, flush=True)

