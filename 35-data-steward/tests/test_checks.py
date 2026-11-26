import json
from pathlib import Path

import pytest

from data_steward import checks

ROWS = json.loads((Path(__file__).parent / "data" / "recount_rows.json").read_text())


@pytest.mark.parametrize("answer", sorted(checks.RECOUNTS))
def test_every_recount_matches_the_made_up_rows_written_for_the_eval_harness_seat(answer):
    table, check = checks.RECOUNTS[answer]
    case = ROWS[answer]
    assert case["table"] == table and check(case["rows"]) == case["expected"]


@pytest.mark.parametrize("answer, field", [("parties_without_contact", "phone"), ("items_without_category", "category_id"),
                                           ("items_without_preferred_vendor", "preferred_vendor_id"),
                                           ("shared_party_names", "name")])
def test_a_field_no_row_carries_is_an_error_not_every_record(answer, field):
    _, check = checks.RECOUNTS[answer]
    rows = [{k: v for k, v in r.items() if k != field} for r in ROWS[answer]["rows"]]
    with pytest.raises(RuntimeError, match=field):
        check(rows)


def test_an_id_under_two_shared_names_is_listed_once():
    rows = [{"id": i, "name": n, "privacy_state": "normal"}
            for i, n in (("A", "North"), ("B", "North"), ("A", "South"), ("C", "South"))]
    assert checks.shared_party_names(rows) == ["A", "B", "C"]


def test_no_shared_name_is_an_empty_answer():
    rows = [{"id": "A", "name": "North", "privacy_state": "normal"}, {"id": "B", "name": "South", "privacy_state": "normal"}]
    assert checks.shared_party_names(rows) == []


def test_rows_without_a_privacy_state_field_are_an_error_not_an_empty_answer():
    with pytest.raises(RuntimeError, match="privacy_state"):
        checks.shared_party_names([{"id": "A", "name": "North"}, {"id": "B", "name": "North"}])


@pytest.mark.parametrize("value, is_blank", [(None, True), ("", True), (" \t\n", True), ([], True),
                                             (0, False), (False, False), ("x", False), ([0], False)])
def test_blank(value, is_blank):
    assert checks.blank(value) is is_blank


def vendor_items(*links):
    return [{"id": f"i{n}", "preferred_vendor_id": v} for n, v in enumerate(links)]


PARTIES = [{"id": "p1", "privacy_state": "normal"}, {"id": "p2", "privacy_state": "restricted"}]


def test_a_vendor_that_is_not_a_listed_party_is_an_orphan_and_a_withheld_party_still_exists():
    out = checks.orphan_vendors(vendor_items("p1", "p2", "p9", None, "  "), PARTIES, False, False)
    assert out == {"status": "complete", "links_checked": 3, "orphans": 1, "orphan_item_ids": ["i2"]}


def test_a_cut_off_party_table_names_no_orphan():
    out = checks.orphan_vendors(vendor_items("p9"), PARTIES, False, True)
    assert out["status"] == "incomplete" and out["orphans"] is None and out["orphan_item_ids"] == []


def test_a_cut_off_item_table_is_a_lower_bound():
    out = checks.orphan_vendors(vendor_items("p9"), PARTIES, True, False)
    assert out["status"] == "partial" and out["orphans"] == 1


def test_no_links_shows_zero_checked():
    assert checks.orphan_vendors(vendor_items(None), PARTIES, False, False)["links_checked"] == 0
