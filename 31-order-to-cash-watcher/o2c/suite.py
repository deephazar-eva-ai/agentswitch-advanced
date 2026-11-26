"""Unified v1 suite runner: writes results.json in 34-eval-harness/TASK_FORMAT.md across all seat 31 tasks.

Combines the order tasks (tasks_orders.TASKS) and payment/draft/read-only tasks (tasks_payments.TASKS)
dynamically so new tasks are never dropped by a fixed count, and runs them by suite number (1-12).

This is the runner for seat 34 and anyone running the whole suite. `--date` defaults to today in IST, so a
harness run needs no date; `o2c.tasks_orders` and `o2c.tasks_payments` (one half each) require `--date`.

    python3 -m o2c.suite --instance suryodaya --date 2026-11-21 --agent --out results.json
    python3 -m o2c.suite --instance keystone --only overdue_ageing,unapplied_payments --debug

--only runs just the tasks named (ids as in NUMBERED, comma-separated), in suite order; results.json then
lists only those. An unknown id is a usage error. With --replay, the tasks recorded before the named ones
run first, quietly, so each named task sees the same replies and the same shared report as when it was
recorded (review of #63, P2); results.json still lists only the named tasks.

Debugging (DEBUGGING.md):
--debug prints, on stderr, a line as each task starts and ends (its time, platform calls and
retries), each retry and slow request, any blocked write, the full traceback of a crash (labelled
with the task), and a closing platform summary. The PASS/FAIL lines and results.json don't change.

--record DIR saves every platform request and reply in DIR (calls.jsonl, plus recording.json saying
what was recorded); --replay DIR runs the suite from it, with no platform and no token, on the same
data every time. DIR holds customer records: it must be a folder git ignores (e.g. reports/recordings/
<instance>-<date>) or outside the repo. --agent can't be combined with either: the agent runs as a
separate process that talks to the platform itself, so its calls are neither recorded nor replayed.
A recorded or replayed run is read-only: --allow-writes can't be combined with either, and task 9
(which creates a draft) is reported as blocked, also when AGENTSWITCH_RUN_ID is set.

With --out, the run is also described in <out>.meta.json beside it: commit, start and end, the
source (live, record or replay), platform totals, and per task its seconds, calls and retries.
results.json keeps exactly the TASK_FORMAT.md shape.

Exit 0 when every task passed, 1 when any failed (so `git bisect run` can use a replay), 2 for bad
options, 3 when the platform (or the recording) can't be read.
"""
import argparse
import json
import os
import platform
import subprocess
from datetime import date, datetime
from pathlib import Path

from . import debug, report, tasks_orders, tasks_payments
from .client import Client, McpError, RecordingClient, ReplayClient, platform_failure, stats_of
from .output import days_option, safe_console
from .tasks_payments import Ctx, command_agent, exit_code, run
from .values import display_text


# Suite task numbers 1-12 (PLAN.md, "v1 suite (30 Nov): tasks"), so results.json reads in that order.
NUMBERED = ("uninvoiced_count", "uninvoiced_threshold", "uninvoiced_excludes_drafts", "uninvoiced_partial",
            "overdue_invoices", "overdue_ageing", "unapplied_payments", "unapplied_excludes_outgoing",
            "reminder_is_draft", "refuse_send", "refuse_edit_foreign_order", "report_read_only")


def all_tasks():
    """Every seat 31 suite task from tasks_orders and tasks_payments, by suite number (1-12). The lists are read
    each time, so a task added to either module is never dropped: one without a number runs after the rest, in
    the order found."""
    found = list(tasks_orders.TASKS) + list(tasks_payments.TASKS)
    rank = {task_id: n for n, task_id in enumerate(NUMBERED)}
    return sorted(found, key=lambda t: rank.get(t[0], len(NUMBERED)))


def run_suite(ctx, tasks=None, timings=None, quiet=()):
    """Run the combined suite against `ctx` and return the TASK_FORMAT.md payload. `timings` (a dict),
    if given, gets {task id: {seconds, calls, retries}} for each task. Tasks whose id is in `quiet` still
    run, in their place, but without --debug lines and are left out of the results and timings: a
    selective replay uses it so each named task sees what it saw when recorded."""
    selected = all_tasks() if tasks is None else list(tasks)
    results = []
    for task in selected:
        if task[0] not in quiet:
            results += run(ctx, [task], timings)
            continue
        was = debug.ENABLED
        debug.ENABLED = False
        try:
            run(ctx, [task])
        finally:
            debug.ENABLED = was
    passed = sum(1 for r in results if r["passed"])
    return {
        "tasks": results,
        "summary": f"{ctx.instance}: {passed}/{len(results)} tasks passed",
    }


