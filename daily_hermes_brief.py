#!/usr/bin/env python3
"""
Daily Hermes Brief: the nightly entry point.

Runs Parts 1, 2 and 3 and writes one brief into an archive folder:

    <archive>/briefs/YYYY-MM-DD.md     the brief, for agents (and people) to read
    <archive>/briefs/YYYY-MM-DD.json   the same facts as structured data
    <archive>/state/snapshots/YYYY-MM-DD.json   compact catalogue, tomorrow's baseline

Earlier briefs are the run log: Part 2's exclusion list and Part 3's
already-reported list are read back from them, so there is no separate state
to drift out of step. A part that fails is recorded as failed and the brief is
still written. A brief that already exists is never overwritten.

Standard library only. Runs on Linux, macOS and Windows.

Usage:
    python3 daily_hermes_brief.py --archive DIR             # normal nightly run
    python3 daily_hermes_brief.py --archive DIR --dry-run   # print the brief, write nothing
    python3 daily_hermes_brief.py --catalogue saved.json    # use a saved catalogue (testing)
"""

import argparse
import json
import sys
import traceback
from pathlib import Path

import daily_hermes_brief_part1 as part1
import daily_hermes_brief_part2 as part2
import daily_hermes_brief_part3 as part3
from brief_common import acquire_lock, brief_date, get_json, get_text, iso, now_utc, usd

SCHEMA_VERSION = 1
HISTORY_BRIEFS = 30          # how many earlier briefs to read back for exclusions and repeats


# --- Archive ---------------------------------------------------------------

def load_history(briefs_dir, before_date):
    """Earlier briefs, newest first. Unreadable files are skipped."""
    briefs_dir = Path(briefs_dir)
    if not briefs_dir.exists():
        return []
    paths = sorted((p for p in briefs_dir.glob("*.json") if p.stem < before_date), reverse=True)
    history = []
    for path in paths[:HISTORY_BRIEFS]:
        try:
            history.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
    return history


def find_part(brief, number):
    return next((p for p in brief.get("parts") or [] if p.get("part") == number), {})


def featured_history(history):
    """One entry per earlier brief, newest first: the featured model ID or None."""
    out = []
    for brief in history:
        model = find_part(brief, 2).get("model") or {}
        out.append(model.get("id"))
    return out


def reported_ids(history):
    ids = set()
    for brief in history:
        for f in find_part(brief, 3).get("findings") or []:
            ids.add(f.get("id"))
            ids.update(f.get("aliases") or [])
    ids.discard(None)
    return ids


def failed_part(number, title, error):
    return {
        "part": number,
        "title": title,
        "status": "error",
        "text": f"Part {number} failed tonight and was skipped.",
        "notes": [f"Unexpected error: {type(error).__name__}: {error}"],
    }


# --- Building --------------------------------------------------------------

def build_brief(archive, raw_catalogue=None, now=None, fetch_json=get_json,
                fetch_text=get_text, pause=None):
    """Return (brief, raw_to_save, today). Never raises for a part's failure."""
    archive = Path(archive)
    now = now or now_utc()
    today = brief_date(now)
    history = load_history(archive / "briefs", today)

    p1, raw_to_save, _ = part1.run(archive / "state" / "snapshots", raw=raw_catalogue,
                                   checked_at=now, today=today)
    # Part 1 withholds a suspect catalogue from the baseline; Part 2 must not use it either.
    usable = raw_to_save if p1["status"] in ("ok", "baseline") else None

    try:
        p2 = part2.run(p1, usable, featured_history(history),
                       fetch_json=fetch_json, fetch_text=fetch_text)
    except Exception as e:                              # a bug must not cost the whole brief
        traceback.print_exc()
        p2 = failed_part(2, "Model of the day", e)

    try:
        kwargs = {"pause": pause} if pause else {}
        p3 = part3.run(reported_ids(history), now=now, fetch_json=fetch_json,
                       fetch_text=fetch_text, **kwargs)
    except Exception as e:
        traceback.print_exc()
        p3 = failed_part(3, "AI security news", e)

    brief = {
        "brief": "Daily Hermes Brief",
        "schema_version": SCHEMA_VERSION,
        "date": today,
        "generated_at_utc": iso(now),
        "conventions": ("Facts only. The brief is dated by UK calendar date; all times are UTC. "
                        "Prices are USD per million tokens. "
                        "Tiers: act = worth acting on, note = worth knowing."),
        "parts": [p1, p2, p3],
    }
    return brief, raw_to_save, today


