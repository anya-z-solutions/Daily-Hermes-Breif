#!/usr/bin/env python3
"""
Daily Hermes Brief, Part 3: AI security news.

Two standing sections, repeated every night so an agent reading only the
newest brief still has the full picture: Hermes Agent's security status
(latest release, every known advisory, fixed or not) and a measured threat
picture (CVE counts per 30-day window, exploited AI software). Then up to
three new advisories published in the last LOOKBACK_HOURS. All taken from
structured vulnerability databases only:

  1. OSV (which carries GitHub Security Advisories and PyPI advisories) for
     hermes-agent itself and the exact dependency versions it pins.
  2. NVD, by publication date and a short list of AI and agent keywords.
  3. CVE.org records, to name the affected product and versions.
  4. CISA's Known Exploited Vulnerabilities list, for confirmed exploitation.
  5. GitHub release tags and PyPI, for hermes-agent's latest release.

Each finding is assembled from structured fields only (identifiers, product
names, version ranges, CWE, CVSS, dates, URLs). Free-text descriptions and
summaries from the sources are never copied, so no exploit mechanics or
proof-of-concept detail can reach the brief.

Standard library only. Runs on Linux, macOS and Windows.

Usage (prints Part 3 on its own, writes nothing):
    python3 daily_hermes_brief_part3.py
"""

import datetime as dt
import json
import re
import time
import urllib.parse

from brief_common import FetchFailure, get_json, get_text, iso, now_utc, parse_iso

try:
    import tomllib                              # Python 3.11+
except ImportError:                             # older Python: fall back to a regex
    tomllib = None

# --- Configuration ---------------------------------------------------------

LOOKBACK_HOURS = 72          # a missed night or a slow database still gets caught; repeats are filtered
MAX_FINDINGS = 3
HERMES_PACKAGE = "hermes-agent"
PYPROJECT_URL = "https://raw.githubusercontent.com/NousResearch/hermes-agent/main/pyproject.toml"
OSV_BATCH_URL = "https://api.osv.dev/v1/querybatch"
OSV_VULN_URL = "https://api.osv.dev/v1/vulns/"
NVD_URL = "https://services.nvd.nist.gov/rest/json/cves/2.0"
NVD_KEYWORDS = ["Hermes Agent", "large language model", "LLM", "AI agent",
                "Model Context Protocol", "prompt injection"]
NVD_PAUSE_SECONDS = 6.5      # NVD allows 5 requests per 30 seconds without an API key
NVD_PAGE_SIZE = 100
CVE_ORG_URL = "https://cveawg.mitre.org/api/cve/"
KEV_URL = "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json"
NO_NEWS_TEXT = "No security news today."
OSV_QUERY_URL = "https://api.osv.dev/v1/query"
PYPI_URL = "https://pypi.org/pypi/hermes-agent/json"
# Release tags, listed with git's protocol v2 and filtered to refs/tags/v: about 7 KB, where the
# plain ref listing is about 10 MB. PyPI stopped at 0.19.0 while GitHub releases carried on.
GIT_UPLOAD_PACK_URL = "https://github.com/NousResearch/hermes-agent.git/git-upload-pack"
GIT_HEADERS = {"Git-Protocol": "version=2", "Content-Type": "application/x-git-upload-pack-request"}
RELEASE_TAG = re.compile(r"refs/tags/v(\d{1,3}\.\d+\.\d+)\b(?![+\-.\w])")   # v0.22.2, not v2026.9.24 or canary
TREND_KEYWORDS = ["prompt injection", "LLM", "AI agent", "Model Context Protocol"]
TREND_WINDOW_DAYS = 30
TREND_WINDOWS = 3
KEV_PAGE = "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"
KEV_RECENT_DAYS = 90
PROMPT_INJECTION_CWE = "CWE-1427"   # Improper Neutralization of Input Used for LLM Prompting
# Products on CISA's exploited list that count as AI or agent software. Matched on vendor and
# product names only, so the count is a floor: a product missing from this list is not counted.
AI_PRODUCTS = re.compile(
    r"\b(langflow|langchain|llama ?index|ollama|vllm|ray|comfyui|n8n|flowise|dify|openai|anthropic|"
    r"hugging ?face|mlflow|gradio|open ?webui|anythingllm|litellm|autogpt|copilot|chatgpt|gemini|"
    r"triton inference|torchserve|kubeflow|hermes agent|model context protocol|mcp)\b", re.I)

SCOPE_RANK = {"hermes": 0, "dependency": 1, "general": 2}
SCOPE_TEXT = {"hermes": "Hermes Agent", "dependency": "a dependency pinned by Hermes Agent",
              "general": "general AI and agent software"}
SEVERITY_RANK = {"CRITICAL": 4, "HIGH": 3, "MODERATE": 2, "MEDIUM": 2, "LOW": 1}

