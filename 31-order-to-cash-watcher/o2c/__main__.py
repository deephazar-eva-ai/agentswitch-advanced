"""Run the daily exception report.

    python3 -m o2c --days 7 --instance suryodaya --out reports/

Reads AGENTSWITCH_BASE_URL and AGENTSWITCH_TOKEN from the environment.

Standard output carries only the record ids of the orders not invoiced, one per line, oldest
first, so seat 34 can read it without filtering. Everything else (the summary, warnings) goes to
standard error. `--markdown` prints the full report on standard output instead, for a person.
Exit 4 means the report was produced but the ID list may be missing orders; stderr says why.
The last line is `AGENT_RESULT {...}` for seat 34's harness: the claim ("done", or "refused" when the
prompt asked for a change) and the same ids under `answer.orders_not_invoiced`.

Exit 1 is an unexpected error: the traceback, then `AGENT_RESULT {"claim":"failed"}`, then one plain
line, `o2c: unexpected error: <type>: <message>`, last, which seat 34's harness reports as the reason
(agent contract, section 5). Exit 3 is a platform that can't be read. --debug also prints, on
stderr, each retry, any request over 10 s, any blocked write, and the traceback of a platform error
(o2c/debug.py). Seat 34's harness adds only --prompt, so --debug goes in the command seat 31 registers.

`--explain ID` says why one order, invoice or payment is in or out of each section, rule by rule,
with the report's own verdict (o2c/explain.py); add `--replay DIR` to read from a suite recording.
It prints no ID list and no AGENT_RESULT; exit 5 means no record has that id or number.
"""
import argparse
import json
import os
import sys
from datetime import date
from pathlib import Path

from . import debug, explain, prompt as prompt_mod, report
from .client import RECORDING_FILE, Client, McpError, ReplayClient, platform_failure
from .values import display_text
from .output import (EXIT_INCOMPLETE, agent_result, days_option, instance_label, order_ids, order_list_gaps,
                     report_stem, safe_console, write_pair)

nonnegative_days = days_option   # older name, kept for callers
FAILED_RESULT = 'AGENT_RESULT {"claim":"failed"}'


def unexpected(prog, error, claim=True):
    """The traceback (stderr), a "failed" claim (stdout), then one plain reason line (stderr, last).
    Seat 34's harness merges stderr into stdout and reports the last line with a letter in it as the
    reason, so the reason line must come after the claim; the claim is found by scanning back for
    AGENT_RESULT, so it needn't be last. Every line goes through display_text, so server text can't
    put control characters on the terminal (bug hunt E2). No token can be in it: client errors name
    settings, never values. claim=False (--explain) prints no AGENT_RESULT."""
    debug.print_traceback(error)
    if claim:
        print(FAILED_RESULT, flush=True)
    print(f"{prog}: unexpected error: {type(error).__name__}: {' '.join(display_text(error).split())}",
          file=sys.stderr, flush=True)


def write_report(rep, md, out, instance):
    """Write <instance>-<date>.json and .md as UTF-8, whatever the machine's default encoding.

    Returns the file stem. Customer names can be any script (e.g. 客户); a cp1252 default on
    Windows would otherwise fail on the Markdown after the JSON was already written.
    """
    stem = report_stem(instance, rep["report_date"])
    # Serialize before touching output files. NaN/Infinity must never appear in JSON.
    encoded = json.dumps(rep, indent=2, allow_nan=False)
    write_pair(out, stem, encoded, md)
    return stem