# --- Markdown --------------------------------------------------------------

CHANGE_LABELS = {
    "new_model": "New model",
    "expiry_set": "Expiry date set",
    "expiry_changed": "Expiry date changed",
    "discount_started": "Discount started",
    "discount_ended": "Discount ended",
    "price_increase": "Price increase",
}


def change_line(c):
    label = CHANGE_LABELS.get(c["type"], c["type"])
    head = f"- [{c['tier']}] {label}: {c['id']}"
    if c["type"] == "new_model":
        text = f"{head}. Input {usd(c['prompt_per_1m'])}, output {usd(c['completion_per_1m'])}"
        if c.get("original_prompt_per_1m") is not None:
            text += (f" (list {usd(c['original_prompt_per_1m'])} and "
                     f"{usd(c['original_completion_per_1m'])})")
        return text + "."
    if c["type"] in ("expiry_set", "expiry_changed"):
        before = f", previously {c['previous_expiration_date']}" if c.get("previous_expiration_date") else ""
        return f"{head}. Expires {c['expiration_date']}{before}."
    if c["type"] == "discount_started":
        return (f"{head}. Input {usd(c['prompt_per_1m'])} (list {usd(c['original_prompt_per_1m'])}), "
                f"output {usd(c['completion_per_1m'])} (list {usd(c['original_completion_per_1m'])}).")
    if c["type"] == "discount_ended":
        return f"{head}. Now input {usd(c['prompt_per_1m'])}, output {usd(c['completion_per_1m'])}."
    if c["type"] == "price_increase":
        side = "Input" if c["side"] == "prompt" else "Output"
        return (f"{head}. {side} {usd(c['previous_per_1m'])} to {usd(c['current_per_1m'])} "
                f"(+{c['increase_pct']}%).")
    return head + "."


def render_part1(p):
    lines = ["## Part 1: Nous Portal catalogue changes", ""]
    summary = f"Status: {p['status']}. Checked {p['checked_at_utc']}."
    if p.get("compared_against"):
        summary += f" Compared against the snapshot of {p['compared_against']}."
    if p.get("model_count") is not None:
        summary += f" {p['model_count']} catalogue entries."
    lines += [summary, ""]
    if p.get("changes"):
        lines += ["### Changes", ""] + [change_line(c) for c in p["changes"]] + [""]
    elif p["status"] == "ok":
        lines += ["No changes since the last snapshot.", ""]
    if p.get("expiry_rollcall"):
        lines += ["### Models with an expiry date", ""]
        lines += [f"- {r['id']}: {r['expiration_date']}" for r in p["expiry_rollcall"]] + [""]
    notes = [n for n in p.get("notes") or [] if n != "No changes since the last snapshot."]
    if p.get("unparseable_models"):
        notes.append("Unparseable entries: " + "; ".join(p["unparseable_models"][:10]) + ".")
    if notes:
        lines += ["Notes:", ""] + [f"- {n}" for n in notes] + [""]
    return lines


def fmt_list(values):
    return ", ".join(str(v) for v in values) if values else "not listed"


