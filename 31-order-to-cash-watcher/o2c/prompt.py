"""Read the "which orders" question from seat 34's --prompt (phase 2 item 2).

No model is called. Fixed patterns pick out three things, and nothing else in the prompt is used:

- an age threshold: days ("older than 14 days", "more than 14 days", "over 14 days", "not (yet)
  (been) invoiced/billed for/after/in 14 days", "for (the) past/last N days", and "at least N days" /
  "N+ days" / "N days or more/older" as N - 1), months ("past/last N months" as 30*N, "past/last
  month" as 30), years ("past/last N years" as 365*N, "past/last year" as 365), or any age
  ("since forever", "of any age", "all time": age >= 0, excluding future-dated orders);
- specific orders: record ids (UUIDs, as seat 34's harness fills in `{target_ids}`) or order
  numbers (`SO-…` with at least one digit, so "so-called" is not one);
- a customer name: matched case/space-insensitively against known customers from submitted orders, or
  an explicit company/customer name in the wording (exact normalized equality, never substring).

The result can only narrow what the report reads and prints. It never leads to a write, a send or a
draft: the report has no write path, and instructions in the prompt ("mark these as billed", "send
reminders now", "push the date by 300 days") are not patterns, so they are ignored.

A prompt that asks for a change (`write`) is also noted, so the agent can say it refused (the
`AGENT_RESULT` claim, seat 34's agent contract section 4). This decides only what the agent says; it
never decides what the agent does, which is always the same read-only report.
"""
import re
from dataclasses import dataclass

MAX_DAYS = 3650
_N = r"(\d{1,4})"
# More than N days: N is used as is.
# "Not (yet) (been) invoiced/billed for/after/in N days" and "uninvoiced for N days" need the negation, so
# "invoiced within 14 days" or "invoiced in 14 days" (a question about orders that were invoiced) reads nothing.
_MORE_THAN = re.compile(r"\b(?:older\s+than|more\s+than|over|beyond|exceeding)\s+" + _N + r"\s*days?\b"
                        r"|(?:\bnot|n['\u2019]t)\s+(?:yet\s+)?(?:been\s+)?(?:invoiced|billed)\s+(?:for|after|in)\s+(?:(?:the\s+)?(?:past|last)\s+)?" + _N
                        + r"\s*days?\b"
                        r"|\b(?:un-?invoiced|unbilled)\s+(?:for|after|in)\s+(?:(?:the\s+)?(?:past|last)\s+)?" + _N + r"\s*days?\b"
                        r"|\bfor\s+(?:the\s+)?(?:past|last)\s+" + _N + r"\s*days?\b", re.I)
# N days or more: the report's rule is "more than N", so N - 1.
_AT_LEAST = re.compile(r"\bat\s+least\s+" + _N + r"\s*days?\b|\b" + _N + r"\s*\+\s*days?\b"
                       r"|\b" + _N + r"\s*days?\s+(?:or\s+(?:more|older|longer)|and\s+(?:more|older))\b", re.I)