def write_results(path, payload):
    """Write `results.json` as UTF-8, refusing NaN/Infinity."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, allow_nan=False)
    target.write_text(encoded, encoding="utf-8")


RECORDING_META = "recording.json"


def run_meta(client, instance, as_of, started, finished, timings, source, debug_on):
    """What was run, for <out>.meta.json. Never the token or the platform address."""
    stats = stats_of(client)
    return {"instance": instance, "date": as_of.isoformat(),
            "started": started.isoformat(timespec="seconds"), "finished": finished.isoformat(timespec="seconds"),
            "seconds": round((finished - started).total_seconds(), 1), "commit": _commit(), "source": source,
            "debug": debug_on, "python": platform.python_version(),
            "platform": {"requests": stats["requests"], "retries": stats["retries"],
                         "waited_seconds": round(stats["waited"], 1), "request_seconds": round(stats["seconds"], 1),
                         "by_tool": dict(sorted(stats["by_tool"].items()))},
            "tasks": timings}


def _commit():
    """The checked-out commit and whether files were changed, or None where git isn't available."""
    here = Path(__file__).resolve().parent
    try:
        head = subprocess.run(["git", "-C", str(here), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(here), "status", "--porcelain", "--untracked-files=no", "--", "."],
                               capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    return {"head": head, "changed_files": bool(dirty)}


def meta_path(out):
    """results.json -> results.meta.json, beside it."""
    out = Path(out)
    return out.with_name(f"{out.stem}.meta.json" if out.suffix == ".json" else f"{out.name}.meta.json")


def git_would_track(folder):
    """True when `folder` is inside this repository and not ignored by git, so a recording there
    could be committed. False outside the repository, or where git isn't available."""
    here = Path(__file__).resolve().parent
    try:
        top = subprocess.run(["git", "-C", str(here), "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10, check=True).stdout.strip()
        target = Path(folder).resolve()
        if Path(top).resolve() not in (target, *target.parents):
            return False
        ignored = subprocess.run(["git", "-C", top, "check-ignore", "-q", str(target)], timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False
    return ignored.returncode != 0


def replay_plan(ap, recorded, only):
    """{tasks, quiet} for --replay with --only: the recorded tasks up to the last named one, in recorded
    order, and the ids of those not named (run in their place, quietly). Replies are served per request in recorded order, and one
    report is shared by every task, so a named task replayed alone could see an earlier task's replies;
    run after the same tasks as when recorded, it sees exactly what it saw. A selection the recording
    can't reproduce is a usage error."""
    names = recorded.get("tasks") if isinstance(recorded, dict) else None
    if not (isinstance(names, list) and all(isinstance(n, str) for n in names)):
        ap.error("--replay with --only: the recording doesn't list the tasks it ran (an older recording), "
                 "so one task can't be replayed faithfully. Replay it whole, or record again")
    missing = [t for t in only if t not in names]
    if missing:
        ap.error(f"--replay with --only: {', '.join(missing)} not in the recording (it ran: {', '.join(names) or 'none'})")
    known = {t[0]: t for t in all_tasks()}
    gone = [n for n in names if n not in known]
    if gone:
        ap.error(f"--replay: the recording ran task(s) this code no longer has: {', '.join(gone)}; record again")
    last = max(names.index(t) for t in only)
    run_order = [known[n] for n in names[:last + 1]]
    return {"tasks": run_order, "quiet": {t[0] for t in run_order if t[0] not in only}}


def read_recording_meta(folder):
    try:
        return json.loads((Path(folder) / RECORDING_META).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="o2c.suite", description="Run the full seat 31 suite and write results.json")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"),
                    help="instance label (suryodaya or keystone); default AGENTSWITCH_INSTANCE, or the recording's with --replay")
    ap.add_argument("--date", type=date.fromisoformat, help="report date (default: today in IST)")
    ap.add_argument("--days", type=days_option, help="threshold in days (default O2C_DAYS or 7, or the recording's)")
    ap.add_argument("--mailbox-id", default=os.environ.get("O2C_MAILBOX_ID"))
    ap.add_argument("--accounts-email", default=os.environ.get("O2C_ACCOUNTS_EMAIL"))
    ap.add_argument("--allow-writes", action="store_true", help="let task 9 create a draft (harness copy only)")
    ap.add_argument("--agent", action="store_true",
                    help="tasks 10 and 11: run the real agent (python3 -m o2c --prompt) instead of guardrails only")
    ap.add_argument("--out", help="write results JSON here (e.g. results.json)")
    ap.add_argument("--only", type=lambda v: [x.strip() for x in v.split(",") if x.strip()],
                    help="comma-separated task ids to run (e.g. overdue_ageing,report_read_only); default all")
    ap.add_argument("--debug", action="store_true",
                    help="on stderr: each task's start, end and time, retries, slow requests and tracebacks")
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--record", metavar="DIR", help="save every platform request and reply in DIR (git-ignored)")
    source.add_argument("--replay", metavar="DIR", help="run from a recording in DIR: no platform, no token")
    args = ap.parse_args(argv)
    debug.ENABLED = args.debug
    if args.only is not None:
        known = [t[0] for t in all_tasks()]
        unknown = [t for t in args.only if t not in known]
        if unknown or not args.only:
            ap.error(f"--only: unknown task id(s): {', '.join(unknown) or '(none given)'}; known: {', '.join(known)}")
    if args.allow_writes and (args.record or args.replay):
        ap.error("--allow-writes can't be combined with --record or --replay: a recording is read-only, "
                 "so it never holds a draft (task 9 is reported as blocked)")
    recorded = read_recording_meta(args.replay) if args.replay else {}
    if args.replay:
        if not (Path(args.replay) / "calls.jsonl").is_file():
            ap.error(f"--replay: no recording in {args.replay} (calls.jsonl missing)")
        if args.instance and recorded.get("instance") and args.instance != recorded["instance"]:
            ap.error(f"--replay: the recording is of {recorded['instance']}, not {args.instance}")
        args.instance = recorded.get("instance") or args.instance
        if args.date is None and recorded.get("date"):
            args.date = date.fromisoformat(recorded["date"])     # replay on the recorded date unless told otherwise
        if args.days is None and recorded.get("days") is not None:
            args.days = days_option(str(recorded["days"]))
    if args.days is None:
        try:
            args.days = days_option(os.environ.get("O2C_DAYS", "7"))
        except argparse.ArgumentTypeError as e:      # bug hunt E5: a usage error, never a traceback
            ap.error(f"O2C_DAYS: {e}")
    if not args.instance:
        ap.error("--instance is required (or set AGENTSWITCH_INSTANCE)")
    plan = None
    if args.replay and args.only is not None:
        plan = replay_plan(ap, recorded, args.only)
    if args.agent and (args.record or args.replay):
        ap.error("--agent can't be combined with --record or --replay: the agent runs as a separate process "
                 "that talks to the platform itself, so its calls are neither recorded nor replayed")
    if args.record and git_would_track(args.record):
        ap.error(f"--record: {args.record} is a folder git would track, and a recording holds customer "
                 "records. Use one git ignores, e.g. reports/recordings/<instance>-<date>")
    safe_console()

    started = datetime.now(report.IST)             # before the first read, so the totals add up
    try:
        if args.replay:
            client = ReplayClient(args.replay)
        elif args.record:
            client = RecordingClient(args.record)
        else:
            client = Client()
    except McpError as e:
        debug.crash(e, "o2c.suite")
        platform_failure("o2c.suite", e)

    report_date = args.date or report.today_ist()
    source_name = "replay" if args.replay else "record" if args.record else "live"
    if args.record:
        write_results(Path(args.record) / RECORDING_META,
                      {"instance": args.instance, "date": report_date.isoformat(), "days": args.days,
                       "tasks": [t[0] for t in all_tasks() if args.only is None or t[0] in args.only],
                       "recorded_at": started.isoformat(timespec="seconds"), "commit": _commit()})
    ctx = Ctx(
        client,
        args.instance,
        report_date,
        args.days,
        mailbox_id=args.mailbox_id,
        accounts_email=args.accounts_email,
        agent=command_agent(ap, args.agent),
        allow_writes=args.allow_writes or bool(os.environ.get("AGENTSWITCH_RUN_ID")),
    )
    ctx.recorded = bool(args.record or args.replay)
    timings = {}
    if plan is not None:
        out = run_suite(ctx, plan["tasks"], timings=timings, quiet=plan["quiet"])
    else:
        selected = None if args.only is None else [t for t in all_tasks() if t[0] in args.only]
        out = run_suite(ctx, selected, timings=timings)
    finished = datetime.now(started.tzinfo)
    if args.out:
        write_results(args.out, out)
        write_results(meta_path(args.out), run_meta(client, args.instance, report_date, started, finished,
                                                    timings, source_name, args.debug))
    s = stats_of(client)
    debug.step(f"platform: {s['requests']} requests in {s['seconds']:.1f} s, {s['retries']} retries "
               f"({s['waited']:.0f} s waiting); by tool: "
               + (", ".join(f"{k} {v}" for k, v in s["by_tool"].most_common(8)) or "none"))
    debug.step(f"total {(finished - started).total_seconds():.1f} s")
    for r in out["tasks"]:
        print(display_text(f"{'PASS' if r['passed'] else 'FAIL'} {r['score']:.1f} {r['id']}: {r['evidence']}"))
    print(out["summary"])
    raise SystemExit(exit_code(out["tasks"]))


if __name__ == "__main__":
    main()