def render_part2(p):
    lines = ["## Part 2: Model of the day", ""]
    m = p.get("model")
    if not m:
        lines += [p.get("text") or part2.NO_MODEL_TEXT, ""]
        lines += [f"- {n}" for n in p.get("notes") or []]
        return lines + ([""] if p.get("notes") else [])

    pr = m["pricing_usd_per_1m"]
    lines += [f"### {m['name']} (`{m['id']}`)", ""]
    if m.get("description"):
        lines += [f"Catalogue description: {m['description']}", ""]
    facts = [
        f"Provider: {m['provider']}",
        f"Listed on Nous Portal: {m['listed_on_portal'] or 'date not listed'}",
        f"Context window: {m['context_length'] or 'not listed'} tokens; "
        f"maximum output: {m['max_output_tokens'] or 'not listed'} tokens",
        f"Input: {fmt_list(m['input_modalities'])}; output: {fmt_list(m['output_modalities'])}",
        f"Reasoning efforts: {fmt_list(m['reasoning_efforts'])}",
        f"Knowledge cutoff: {m['knowledge_cutoff'] or 'not listed'}",
        f"Model expiry date: {m['expiration_date'] or 'none listed'}",
    ]
    price = f"Price: input {usd(pr['input'])}, output {usd(pr['output'])}"
    if pr.get("cache_read") is not None:
        price += f", cache read {usd(pr['cache_read'])}"
    facts.append(price)
    if pr.get("list_input") is not None:
        listed = (f"List price: input {usd(pr['list_input'])} ({pr['input_discount_pct']}% off), "
                  f"output {usd(pr['list_output'])} ({pr['output_discount_pct']}% off)")
        if pr.get("list_cache_read") is not None:
            listed += f", cache read {usd(pr['list_cache_read'])}"
        facts.append(listed)
    hf = m.get("hugging_face")
    if hf:
        facts.append(f"Hugging Face: {hf['id']}; licence {hf['licence'] or 'not stated'}; "
                     f"pipeline {hf['pipeline'] or 'not stated'}; library {hf['library'] or 'not stated'}")
        if hf.get("base_model"):
            facts.append(f"Base model: {fmt_list(hf['base_model'])}")
        facts.append(f"Hugging Face repository created {hf['repository_created'] or 'date not stated'}, "
                     f"last modified {hf['last_modified'] or 'date not stated'}; "
                     f"downloads (last 30 days) {hf['downloads_last_30_days']}; likes {hf['likes']}")
        for paper in hf.get("papers") or []:
            facts.append(f"Paper: {paper['title'] or 'title unavailable'} "
                         f"(arXiv {paper['arxiv_id']}, {paper['published'] or 'date unavailable'})")
        if hf.get("spaces_listed") is not None:
            examples = f" (for example {fmt_list(hf['space_examples'])})" if hf["space_examples"] else ""
            facts.append(f"Spaces using the model, as listed by the Hugging Face API: "
                         f"{hf['spaces_listed']}{examples}")
        if hf.get("discussions"):
            facts.append(f"Hugging Face discussions: {hf['discussions']['total']} "
                         f"({hf['discussions']['closed']} closed)")
    for b in m.get("benchmarks") or []:
        facts.append(f"Benchmark: {b['benchmark']}, {b['metric']} {b['value']} ({b['source']})")
    lines += [f"- {f}." for f in facts] + [""]
    if m.get("notes") or p.get("notes"):
        lines += ["Notes:", ""] + [f"- {n}" for n in (p.get("notes") or []) + m["notes"]] + [""]
    lines += ["Sources: " + ", ".join(m["sources"]) + ".", ""]
    return lines


