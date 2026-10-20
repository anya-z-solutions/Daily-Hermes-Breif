import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import NOW, FakeFetch, catalogue, filler, model

import daily_hermes_brief as brief_mod
import daily_hermes_brief_part1 as part1
import daily_hermes_brief_part3 as part3
from brief_common import FetchFailure

QUIET = {part3.PYPROJECT_URL: "[project]\ndependencies = []\n",
         part3.OSV_BATCH_URL: {"results": []},
         part3.NVD_URL: {"vulnerabilities": []},
         part3.KEV_URL: {"vulnerabilities": []}}


def nightly_catalogue():
    return catalogue(*filler(5),
                     model("a/big", prompt="0.000001", completion="0.000002",
                           original=("0.00001", "0.00002")),
                     model("a/small", prompt="0.0000009", completion="0.0000018",
                           original=("0.000001", "0.000002")))


class BriefTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.archive = Path(self.tmp.name)
        self.fetch = FakeFetch(QUIET)

    def tearDown(self):
        self.tmp.cleanup()

    def night(self, now, raw):
        brief, raw_to_save, today = brief_mod.build_brief(
            self.archive, raw_catalogue=raw, now=now, fetch_json=self.fetch,
            fetch_text=self.fetch, pause=lambda s: None)
        return brief, brief_mod.write_brief(self.archive, brief, raw_to_save, today)

    def test_one_brief_in_two_formats_plus_snapshot(self):
        brief, written = self.night(NOW, nightly_catalogue())
        names = sorted(p.relative_to(self.archive).as_posix() for p in written)
        self.assertEqual(names, ["briefs/2026-10-20.json", "briefs/2026-10-20.md",
                                 "state/snapshots/2026-10-20.json"])
        md = (self.archive / "briefs/2026-10-20.md").read_text()
        for heading in ("## Part 1", "## Part 2", "## Part 3"):
            self.assertIn(heading, md)
        self.assertIn("No security news today.", md)
        saved = json.loads((self.archive / "briefs/2026-10-20.json").read_text())
        self.assertEqual([p["part"] for p in saved["parts"]], [1, 2, 3])

    def test_never_overwrites(self):
        self.night(NOW, nightly_catalogue())
        with self.assertRaises(FileExistsError):
            self.night(NOW, nightly_catalogue())

    def test_second_night_diffs_and_excludes_yesterdays_pick(self):
        first, _ = self.night(NOW, nightly_catalogue())
        self.assertEqual(first["parts"][1]["model"]["id"], "a/big")
        second, _ = self.night(NOW + dt.timedelta(days=1), nightly_catalogue())
        self.assertEqual(second["parts"][0]["status"], "ok")
        self.assertEqual(second["parts"][1]["model"]["id"], "a/small")

    def test_portal_down_still_writes_a_brief_without_a_snapshot(self):
        with mock.patch.object(part1, "fetch_catalogue", side_effect=FetchFailure("portal timed out")):
            brief, written = self.night(NOW, None)
        self.assertEqual(brief["parts"][0]["status"], "fetch_failed")
        self.assertEqual(brief["parts"][1]["status"], "skipped")
        self.assertEqual(len(written), 2)

    def test_a_crashing_part_is_recorded_and_the_brief_still_written(self):
        with mock.patch.object(brief_mod.part2, "run", side_effect=KeyError("boom")):
            brief, written = self.night(NOW, nightly_catalogue())
        self.assertEqual(brief["parts"][1]["status"], "error")
        self.assertIn("Part 2 failed tonight", (self.archive / "briefs/2026-10-20.md").read_text())

    def test_markdown_renders_every_change_type(self):
        p1 = {"part": 1, "status": "ok", "checked_at_utc": "x", "compared_against": "y",
              "model_count": 1, "expiry_rollcall": [], "unparseable_models": [], "notes": [],
              "changes": [
                  {"type": "new_model", "tier": "act", "id": "a", "prompt_per_1m": 1,
                   "completion_per_1m": 2, "original_prompt_per_1m": None},
                  {"type": "expiry_set", "tier": "act", "id": "b", "expiration_date": "2026-11-01",
                   "previous_expiration_date": None},
                  {"type": "discount_started", "tier": "note", "id": "c", "prompt_per_1m": 1,
                   "completion_per_1m": 2, "original_prompt_per_1m": 2, "original_completion_per_1m": 4},
                  {"type": "discount_ended", "tier": "note", "id": "d", "prompt_per_1m": 1,
                   "completion_per_1m": 2},
                  {"type": "price_increase", "tier": "note", "id": "e", "side": "prompt",
                   "previous_per_1m": 1, "current_per_1m": 1.5, "increase_pct": 50.0}]}
        text = "\n".join(brief_mod.render_part1(p1))
        for word in ("New model", "Expiry date set", "Discount started", "Discount ended", "+50.0%"):
            self.assertIn(word, text)


if __name__ == "__main__":
    unittest.main()