# Names for the CWEs most often seen in AI and agent advisories. Others show as the ID alone.
CWE_NAMES = {
    "CWE-20": "Improper Input Validation",
    "CWE-22": "Path Traversal",
    "CWE-74": "Injection",
    "CWE-77": "Command Injection",
    "CWE-78": "OS Command Injection",
    "CWE-79": "Cross-site Scripting",
    "CWE-89": "SQL Injection",
    "CWE-94": "Code Injection",
    "CWE-200": "Exposure of Sensitive Information",
    "CWE-269": "Improper Privilege Management",
    "CWE-276": "Incorrect Default Permissions",
    "CWE-284": "Improper Access Control",
    "CWE-285": "Improper Authorization",
    "CWE-287": "Improper Authentication",
    "CWE-306": "Missing Authentication for Critical Function",
    "CWE-345": "Insufficient Verification of Data Authenticity",
    "CWE-346": "Origin Validation Error",
    "CWE-352": "Cross-Site Request Forgery",
    "CWE-400": "Uncontrolled Resource Consumption",
    "CWE-434": "Unrestricted Upload of File with Dangerous Type",
    "CWE-502": "Deserialisation of Untrusted Data",
    "CWE-532": "Sensitive Information in Log File",
    "CWE-601": "Open Redirect",
    "CWE-639": "Authorisation Bypass Through User-Controlled Key",
    "CWE-697": "Incorrect Comparison",
    "CWE-707": "Improper Neutralisation",
    "CWE-732": "Incorrect Permission Assignment for Critical Resource",
    "CWE-770": "Allocation of Resources Without Limits",
    "CWE-787": "Out-of-bounds Write",
    "CWE-798": "Use of Hard-coded Credentials",
    "CWE-862": "Missing Authorisation",
    "CWE-863": "Incorrect Authorisation",
    "CWE-918": "Server-Side Request Forgery",
    "CWE-1321": "Prototype Pollution",
    "CWE-1333": "Inefficient Regular Expression Complexity",
    "CWE-1427": "Improper Neutralisation of Input Used for LLM Prompting (prompt injection)",
    "CWE-526": "Exposure of Sensitive Information Through Environment Variables",
    "CWE-749": "Exposed Dangerous Method or Function",
    "CWE-913": "Improper Control of Dynamically-Managed Code Resources",
    "CWE-95": "Eval Injection",
    "CWE-829": "Inclusion of Functionality from Untrusted Control Sphere",
}


# --- Dependencies ----------------------------------------------------------

REQUIREMENT = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)(\[[^\]]*\])?\s*(==\s*([^\s;,]+))?")


def parse_dependencies(pyproject_text):
    """[(package, pinned_version or None)] from hermes-agent's [project].dependencies."""
    deps = []
    if tomllib is not None:
        data = tomllib.loads(pyproject_text)
        entries = (data.get("project") or {}).get("dependencies") or []
    else:
        block = re.search(r"(?ms)^dependencies\s*=\s*\[(.*?)(?:^\]|^\[|\Z)", pyproject_text)
        entries = re.findall(r'(?m)^\s*"([^"]+)"', block.group(1)) if block else []   # skips comments
    seen = set()
    for entry in entries:
        if not isinstance(entry, str) or " @ " in entry:      # skip direct git URLs
            continue
        match = REQUIREMENT.match(entry)
        if not match:
            continue
        name = match.group(1).lower().replace("_", "-")
        if name in seen:
            continue
        seen.add(name)
        deps.append((name, match.group(4)))
    return deps


# --- OSV -------------------------------------------------------------------

def osv_candidates(deps, window_start, window_end, fetch_json, notes, sources):
    queries = [{"package": {"name": HERMES_PACKAGE, "ecosystem": "PyPI"}}]
    labels = [HERMES_PACKAGE]
    for name, version in deps:
        q = {"package": {"name": name, "ecosystem": "PyPI"}}
        if version:
            q["version"] = version
        queries.append(q)
        labels.append(name)
    try:
        batch = fetch_json(OSV_BATCH_URL, data={"queries": queries})
    except FetchFailure as e:
        notes.append(f"OSV could not be queried: {e}.")
        sources.append({"source": "OSV", "status": "failed"})
        return []
    sources.append({"source": "OSV", "status": "ok", "packages_checked": len(queries)})

    wanted = {}
    for label, res in zip(labels, (batch or {}).get("results") or []):
        for v in (res or {}).get("vulns") or []:
            modified = parse_iso(v.get("modified"))
            if v.get("id") and modified and modified >= window_start:   # published <= modified
                wanted.setdefault(v["id"], label)

    found = []
    for vuln_id, label in sorted(wanted.items()):
        try:
            record = fetch_json(OSV_VULN_URL + urllib.parse.quote(vuln_id))
        except FetchFailure as e:
            notes.append(f"OSV record {vuln_id} could not be read: {e}.")
            continue
        published = parse_iso(record.get("published"))
        if not published or not (window_start <= published <= window_end):
            continue
        found.append(from_osv(record, label))
    return found