def render_hermes_status(h):
    lines = ["### Hermes Agent security status", ""]
    if not h:
        return lines + ["Not available tonight (see notes).", ""]
    latest = (f"{h['latest_release']} ({h['latest_release_date'] or 'date not stated'})"
              if h["latest_release"] else "unknown (PyPI could not be read)")
    open_ids = h["not_fixed_in_latest_release"]
    lines += [f"Latest hermes-agent release on PyPI: {latest}. Known advisories (OSV, duplicates "
              f"merged): {h['advisories_total']}. Not fixed in the latest release: {len(open_ids)}.", ""]
    if h["minimum_version_clearing_fixed_advisories"]:
        fixed_count = sum(1 for a in h["advisories"] if a["fixed_in"])
        lines += [f"Update to {h['minimum_version_clearing_fixed_advisories']} or later to clear the "
                  f"{fixed_count} advisories that have a fixed release. Each line gives the version "
                  f"that fixes it, so any installed version below that is affected.", ""]

    def line(a):
        ids = a["id"] + (f" ({a['cve']})" if a["cve"] else "")
        if a["fixed_in"]:
            status = f"Fixed in {a['fixed_in']}"
        elif a["last_affected"]:
            status = f"No fixed release; affects versions up to {a['last_affected']} inclusive"
        else:
            status = "No fixed release stated"
        return (f"- {status}: {ids}. {a['category']}. Severity {(a['severity'] or 'not rated').title()}. "
                f"{(a['access_and_impact'] or 'No CVSS vector').capitalize()}. "
                f"Published {a['published'] or 'date not stated'}. {a['url']}")

    unfixed = [a for a in h["advisories"] if a["affects_latest_release"]]
    fixed = [a for a in h["advisories"] if not a["affects_latest_release"]]
    if unfixed:
        lines += ["Affecting the latest release:", ""] + [line(a) for a in unfixed] + [""]
    if fixed:
        lines += ["Fixed in a released version:", ""] + [line(a) for a in fixed] + [""]
    return lines


def render_threat_picture(t):
    lines = ["### Threat picture", ""]
    if not t:
        return lines + ["Not available tonight (see notes).", ""]
    trend = t.get("nvd_cves_published") or []
    if trend:
        lines += [f"CVEs published per {part3.TREND_WINDOW_DAYS}-day window, by NVD keyword search. "
                  f"Newest window first; each link returns the exact count (totalResults).", ""]
        for k in trend:
            parts = []
            for w in k["windows"]:
                count = "unavailable" if w["count"] is None else str(w["count"])
                parts.append(f"{count} ({w['start']} to {w['end']})")
            change = k["change_pct_vs_previous_window"]
            change_text = (f"; {'+' if change >= 0 else ''}{change}% on the previous window"
                           if change is not None else "")
            check = k["windows"][0]["check_url"]
            lines.append(f"- \"{k['keyword']}\": " + ", ".join(parts) + change_text
                         + (f". Check: {check}" if check else "."))
        lines.append("")
    kev = t.get("exploited_ai_software")
    if kev:
        lines += [f"Confirmed exploited (CISA Known Exploited Vulnerabilities, {kev['kev_entries_total']} "
                  f"entries in total): {kev['ai_entries_total']} are AI or agent software, matched by "
                  f"product name, so this is a floor. {kev['ai_added_last_90_days']} were added in the last "
                  f"{part3.KEV_RECENT_DAYS} days and {kev['ai_added_previous_90_days']} in the "
                  f"{part3.KEV_RECENT_DAYS} days before. Entries classed {part3.PROMPT_INJECTION_CWE} "
                  f"(prompt injection): {kev['prompt_injection_entries']}. Check: {kev['check_url']}", ""]
        for v in kev["most_recent"]:
            cwe = ", ".join(f"{c} ({part3.CWE_NAMES[c]})" if c in part3.CWE_NAMES else c for c in v["cwe"])
            ref = f" Vendor reference: {v['reference']}." if v["reference"] else ""
            lines.append(f"- {v['cve']}: {v['vendor']} {v['product']}, added {v['date_added']}. "
                         f"{cwe or 'CWE not stated'}. {v['nvd']}.{ref}")
        lines.append("")
    return lines


