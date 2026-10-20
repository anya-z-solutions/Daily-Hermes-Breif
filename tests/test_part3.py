import unittest
from unittest import mock

from helpers import NOW, FakeFetch

import daily_hermes_brief_part3 as part3
from brief_common import FetchFailure

PYPROJECT = '''
[project]
name = "hermes-agent"
dependencies = [
  "requests==2.33.0; python_version >= '3.14'",
  "httpx[socks]==0.28.1",
  "fastapi>=0.104.0,<1",
  "psutil @ git+https://example.invalid/psutil.git ; sys_platform == 'android'",
]
'''
SECRET_TEXT = "send a crafted payload to the /exec endpoint"


def osv_record(vuln_id, published, cve=None, package="hermes-agent", severity="HIGH", fixed="1.2.0"):
    return {
        "id": vuln_id,
        "aliases": [cve] if cve else [],
        "summary": f"Remote attackers can {SECRET_TEXT}",
        "details": f"Step by step: {SECRET_TEXT}",
        "published": published,
        "modified": published,
        "database_specific": {"cwe_ids": ["CWE-78"], "severity": severity},
        "affected": [{"package": {"name": package, "ecosystem": "PyPI"},
                      "ranges": [{"type": "ECOSYSTEM", "events": [{"introduced": "0"}, {"fixed": fixed}]}]}],
        "references": [{"type": "ADVISORY", "url": f"https://github.com/advisories/{vuln_id}"}],
    }


def nvd_item(cve_id, published, score=7.5, severity="HIGH"):
    return {"cve": {
        "id": cve_id, "published": published, "vulnStatus": "Awaiting Analysis",
        "descriptions": [{"lang": "en", "value": f"Attackers {SECRET_TEXT}"}],
        "metrics": {"cvssMetricV31": [{"type": "Secondary",
                                       "cvssData": {"baseScore": score, "baseSeverity": severity}}]},
        "weaknesses": [{"description": [{"value": "CWE-94"}]}],
        "references": [{"url": "https://example.invalid/fix", "tags": ["Patch"]}],
    }}


def cve_org(product, vendor="acme"):
    return {"containers": {"cna": {
        "descriptions": [{"value": f"Attackers {SECRET_TEXT}"}],
        "affected": [{"vendor": vendor, "product": product,
                      "versions": [{"version": "0", "lessThan": "2.0", "status": "affected"}]}],
        "problemTypes": [{"descriptions": [{"cweId": "CWE-94", "description": "Code Injection"}]}],
    }}}


class Sources:
    """Build a FakeFetch for one night's sources."""

    def __init__(self):
        self.osv_ids = {}          # package -> list of (id, modified)
        self.osv_records = {}
        self.nvd = {}              # keyword -> list of items
        self.cve_org = {}
        self.kev = []
        self.down = set()
        self.package_records = []  # every OSV record for hermes-agent (the standing section)
        self.tags = ["v1.4.0", "v1.5.0"]
        self.pypi = {"info": {"version": "1.5.0"},
                     "releases": {"1.5.0": [{"upload_time_iso_8601": "2026-09-01T00:00:00Z"}]}}

    def add_osv(self, record, package="hermes-agent"):
        self.osv_ids.setdefault(package, []).append({"id": record["id"], "modified": record["modified"]})
        self.osv_records[record["id"]] = record

    def fetch(self):
        def batch(url, data):
            return {"results": [{"vulns": self.osv_ids.get(q["package"]["name"], [])}
                                for q in data["queries"]]}

        def nvd(url, data):
            for keyword, items in self.nvd.items():
                if "keywordSearch=" + keyword.replace(" ", "%20") + "&" in url:
                    return {"vulnerabilities": items, "totalResults": len(items)}
            return {"vulnerabilities": [], "totalResults": 0}

        routes = {
            part3.PYPROJECT_URL: PYPROJECT,
            part3.OSV_BATCH_URL: batch,
            part3.NVD_URL: nvd,
            part3.KEV_URL: {"catalogVersion": "test", "vulnerabilities": self.kev},
            part3.OSV_QUERY_URL: {"vulns": self.package_records},
            part3.PYPI_URL: self.pypi,
            part3.GIT_UPLOAD_PACK_URL: "".join(f"0040{'0' * 40} refs/tags/{t}\n" for t in self.tags) + "0000",
        }
        for vuln_id, record in self.osv_records.items():
            routes[part3.OSV_VULN_URL + vuln_id] = record
        for cve_id, record in self.cve_org.items():
            routes[part3.CVE_ORG_URL + cve_id] = record
        for url in self.down:
            routes[url] = FetchFailure("HTTP 503")
        return FakeFetch(routes)