def osv_affected(record):
    lines, fixed = [], []
    for a in record.get("affected") or []:
        pkg = (a.get("package") or {})
        name = pkg.get("name")
        if not name:
            continue
        spans = []
        for r in a.get("ranges") or []:
            start = end = None
            for ev in r.get("events") or []:
                if "introduced" in ev:
                    start = ev["introduced"]
                if "fixed" in ev:
                    end = ev["fixed"]
                    fixed.append(f"{name} {end}")
                if "last_affected" in ev:
                    end = f"{ev['last_affected']} inclusive"
            if start in (None, "0") and end:
                spans.append(f"before {end}")
            elif start and end:
                spans.append(f"from {start} before {end}")
            elif start:
                spans.append(f"from {start}, no fixed version stated")
        ecosystem = pkg.get("ecosystem") or "package"
        lines.append(f"{name} ({ecosystem}): {', '.join(spans) if spans else 'versions not stated'}")
    return lines, fixed


def from_osv(record, package_label):
    db = record.get("database_specific") or {}
    aliases = [a for a in record.get("aliases") or [] if isinstance(a, str)]
    cve = next((a for a in aliases if a.startswith("CVE-")), None)
    affected, fixed = osv_affected(record)
    severity = (db.get("severity") or "").upper() or None
    advisory_url = next((r.get("url") for r in record.get("references") or []
                         if r.get("type") == "ADVISORY" and "github.com/advisories" in (r.get("url") or "")),
                        None)
    return {
        "id": cve or record["id"],
        "aliases": sorted(set([record["id"]] + aliases) - {cve or record["id"]}),
        "scope": "hermes" if package_label == HERMES_PACKAGE else "dependency",
        "affected": affected,
        "cwe": [c for c in db.get("cwe_ids") or [] if isinstance(c, str)],
        "cwe_names": {},
        "severity": severity,
        "cvss": None,
        "fixed_in": fixed,
        "nvd_patch_reference": None,
        "published": iso(parse_iso(record.get("published"))),
        "exploited": None,
        "sources": [u for u in [f"https://osv.dev/vulnerability/{record['id']}", advisory_url] if u],
        "found_via": [f"OSV advisory for {package_label}"],
    }


# --- NVD -------------------------------------------------------------------

