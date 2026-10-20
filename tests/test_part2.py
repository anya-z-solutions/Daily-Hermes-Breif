import unittest

from helpers import FakeFetch, catalogue, model

import daily_hermes_brief_part2 as part2
from brief_common import FetchFailure

OK = {"status": "ok", "changes": []}
ARXIV_XML = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>A Test  Paper</title><published>2026-08-01T00:00:00Z</published>
</entry></feed>"""


def discounted(model_id, now=("0.0000005", "0.000001"), before=("0.000001", "0.000002"), **kw):
    return model(model_id, prompt=now[0], completion=now[1], original=before, **kw)


class SelectionTest(unittest.TestCase):
    def run_part2(self, part1, cat, featured=(), fetch=None):
        fetch = fetch or FakeFetch()
        return part2.run(part1, cat, list(featured), fetch_json=fetch, fetch_text=fetch)

    def test_new_model_wins_over_any_discount(self):
        cat = catalogue(discounted("a/disc"), model("a/new"))
        p1 = {"status": "ok", "changes": [{"type": "new_model", "id": "a/new"}]}
        self.assertEqual(self.run_part2(p1, cat)["model"]["id"], "a/new")

    def test_several_new_models_take_highest_list_price(self):
        cat = catalogue(model("a/cheap", prompt="0.000001"), model("a/dear", prompt="0.000009"),
                        discounted("a/disc-dear", now=("0.000001", "0.000001"),
                                   before=("0.00002", "0.00002")))
        p1 = {"status": "ok", "changes": [{"type": "new_model", "id": i}
                                          for i in ("a/cheap", "a/dear", "a/disc-dear")]}
        # a/disc-dear's list price is the highest even though its current price is low.
        self.assertEqual(self.run_part2(p1, cat)["model"]["id"], "a/disc-dear")

    def test_discount_ranked_by_usd_saved(self):
        cat = catalogue(discounted("a/small"),
                        discounted("a/big", now=("0.000002", "0.00001"), before=("0.000004", "0.00002")))
        self.assertEqual(self.run_part2(OK, cat)["model"]["id"], "a/big")

    def test_tie_on_value_prefers_newer_model(self):
        cat = catalogue(discounted("google/gemini-3.7-flash", created=1_786_000_000),
                        discounted("google/gemini-3.8-flash", created=1_788_000_000))
        self.assertEqual(self.run_part2(OK, cat)["model"]["id"], "google/gemini-3.8-flash")

    def test_recent_picks_are_excluded(self):
        cat = catalogue(discounted("a/big", before=("0.00001", "0.00001")), discounted("a/small"))
        result = self.run_part2(OK, cat, featured=["a/big"])
        self.assertEqual(result["model"]["id"], "a/small")

    def test_exclusion_counts_runs_not_days(self):
        cat = catalogue(discounted("a/only"))
        featured = [None] * part2.EXCLUSION_RUNS + ["a/only"]     # featured 15 briefs ago
        self.assertEqual(self.run_part2(OK, cat, featured)["model"]["id"], "a/only")

    def test_everything_excluded(self):
        cat = catalogue(discounted("a/one"), discounted("a/two"))
        result = self.run_part2(OK, cat, featured=["a/one", "a/two"])
        self.assertEqual(result["status"], "none")
        self.assertEqual(result["text"], "No model of the day.")

    def test_no_discounts_and_no_new_models(self):
        result = self.run_part2(OK, catalogue(model("a/plain")))
        self.assertEqual(result["status"], "none")
        self.assertIsNone(result["model"])

    def test_variants_and_aliases_are_never_picked(self):
        cat = catalogue(discounted("a/x:batch"), discounted("~a/x-latest"))
        self.assertEqual(self.run_part2(OK, cat)["status"], "none")

    def test_portal_down_skips_part2(self):
        result = self.run_part2({"status": "fetch_failed"}, None)
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["text"], "No model of the day.")

    def test_new_models_ignored_on_a_baseline_night(self):
        cat = catalogue(model("a/new"), discounted("a/disc"))
        p1 = {"status": "baseline", "changes": [{"type": "new_model", "id": "a/new"}]}
        self.assertEqual(self.run_part2(p1, cat)["model"]["id"], "a/disc")


class CardTest(unittest.TestCase):
    def card(self, entry, fetch):
        result = part2.run(OK, catalogue(entry), [], fetch_json=fetch, fetch_text=fetch)
        self.assertEqual(result["status"], "ok")
        return result["model"]

    def test_hugging_face_404_falls_back_to_catalogue_facts(self):
        fetch = FakeFetch({part2.HF_API: FetchFailure("HTTP 404")})
        card = self.card(discounted("a/open", hf="org/open"), fetch)
        self.assertIsNone(card["hugging_face"])
        self.assertTrue(any("no public record" in n for n in card["notes"]))
        self.assertEqual(card["pricing_usd_per_1m"]["list_input"], 1.0)
        self.assertEqual(card["pricing_usd_per_1m"]["input_discount_pct"], 50.0)

    def test_empty_hugging_face_id_is_treated_as_none(self):
        fetch = FakeFetch()
        card = self.card(discounted("a/prop", hf=""), fetch)
        self.assertEqual(fetch.calls, [])
        self.assertTrue(any("No Hugging Face ID" in n for n in card["notes"]))

    def test_full_hugging_face_chain(self):
        hf = {
            "author": "org", "pipeline_tag": "text-generation", "library_name": "transformers",
            "downloads": 1000, "likes": 50, "createdAt": "2026-08-01T00:00:00.000Z",
            "lastModified": "2026-08-05T00:00:00.000Z",
            "tags": ["license:apache-2.0", "arxiv:2608.00001"],
            "cardData": {"base_model": "org/base"},
            "spaces": ["u/s1", "u/s2", "u/s3", "u/s4"],
            "model-index": [{"results": [{"dataset": {"name": "MMLU"},
                                          "metrics": [{"type": "accuracy", "value": 81.2}]}]}],
        }
        fetch = FakeFetch({
            part2.HF_API + "org/open/discussions": {"count": 12, "numClosedDiscussions": 3},
            part2.HF_API + "org/open": hf,
            part2.ARXIV_API: ARXIV_XML,
        })
        card = self.card(discounted("a/open", hf="org/open"), fetch)
        h = card["hugging_face"]
        self.assertEqual(h["licence"], "apache-2.0")
        self.assertEqual(h["base_model"], ["org/base"])
        self.assertEqual(h["papers"][0]["title"], "A Test Paper")
        self.assertEqual(h["spaces_listed"], 4)
        self.assertEqual(len(h["space_examples"]), 3)
        self.assertEqual(h["discussions"], {"total": 12, "closed": 3})
        self.assertEqual(card["benchmarks"][0]["benchmark"], "MMLU")

    def test_discussions_failure_does_not_lose_the_card(self):
        fetch = FakeFetch({part2.HF_API + "org/open/discussions": FetchFailure("timed out"),
                           part2.HF_API + "org/open": {"tags": []}})
        card = self.card(discounted("a/open", hf="org/open"), fetch)
        self.assertIsNotNone(card["hugging_face"])
        self.assertTrue(any("discussions" in n for n in card["notes"]))


if __name__ == "__main__":
    unittest.main()
