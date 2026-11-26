"""Runs the EA agent once, as its own process, with only the settings it needs.

Course run (harness): the course runner's variables, exactly as the agent contract lists them, plus a few
operating-system basics. Nothing else from our environment reaches the agent.
Live run (--live): the agent's normal login through the env file, plus the model settings. No platform token.

The process runs in its own process group, so a time-out stops everything it started.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ea_agent import redact

AGENT_DIR = Path(__file__).resolve().parent.parent   # 28-ea/agent, where `python -m ea_agent` works
MAIL_CAP = 10          # the agent reads the newest 10 inbox emails; a task's own test mail is always the newest
AGENT_SECONDS = 150    # per agent run; the agent's own model limit is 60 s, a live run takes about 40 s
OUTPUT_TAIL = 4000     # characters of output kept, as the course runner does

CONTRACT_VARS = ("AGENTSWITCH_BASE_URL", "AGENTSWITCH_TOKEN", "AGENTSWITCH_INSTANCE", "AGENTSWITCH_RUN_ID",
                 "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_PROJECT_ID", "OPENAI_EXTRA_BODY")
MODEL_VARS = ("EA_LLM_URL", "EA_LLM_API", "EA_LLM_MODEL", "EA_LLM_KEY", "EA_LLM_EXTRA_BODY",
              "OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_PROJECT_ID", "OPENAI_EXTRA_BODY")
OS_VARS = ("PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT",
           "SSL_CERT_FILE", "SSL_CERT_DIR", "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
           "http_proxy", "https_proxy", "no_proxy")
NOT_ASSESSED = ("not assessed", "could not be assessed")
RESULT_PREFIX = "AGENT_RESULT "


@dataclass
class AgentRun:
    code: int | None       # exit code; None when the run was stopped at the time limit
    output: str            # stdout and stderr, redacted, last OUTPUT_TAIL characters
    claim: str | None      # the agent's AGENT_RESULT claim, if it printed one (never decides a verdict)
    timed_out: bool
    seconds: float

    @property
    def not_assessed(self) -> bool:
        low = self.output.lower()
        return any(marker in low for marker in NOT_ASSESSED)

    def last_line(self) -> str:
        lines = [ln for ln in self.output.splitlines() if ln.strip() and not ln.startswith(RESULT_PREFIX)]
        return lines[-1][:200] if lines else ""


def agent_env(mode, environ: dict) -> dict:
    names = OS_VARS + (CONTRACT_VARS if mode.name == "harness" else MODEL_VARS + ("EA_TOKEN_CACHE",))
    return {k: environ[k] for k in names if environ.get(k)}


def agent_command(mode, prompt: str) -> list[str]:
    cmd = [sys.executable, "-m", "ea_agent", "--no-file", "--mail-cap", str(MAIL_CAP)]
    if mode.name == "live":
        cmd += ["--system", "keystone", "--env-file", str(mode.env_file)]
    return cmd + ["--prompt", prompt]


def claim_of(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        if line.startswith(RESULT_PREFIX):
            try:
                value = json.loads(line[len(RESULT_PREFIX):])
            except ValueError:
                return None
            return value.get("claim") if isinstance(value, dict) else None
    return None


def run_agent(mode, prompt: str, environ: dict, timeout: float = AGENT_SECONDS) -> AgentRun:
    env = agent_env(mode, environ)
    secrets = redact.secrets_from_env(environ) | set(mode.secrets)
    start = time.monotonic()
    proc = subprocess.Popen(agent_command(mode, prompt), cwd=str(AGENT_DIR), env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
        code, timed_out = proc.returncode, False
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        out, _ = proc.communicate()
        code, timed_out = None, True
    text = redact.redact((out or b"").decode("utf-8", "replace"), secrets)
    return AgentRun(code=code, output=text[-OUTPUT_TAIL:], claim=claim_of(text), timed_out=timed_out,
                    seconds=round(time.monotonic() - start, 1))