def nvd_time(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def nvd_cvss(cve):
    """Best available CVSS: v4.0, then v3.1, then v3.0. Primary source preferred."""
    metrics = cve.get("metrics") or {}
    for key, label in (("cvssMetricV40", "4.0"), ("cvssMetricV31", "3.1"), ("cvssMetricV30", "3.0")):
        entries = metrics.get(key) or []
        entries = sorted(entries, key=lambda e: e.get("type") != "Primary")
        for e in entries:
            data = e.get("cvssData") or {}
            if data.get("baseScore") is not None:
                return {"version": label, "score": data["baseScore"],
                        "severity": (data.get("baseSeverity") or "").upper() or None}
    return None


def nvd_candidates(window_start, window_end, fetch_json, notes, sources, pause):
    found, ok_keywords = {}, 0
    for i, keyword in enumerate(NVD_KEYWORDS):
        if i:
            pause(NVD_PAUSE_SECONDS)
        url = (f"{NVD_URL}?keywordSearch={urllib.parse.quote(keyword)}"
               f"&pubStartDate={nvd_time(window_start)}&pubEndDate={nvd_time(window_end)}"
               f"&resultsPerPage={NVD_PAGE_SIZE}")
        try:
            data = fetch_json(url, timeout=60)
        except FetchFailure as e:
            notes.append(f"NVD search for '{keyword}' failed: {e}.")
            continue
        ok_keywords += 1
        for item in (data or {}).get("vulnerabilities") or []:
            cve = (item or {}).get("cve") or {}
            if not cve.get("id") or cve.get("vulnStatus") == "Rejected":
                continue
            entry = found.setdefault(cve["id"], {"cve": cve, "keywords": []})
            entry["keywords"].append(keyword)
    sources.append({"source": "NVD", "status": "ok" if ok_keywords else "failed",
                    "keywords_checked": ok_keywords, "keywords_total": len(NVD_KEYWORDS)})

    out = []
    for cve_id, entry in sorted(found.items()):
        cve = entry["cve"]
        cvss = nvd_cvss(cve)
        cwes = sorted({d.get("value") for w in cve.get("weaknesses") or []
                       for d in w.get("description") or []
                       if (d.get("value") or "").startswith("CWE-")})
        patch = any("Patch" in (r.get("tags") or []) for r in cve.get("references") or [])
        out.append({
            "id": cve_id,
            "aliases": [],
            "scope": "general",                # CVE.org's affected product can raise it to "hermes"
            "affected": [],
            "cwe": cwes,
            "cwe_names": {},
            "severity": cvss["severity"] if cvss else None,
            "cvss": cvss,
            "fixed_in": [],
            "nvd_patch_reference": patch,
            "published": iso(published) if (published := parse_iso(cve.get("published"))) else None,
            "exploited": None,
            "sources": [f"https://nvd.nist.gov/vuln/detail/{cve_id}"],
            "found_via": [f"NVD keyword '{k}'" for k in entry["keywords"]],
            "_hermes_keyword_only": entry["keywords"] == ["Hermes Agent"],
        })
    return out


# --- CVE.org and KEV -------------------------------------------------------

def summarise_versions(versions):
    """Turn a CVE record's version list into one short phrase."""
    plain, ranges = [], []
    for v in versions or []:
        if not isinstance(v, dict) or v.get("status") != "affected":
            continue
        start = v.get("version")
        if v.get("lessThanOrEqual") and v["lessThanOrEqual"] == start:
            plain.append(str(start))
        elif v.get("lessThan"):
            ranges.append(f"from {start} before {v['lessThan']}" if start not in (None, "0", "*")
                          else f"before {v['lessThan']}")
        elif v.get("lessThanOrEqual"):
            ranges.append(f"from {start} to {v['lessThanOrEqual']} inclusive"
                          if start not in (None, "0", "*") else f"up to {v['lessThanOrEqual']} inclusive")
        elif start:
            plain.append(str(start))
    if ranges:
        return "; ".join(ranges[:3])
    if len(plain) > 3:
        return f"{len(plain)} listed versions, {plain[0]} to {plain[-1]}"
    if plain:
        return ", ".join(plain)
    return "versions not stated"


def enrich_from_cve_org(finding, fetch_json, notes):
    """Fill affected product, CWE names and CVSS from the CVE record. Never copies descriptions."""
    if not finding["id"].startswith("CVE-"):
        return
    try:
        record = fetch_json(CVE_ORG_URL + finding["id"])
    except FetchFailure as e:
        notes.append(f"CVE.org record {finding['id']} could not be read: {e}.")
        return
    cna = ((record or {}).get("containers") or {}).get("cna") or {}
    products = []
    for a in cna.get("affected") or []:
        product = a.get("product") or a.get("packageName")
        if not product or product.lower() in ("n/a", "unknown"):
            continue
        vendor = a.get("vendor")
        label = f"{vendor} {product}" if vendor and vendor.lower() not in ("n/a", "unknown") else product
        products.append(f"{label}: {summarise_versions(a.get('versions'))}")
        if "hermes" in product.lower() and "agent" in product.lower():
            finding["scope"] = "hermes"
    if products and not finding["affected"]:
        finding["affected"] = products[:3]
    for pt in cna.get("problemTypes") or []:
        for d in pt.get("descriptions") or []:
            cwe = d.get("cweId")
            if cwe:
                if cwe not in finding["cwe"]:
                    finding["cwe"].append(cwe)
                name = d.get("description") or ""
                if name and not name.upper().startswith("CWE-") and len(name) <= 80:
                    finding["cwe_names"][cwe] = name
    if not finding["cvss"]:
        for metric in cna.get("metrics") or []:
            for key, label in (("cvssV4_0", "4.0"), ("cvssV3_1", "3.1"), ("cvssV3_0", "3.0")):
                data = metric.get(key)
                if data and data.get("baseScore") is not None:
                    finding["cvss"] = {"version": label, "score": data["baseScore"],
                                       "severity": (data.get("baseSeverity") or "").upper() or None}
                    break
            if finding["cvss"]:
                break
        if finding["cvss"] and not finding["severity"]:
            finding["severity"] = finding["cvss"]["severity"]
    finding["sources"].append(f"https://www.cve.org/CVERecord?id={finding['id']}")


def load_kev(fetch_json, notes, sources):
    try:
        data = fetch_json(KEV_URL, timeout=90)
    except FetchFailure as e:
        notes.append(f"CISA Known Exploited Vulnerabilities list could not be read: {e}.")
        sources.append({"source": "CISA KEV", "status": "failed"})
        return None
    entries = [v for v in (data or {}).get("vulnerabilities") or [] if isinstance(v, dict) and v.get("cveID")]
    sources.append({"source": "CISA KEV", "status": "ok", "catalogue_version": (data or {}).get("catalogVersion")})
    return entries


# --- Ranking and paragraphs ------------------------------------------------

def merge(candidates):
    """Merge records that describe the same vulnerability (shared ID or alias)."""
    merged = []
    for c in candidates:
        ids = {c["id"], *c["aliases"]}
        target = next((m for m in merged if ids & ({m["id"], *m["aliases"]})), None)
        if target is None:
            merged.append(c)
            continue
        if c["id"].startswith("CVE-") and not target["id"].startswith("CVE-"):
            target["aliases"].append(target["id"])
            target["id"] = c["id"]
        target["aliases"] = sorted(({*target["aliases"], *ids}) - {target["id"]})
        target["scope"] = min(target["scope"], c["scope"], key=SCOPE_RANK.get)
        for key in ("affected", "cwe", "fixed_in", "sources", "found_via"):
            target[key] += [x for x in c[key] if x not in target[key]]
        target["cwe_names"].update(c["cwe_names"])
        for key in ("severity", "cvss", "nvd_patch_reference"):
            target[key] = target[key] or c[key]
        target["_hermes_keyword_only"] = (target.get("_hermes_keyword_only", False)
                                          and c.get("_hermes_keyword_only", False))
    return merged


def rank_key(f):
    """Exploited first, then Hermes Agent, then its dependencies, then severity, then most recent."""
    score = (f["cvss"] or {}).get("score") or 0
    published = parse_iso(f["published"]) or dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    return (not f["exploited"], SCOPE_RANK[f["scope"]],
            -SEVERITY_RANK.get(f["severity"] or "", 0), -score, -published.timestamp(), f["id"])


def category_text(f):
    if not f["cwe"]:
        return "not classified in the source"
    parts = []
    for cwe in f["cwe"]:
        name = f["cwe_names"].get(cwe) or CWE_NAMES.get(cwe)
        parts.append(f"{cwe} ({name})" if name else cwe)
    return ", ".join(parts)


def paragraph(f, kev_checked_at):
    ids = f["id"] + (f" ({', '.join(f['aliases'])})" if f["aliases"] else "")
    affected = "; ".join(f["affected"]) if f["affected"] else "affected product not yet stated in structured data"
    if f["cvss"]:
        severity = f"{(f['severity'] or f['cvss']['severity'] or 'unrated').title()}, CVSS {f['cvss']['version']} base score {f['cvss']['score']}"
    elif f["severity"]:
        severity = f["severity"].title()
    else:
        severity = "not yet rated"
    if f["exploited"]:
        exploited = f"Yes: listed in CISA's Known Exploited Vulnerabilities catalogue on {f['exploited']}"
    elif f["exploited"] is False:
        exploited = f"Not confirmed: not in CISA's Known Exploited Vulnerabilities catalogue at {kev_checked_at}"
    else:
        exploited = "Unknown: the CISA exploited list could not be checked"
    if f["fixed_in"]:
        patch = "Yes, fixed in " + ", ".join(f["fixed_in"])
    elif f["nvd_patch_reference"]:
        patch = "NVD lists a patch reference; no fixed version is stated"
    else:
        patch = "No fixed version stated in the source"
    return (f"{ids}. Scope: {SCOPE_TEXT[f['scope']]}. Affected: {affected}. "
            f"Category: {category_text(f)}. Severity: {severity}. Exploitation: {exploited}. "
            f"Patch: {patch}. Published: {(f['published'] or '')[:10]}. "
            f"Found via: {'; '.join(f['found_via'])}. Source: {', '.join(f['sources'])}.")


# --- Standing section: Hermes Agent security status -------------------------

def version_key(version):
    return tuple(int(n) for n in re.findall(r"\d+", str(version)))


REACH_WORDS = {
    "AV": {"N": "network", "A": "adjacent network", "L": "local access", "P": "physical access"},
    "PR": {"N": "no login", "L": "low-privilege login", "H": "high-privilege login"},
    "UI": {"N": "no user action", "R": "user action needed", "P": "passive user action",
           "A": "active user action"},
    "AC": {"L": "low complexity", "H": "high complexity"},
}
IMPACT_LETTERS = {"H": "high", "L": "low", "N": "none"}

CVSS_TERMS = [
    (("AV",), "attack vector", {"N": "network", "A": "adjacent network", "L": "local", "P": "physical"}),
    (("AC",), "complexity", {"L": "low", "H": "high"}),
    (("PR",), "privileges required", {"N": "none", "L": "low", "H": "high"}),
    (("UI",), "user interaction", {"N": "none", "R": "required", "P": "passive", "A": "active"}),
    (("C", "VC"), "confidentiality impact", {"H": "high", "L": "low", "N": "none"}),
    (("I", "VI"), "integrity impact", {"H": "high", "L": "low", "N": "none"}),
    (("A", "VA"), "availability impact", {"H": "high", "L": "low", "N": "none"}),
]


def describe_cvss(vector):
    """Turn a CVSS 3.x or 4.0 vector into plain words. Exploit-maturity metrics are left out."""
    if not vector:
        return None
    metrics = dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)
    words = []
    for keys, label, values in CVSS_TERMS:
        value = next((metrics[k] for k in keys if k in metrics), None)
        if value in values:
            words.append(f"{label} {values[value]}")
    return "; ".join(words) or None