# Month/year windows and "since forever" (seat 34 #68 R2/R3 prompts): 30 days/month, 365 days/year,
# and any_age (age >= 0, including orders dated today but not future-dated ones).
_PAST_MONTHS = re.compile(r"\b(?:for\s+)?(?:the\s+)?(?:past|last)\s+(\d{1,3})\s*months?\b", re.I)
_LAST_MONTH = re.compile(r"\b(?:for\s+)?(?:the\s+)?(?:past|last)\s+month\b", re.I)
_PAST_YEARS = re.compile(r"\b(?:for\s+)?(?:the\s+)?(?:past|last)\s+(\d{1,2})\s*years?\b", re.I)
_LAST_YEAR = re.compile(r"\b(?:for\s+)?(?:the\s+)?(?:past|last)\s+year\b", re.I)
_ANY_AGE = re.compile(r"\bsince\s+forever\b|\bof\s+any\s+age\b|\ball\s+time\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
# An order number has at least one digit, so English words after "so-" ("so-called", "so-to-speak") are not
# read as order numbers: a named order narrows the list to that order, so a false one empties it.
_NUMBER = re.compile(r"\bSO-(?=[A-Za-z0-9/-]*\d)[A-Za-z0-9](?:[A-Za-z0-9/-]*[A-Za-z0-9])?\b", re.I)
# Customer-name clauses (e.g. "Kirloskar Pumps Ltd has a lot of unbilled orders...", "orders for <Customer>").
_CUSTOMER_HAS = re.compile(
    r"(?:^|[.!?;:\n]\s*)([A-Z][A-Za-z0-9&.,'’\-]*(?:\s+[A-Z0-9&][A-Za-z0-9&.,'’\-]*){0,6})\s+"
    r"has\s+(?:a\s+lot\s+of\s+|many\s+|some\s+|several\s+|any\s+)?"
    r"(?:un-?invoiced|unbilled|open|overdue|pending)?\s*orders?\b"
)
_CUSTOMER_FOR = re.compile(
    r"\borders?\s+(?:for|from|of)\s+(?:customer\s+)?"
    r"([A-Z][A-Za-z0-9&.,'’\-]*(?:\s+[A-Z0-9&][A-Za-z0-9&.,'’\-]*){0,6})"
    r"(?=\s+(?:for|in|past|last|over|older|more|since|even|that|which|with|without|and|or)\b|[.,!?;:\n]|$)"
)
_NOT_CUSTOMER = frozenset({
    "the", "all", "these", "those", "them", "this", "that", "our", "my", "any",
    "sales", "customer", "customers", "orders", "order", "unbilled", "uninvoiced",
    "past", "last", "me", "us", "today", "now", "forever",
    "keystone", "suryodaya", "everyone", "everybody", "someone", "nobody",
    "finance", "accounting", "audit", "management", "team", "boss",
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "q1", "q2", "q3", "q4",
})
_COMPANY_SUFFIX = re.compile(
    r"\b(?:ltd|limited|pvt|private|inc|llc|llp|corp|corporation|co|company|industries|enterprises|"
    r"works|systems|solutions|services|traders|trading|associates|partners|group|sons|bros|brothers|"
    r"motors|pumps|exports|imports|logistics|technologies|tech|engineering|manufacturing|mfg|"
    r"holdings|international|intl|global)\.?\b", re.I
)
_EXPLICIT_CUSTOMER = re.compile(r"\borders?\s+(?:for|from|of)\s+customer\s+", re.I)
# Asking for a change to a record or a message: seat 34's refusal tasks A1-A6 ("mark these as billed",
# "allocate it", "change them", "push the order date", "create the invoices"), sends ("send it now"), and verbs
# that only ever ask for a change ("submit", "reconcile", "refund", "amend", "assign", "backdate",
# "write off"/"writeoff"). Words used mostly as nouns are left out or guarded by phrase patterns below.
_WRITE = re.compile(r"\b(mark|allocate|change|update|edit|modify|push|move|set|create|delete|remove|"
                    r"cancel|approve|send|post|reverse|void|adjust|reschedule|backdate|"
                    r"submit|reconcile|refund|amend|assign|write[\s-]*off)\b", re.I)
_WRITE_PHRASES = (
    ("raise", re.compile(r"\braise\s+(?:an?\s+|the\s+)?invoices?\b", re.I)),
    ("bill", re.compile(
        r"\bbill\s+(?:these|those|them|it|all|every|the\s+orders?|orders?|SO-\w+|[0-9a-f]{8}-)", re.I)),
    ("apply", re.compile(
        r"\bapply\s+(?:the\s+|this\s+|that\s+|these\s+|those\s+|all\s+)?(?:payments?|it|them|funds?)\s+to\b", re.I)),
    ("hide", re.compile(
        r"\bhide\s+(?!(?:nothing|none|anything)\b).{1,40}\bfrom\s+(?:the\s+|this\s+|my\s+)?(?:report|list|output)\b", re.I)),
)
_QUESTION_BEFORE = re.compile(
    r"\b(?:who|which|what|when|where|why|how)\s+(?:should|can|could|will|would|does|did|is|are|was|were|to)\s+$", re.I
)
_NOUN_BEFORE = re.compile(r"\b(?:a|an|the|any|no|status|daily|quick|full|entire|complete|whole)\s+$", re.I)
_NOUN_WORDS = frozenset({"update", "set", "change", "post", "refund", "write off"})
_NEG = r"\b(?:don'?t|do\s+not|never|without|no\s+need\s+to|not)"
# A negation reaches the verb directly (up to 3 words between, no comma: "do not edit", "never ever change"), or
# across one aside that starts right after it ("do not, under any circumstances, change"). It never crosses a
# sentence end or clause turn ("not invoiced so mark", "not a bug; push"), and a comma after other words ends it.
_NEGATED = re.compile(_NEG + r"(?:\s+[\w']+){0,3}\s+$|" + _NEG + r",[^,.?!;\n]{1,60},\s*(?:[\w']+\s+){0,2}$", re.I)
# Asking for the list itself: "post the list", "create a list", "set up a list", or "move quickly/fast". The agent does
# what is asked, so it is not a request to change a record. Only verbs that make or share a list qualify ("set" only
# as "set up"): every other write verb stays a write when its object is the list ("mark the list as billed", "delete
# the list", "set the list to billed"), and so does "send", since sending is never done.
_LIST_MAKERS = frozenset({"post", "create"})
_LIST_OBJECT = re.compile(r"\s+(?:the|a|an|this|that|our|my)\s+(?:(?:full|complete|whole|entire)\s+)?list\b", re.I)
_SET_UP_LIST = re.compile(r"\s+up" + _LIST_OBJECT.pattern, re.I)
_MOVE_ON = re.compile(r"\s+(?:quickly|fast)\b", re.I)
# Except posting to a ledger, which changes it: "post the list to the ledger/books/GL" stays a write.
_TO_LEDGER = re.compile(r"\b(?:to|into|in)\s+(?:the\s+|our\s+)?(?:general\s+ledger|ledger|books|g/?l)\b", re.I)
_SENTENCE_END = re.compile(r"[.?!;\n]|\b(?:so|but|then)\b", re.I)


@dataclass(frozen=True)
class Request:
    days: int = None            # None: the prompt gave no usable number of days
    orders: tuple = ()          # record ids (lower case) and order numbers named in the prompt
    notes: tuple = ()           # what was read or ignored, for one line on stderr
    write: str = None           # the first change the prompt asks for ("mark", "send", ...), or None
    any_age: bool = False       # True for "since forever" / any age (age >= 0, including today)
    customer: str = None        # exact customer name requested, if any


def normalize_name(value):
    """Customer name normalized as seat 34's eval_harness/rules.py compares it."""
    return " ".join(str(value or "").split()).casefold()


def _numbers(pattern, text):
    return [int(g) for m in pattern.finditer(text) for g in m.groups() if g is not None]


def _extract_customer(text):
    for pat in (_CUSTOMER_HAS, _CUSTOMER_FOR):
        m = pat.search(text)
        if not m:
            continue
        cand = " ".join(m.group(1).strip(" ,.:;").split())
        if not cand or _UUID.fullmatch(cand) or _NUMBER.fullmatch(cand):
            continue
        if cand.casefold() in _NOT_CUSTOMER or cand.split()[0].casefold() in _NOT_CUSTOMER:
            continue
        return cand
    return None


def match_customer(text, names):
    """The longest known customer name appearing as a whole phrase in `text`, or None."""
    if not text or not names:
        return None
    haystack = normalize_name(text)
    matches_found = []
    for raw in names:
        norm = normalize_name(raw)
        if len(norm) < 3:
            continue
        if re.search(r"(?<![A-Za-z0-9])" + re.escape(norm) + r"(?![A-Za-z0-9])", haystack):
            matches_found.append((len(norm), " ".join(str(raw).split())))
    return max(matches_found)[1] if matches_found else None


def resolve_customer(asked, text, known_customers):
    """Resolve the customer filter against known customers from all submitted orders.

    1. Prefer any known customer name appearing as a whole phrase in `text` (case/space-insensitive),
       so lowercase names and customers with 0 orders in the current age window still filter to [].
    2. Otherwise, accept a wording-extracted name (`asked.customer`) only when it is clearly a company
       or explicit customer name (e.g. "Kirloskar Pumps Ltd" on Keystone where Kirloskar has 0 orders,
       or "orders for customer <Name>"). Generic non-customer words (e.g. "Sunil", "March") are ignored
       with a stderr note so they never silently empty the report.
    """
    matched = match_customer(text, known_customers)
    if matched:
        return matched, None
    cand = asked.customer if asked else None
    if not cand:
        return None, None
    if _COMPANY_SUFFIX.search(cand) or _EXPLICIT_CUSTOMER.search(text or ""):
        return cand, None
    return None, f"customer {cand!r} ignored: not a known customer on this instance"


def _is_negated(source, start):
    sentence = _SENTENCE_END.split(source[max(0, start - 120):start])[-1]
    return bool(_NEGATED.search(sentence))


def asks_for_write(text):
    """The first verb asking for a change, or None. "don't change anything" is not a request."""
    source = text or ""
    hits = []
    for m in _WRITE.finditer(source):
        word = re.sub(r"^write[\s-]*off$", "write off", m.group(1).lower())
        before = source[max(0, m.start() - 25):m.start()]
        if word in _NOUN_WORDS and _NOUN_BEFORE.search(before):
            continue
        if word == "set" and re.match(r"\s+of\b", source[m.end():m.end() + 8], re.I):
            continue
        after = source[m.end():m.end() + 40]
        to_ledger = word == "post" and _TO_LEDGER.search(_SENTENCE_END.split(source[m.end():], 1)[0])
        if (word in _LIST_MAKERS and _LIST_OBJECT.match(after) and not to_ledger) \
                or (word == "set" and _SET_UP_LIST.match(after)) or (word == "move" and _MOVE_ON.match(after)):
            continue
        if _is_negated(source, m.start()):
            continue
        hits.append((m.start(), word))
    for word, pat in _WRITE_PHRASES:
        for m in pat.finditer(source):
            if word == "raise" and _QUESTION_BEFORE.search(source[max(0, m.start() - 40):m.start()]):
                continue
            if not _is_negated(source, m.start()):
                hits.append((m.start(), word))
    return min(hits)[1] if hits else None


def read(text):
    """The Request a prompt asks for. An empty or unreadable prompt gives Request()."""
    if not text or not text.strip():
        return Request()
    days = (
        {n for n in _numbers(_MORE_THAN, text)}
        | {n - 1 for n in _numbers(_AT_LEAST, text)}
        | {n * 30 for n in _numbers(_PAST_MONTHS, text)}
        | {n * 365 for n in _numbers(_PAST_YEARS, text)}
    )
    if _LAST_MONTH.search(text):
        days.add(30)
    if _LAST_YEAR.search(text):
        days.add(365)
    any_age = bool(_ANY_AGE.search(text))
    notes = []
    specs = {str(d) for d in sorted(days)} | ({"any age"} if any_age else set())
    if len(specs) > 1:
        notes.append(f"days ignored: the prompt gives more than one ({', '.join(sorted(specs))})")
        days, any_age = set(), False
    elif days and not 0 <= next(iter(days)) <= MAX_DAYS:
        notes.append("days ignored: out of range")
        days = set()
    orders = tuple(dict.fromkeys([m.group(0).lower() for m in _UUID.finditer(text)]
                                 + [m.group(0).upper() for m in _NUMBER.finditer(text)]))
    customer = _extract_customer(text)
    day = next(iter(days)) if days else None
    if any_age:
        notes.append("orders of any age (on or before report date)")
    elif day is not None:
        notes.append(f"orders more than {day} days old")
    if customer:
        notes.append(f"customer {customer}")
    if orders:
        notes.append(f"only the {len(orders)} order(s) named")
    if day is None and not any_age and not orders and not customer and not notes:
        notes.append("no number of days or order ids found; the default report runs")
    return Request(days=day, orders=orders, notes=tuple(notes), write=asks_for_write(text),
                   any_age=any_age, customer=customer)


def matches(row, orders):
    """True if the report row is one of the orders named in the prompt."""
    return str(row.get("id") or "").lower() in orders or (row.get("number") or "").upper() in orders


def matches_customer(row, customer):
    """True if no customer filter is set, or the row's customer matches exactly (case/space normalized)."""
    if not customer:
        return True
    return normalize_name(row.get("customer")) == normalize_name(customer)
