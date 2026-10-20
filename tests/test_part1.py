import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import NOW, catalogue, filler, model

import daily_hermes_brief_part1 as part1
from brief_common import FetchFailure


class Part1Test(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.snapshots = Path(self.tmp.name) / "snapshots"

    def tearDown(self):
        self.tmp.cleanup()

    def save_snapshot(self, date, raw):
        self.snapshots.mkdir(parents=True, exist_ok=True)
        (self.snapshots / f"{date}.json").write_text(json.dumps(part1.compact_catalogue(raw)))

    def run_part1(self, raw):
        return part1.run(self.snapshots, raw=raw, checked_at=NOW)

    def test_first_run_is_a_baseline(self):
        result, raw, today = self.run_part1(catalogue(*filler(3)))
        self.assertEqual(result["status"], "baseline")
        self.assertIsNotNone(raw)
        self.assertEqual(today, "2026-10-20")

    def test_portal_down_records_failure_and_saves_nothing(self):
        with mock.patch.object(part1, "fetch_catalogue", side_effect=FetchFailure("portal HTTP 503")):
            result, raw, _ = part1.run(self.snapshots, checked_at=NOW)
        self.assertEqual(result["status"], "fetch_failed")
        self.assertIsNone(raw)
        self.assertIn("HTTP 503", result["notes"][0])
        self.assertIn("2026-10-20T01:07:00+00:00", result["notes"][0])

    def test_empty_list_is_a_failure(self):
        result, raw, _ = self.run_part1({"data": []})
        self.assertEqual(result["status"], "fetch_failed")
        self.assertIsNone(raw)

    def test_sharp_drop_is_treated_as_partial(self):
        self.save_snapshot("2026-10-19", catalogue(*filler(10)))
        result, raw, _ = self.run_part1(catalogue(*filler(7)))
        self.assertEqual(result["status"], "suspect_response")
        self.assertIsNone(raw)

    def test_no_changes(self):
        cat = catalogue(*filler(5))
        self.save_snapshot("2026-10-19", cat)
        result, _, _ = self.run_part1(cat)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["changes"], [])
        self.assertEqual(result["compared_against"], "2026-10-19")

    def test_new_model_is_reported_under_its_base_id_not_a_variant(self):
        # Bug fix: the :free variant arriving first used to be reported, with price 0.
        self.save_snapshot("2026-10-19", catalogue(*filler(5)))
        new = catalogue(*filler(5),
                        model("acme/x:free", prompt="0", completion="0", slug="acme/x"),
                        model("acme/x", prompt="0.000003", completion="0.000006", slug="acme/x"),
                        model("acme/x:batch", slug="acme/x"))
        result, _, _ = self.run_part1(new)
        news = [c for c in result["changes"] if c["type"] == "new_model"]
        self.assertEqual(len(news), 1)
        self.assertEqual(news[0]["id"], "acme/x")
        self.assertEqual(news[0]["prompt_per_1m"], 3.0)

    def test_new_variant_of_existing_model_is_not_new(self):
        self.save_snapshot("2026-10-19", catalogue(*filler(5), model("acme/y")))
        result, _, _ = self.run_part1(catalogue(*filler(5), model("acme/y"), model("acme/y:batch")))
        self.assertEqual([c for c in result["changes"] if c["type"] == "new_model"], [])

    def test_floating_alias_is_not_a_new_model(self):
        self.save_snapshot("2026-10-19", catalogue(*filler(5)))
        result, _, _ = self.run_part1(catalogue(*filler(5), model("~acme/x-latest")))
        self.assertEqual(result["changes"], [])

    def test_discounts_and_price_rises(self):
        old = catalogue(*filler(5),
                        model("a/start"),
                        model("a/end", prompt="0.0000005", completion="0.000001",
                              original=("0.000001", "0.000002")),
                        model("a/rise", prompt="0.000001"))
        new = catalogue(*filler(5),
                        model("a/start", prompt="0.0000005", original=("0.000001", "0.000002")),
                        model("a/end"),
                        model("a/rise", prompt="0.0000013"))
        self.save_snapshot("2026-10-19", old)
        result, _, _ = self.run_part1(new)
        types = {(c["type"], c["id"]) for c in result["changes"]}
        self.assertEqual(types, {("discount_started", "a/start"), ("discount_ended", "a/end"),
                                 ("price_increase", "a/rise")})

    def test_expiry_change_on_variant_is_not_repeated(self):
        old = catalogue(*filler(5), model("a/x"), model("a/x:free"))
        new = catalogue(*filler(5), model("a/x", expiry="2026-11-01"),
                        model("a/x:free", expiry="2026-11-01"))
        self.save_snapshot("2026-10-19", old)
        result, _, _ = self.run_part1(new)
        expiries = [c for c in result["changes"] if c["type"] == "expiry_set"]
        self.assertEqual([c["id"] for c in expiries], ["a/x"])
        self.assertEqual(len(result["expiry_rollcall"]), 2)

    def test_gap_of_more_than_a_day_is_stated(self):
        cat = catalogue(*filler(5))
        self.save_snapshot("2026-10-16", cat)
        result, _, _ = self.run_part1(cat)
        self.assertTrue(any("4 days ago" in n for n in result["notes"]))

    def test_corrupt_snapshot_falls_back_to_earlier_one(self):
        cat = catalogue(*filler(5))
        self.save_snapshot("2026-10-18", cat)
        (self.snapshots / "2026-10-19.json").write_text("{not json")
        result, _, _ = self.run_part1(cat)
        self.assertEqual(result["compared_against"], "2026-10-18")

    def test_unparseable_models_are_listed(self):
        result, _, _ = self.run_part1(catalogue(*filler(3), {"id": "bad/x", "pricing": {"prompt": "x"}}))
        self.assertEqual(len(result["unparseable_models"]), 1)


if __name__ == "__main__":
    unittest.main()