def cvss_metrics(vector):
    if not vector:
        return {}
    return dict(part.split(":", 1) for part in vector.split("/")[1:] if ":" in part)


def reach_text(vector):
    """How the flaw is reached, e.g. 'network, no login, no user action, low complexity'."""
    m = cvss_metrics(vector)
    words = [REACH_WORDS[k][m[k]] for k in ("AV", "PR", "UI", "AC") if m.get(k) in REACH_WORDS[k]]
    return ", ".join(words) or None


def impact_text(vector):
    """Confidentiality/integrity/availability impact, e.g. 'none/none/low'."""
    m = cvss_metrics(vector)
    values = [IMPACT_LETTERS.get(m.get(a) or m.get(b)) for a, b in (("C", "VC"), ("I", "VI"), ("A", "VA"))]
    return "/".join(values) if all(values) else None


def affected_text(spans):
    """The affected range in comparison form: '< 0.18.0', '<= 0.19.0', '>= 0.5.0, < 0.9.0'."""
    parts = []
    for start, fixed, last in spans:
        low = f">= {start}, " if start not in ("0", None) else ""
        if fixed:
            parts.append(f"{low}< {fixed}")
        elif last:
            parts.append(f"{low}<= {last}")
        else:
            parts.append(f"{low.rstrip(', ')}" if low else "all versions")
    return "; ".join(parts) or "not stated"


