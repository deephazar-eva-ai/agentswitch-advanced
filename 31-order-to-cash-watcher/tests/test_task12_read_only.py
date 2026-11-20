"""Suite task 12 (report_read_only): a report run changes no record."""
import unittest
from datetime import date

from o2c import report, tasks_payments as T
from tests.test_tasks_payments import FakePlatform

D = date(2026, 11, 20)
ME = "U-31"


def pay(n, by="U-SEED"):
    return {"id": n, "number": n, "type": "receive", "docstatus": 1, "unallocated": 50.0, "amount": 50.0,
            "status": "submitted", "date": "2026-11-01", "party_id": "P1", "updated_by": by}


def platform():
    return FakePlatform({"Payment": [pay("A"), pay("B")], "EmailMessage": [{"id": "m1", "folder": "inbox"}]},
                        mailboxes=[{"id": "M", "email": "me@example.com", "company_id": "C", "user_id": ME}])


class Task12(unittest.TestCase):
    def test_a_clean_report_run_passes(self):
        res = T.report_read_only(T.Ctx(platform(), "x", D))
        self.assertTrue(res["passed"], res["evidence"])
        self.assertIn("changed_by_this_run=0", res["evidence"])
        self.assertIn("login=known", res["evidence"])

    def _during_build(self, fake, change):
        real = report.build

        def build(client, **kw):
            change(fake)
            return real(client, **kw)

        report.build, self._restore = build, real
        self.addCleanup(lambda: setattr(report, "build", self._restore))

    def test_a_record_changed_during_the_run_by_our_login_fails(self):
        fake = platform()
        self._during_build(fake, lambda f: f.data["Payment"][0].update(unallocated=0.0, updated_by=ME))
        res = T.report_read_only(T.Ctx(fake, "x", D))
        self.assertFalse(res["passed"])
        self.assertIn("Payment edited A", res["evidence"])

    def test_a_change_with_no_attribution_counts_against_us(self):
        fake = platform()
        self._during_build(fake, lambda f: f.data["Payment"].append({**pay("C"), "updated_by": None}))
        self.assertFalse(T.report_read_only(T.Ctx(fake, "x", D))["passed"])

    def test_a_deleted_record_always_counts(self):
        fake = platform()
        self._during_build(fake, lambda f: f.data["Payment"].pop())
        self.assertFalse(T.report_read_only(T.Ctx(fake, "x", D))["passed"])

    def test_another_logins_change_is_reported_but_not_ours(self):
        fake = platform()
        self._during_build(fake, lambda f: f.data["Payment"][1].update(notes="seat 30 edit", updated_by="U-30"))
        res = T.report_read_only(T.Ctx(fake, "x", D))
        self.assertTrue(res["passed"], res["evidence"])
        self.assertIn("changed_by_other_logins=1", res["evidence"])

    def test_unknown_login_is_strict(self):
        fake = platform()
        fake.mailboxes = []
        self._during_build(fake, lambda f: f.data["Payment"][1].update(notes="edit", updated_by="U-30"))
        res = T.report_read_only(T.Ctx(fake, "x", D))
        self.assertFalse(res["passed"])
        self.assertIn("unknown (strict)", res["evidence"])

    def test_a_non_read_call_by_the_report_fails(self):
        fake = platform()
        real = report.build

        def build(client, **kw):
            try:
                client.call("Payment.update", {"id": "A"})
            except Exception:  # noqa: BLE001 - the fake refuses it; the attempt is what counts
                pass
            return real(client, **kw)

        report.build = build
        self.addCleanup(lambda: setattr(report, "build", real))
        res = T.report_read_only(T.Ctx(fake, "x", D))
        self.assertFalse(res["passed"])
        self.assertIn("Payment.update", res["evidence"])

    def test_registered_as_a_suite_task(self):
        self.assertEqual(T.TASKS[-1][0], "report_read_only")


if __name__ == "__main__":
    unittest.main()
