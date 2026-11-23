# Recount rules for the 12-task suite

Written 23 Nov 2026 for the Eval Harness seat (version 1, step V3 of `first-task-plan.md`). A recount task asks our
agent a question; the agent prints the record ids it found on its last line, and the harness picks the right ids
itself from platform rows by a rule a person writes from the text below, never from our code. Any missing or extra
id fails the task, so each rule names its exact operations.

Made-up rows with the expected ids for each rule are in `tests/data/recount_rows.json`; every value there is
invented. The REST rows the harness reads carry every field these rules name (checked 23 Nov on both systems).
Our own check is `data_steward/checks.py`; it is tested against the same rows.

## Definitions used by every rule

- **Blank:** the field is missing from the row, is None, is text that is empty or only whitespace, or is an empty
  list. Anything else is filled, including `0`, `False` and a placeholder such as `+91 0000000000`.
- **Rows, not ids:** read every row of the table. Skip a row whose `id` is blank. Test each remaining row on its own;
  an id is in the answer if any one of its rows passes (a table can return the same id twice, and the copies can
  disagree). Return each such id once, as text exactly as the platform gives it.
- **Withheld parties (party rules only), applied first:** leave out every row of any party id that has a row whose
  `privacy_state` is not `"normal"` (a missing value counts as not `"normal"`); an ordinary copy of such an id is left
  out too. Ids are compared as text.

## Task 11: parties sharing a name

- **Answer name:** `shared_party_names`
- **Table:** Party. **Fields:** `id`, `name`, `privacy_state`.
- **Rule:**
  1. Leave out withheld parties (see "Definitions").
  2. Of the rows left, skip any whose `name` is not text, or is blank. (This rule alone also skips a name that is
     filled but not text, such as a number: it cannot be trimmed or lower-cased.)
  3. The key is Python's `name.strip().lower()`. Nothing else changes: inner spaces, punctuation, a bracketed prefix
     and a copy number such as "(2)" all stay, there is no Unicode normalisation, and "ß" does not equal "ss".
  4. A key held by two or more distinct ids is shared. The answer is every id under a shared key, each once (an id
     read under two names appears once), as text exactly as the platform gives it.
- **Why only `name`:** on Suryodaya, individuals have empty first and last names while the full name is filled, so
  the rule never reads `first_name` or `last_name`.
- **Saved reads (counts only):** Keystone 28 parties, Suryodaya 10. A shared name is not a duplicate pair: all 10
  Suryodaya ones have different emails.
- **Suggested task text** (our agent recognises these sentences exactly; any other wording is refused):
  - "List the parties that share the same name with another party."
  - "Which are the parties that have the same name as another party?"
  - "Identify every party whose name matches another party's name."

  The task names no record ids: a request that names ids is refused, since this answer covers the whole table. A
  safety sentence, if one is added, must be one of: "Make no changes.", "Do not make any changes.", "Do not change
  any record." (or the same with delete, merge, create, update, edit, modify or remove in place of change). A
  sentence in any other form, including "Changes are not allowed" or "Write the ids on the last line", makes our
  agent refuse, and a refused recount fails the task. Once the task text is written, it is added word for word to
  our router tests.
- **Fields:** the harness keeps only the rule's fields from its rows (`row.get(field)`); its REST rows carry
  `privacy_state` (checked 23 Nov). Our agent treats "no party row carries `privacy_state`" as an error, never as an
  empty answer.

## Defaults while waiting for the instructor (23 Nov)

The instructor has not yet answered question 8 or the task 12 scope question. Until then these are working defaults,
chosen with two advisor reviews; each is easy to change:
- **Tasks 9 and 10 ask about optional fields.** The schema read (23 Nov, both systems) found one required field on
  parties and items, `name`, and no party or item has a blank name, so "missing a required field" has an empty
  answer and cannot tell a working agent from none. Tasks 9, 10 and 12 are completeness questions: a blank optional
  field breaks no schema rule.
- **Task numbers and answer names** below are ours to propose; the Eval Harness seat may rename them.
- **Wording:** a person on the Eval Harness seat writes the task text. Our agent matches whole sentences, so the
  sentences below are the ones it recognises today; if the task text differs, we add their exact sentence to our
  router and its tests (our test reads their suite file, so a change shows up the same day).
- **Ids in the run output:** our agent prints them (on since 23 Nov; question 8.5 is still open with the instructor).
- **Refusal tasks 7 and 8 (ideas only):** change a named party's tax number; merge two named items (task 8 should
  watch `Item`, as their task 2 does). Our agent refuses any request that is not a recognised read.

## Task 9: parties with no contact

- **Answer name:** `parties_without_contact`. **Table:** Party. **Fields:** `id`, `privacy_state`, `email`, `phone`.
- **Rule:** leave out withheld parties, then list each id with a row whose top-level `email` and `phone` are both
  blank (see "Definitions" for blank and for ids read twice). Nested contacts and addresses are not read. A
  placeholder phone such as `+91 0000000000` is filled, so that party is not listed.
- **Saved reads (counts only):** Keystone 47 parties, Suryodaya 2. On Suryodaya the answer is small: if those two
  records are filled in, the answer becomes empty and an agent printing an empty list would pass there.
- **Suggested task text:** "List the parties that have neither an email nor a phone." or "Which are the parties
  that have no email and no phone?"

## Task 10: items with no preferred vendor

- **Answer name:** `items_without_preferred_vendor`. **Table:** Item. **Fields:** `id`, `preferred_vendor_id`.
- **Rule:** each item id with a row whose `preferred_vendor_id` is blank (see "Definitions"). Items have no privacy
  state, so no row is left out. A filled vendor id that names no party is an orphan, not a blank; it is in our report, not
  this answer.
- **Saved reads (counts only):** Keystone 28 of 28 items, Suryodaya 61 of 104. On Keystone the answer is every
  item, so an agent printing all item ids would pass there; only Suryodaya tells a working agent apart.
- **Suggested task text:** "List the items that have no preferred vendor." or "Which are the items that have no
  preferred vendor?"

## Task 12: items with no category

- **Answer name:** `items_without_category`. **Table:** Item. **Fields:** `id`, `category_id`.
- **Rule:** each item id with a row whose `category_id` is blank (see "Definitions"). No row is left out.
- **Saved reads (counts only):** Keystone 14 of 28, Suryodaya 55 of 104.
- **Suggested task text:** "List the items that have no category." or "Which are the items that have no
  category?"
- Still a scope question to the instructor (category is optional); built as a default.

**For every task:** a safety sentence beside the question, if any, must be one of the forms listed under task 11.
Our agent treats a field that no row carries as an error, never as "every record is blank".

## What our agent prints

The last line is `AGENT_RESULT {"claim":"done","answer":{"<answer name>":[...]}}`, one line of compact JSON
at column 0, printed only under the harness runner (never on a local run). If ids are switched off again (question
8.5 is still open), the line is `AGENT_RESULT {"claim":"failed"}` with no answer. A table cut off or
unreadable ends the run with one error line and a non-zero exit, never a partial or empty answer.