def latest_release_tag(fetch_text):
    """Highest stable vX.Y.Z tag on GitHub. Raises FetchFailure."""
    def pkt(line):
        return f"{len(line) + 4:04x}{line}"
    body = (pkt("command=ls-refs\n") + "0001" + pkt("ref-prefix refs/tags/v\n") + "0000").encode()
    text = fetch_text(GIT_UPLOAD_PACK_URL, data=body, headers=GIT_HEADERS)
    versions = RELEASE_TAG.findall(text or "")
    if not versions:
        raise FetchFailure("no release tags found")
    return max(versions, key=version_key)


def osv_vector(record):
    vectors = {s.get("type"): s.get("score") for s in record.get("severity") or [] if isinstance(s, dict)}
    return vectors.get("CVSS_V3") or vectors.get("CVSS_V4")


def hermes_ranges(record):
    """[(introduced, fixed, last_affected)] for hermes-agent in one OSV record."""
    spans = []
    for a in record.get("affected") or []:
        if (a.get("package") or {}).get("name") != HERMES_PACKAGE:
            continue
        for r in a.get("ranges") or []:
            start = fixed = last = None
            for ev in r.get("events") or []:
                start = ev.get("introduced", start)
                fixed = ev.get("fixed", fixed)
                last = ev.get("last_affected", last)
            spans.append((start or "0", fixed, last))
    return spans


def affects(spans, version):
    v = version_key(version)
    for start, fixed, last in spans:
        if v < version_key(start):
            continue
        if fixed and v < version_key(fixed):
            return True
        if last and v <= version_key(last):
            return True
        if not fixed and not last:
            return True
    return False


def fetch_osv_package(fetch_json):
    """Every OSV record for hermes-agent, following pagination. Raises FetchFailure."""
    records, token = [], None
    for _ in range(5):
        query = {"package": {"name": HERMES_PACKAGE, "ecosystem": "PyPI"}}
        if token:
            query["page_token"] = token
        page = fetch_json(OSV_QUERY_URL, data=query) or {}
        records += page.get("vulns") or []
        token = page.get("next_page_token")
        if not token:
            break
    return records


def dedupe_osv(records):
    """OSV lists most advisories twice (GHSA and PYSEC). Keep one, preferring the GitHub record."""
    kept, seen = [], set()
    for r in sorted(records, key=lambda r: (not r.get("id", "").startswith("GHSA"), r.get("id", ""))):
        ids = {r.get("id"), *(r.get("aliases") or [])}
        if not r.get("id") or ids & seen:
            continue
        seen |= ids
        kept.append(r)
    return kept


