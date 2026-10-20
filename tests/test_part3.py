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
                    return {"vulnerabilities": items}
            return {"vulnerabilities": []}

        routes = {
            part3.PYPROJECT_URL: PYPROJECT,
            part3.OSV_BATCH_URL: batch,
            part3.NVD_URL: nvd,
            part3.KEV_URL: {"catalogVersion": "test", "vulnerabilities": self.kev},
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


if __name__ == "__main__":
    unittest.main()
