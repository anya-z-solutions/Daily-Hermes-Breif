#!/usr/bin/env python3
"""
Daily Hermes Brief, Part 3: AI security news.

Up to three advisories published in the last LOOKBACK_HOURS, taken from
structured vulnerability databases only:

  1. OSV (which carries GitHub Security Advisories and PyPI advisories) for
     hermes-agent itself and the exact dependency versions it pins.
  2. NVD, by publication date and a short list of AI and agent keywords.
  3. CVE.org records, to name the affected product and versions.
  4. CISA's Known Exploited Vulnerabilities list, for confirmed exploitation.

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
    kev = {v.get("cveID"): v.get("dateAdded") for v in (data or {}).get("vulnerabilities") or []
           if v.get("cveID")}
    sources.append({"source": "CISA KEV", "status": "ok", "catalogue_version": (data or {}).get("catalogVersion")})
    return kev


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

    kev = load_kev(fetch_json, notes, sources)
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
