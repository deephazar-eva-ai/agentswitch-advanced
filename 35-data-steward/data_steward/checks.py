"""Recount checks and the orphan check. Pure functions, no network.

Each recount check follows its written rule in docs/recount-rules.md word for word, because the Eval Harness seat
writes its own rule from that text and any missing or extra id fails the task.
"""
from collections import defaultdict

from .dedupe import ORDINARY_PRIVACY


def blank(value) -> bool:
    """None, empty or spaces-only text, or an empty list. A missing field is read as None. Zero and False are values."""
    return value is None or (isinstance(value, str) and not value.strip()) or (isinstance(value, list) and not value)


def ordinary_parties(rows: list[dict]) -> list[dict]:
    """Party rows whose id has no row under a privacy state other than the ordinary one, or none. An ordinary copy of
    a withheld id is left out too, so nothing about a withheld party reaches an answer. Ids compare as text."""
    if rows and not any("privacy_state" in r for r in rows):
        # Without the field every party would count as withheld and the answer would be empty: an empty answer here
        # would be a false "done", so it is an error instead.
        raise RuntimeError("privacy_state is missing from every party row; no answer is given")
    withheld = {str(r.get("id")) for r in rows if r.get("privacy_state") != ORDINARY_PRIVACY}
    return [r for r in rows if str(r.get("id")) not in withheld]


def shared_party_names(rows: list[dict]) -> list[str]:
    """Ids of ordinary parties whose name, trimmed and lower-cased, is not blank and is held by two or more distinct
    ids. Nothing else is changed: inner spaces, punctuation, prefixes and copy numbers stay. A name that is not text is
    skipped. Sorted, each id once, even when one id is read under two shared names."""
    _carried(rows, "name")
    groups: dict[str, set[str]] = defaultdict(set)
    for row in ordinary_parties(rows):
        name, rid = row.get("name"), row.get("id")
        if not isinstance(name, str) or blank(name) or blank(rid):
            continue
        groups[name.strip().lower()].add(str(rid))
    return sorted(set().union(*(ids for ids in groups.values() if len(ids) > 1)))


def _carried(rows: list[dict], *fields: str) -> None:
    """A field no row carries would make every record look blank: an error, never an answer."""
    for field in fields:
        if rows and not any(field in r for r in rows):
            raise RuntimeError(f"{field} is missing from every row; no answer is given")


def parties_without_contact(rows: list[dict]) -> list[str]:
    """Ids of ordinary parties whose top-level email and phone are both blank. Nested contacts and addresses are not
    read, and a placeholder phone (all zeros, say) is a value: this asks what is filled, not what is real."""
    _carried(rows, "email", "phone")
    return sorted({str(r["id"]) for r in ordinary_parties(rows)
                   if not blank(r.get("id")) and blank(r.get("email")) and blank(r.get("phone"))})


def _items_without(field: str):
    def check(rows: list[dict]) -> list[str]:
        _carried(rows, field)
        return sorted({str(r["id"]) for r in rows if not blank(r.get("id")) and blank(r.get(field))})
    check.__doc__ = f"Ids of items whose {field} is blank."
    return check


items_without_preferred_vendor = _items_without("preferred_vendor_id")
items_without_category = _items_without("category_id")

# Answer name -> (table, check), one per recount task (docs/recount-rules.md). The schema's required fields are filled
# on every party and item (schema read, 23 Nov), so tasks 9, 10 and 12 ask about optional fields; all four are working
# defaults until the instructor answers.
RECOUNTS = {
    "parties_without_contact": ("Party", parties_without_contact),          # task 9
    "items_without_preferred_vendor": ("Item", items_without_preferred_vendor),  # task 10
    "shared_party_names": ("Party", shared_party_names),                    # task 11
    "items_without_category": ("Item", items_without_category),              # task 12
}


def orphan_vendors(items, parties, items_cut_off: bool, parties_cut_off: bool) -> dict:
    """Items whose preferred vendor id names no party this login can list. Every party read counts, withheld ones
    included, since they exist. A cut-off Party table proves no absence, so nothing is called an orphan; a cut-off
    Item table leaves the count a lower bound."""
    linked = [i for i in items if not blank(i.get("preferred_vendor_id"))]
    if parties_cut_off:
        return {"status": "incomplete", "links_checked": 0, "orphans": None, "orphan_item_ids": []}
    known = {str(p.get("id")) for p in parties}
    orphans = sorted(str(i.get("id")) for i in linked if str(i["preferred_vendor_id"]) not in known)
    return {"status": "partial" if items_cut_off else "complete", "links_checked": len(linked),
            "orphans": len(orphans), "orphan_item_ids": orphans}
