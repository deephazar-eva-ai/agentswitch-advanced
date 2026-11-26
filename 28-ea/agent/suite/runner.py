"""Runs the seat 28 suite and writes results.json in the course format (34-eval-harness/TASK_FORMAT.md, section 3).

Run from 28-ea/agent:
    python -m suite.runner --list                                  # show the tasks; no network
    python -m suite.runner --out results.json                      # course run: AGENTSWITCH_* variables are set
    python -m suite.runner --live --env-file PATH --out results.json   # our own check on live Keystone
    python -m suite.runner --live --env-file PATH --only t09,t12   # only some tasks

For each task: setup (test data) -> the agent runs with the task's prompt -> check (platform data) -> verdict.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import pkgutil
import secrets as _secrets
import sys
import time
from dataclasses import dataclass, field

from ea_agent import config, redact
from ea_agent.mcp import McpClient

from . import agent_run
from .platform import KEYSTONE_URL, Account, InfraError, SeedClient, ThreadReader, is_live
from .task import KINDS, Context, Task

HARNESS_VARS = ("AGENTSWITCH_BASE_URL", "AGENTSWITCH_TOKEN", "AGENTSWITCH_INSTANCE")
# The whole run must end inside the harness file's timeout_minutes (25). Past this, the tasks left are reported as
# infrastructure failures instead of being cut off with no results at all.
SUITE_SECONDS = 22 * 60


@dataclass
class Mode:
    name: str                      # "harness" (course run on a copy) or "live" (our check on live Keystone)
    base_url: str
    instance: str
    run_id: str
    env_file: str | None = None
    secrets: set = field(default_factory=set, repr=False)  # values to redact; never printed


def resolve_mode(args, environ: dict) -> Mode:
    """The run mode, or SystemExit with one plain line. Never prints a secret value."""
    given = [k for k in HARNESS_VARS if (environ.get(k) or "").strip()]
    if args.live:
        if given or (environ.get("AGENTSWITCH_RUN_ID") or "").strip():
            raise SystemExit("--live cannot be combined with AGENTSWITCH_* variables; unset them first")
        if not args.env_file:
            raise SystemExit("--live needs --env-file PATH (a file with EA_EMAIL and EA_PASSWORD_KEYSTONE)")
        run_id = "live-" + time.strftime("%Y%m%d%H%M%S", time.gmtime()) + "-" + _secrets.token_hex(3)
        return Mode("live", KEYSTONE_URL, "keystone", run_id, env_file=os.path.abspath(args.env_file))
    if not given:
        raise SystemExit("no AGENTSWITCH_* variables: this is not a course run. For our own live check, "
                         "pass --live --env-file PATH (see TEST_SUITE_GUIDE.md)")
    missing = [k for k in HARNESS_VARS + ("AGENTSWITCH_RUN_ID",) if not (environ.get(k) or "").strip()]
    if missing:
        raise SystemExit(f"missing {', '.join(missing)}: the course runner always sets all four")
    base = environ["AGENTSWITCH_BASE_URL"].strip().rstrip("/")
    if not base.startswith(("https://", "http://")):
        raise SystemExit("AGENTSWITCH_BASE_URL must be an http(s) URL")
    if is_live(base):
        raise SystemExit("AGENTSWITCH_BASE_URL is a live system. The suite writes test data, so a course-style run "
                         "only goes to a copy. For our own live check use --live (see TEST_SUITE_GUIDE.md)")
    instance = environ["AGENTSWITCH_INSTANCE"].strip().lower()
    if instance != "keystone":
        raise SystemExit("the seat 28 suite runs on keystone only (Suryodaya has no mailbox for this login)")
    return Mode("harness", base, instance, environ["AGENTSWITCH_RUN_ID"].strip())


def connect(mode: Mode, environ: dict, opener=None) -> tuple[Account, SeedClient, ThreadReader]:
    if mode.name == "harness":
        token = environ["AGENTSWITCH_TOKEN"].strip()
        reader = McpClient(mode.base_url, token, opener=opener)
    else:
        settings = config.load_env_file(mode.env_file)
        email, password = (settings.get("EA_EMAIL") or "").strip(), settings.get("EA_PASSWORD_KEYSTONE") or ""
        if not email or not password:
            raise InfraError("the env file has no EA_EMAIL or EA_PASSWORD_KEYSTONE")
        mode.secrets.add(password)
        reader = McpClient.login(KEYSTONE_URL, email, password, opener=opener)
        token = reader._token  # the login's own token, used only for test-data writes in this process
    mode.secrets.add(token)
    account = Account(reader)
    threads = ThreadReader(mode.base_url, token, reader._parse, opener=opener)
    return account, SeedClient(mode.base_url, token, account, opener=opener), threads


@dataclass
class Broken:
    """A task file that cannot be used. It still shows up, as an infrastructure failure."""
    id: str
    title: str
    reason: str


def discover(package: str = "suite.tasks") -> list:
    pkg = importlib.import_module(package)
    found, seen = [], set()
    for m in sorted(pkgutil.iter_modules(pkg.__path__), key=lambda m: m.name):
        if m.name.startswith("_"):
            continue
        try:
            task = getattr(importlib.import_module(f"{package}.{m.name}"), "TASK", None)
        except Exception as exc:
            found.append(Broken(m.name, f"{m.name} (could not be loaded)", f"import failed ({type(exc).__name__})"))
            continue
        problem = _task_problem(task, seen)
        if problem:
            found.append(Broken(m.name, f"{m.name} (not a valid task)", problem))
            continue
        seen.add(task.id)
        found.append(task)
    return found


def _task_problem(task, seen: set) -> str | None:
    if not isinstance(task, Task):
        return "has no TASK = Task(...)"
    if not task.id or task.id in seen:
        return "task id is empty or used twice"
    if task.kind not in KINDS:
        return f"kind must be one of {KINDS}"
    if task.runs not in (1, 2):
        return "runs must be 1 or 2"
    if not all(callable(f) for f in (task.prompt, task.setup, task.check)):
        return "prompt, setup and check must be functions"
    return None


def _infra(task_id: str, title: str, reason: str) -> dict:
    return {"id": task_id, "title": title, "passed": False, "score": 0.0, "evidence": f"infrastructure: {reason}"}


def _agent_problem(mode: Mode, run: agent_run.AgentRun) -> str | None:
    if run.timed_out:
        return f"the agent was stopped at the time limit ({agent_run.AGENT_SECONDS} s)"
    ok = (0,) if mode.name == "harness" else (0, 2)   # exit 2 in a live run: briefing printed, part failed
    if run.code not in ok:
        return f"the agent could not finish (exit {run.code}): {run.last_line() or 'no output'}"
    return None


def _check_verdict(v) -> dict:
    if not isinstance(v, dict) or not isinstance(v.get("passed"), bool) or not isinstance(v.get("evidence"), str):
        raise InfraError("check returned no verdict; use passed(...) or failed(...)")
    return v


def run_task(task: Task, account: Account, seeder: SeedClient, mode: Mode, environ: dict,
             agent=agent_run.run_agent, threads=None) -> dict:
    task_id, title = f"{mode.instance}:{task.id}", task.title
    ctx = Context(account, seeder, mode.run_id, task.id, threads=threads)
    try:
        task.setup(ctx)
        if not ctx.seeds:
            raise InfraError("setup created no test data")
        # the earliest test email or thread: each thread is created just before its email
        stamps = [s.get("created_at") for s in list(ctx.seeds.values()) + list(ctx.seed_threads.values())]
        if not all(isinstance(s, str) and s for s in stamps):
            raise InfraError("test mail has no created_at time, so later writes cannot be told apart")
        ctx.since = min(stamps)
        prompt = task.prompt(ctx)
        runs = []
        for _ in range(task.runs):
            run = agent(mode, prompt, environ)
            runs.append(run)
            problem = _agent_problem(mode, run)
            if problem:
                return _infra(task_id, title, problem)
        verdict = _check_verdict(task.check(ctx, runs))
        if not verdict["passed"] and verdict.get("model_dependent") and any(r.not_assessed for r in runs):
            return _infra(task_id, title, f"the model did not assess all mail, so this cannot be judged "
                                          f"({verdict['evidence']})")
        last = runs[-1]
        tail = f"agent exit {last.code} in {last.seconds} s" + (f", claim {last.claim}" if last.claim else "")
        return {"id": task_id, "title": title, "passed": verdict["passed"], "score": float(verdict["score"]),
                "evidence": f"{verdict['evidence']}; {tail}"}
    except InfraError as exc:
        return _infra(task_id, title, str(exc))
    except Exception as exc:  # a bug in a task must show up, never pass and never vanish
        return _infra(task_id, title, f"{type(exc).__name__} in the task code")


def _selected(tasks: list, only: str | None) -> list:
    if not only:
        return tasks
    wanted = [w.strip() for w in only.split(",") if w.strip()]
    return [t for t in tasks if any(t.id == w or t.id.startswith(f"ea_{w}_") or t.id.startswith(w) for w in wanted)]


def write_results(path: str, results: list, instance: str, secrets: set) -> None:
    """Written after every task, through a temporary file, so a run that is stopped midway still leaves a complete
    results.json: tasks not reached yet are already in it as infrastructure failures."""
    passed = sum(1 for r in results if r["passed"])
    report = {"tasks": results, "summary": f"{instance}: {passed}/{len(results)} tasks passed"}
    report = redact.redact_value(report, secrets)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)


def main(argv=None, environ=None, opener=None, agent=agent_run.run_agent, clock=time.monotonic) -> int:
    environ = dict(os.environ if environ is None else environ)
    p = argparse.ArgumentParser(prog="python -m suite.runner", description="Seat 28 EA test suite")
    p.add_argument("--out", default="results.json", help="where results.json goes (default ./results.json)")
    p.add_argument("--live", action="store_true", help="our own check on live Keystone (see the guide)")
    p.add_argument("--env-file", dest="env_file", help="with --live: file with EA_EMAIL and EA_PASSWORD_KEYSTONE")
    p.add_argument("--only", help="comma-separated task numbers or ids, e.g. t09,t12")
    p.add_argument("--list", action="store_true", help="list the tasks and exit; no network")
    args = p.parse_args(argv)
    tasks = _selected(discover(), args.only)
    if args.list:
        for t in tasks:
            print(f"{t.id:40} {getattr(t, 'kind', 'broken'):8} {t.title}")
        return 0
    if not tasks:
        print("error: no task matches --only", file=sys.stderr)
        return 1
    try:
        mode = resolve_mode(args, environ)
    except SystemExit as exc:
        print(f"error: {exc.code}", file=sys.stderr)
        return 1
    secrets = redact.secrets_from_env(environ)
    try:
        account, seeder, threads = connect(mode, environ, opener=opener)
        start_problem = None
    except InfraError as exc:
        start_problem = str(exc)
    except SystemExit as exc:
        start_problem = str(exc.code)
    except Exception as exc:
        start_problem = f"could not connect ({getattr(exc, 'message', type(exc).__name__)})"
    secrets |= mode.secrets
    results = [_infra(f"{mode.instance}:{t.id}", t.title, "not run: the suite stopped before this task")
               for t in tasks]
    write_results(args.out, results, mode.instance, secrets)
    started = clock()
    for i, task in enumerate(tasks):
        if isinstance(task, Broken):
            result = _infra(f"{mode.instance}:{task.id}", task.title, f"task file {task.id}.py {task.reason}")
        elif start_problem:
            result = _infra(f"{mode.instance}:{task.id}", task.title, start_problem)
        elif clock() - started + task.runs * agent_run.AGENT_SECONDS + 60 > SUITE_SECONDS:
            result = _infra(f"{mode.instance}:{task.id}", task.title,
                            f"suite time budget reached ({SUITE_SECONDS // 60} minutes); task not started")
        else:
            result = run_task(task, account, seeder, mode, environ, agent=agent, threads=threads)
        result = redact.redact_value(result, secrets)
        print(f"{'PASS' if result['passed'] else 'FAIL'}  {result['id']}  {result['evidence']}", flush=True)
        results[i] = result
        write_results(args.out, results, mode.instance, secrets)
    print(f"{sum(r['passed'] for r in results)}/{len(results)} passed; results in {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
