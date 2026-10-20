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
         part3.NVD_URL: {"vulnerabilities": [], "totalResults": 0},
         part3.KEV_URL: {"vulnerabilities": []},
         part3.OSV_QUERY_URL: {"vulns": []},
         part3.PYPI_URL: {"info": {"version": "1.0.0"}, "releases": {}},
         part3.GIT_UPLOAD_PACK_URL: "0000"}


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
        for heading in ("## Part 1", "## Part 2", "## Part 3", "### Hermes Agent security status",
                        "### Threat picture", "### New advisories"):
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

    def test_brief_is_dated_by_uk_calendar_date(self):
        midnight_bst = dt.datetime(2026, 7, 1, 23, 0, tzinfo=dt.timezone.utc)   # 00:00 on 2 July in London
        brief, _, today = brief_mod.build_brief(self.archive, raw_catalogue=nightly_catalogue(),
                                                now=midnight_bst, fetch_json=self.fetch,
                                                fetch_text=self.fetch, pause=lambda s: None)
        self.assertEqual(today, "2026-07-02")
        self.assertEqual(brief["date"], "2026-07-02")

    def test_hermes_table_and_beyond_range_wording(self):
        h = {"latest_release": "0.22.2", "latest_release_source": "GitHub release tag v0.22.2",
             "pypi_latest": "0.19.0", "pypi_latest_date": "2026-07-20", "advisories_total": 2,
             "minimum_version_clearing_fixed_advisories": "0.18.0",
             "advisories": [
                 {"id": "GHSA-f", "cve": "CVE-1", "status": "fixed", "affected": "< 0.18.0",
                  "fixed_in": "0.18.0", "category": "CWE-74 (Injection)", "reach": "network",
                  "impact_cia": "low/low/low", "severity": "LOW", "url": "u1"},
                 {"id": "GHSA-o", "cve": "CVE-2", "status": "no_fix_named_latest_beyond_range",
                  "affected": "<= 0.19.0", "fixed_in": None, "category": "CWE-400", "reach": "network",
                  "impact_cia": "none/none/low", "severity": "MODERATE", "url": "u2"}]}
        text = "\n".join(brief_mod.render_hermes_status(h))
        self.assertIn("Latest hermes-agent release: 0.22.2 (GitHub release tag v0.22.2).", text)
        self.assertIn("PyPI's newest release is 0.19.0", text)
        self.assertIn("neither names it as affected nor confirms a fix", text)
        self.assertIn("| [CVE-2](u2) | CWE-400 | network | none/none/low | Moderate | <= 0.19.0 | none named |", text)

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
