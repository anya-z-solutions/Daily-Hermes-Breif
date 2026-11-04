import json
import os
import tempfile
import unittest
from unittest import mock

import helpers  # noqa: F401  (puts the project on the path)

import brief_common
from brief_common import FetchFailure


class FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class CachedFetchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ, {"HERMES_BRIEF_CACHE": self.tmp.name})
        self.env.start()
        self.sent = []

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def fake_request(self, responses):
        def request(url, headers=None, with_headers=False, **kwargs):
            self.sent.append(dict(headers or {}))
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result
        return request

    def test_unchanged_file_is_not_downloaded_again(self):
        body = json.dumps({"vulnerabilities": [{"cveID": "CVE-1"}]})
        responses = [(body, FakeHeaders({"ETag": '"v1"', "Last-Modified": "Mon, 02 Nov 2026 10:00:00 GMT"})),
                     FetchFailure("HTTP 304")]
        with mock.patch.object(brief_common, "_request", self.fake_request(responses)):
            first = brief_common.get_json_cached("https://example.invalid/kev.json")
            second = brief_common.get_json_cached("https://example.invalid/kev.json")
        self.assertEqual(first, second)
        self.assertNotIn("If-None-Match", self.sent[0])
        self.assertEqual(self.sent[1]["If-None-Match"], '"v1"')

    def test_changed_file_replaces_the_cache(self):
        responses = [(json.dumps({"v": 1}), FakeHeaders({"ETag": '"v1"'})),
                     (json.dumps({"v": 2}), FakeHeaders({"ETag": '"v2"'})),
                     FetchFailure("HTTP 304")]
        with mock.patch.object(brief_common, "_request", self.fake_request(responses)):
            brief_common.get_json_cached("https://example.invalid/kev.json")
            self.assertEqual(brief_common.get_json_cached("https://example.invalid/kev.json"), {"v": 2})
            self.assertEqual(brief_common.get_json_cached("https://example.invalid/kev.json"), {"v": 2})
        self.assertEqual(self.sent[2]["If-None-Match"], '"v2"')

    def test_failure_without_a_cache_is_still_a_failure(self):
        with mock.patch.object(brief_common, "_request", self.fake_request([FetchFailure("HTTP 503")])):
            with self.assertRaises(FetchFailure):
                brief_common.get_json_cached("https://example.invalid/kev.json")


if __name__ == "__main__":
    unittest.main()