def run(sources, reported=()):
    fetch = sources.fetch()
    return part3.run(set(reported), now=NOW, fetch_json=fetch, fetch_text=fetch, pause=lambda s: None)


class Part3Test(unittest.TestCase):
    def test_dependencies_parsed_with_pins_and_git_urls_skipped(self):
        deps = part3.parse_dependencies(PYPROJECT)
        self.assertEqual(deps, [("requests", "2.33.0"), ("httpx", "0.28.1"), ("fastapi", None)])

    def test_fallback_parser_matches_tomllib(self):
        text = PYPROJECT.replace('"fastapi>=0.104.0,<1",\n]',
                                 '# uvicorn "standard" bundles uvloop\n  "fastapi>=0.104.0,<1"]\n'
                                 '[project.optional-dependencies]\nx = [\n  "uvloop>=0.15"\n]')
        expected = part3.parse_dependencies(text)
        with mock.patch.object(part3, "tomllib", None):
            self.assertEqual(part3.parse_dependencies(text), expected)

    def test_quiet_night(self):
        result = run(Sources())
        self.assertEqual(result["status"], "none")
        self.assertEqual(result["text"], "No security news today.")

    def test_hermes_advisory_reported_without_any_source_text(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-aaaa", "2026-10-19T10:00:00Z", cve="CVE-2026-1"))
        s.cve_org["CVE-2026-1"] = cve_org("hermes-agent", vendor="NousResearch")
        result = run(s)
        self.assertEqual(result["status"], "ok")
        f = result["findings"][0]
        self.assertEqual(f["id"], "CVE-2026-1")
        self.assertEqual(f["scope"], "hermes")
        self.assertEqual(f["fixed_in"], ["hermes-agent 1.2.0"])
        self.assertIn("CWE-78 (OS Command Injection)", f["paragraph"])
        self.assertNotIn("crafted", str(result))          # no summary, details or description text

    def test_old_advisory_outside_window_is_ignored(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-old", "2026-10-10T00:00:00Z"))
        self.assertEqual(run(s)["findings"], [])

    def test_already_reported_alias_is_not_repeated(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-aaaa", "2026-10-19T10:00:00Z", cve="CVE-2026-1"))
        self.assertEqual(run(s, reported={"GHSA-aaaa"})["findings"], [])

    def test_exploited_ranks_above_hermes_and_severity(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-h", "2026-10-19T10:00:00Z", cve="CVE-2026-1", severity="CRITICAL"))
        s.nvd["LLM"] = [nvd_item("CVE-2026-2", "2026-10-19T09:00:00.000", score=5.0, severity="MEDIUM")]
        s.kev = [{"cveID": "CVE-2026-2", "dateAdded": "2026-10-19"}]
        ids = [f["id"] for f in run(s)["findings"]]
        self.assertEqual(ids, ["CVE-2026-2", "CVE-2026-1"])

    def test_dependency_ranks_between_hermes_and_general(self):
        s = Sources()
        s.nvd["LLM"] = [nvd_item("CVE-2026-9", "2026-10-19T09:00:00.000", score=10.0, severity="CRITICAL")]
        s.add_osv(osv_record("GHSA-dep", "2026-10-19T10:00:00Z", package="requests", severity="LOW"),
                  package="requests")
        s.add_osv(osv_record("GHSA-h", "2026-10-19T10:00:00Z", severity="LOW"))
        scopes = [f["scope"] for f in run(s)["findings"]]
        self.assertEqual(scopes, ["hermes", "dependency", "general"])

    def test_same_cve_from_two_sources_is_merged(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-aaaa", "2026-10-19T10:00:00Z", cve="CVE-2026-1"))
        s.nvd["prompt injection"] = [nvd_item("CVE-2026-1", "2026-10-19T09:00:00.000")]
        findings = run(s)["findings"]
        self.assertEqual(len(findings), 1)
        self.assertIn("GHSA-aaaa", findings[0]["aliases"])

    def test_unrelated_hermes_software_is_dropped(self):
        s = Sources()
        s.nvd["Hermes Agent"] = [nvd_item("CVE-2026-5", "2026-10-19T09:00:00.000")]
        s.cve_org["CVE-2026-5"] = cve_org("hermes javascript engine", vendor="facebook")
        self.assertEqual(run(s)["findings"], [])

    def test_at_most_three_findings(self):
        s = Sources()
        s.nvd["LLM"] = [nvd_item(f"CVE-2026-{i}", "2026-10-19T09:00:00.000") for i in range(10, 16)]
        self.assertEqual(len(run(s)["findings"]), 3)

    def test_all_databases_down(self):
        s = Sources()
        s.down = {part3.OSV_BATCH_URL, part3.NVD_URL, part3.KEV_URL}
        result = run(s)
        self.assertEqual(result["status"], "failed")
        self.assertIn("could not be reached", result["text"])

    def test_kev_down_marks_exploitation_unknown(self):
        s = Sources()
        s.add_osv(osv_record("GHSA-aaaa", "2026-10-19T10:00:00Z"))
        s.down = {part3.KEV_URL}
        result = run(s)
        self.assertEqual(result["status"], "partial")
        self.assertIn("Unknown", result["findings"][0]["paragraph"])

    def test_dependency_list_unavailable_still_checks_hermes(self):
        s = Sources()
        s.down = {part3.PYPROJECT_URL}
        s.add_osv(osv_record("GHSA-aaaa", "2026-10-19T10:00:00Z"))
        result = run(s)
        self.assertEqual(len(result["findings"]), 1)
        self.assertTrue(any("dependency list" in n for n in result["notes"]))


def with_vector(record, vector, fixed=None, last=None):
    record = dict(record, severity=[{"type": "CVSS_V3", "score": vector}])
    events = [{"introduced": "0"}] + ([{"fixed": fixed}] if fixed else []) + \
             ([{"last_affected": last}] if last else [])
    record["affected"] = [{"package": {"name": "hermes-agent", "ecosystem": "PyPI"},
                           "ranges": [{"type": "ECOSYSTEM", "events": events}]}]
    return record


class StandingSectionsTest(unittest.TestCase):
    def test_hermes_status_merges_duplicates_and_splits_fixed_from_open(self):
        s = Sources()
        net = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:L/I:L/A:L/E:P"
        ghsa_fixed = with_vector(osv_record("GHSA-f", "2026-06-01T00:00:00Z", cve="CVE-2026-7"), net, fixed="1.2.0")
        pysec_dup = dict(ghsa_fixed, id="PYSEC-1", aliases=["CVE-2026-7", "GHSA-f"])
        pysec_dup.pop("database_specific")
        ghsa_open = with_vector(osv_record("GHSA-o", "2026-06-02T00:00:00Z", cve="CVE-2026-8"), net, last="1.5.0")
        ghsa_new = with_vector(osv_record("GHSA-n", "2026-06-03T00:00:00Z"), net, fixed="1.4.0")
        s.package_records = [pysec_dup, ghsa_fixed, ghsa_open, ghsa_new]
        h = run(s)["hermes_status"]
        self.assertEqual(h["latest_release"], "1.5.0")
        self.assertEqual(h["advisories_total"], 3)
        self.assertEqual(h["no_fix_named_affecting_latest_release"], ["GHSA-o"])
        self.assertEqual(next(a for a in h["advisories"] if a["id"] == "GHSA-o")["affected"], "<= 1.5.0")
        self.assertEqual(h["minimum_version_clearing_fixed_advisories"], "1.4.0")
        fixed = next(a for a in h["advisories"] if a["id"] == "GHSA-f")
        self.assertEqual(fixed["access_and_impact"],
                         "attack vector network; complexity low; privileges required none; "
                         "user interaction none; confidentiality impact low; integrity impact low; "
                         "availability impact low")
        self.assertNotIn("crafted", str(h))

    def test_github_release_beyond_named_range_is_stated_not_called_fixed(self):
        # PyPI stopped at 1.5.0; GitHub has 1.7.0 and a canary and a calendar tag.
        s = Sources()
        s.tags = ["v1.5.0", "v1.7.0", "v1.7.1+canary.20261020T070000Z", "v2026.9.24"]
        net = "CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:L"
        s.package_records = [with_vector(osv_record("GHSA-o", "2026-06-02T00:00:00Z"), net, last="1.5.0")]
        h = run(s)["hermes_status"]
        self.assertEqual(h["latest_release"], "1.7.0")
        self.assertEqual(h["latest_release_source"], "GitHub release tag v1.7.0")
        self.assertEqual(h["pypi_latest"], "1.5.0")
        a = h["advisories"][0]
        self.assertEqual(a["status"], "no_fix_named_latest_beyond_range")
        self.assertEqual(a["reach"], "network, no login, no user action, low complexity")
        self.assertEqual(a["impact_cia"], "none/none/low")
        self.assertEqual(h["no_fix_named"], ["GHSA-o"])
        self.assertEqual(h["no_fix_named_affecting_latest_release"], [])

    def test_tags_unavailable_falls_back_to_pypi(self):
        s = Sources()
        s.down = {part3.GIT_UPLOAD_PACK_URL}
        h = run(s)["hermes_status"]
        self.assertEqual(h["latest_release"], "1.5.0")
        self.assertEqual(h["latest_release_source"], "PyPI")

    def test_version_comparison_is_numeric(self):
        self.assertTrue(part3.version_key("0.18.0") > part3.version_key("0.9.0"))
        spans = [("0", "0.18.0", None)]
        self.assertTrue(part3.affects(spans, "0.9.0"))
        self.assertFalse(part3.affects(spans, "0.18.0"))
        self.assertTrue(part3.affects([("0", None, "0.19.0")], "0.19.0"))

    def test_trend_counts_three_windows_with_check_urls(self):
        s = Sources()
        s.nvd["prompt injection"] = [nvd_item("CVE-2026-1", "2026-10-19T09:00:00.000")]
        trend = run(s)["threat_picture"]["nvd_cves_published"]
        pi = next(t for t in trend if t["keyword"] == "prompt injection")
        self.assertEqual(len(pi["windows"]), 3)
        self.assertEqual(pi["windows"][0]["end"], "2026-10-20")
        self.assertEqual(pi["windows"][0]["start"], "2026-09-20")
        self.assertIn("resultsPerPage=1", pi["windows"][0]["check_url"])
        self.assertEqual(pi["change_pct_vs_previous_window"], 0)

    def test_exploited_ai_software_counted_by_product(self):
        s = Sources()
        s.kev = [
            {"cveID": "CVE-2026-50", "vendorProject": "Langflow", "product": "Langflow",
             "dateAdded": "2026-08-04", "cwes": ["CWE-94"], "notes": "https://vendor.example/adv ; x"},
            {"cveID": "CVE-2026-51", "vendorProject": "Ray-Project", "product": "Ray",
             "dateAdded": "2026-05-01", "cwes": ["CWE-352"], "notes": ""},
            {"cveID": "CVE-2026-52", "vendorProject": "Acme", "product": "Router",
             "dateAdded": "2026-10-01", "cwes": ["CWE-1427"], "notes": ""},
        ]
        kev = run(s)["threat_picture"]["exploited_ai_software"]
        self.assertEqual(kev["ai_entries_total"], 2)
        self.assertEqual(kev["ai_added_last_90_days"], 1)
        self.assertEqual(kev["ai_added_previous_90_days"], 1)
        self.assertEqual(kev["prompt_injection_entries"], 1)
        self.assertEqual(kev["most_recent"][0]["reference"], "https://vendor.example/adv")

    def test_standing_sections_fail_softly(self):
        s = Sources()
        s.down = {part3.OSV_QUERY_URL, part3.PYPI_URL}
        result = run(s)
        self.assertIsNone(result["hermes_status"])
        self.assertTrue(any("advisory list" in n for n in result["notes"]))


if __name__ == "__main__":
    unittest.main()