def explain_main(ap, args):
    """--explain: never the ID list or AGENT_RESULT, so it can't be mistaken for a harness run."""
    if args.prompt is not None or args.out or args.markdown:
        ap.error("--explain can't be combined with --prompt, --out or --markdown")
    if not args.explain.strip():
        ap.error("--explain needs a record id or number")
    recorded = {}
    if args.replay:
        if not (Path(args.replay) / RECORDING_FILE).is_file():
            ap.error(f"--replay: no recording in {args.replay} ({RECORDING_FILE} missing)")
        recorded = explain.read_recording_meta(args.replay)
        if args.instance and recorded.get("instance") and args.instance != recorded["instance"]:
            ap.error(f"--replay: the recording is of {recorded['instance']}, not {args.instance}")
        if args.date is None and recorded.get("date"):
            args.date = date.fromisoformat(recorded["date"])     # the recorded date unless told otherwise
    try:
        days = explain.resolve_days(args, recorded, days_option)
    except argparse.ArgumentTypeError as e:
        ap.error(f"O2C_DAYS: {e}")
    try:
        client = ReplayClient(args.replay) if args.replay else Client()
        code = explain.run(client, args.explain.strip(), args.date or report.today_ist(), days)
    except McpError as e:
        debug.crash(e, "o2c --explain")
        platform_failure("o2c", e)
    except Exception as e:                           # a reason line, never a raw traceback (D2, E2)
        unexpected("o2c --explain", e, claim=False)
        raise SystemExit(1)
    raise SystemExit(code)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="o2c", description="Daily order-to-cash exception report (read-only)")
    ap.add_argument("--days", type=days_option,
                    help="N: nonnegative days before an order counts as not invoiced (default: the prompt, then O2C_DAYS, then 7)")
    ap.add_argument("--date", type=date.fromisoformat, help="report date (default: today in IST)")
    ap.add_argument("--instance", default=os.environ.get("AGENTSWITCH_INSTANCE"), help="label for the report, e.g. suryodaya")
    ap.add_argument("--out", type=Path, help="folder to write <instance>-<date>.md and .json")
    ap.add_argument("--markdown", action="store_true",
                    help="print the full Markdown report on stdout instead of the order ID list")
    # Seat 34 appends this argument to every seat command. Only a number of days and specific order
    # ids or numbers are read from it (o2c/prompt.py); instructions in it are never followed.
    ap.add_argument("--prompt", help="the \"which orders\" question: a number of days and/or order ids; no model is called")
    ap.add_argument("--debug", action="store_true",
                    help="on stderr: retries, slow requests, blocked writes and tracebacks")
    ap.add_argument("--explain", metavar="ID",
                    help="say why one order, invoice or payment (id or number) is in or out of each section; "
                         "prints its values, which are customer data (o2c/explain.py)")
    ap.add_argument("--replay", metavar="DIR",
                    help="with --explain: read from a suite recording (o2c.suite --record) instead of the platform")
    args = ap.parse_args(argv)
    debug.ENABLED = args.debug
    safe_console()
    if args.replay and args.explain is None:
        ap.error("--replay works only with --explain (to replay a suite run: python3 -m o2c.suite --replay DIR)")
    if args.explain is not None:
        explain_main(ap, args)
    try:
        instance_label(args.instance)
    except ValueError as e:
        ap.error(str(e))
    asked = prompt_mod.read(args.prompt)
    notes = list(asked.notes)
    if args.days is not None:
        days = args.days
        if asked.days is not None and asked.days != args.days:
            notes = [n for n in notes if not n.startswith("orders more than")] + [f"--days {args.days} wins over the prompt"]
    elif asked.days is not None:
        days = asked.days
    else:
        try:
            days = days_option(os.environ.get("O2C_DAYS", "7"))
        except argparse.ArgumentTypeError as e:
            ap.error(f"O2C_DAYS: {e}")
    args.days = days
    if args.prompt is not None and notes:
        print(f"o2c: prompt: {'; '.join(notes)}", file=sys.stderr)
    if asked.write:
        # Said once, before the read-only report runs. Nothing in this command can change a record.
        print(f"o2c: refused: the prompt asks to {asked.write} something; this agent only reads and reports. "
              "A person makes any change in the platform", file=sys.stderr)

    try:
        rep = report.build(Client(), report_date=args.date, days=args.days, instance=args.instance)
        md = report.to_markdown(rep)
        stem = write_report(rep, md, args.out, args.instance) if args.out else None
        ids = order_ids(rep, asked.orders)
        gaps = order_list_gaps(rep, asked.orders)
    except McpError as e:
        debug.crash(e, "o2c")
        platform_failure("o2c", e)
    except Exception as e:                           # anything else: say what failed, claim "failed"
        unexpected("o2c", e)
        raise SystemExit(1)
    if args.out:
        counts = ", ".join(f"{k}={len(v['rows'])}" if v["status"] == "ok" else f"{k}={v['status']}" for k, v in rep["sections"].items())
        issues = rep["sections"]["orders_not_invoiced"].get("date_issues", [])
        if issues:
            counts += f", order_date_issues={len(issues)}"
        validation = sum(len(sec.get("data_issues", [])) for sec in rep["sections"].values())
        if validation:
            counts += f", validation_issues={validation} (partial coverage)"
        print(f"wrote {args.out / stem}.md ({counts})", file=sys.stderr)
    if args.markdown:
        print(md)
    else:
        if asked.orders:
            print(f"o2c: {len(ids)} of the {len(asked.orders)} order(s) named are not invoiced after {args.days} days "
                  "(the others are invoiced, too recent or not found)", file=sys.stderr)
        elif not args.out:
            print(f"o2c: {len(ids)} orders not invoiced after {args.days} days on {rep['report_date']}; "
                  "order record ids on stdout, oldest first (--out or --markdown for the full report)", file=sys.stderr)
        sys.stdout.write("".join(i + "\n" for i in ids))
    sys.stdout.flush()   # the ids come before the lines below when the harness merges the two streams
    if gaps:
        print(f"o2c: the order list may be incomplete: {'; '.join(gaps)}", file=sys.stderr)
        sys.stderr.flush()
    # Always the last line: seat 34 reads the claim and the recount answer from it.
    print(agent_result(ids, refused=bool(asked.write), gaps=gaps), flush=True)
    if gaps:
        raise SystemExit(EXIT_INCOMPLETE)


if __name__ == "__main__":
    main()