def hermes_status(fetch_json, fetch_text, notes, sources):
    """Latest release, every known advisory, which are fixed, and the version that clears them."""
    try:
        records = dedupe_osv(fetch_osv_package(fetch_json))
        sources.append({"source": "OSV hermes-agent advisories", "status": "ok"})
    except FetchFailure as e:
        notes.append(f"hermes-agent's advisory list could not be read from OSV: {e}.")
        sources.append({"source": "OSV hermes-agent advisories", "status": "failed"})
        return None

    github_latest = pypi_latest = pypi_date = None
    try:
        github_latest = latest_release_tag(fetch_text)
        sources.append({"source": "GitHub release tags", "status": "ok"})
    except FetchFailure as e:
        notes.append(f"hermes-agent's release tags could not be read from GitHub: {e}.")
        sources.append({"source": "GitHub release tags", "status": "failed"})
    try:
        pypi = fetch_json(PYPI_URL)
        pypi_latest = (pypi.get("info") or {}).get("version")
        files = (pypi.get("releases") or {}).get(pypi_latest) or []
        pypi_date = (files[0].get("upload_time_iso_8601") or "")[:10] if files else None
        sources.append({"source": "PyPI", "status": "ok"})
    except (FetchFailure, AttributeError) as e:
        notes.append(f"hermes-agent's newest PyPI release could not be read: {e}.")
        sources.append({"source": "PyPI", "status": "failed"})
    candidates = [v for v in (github_latest, pypi_latest) if v]
    latest = max(candidates, key=version_key) if candidates else None

    advisories = []
    for r in records:
        db = r.get("database_specific") or {}
        spans = hermes_ranges(r)
        fixed = sorted({f for _, f, _ in spans if f}, key=version_key)
        last = sorted({l for _, _, l in spans if l}, key=version_key)
        aliases = [a for a in r.get("aliases") or [] if isinstance(a, str)]
        cwe = [c for c in db.get("cwe_ids") or [] if isinstance(c, str)]
        if fixed:
            status = "fixed"
        elif latest is None:
            status = "no_fix_named"
        elif affects(spans, latest):
            status = "no_fix_named_affects_latest"
        else:
            status = "no_fix_named_latest_beyond_range"
        vector = osv_vector(r)
        advisories.append({
            "id": r["id"],
            "status": status,
            "affected": affected_text(spans),
            "reach": reach_text(vector),
            "impact_cia": impact_text(vector),
            "cve": next((a for a in aliases if a.startswith("CVE-")), None),
            "cwe": cwe,
            "category": ", ".join(f"{c} ({CWE_NAMES[c]})" if c in CWE_NAMES else c for c in cwe)
                        or "not classified in the source",
            "severity": (db.get("severity") or "").upper() or None,
            "cvss_vector": osv_vector(r),
            "access_and_impact": describe_cvss(osv_vector(r)),
            "fixed_in": fixed[-1] if fixed else None,
            "last_affected": last[-1] if last else None,
            "affects_latest_release": affects(spans, latest) if latest else (not fixed),
            "published": (r.get("published") or "")[:10] or None,
            "url": f"https://osv.dev/vulnerability/{r['id']}",
        })
    advisories.sort(key=lambda a: (version_key(a["fixed_in"] or "999999"), a["id"]))
    fixed_versions = [a["fixed_in"] for a in advisories if a["fixed_in"]]
    return {
        "package": HERMES_PACKAGE,
        "latest_release": latest,
        "latest_release_source": ("GitHub release tag v" + latest if latest and latest == github_latest
                                  else "PyPI" if latest else None),
        "pypi_latest": pypi_latest,
        "pypi_latest_date": pypi_date,
        "advisories_total": len(advisories),
        "fixed": [a["id"] for a in advisories if a["status"] == "fixed"],
        "no_fix_named": [a["id"] for a in advisories if a["status"] != "fixed"],
        "no_fix_named_affecting_latest_release": [a["id"] for a in advisories
                                                  if a["status"] == "no_fix_named_affects_latest"],
        "minimum_version_clearing_fixed_advisories": max(fixed_versions, key=version_key) if fixed_versions else None,
        "advisories": advisories,
    }


# --- Standing section: threat picture ---------------------------------------

def nvd_count(params, fetch_json):
    url = f"{NVD_URL}?{params}&resultsPerPage=1"
    data = fetch_json(url, timeout=60)
    count = (data or {}).get("totalResults")
    if not isinstance(count, int):
        raise FetchFailure("NVD returned no result count")
    return count, url


def nvd_trend(now, fetch_json, pause, notes, sources):
    """CVEs published per 30-day window for each keyword, newest window first, with a check URL."""
    end_of_window = now.replace(hour=0, minute=0, second=0, microsecond=0)   # whole UTC days
    trend, failures = [], 0
    for keyword in TREND_KEYWORDS:
        windows = []
        for i in range(TREND_WINDOWS):
            end = end_of_window - dt.timedelta(days=TREND_WINDOW_DAYS * i)
            start = end - dt.timedelta(days=TREND_WINDOW_DAYS)
            pause(NVD_PAUSE_SECONDS)
            params = (f"keywordSearch={urllib.parse.quote(keyword)}"
                      f"&pubStartDate={nvd_time(start)}&pubEndDate={nvd_time(end)}")
            try:
                count, url = nvd_count(params, fetch_json)
            except FetchFailure as e:
                failures += 1
                notes.append(f"NVD count for '{keyword}' {start:%Y-%m-%d} to {end:%Y-%m-%d} failed: {e}.")
                count, url = None, None
            windows.append({"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}",
                            "count": count, "check_url": url})
        newest, previous = windows[0]["count"], windows[1]["count"] if len(windows) > 1 else None
        change = (round((newest - previous) / previous * 100) if newest is not None and previous
                  else None)
        trend.append({"keyword": keyword, "windows": windows, "change_pct_vs_previous_window": change})
    total = len(TREND_KEYWORDS) * TREND_WINDOWS
    sources.append({"source": "NVD trend counts", "status": "failed" if failures == total else "ok",
                    "counts_ok": total - failures, "counts_total": total})
    return trend