def render_part3(p):
    lines = ["## Part 3: AI security news", ""]
    if "hermes_status" in p:
        lines += render_hermes_status(p.get("hermes_status"))
    if "threat_picture" in p:
        lines += render_threat_picture(p.get("threat_picture"))
    lines += ["### New advisories", ""]
    if p.get("window_start_utc"):
        lines += [f"Published {p['window_start_utc']} to {p['window_end_utc']}, "
                  f"not reported in an earlier brief. Up to {part3.MAX_FINDINGS}; exploited first, "
                  f"then Hermes Agent, then its pinned dependencies, then severity. No exploit detail is included.", ""]
    if p.get("findings"):
        for f in p["findings"]:
            lines += [f["paragraph"], ""]
    else:
        lines += [p.get("text") or part3.NO_NEWS_TEXT, ""]
    checked = [f"{s['source']} ({s['status']})" for s in p.get("sources_checked") or []]
    if checked:
        lines += ["Sources checked: " + ", ".join(checked) + ".", ""]
    if p.get("notes"):
        lines += ["Notes:", ""] + [f"- {n}" for n in p["notes"]] + [""]
    return lines


def render_markdown(brief):
    lines = [f"# Daily Hermes Brief, {brief['date']}", "",
             f"Generated {brief['generated_at_utc']}. {brief['conventions']}", ""]
    renderers = {1: render_part1, 2: render_part2, 3: render_part3}
    for p in brief["parts"]:
        if p.get("status") == "error":
            lines += [f"## Part {p['part']}: {p['title']}", "", p["text"], ""]
            lines += [f"- {n}" for n in p.get("notes") or []] + [""]
        else:
            lines += renderers[p["part"]](p)
    return "\n".join(lines).rstrip() + "\n"


# --- Writing ---------------------------------------------------------------

def write_brief(archive, brief, raw_to_save, today):
    """Write the brief and snapshot. Returns the paths written. Refuses to overwrite."""
    archive = Path(archive)
    briefs_dir = archive / "briefs"
    md_path, json_path = briefs_dir / f"{today}.md", briefs_dir / f"{today}.json"
    if md_path.exists() or json_path.exists():
        raise FileExistsError(f"A brief for {today} already exists in {briefs_dir}")
    briefs_dir.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(brief, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(brief), encoding="utf-8")
    written = [md_path, json_path]
    if raw_to_save is not None:
        snap_dir = archive / "state" / "snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_path = snap_dir / f"{today}.json"
        snap_path.write_text(json.dumps(part1.compact_catalogue(raw_to_save), separators=(",", ":")),
                             encoding="utf-8")
        written.append(snap_path)
    return written


def run_nightly(archive, dry_run=False, raw_catalogue=None):
    """Lock, build, write. Returns the list of paths written ([] for a dry run or a repeat)."""
    archive = Path(archive)
    archive.mkdir(parents=True, exist_ok=True)
    with open(archive / ".brief.lock", "w") as lock:
        if not acquire_lock(lock):
            print("Another run is in progress; exiting.", file=sys.stderr)
            return None
        today = brief_date(now_utc())
        if not dry_run and (archive / "briefs" / f"{today}.json").exists():
            print(f"The brief for {today} already exists; nothing to do.")
            return []
        brief, raw_to_save, today = build_brief(archive, raw_catalogue=raw_catalogue)
        if dry_run:
            print(render_markdown(brief))
            return []
        written = write_brief(archive, brief, raw_to_save, today)
        statuses = ", ".join(f"part {p['part']}={p['status']}" for p in brief["parts"])
        print(f"Brief {today} written ({statuses}).")
        return written


def main():
    parser = argparse.ArgumentParser(description="Daily Hermes Brief: run all three parts")
    parser.add_argument("--archive", default="archive",
                        help="folder holding briefs/ and state/ (default: ./archive)")
    parser.add_argument("--dry-run", action="store_true", help="print the brief; write nothing")
    parser.add_argument("--catalogue", help="read the catalogue from a file instead of the portal")
    args = parser.parse_args()
    raw = None
    if args.catalogue:
        raw = json.loads(Path(args.catalogue).read_text(encoding="utf-8"))
    written = run_nightly(args.archive, dry_run=args.dry_run, raw_catalogue=raw)
    sys.exit(1 if written is None else 0)


if __name__ == "__main__":
    main()
