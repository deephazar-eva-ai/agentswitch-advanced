# Seat 33 demo script (≤ 5 minutes)

Timed speakable script for the DoD demo recording. Facts match the seat README and catalogue on `main`.

**Recording checklist:** hide tokens and passwords; use offline tests or redacted sample output only; do not show live request/response bodies for findings; stay under **5:00**.

---

## 0:00–0:30 — Intro

**On screen:** [README.md](../README.md) Scope section, or the `33-red-team/` folder.

**Say:**

> Seat 33 is Red Team. We adversarially test the AgentSwitch platform and the other seats' agents: permission boundaries, prompt injection, and cross-tenant reads. We use a deliberately low-privilege login — crm, email, support, projects. We run on both Suryodaya and Keystone. On live data we stay read-only; anything that writes goes on the Eval-Harness throwaway copy.

---

## 0:30–1:45 — What we tested

**On screen:** [catalogue/attempts.md](../catalogue/attempts.md) theme table, then [suite/README.md](../suite/README.md) task table.

**Say:**

> First we built a boundary catalogue — twenty-five attempts across both systems. The themes were: over-privilege reads, write and privilege escalation, cross-tenant checks, email privacy, auth and tokens, an injection canary on our own record, and foreign writes that we deferred to the suite.
>
> That catalogue fed a twelve-task Eval-Harness suite, all implemented. Tasks one and two check auth — no token and a tampered token. Task three checks email privacy. Tasks four through seven try foreign writes — skill, task, memory, tool policy — on the harness copy only. Task eight is a read-only persona write guard. Tasks nine and ten track our two filed bugs as regression checks. Tasks eleven and twelve were chosen from a scope sweep: both refused on both systems.

---

## 1:45–2:45 — What we found

**On screen:** catalogue result tally; [findings/BUG-33-001.md](../findings/BUG-33-001.md) and [BUG-33-002.md](../findings/BUG-33-002.md) title tables only.

**Say:**

> Catalogue result: thirteen Held, two Broke, ten Unclear.
>
> Broke one — BUG-33-001, Low: a low-privilege seat can read email IP-pool configuration. Seen on Suryodaya; status new, filed and Received.
>
> Broke two — BUG-33-002, Medium: a low-privilege seat can read job-ledger forensics. Seen on both systems; status new, filed and Received.
>
> Most other boundaries Held. The Unclear cases need a same-instance foreign id we cannot see from our login, or a copy run — they are not silent passes.

*(Do not open steps or evidence fields on camera — public files keep title, component, and status only.)*

---

## 2:45–4:30 — How the suite runs

**On screen:** [agentswitch-harness.toml](../agentswitch-harness.toml), then a terminal with offline tests or a prior PASS/SKIP summary — never a live token.

**Say:**

> The official suite entry is `agentswitch-harness.toml` at the top of `33-red-team/`. Its run command is: change into `suite/`, then `python -m harness.runner --out results.json`. Instances are Suryodaya and Keystone; timeout ten minutes.
>
> You need three environment variables: base URL, instance name, and token. No credentials in the repo.
>
> Locally, only read-only tasks actually hit the platform. Copy-only tasks — auth probes and foreign writes — show as SKIP unless Eval-Harness sets a run id on a non-live copy. Offline, we run `python -m unittest discover` under `suite/tests` with no login.
>
> Eval-Harness runs the full twelve on a throwaway copy of each instance. Tasks nine and ten read `bug-status.json`: they pass if the call is refused, or if the bug is still recorded as not fixed. If a bug is marked fixed but data still comes back, that is the regression we catch.

**Optional show (≤15 s):** redacted [replay/sample-results.json](../replay/sample-results.json), or a unittest summary.

---

## 4:30–5:00 — Close

**On screen:** README Definition of done; optional [docs/injection-plan.md](injection-plan.md) title.

**Say:**

> Definition of done: suite on both systems, this demo, a README, and a closed bug list. Catalogue and suite twelve of twelve are done; the two bugs are still new. Next is the twenty December integration run — prompt injection against other seats, on a copy only, per our injection plan. Thanks.

---

## Timing budget

| Segment | Target |
|---|---|
| Intro | 0:30 |
| What we tested | 1:15 |
| What we found | 1:00 |
| How the suite runs | 1:45 |
| Close | 0:30 |
| **Total** | **≤ 5:00** |