def kev_ai_summary(entries, now):
    """Exploited AI and agent software on CISA's list, counted and dated."""
    if entries is None:
        return None
    today = now.date()
    ai = [v for v in entries if AI_PRODUCTS.search(f"{v.get('vendorProject', '')} {v.get('product', '')}")]

    def added_between(days_from, days_to):
        out = 0
        for v in ai:
            try:
                age = (today - dt.date.fromisoformat(v.get("dateAdded", ""))).days
            except ValueError:
                continue
            if days_from <= age < days_to:
                out += 1
        return out

    recent = sorted(ai, key=lambda v: v.get("dateAdded", ""), reverse=True)[:3]
    return {
        "ai_entries_total": len(ai),
        "kev_entries_total": len(entries),
        "ai_added_last_90_days": added_between(0, KEV_RECENT_DAYS),
        "ai_added_previous_90_days": added_between(KEV_RECENT_DAYS, 2 * KEV_RECENT_DAYS),
        "prompt_injection_entries": sum(1 for v in entries if PROMPT_INJECTION_CWE in (v.get("cwes") or [])),
        "most_recent": [{
            "cve": v["cveID"],
            "vendor": v.get("vendorProject"),
            "product": v.get("product"),
            "date_added": v.get("dateAdded"),
            "cwe": v.get("cwes") or [],
            "reference": next(iter(re.findall(r"https?://[^\s;]+", v.get("notes") or "")), None),
            "nvd": f"https://nvd.nist.gov/vuln/detail/{v['cveID']}",
        } for v in recent],
        "check_url": KEV_PAGE,
    }


# --- Main ------------------------------------------------------------------

def run(already_reported, now=None, fetch_json=get_json, fetch_text=get_text, pause=time.sleep):
    """Build Part 3. already_reported: set of IDs and aliases from earlier briefs."""
    now = now or now_utc()
    window_start = now - dt.timedelta(hours=LOOKBACK_HOURS)
    result = {
        "part": 3,
        "title": "AI security news",
        "status": None,
        "window_start_utc": iso(window_start),
        "window_end_utc": iso(now),
        "hermes_status": None,
        "threat_picture": None,
        "findings": [],
        "text": None,
        "sources_checked": [],
        "notes": [],
    }
    notes, sources = result["notes"], result["sources_checked"]

    deps = []
    try:
        deps = parse_dependencies(fetch_text(PYPROJECT_URL))
        sources.append({"source": "hermes-agent pyproject.toml", "status": "ok",
                        "dependencies": len(deps)})
    except (FetchFailure, ValueError) as e:
        notes.append(f"hermes-agent's dependency list could not be read ({e}); "
                     f"checked hermes-agent itself only.")
        sources.append({"source": "hermes-agent pyproject.toml", "status": "failed"})

    candidates = osv_candidates(deps, window_start, now, fetch_json, notes, sources)
    candidates += nvd_candidates(window_start, now, fetch_json, notes, sources, pause)
    candidates = merge(candidates)
    candidates = [c for c in candidates
                  if not ({c["id"], *c["aliases"]} & set(already_reported))]

    kev_entries = load_kev(fetch_json, notes, sources)
    kev = None if kev_entries is None else {v["cveID"]: v.get("dateAdded") for v in kev_entries}
    for c in candidates:
        if kev is not None:
            hits = [kev[i] for i in [c["id"], *c["aliases"]] if i in kev]
            c["exploited"] = hits[0] if hits else False

    # NVD hits from the 'Hermes Agent' keyword alone need CVE.org to confirm the product
    # (the keyword also matches unrelated software called Hermes).
    for c in [c for c in candidates if c.get("_hermes_keyword_only")]:
        enrich_from_cve_org(c, fetch_json, notes)
    candidates = [c for c in candidates if not c.get("_hermes_keyword_only") or c["scope"] == "hermes"]

    chosen = sorted(candidates, key=rank_key)[:MAX_FINDINGS]
    for c in chosen:
        if not c.get("_hermes_keyword_only"):
            enrich_from_cve_org(c, fetch_json, notes)
    chosen = sorted(chosen, key=rank_key)      # enrichment can change scope or severity

    result["hermes_status"] = hermes_status(fetch_json, fetch_text, notes, sources)
    result["threat_picture"] = {
        "nvd_cves_published": nvd_trend(now, fetch_json, pause, notes, sources),
        "exploited_ai_software": kev_ai_summary(kev_entries, now),
    }

    failed = [s["source"] for s in sources if s["status"] == "failed"]
    kev_checked_at = iso(now)
    for c in chosen:
        c.pop("_hermes_keyword_only", None)
        c["paragraph"] = paragraph(c, kev_checked_at)
        result["findings"].append(c)

    if result["findings"]:
        result["status"] = "partial" if failed else "ok"
    elif {"OSV", "NVD"} <= set(failed):
        result["status"] = "failed"
        result["text"] = "Security sources could not be reached tonight; no findings checked."
    else:
        result["status"] = "partial" if failed else "none"
        result["text"] = NO_NEWS_TEXT
    if failed:
        notes.append("Sources that failed tonight: " + ", ".join(failed) + ".")
    return result


def main():
    print(json.dumps(run(set()), indent=2))


if __name__ == "__main__":
    main()
