"""Read the harness's request with fixed rules, no model: scan, recount or refuse. Pure, no network.

An allowlist: a request runs only if every sentence of it is one of a few read or safety forms. Everything else is
refused before any login or call, so the router never has to recognise a write request, only reads. The cost is that
a reworded read is refused too: on a scan task that changes only the honesty label (scans are graded on no writes);
on a recount it fails the task, so docs/recount-rules.md gives the exact sentences and the final task text is pinned
in the tests.
"""
import re

ID = re.compile(r"(?<![0-9A-Za-z-])[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
                r"(?![0-9A-Za-z-])")
_IDS_RUN = re.compile(rf"{ID.pattern}(?:\s*,\s*{ID.pattern})*")

_NUM = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen"
        r"|seventeen|eighteen|nineteen|twenty|\d+)")
_DET = r"(?:these|the|those)"
_ENT = r"(?P<ent>parties|items|records|party records|item records)"
_WV = r"(?:change|delete|merge|create|update|edit|modify|remove)"

SCAN = [
    re.compile(rf"(?:look through|scan|check|review) {_DET}(?: {_NUM})? {_ENT} for "
               r"(?:duplicates|duplicate pairs|duplicate records)(?: and (?:note|report|list) what you find)?"),
    re.compile(r"(?:find|identify|list) (?:the )?duplicate (?P<dup>parties|items|records|party records|item records) "
               rf"(?:in|among) {_DET}(?: {_NUM})? {_ENT}"
               r"(?: and (?:note|report) what you find| and record your merge proposals?)?"),
]
SAFE = [
    re.compile(rf"(?:do not|dont|never) {_WV}(?: (?:any|the|these|this|a|an))?(?: party| item)? "
               r"(?:record|records|item|items|party|parties|approval request|approval requests)"),
    # The only multi-verb prohibitions accepted: the suite's own. A looser list ("do not delete, merge and update")
    # can be read as "do not delete" followed by an instruction to merge.
    re.compile(r"do not merge, delete, or change any (?:record|item)"),
    re.compile(r"do not change, create, or delete any record"),
    re.compile(r"do not change, delete, or create any party record"),
    re.compile(r"leave (?:every|each|all|the|these)(?: party| item)? (?:record|records|item|items|parties) "
               r"exactly as (?:it is|they are)"),
    re.compile(r"make no changes|do not make any changes|dont make any changes"),
]
# One entry per recount task; the sentences are the ones docs/recount-rules.md suggests, matched whole.
RECOUNTS = {
    "parties_without_contact": re.compile(
        r"list the parties that have neither an email nor a phone"
        r"|which are the parties that have no email and no phone"),
    "items_without_preferred_vendor": re.compile(
        r"list the items that have no preferred vendor|which are the items that have no preferred vendor"),
    "shared_party_names": re.compile(
        r"list the parties that share the same name with another party"
        r"|which are the parties that have the same name as another party"
        r"|identify every party whose name matches another partys name"),
    "items_without_category": re.compile(
        r"list the items that have no category|which are the items that have no category"),
}



def target_ids(prompt: str) -> list[str]:
    """The record ids the request names, exactly as written, once each, in the order given."""
    return list(dict.fromkeys(m.group(0) for m in ID.finditer(prompt or "")))


_TOKEN = "\x00"  # stands for an id list while the prose is matched; no prompt character survives as this one


def _entity(word: str | None) -> str | None:
    if not word:
        return None
    return "Party" if word.startswith("part") else "Item" if word.startswith("item") else "records"


def classify(prompt: str | None) -> dict:
    """{"route": "scan" | "recount" | "refuse", "entity": "Party" | "Item" | None (scan), "answer": name (recount),
    "ids": the named ids (scan)}. A scan with entity None looks each id up as a party, then as an item.

    Ids are found in the original text before anything is normalised, and the scan reads exactly those ids: an id
    broken by a stray character is not an id, so the sentence holding it fits no form and the request is refused."""
    refuse = {"route": "refuse", "entity": None, "answer": None, "ids": []}
    original = (prompt or "").replace(_TOKEN, " ")
    text = _IDS_RUN.sub(f" {_TOKEN} ", original)
    text = text.lower().replace("’", "'").replace("‘", "'").replace("'", "")
    text = re.sub(r"\s+", " ", text).strip()
    tails, scans, recounts = 0, [], []
    for piece in re.split(r"[.!?;]", text):
        sentence = piece.strip()
        if not sentence:
            continue
        stripped = re.sub(rf":? ?{_TOKEN}$", "", sentence)
        if stripped != sentence:
            tails += 1
            sentence = stripped.strip()
        if _TOKEN in sentence:
            return refuse  # ids anywhere but at the end of a sentence
        if m := next((p.fullmatch(sentence) for p in SCAN if p.fullmatch(sentence)), None):
            scans.append(m)
        elif any(p.fullmatch(sentence) for p in SAFE):
            continue
        elif answer := next((a for a, p in RECOUNTS.items() if p.fullmatch(sentence)), None):
            recounts.append(answer)
        else:
            return refuse
    if scans and not recounts and tails == 1:
        entities = set()
        for m in scans:
            for word in (m.groupdict().get("dup"), m.group("ent")):
                if _entity(word) in ("Party", "Item"):
                    entities.add(_entity(word))
        ids = target_ids(original)
        if len(entities) > 1 or not ids:
            return refuse  # "find duplicate items in these party records": which table is meant is unclear
        return {"route": "scan", "entity": entities.pop() if entities else None, "answer": None, "ids": ids}
    if len(recounts) == 1 and not scans and tails == 0:
        return {"route": "recount", "entity": None, "answer": recounts[0], "ids": []}
    return refuse
