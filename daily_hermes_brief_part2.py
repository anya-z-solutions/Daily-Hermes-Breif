#!/usr/bin/env python3
"""
Daily Hermes Brief, Part 2: model of the day.

Picks one model by a fixed rule (a new model first, otherwise the discounted
model with the largest USD saving at a reference volume, excluding recent
picks) and builds a facts-only card from the catalogue and Hugging Face.

No language model is involved: every line of the card comes from a named
source field, so the card costs no tokens to produce and can't drift.

Standard library only. Runs on Linux, macOS and Windows.

Usage (prints Part 2 on its own, writes nothing):
    python3 daily_hermes_brief_part2.py
    python3 daily_hermes_brief_part2.py --input catalogue.json
"""

import argparse
import datetime as dt
import json
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from brief_common import FetchFailure, get_json, get_text, per_million

# --- Configuration ---------------------------------------------------------

REFERENCE_INPUT_TOKENS = 10_000_000          # per month, for ranking discounts by USD saved
REFERENCE_OUTPUT_TOKENS = 2_000_000
EXCLUSION_RUNS = 14                          # skip models featured in the last 14 briefs
HF_API = "https://huggingface.co/api/models/"
ARXIV_API = "https://export.arxiv.org/api/query?id_list="
MAX_BENCHMARKS = 10
MAX_PAPERS = 2
MAX_SPACE_EXAMPLES = 3
NO_MODEL_TEXT = "No model of the day."


# --- Catalogue -------------------------------------------------------------

def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def is_base_model(model_id):
    return bool(model_id) and ":" not in model_id and not model_id.startswith("~")


def catalogue_models(raw):
    """{id: raw entry} for base models only (no :variants, no ~aliases)."""
    models = raw.get("data", raw) if isinstance(raw, dict) else raw
    out = {}
    for m in models if isinstance(models, list) else []:
        if isinstance(m, dict) and is_base_model(m.get("id")) and isinstance(m.get("pricing"), dict):
            out[m["id"]] = m
    return out


def prices(m):
    """(prompt, completion, original_prompt, original_completion) per token, floats or None."""
    p = m.get("pricing") or {}
    orig = p.get("original") if isinstance(p.get("original"), dict) else {}
    return (to_float(p.get("prompt")), to_float(p.get("completion")),
            to_float(orig.get("prompt")), to_float(orig.get("completion")))


def cost_at_reference(prompt, completion):
    if prompt is None or completion is None:
        return None
    return prompt * REFERENCE_INPUT_TOKENS + completion * REFERENCE_OUTPUT_TOKENS


def list_cost(m):
    """Monthly cost at the reference volume at list price (original price if discounted)."""
    prompt, completion, oprompt, ocompletion = prices(m)
    return cost_at_reference(oprompt if oprompt is not None else prompt,
                             ocompletion if ocompletion is not None else completion)


def saving(m):
    """USD saved per month at the reference volume, or None if not discounted."""
    prompt, completion, oprompt, ocompletion = prices(m)
    if oprompt is None or ocompletion is None:
        return None
    now, before = cost_at_reference(prompt, completion), cost_at_reference(oprompt, ocompletion)
    if now is None or before is None:
        return None
    return before - now


def created(m):
    value = m.get("created")
    return value if isinstance(value, (int, float)) else 0


# --- Selection -------------------------------------------------------------

def select(part1, models, recently_featured):
    """Return (model_id or None, basis). basis is 'new_model' or 'discount' (kept out of the card)."""
    if part1.get("status") == "ok":
        new_ids = [c["id"] for c in part1.get("changes", [])
                   if c.get("type") == "new_model" and c.get("id") in models]
        if new_ids:
            # Highest list price at the reference volume; newer model; then ID for a stable order.
            best = sorted(new_ids, key=lambda i: (-(list_cost(models[i]) or 0),
                                                  -created(models[i]), i))[0]
            return best, "new_model"

    excluded = set(recently_featured)
    candidates = []
    for model_id, m in models.items():
        s = saving(m)
        if s is not None and s > 0 and model_id not in excluded:
            candidates.append((-round(s, 9), -created(m), model_id))
    if candidates:
        return sorted(candidates)[0][2], "discount"
    return None, None


# --- Hugging Face and arXiv ------------------------------------------------

def clean_hf_id(value):
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def arxiv_ids(hf):
    ids = []
    for tag in hf.get("tags") or []:
        if isinstance(tag, str) and tag.startswith("arxiv:"):
            ids.append(tag.split(":", 1)[1])
    return ids[:MAX_PAPERS]


def fetch_paper(arxiv_id, fetch_text):
    """Title and first-published date of an arXiv paper. Raises FetchFailure."""
    xml_text = fetch_text(ARXIV_API + urllib.parse.quote(arxiv_id))
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        raise FetchFailure("arXiv response was not valid XML") from None
    ns = {"a": "http://www.w3.org/2005/Atom"}
    entry = root.find("a:entry", ns)
    if entry is None:
        raise FetchFailure("arXiv returned no entry")
    title = " ".join((entry.findtext("a:title", default="", namespaces=ns) or "").split())
    published = (entry.findtext("a:published", default="", namespaces=ns) or "")[:10]
    if not title:
        raise FetchFailure("arXiv entry had no title")
    return {"arxiv_id": arxiv_id, "title": title, "published": published or None,
            "url": f"https://arxiv.org/abs/{arxiv_id}"}


def benchmarks(hf):
    """Self-reported results from the model card's model-index, if the publisher gave any."""
    index = hf.get("model-index") or (hf.get("cardData") or {}).get("model-index") or []
    results = []
    for entry in index if isinstance(index, list) else []:
        for r in (entry or {}).get("results") or []:
            dataset = (r.get("dataset") or {}).get("name") or (r.get("dataset") or {}).get("type")
            for metric in r.get("metrics") or []:
                value = metric.get("value")
                if dataset and value is not None:
                    results.append({
                        "benchmark": str(dataset),
                        "metric": str(metric.get("name") or metric.get("type") or "score"),
                        "value": value,
                    })
                if len(results) >= MAX_BENCHMARKS:
                    return results
    return results


def hugging_face_section(hf_id, fetch_json, fetch_text, notes, sources):
    """Branch A of the research chain. Returns a dict, or None if there is no public record."""
    url = HF_API + hf_id
    try:
        hf = fetch_json(url)
    except FetchFailure as e:
        if "HTTP 401" in str(e) or "HTTP 404" in str(e):
            notes.append(f"Hugging Face has no public record for {hf_id} ({e}).")
        else:
            notes.append(f"Hugging Face could not be reached for {hf_id}: {e}.")
        return None
    if not isinstance(hf, dict):
        notes.append(f"Hugging Face returned an unexpected response for {hf_id}.")
        return None
    sources.append(f"https://huggingface.co/{hf_id}")

    card = hf.get("cardData") or {}
    licence = card.get("license") or next(
        (t.split(":", 1)[1] for t in hf.get("tags") or [] if isinstance(t, str) and t.startswith("license:")),
        None)
    base_model = card.get("base_model")
    if isinstance(base_model, str):
        base_model = [base_model]

    section = {
        "id": hf_id,
        "author": hf.get("author"),
        "licence": licence,
        "base_model": base_model or None,
        "pipeline": hf.get("pipeline_tag"),
        "library": hf.get("library_name"),
        "repository_created": (hf.get("createdAt") or "")[:10] or None,
        "last_modified": (hf.get("lastModified") or "")[:10] or None,
        "downloads_last_30_days": hf.get("downloads"),
        "likes": hf.get("likes"),
        "gated": hf.get("gated"),
        "benchmarks_self_reported": benchmarks(hf),
        "papers": [],
        "spaces_listed": None,
        "space_examples": [],
        "discussions": None,
    }

    # Linked papers: only those the card itself references. No searching.
    for arxiv_id in arxiv_ids(hf):
        try:
            section["papers"].append(fetch_paper(arxiv_id, fetch_text))
            sources.append(f"https://arxiv.org/abs/{arxiv_id}")
        except FetchFailure as e:
            section["papers"].append({"arxiv_id": arxiv_id, "title": None, "published": None,
                                      "url": f"https://arxiv.org/abs/{arxiv_id}"})
            notes.append(f"arXiv record {arxiv_id} could not be read: {e}.")

    spaces = hf.get("spaces")
    if isinstance(spaces, list):
        section["spaces_listed"] = len(spaces)
        section["space_examples"] = [s for s in spaces if isinstance(s, str)][:MAX_SPACE_EXAMPLES]

    try:
        disc = fetch_json(url + "/discussions")
        if isinstance(disc, dict) and isinstance(disc.get("count"), int):
            section["discussions"] = {"total": disc["count"],
                                      "closed": disc.get("numClosedDiscussions")}
    except FetchFailure as e:
        notes.append(f"Hugging Face discussions for {hf_id} could not be read: {e}.")
    return section


# --- Card ------------------------------------------------------------------

def epoch_to_date(value):
    if not isinstance(value, (int, float)) or value <= 0:
        return None
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).strftime("%Y-%m-%d")


def discount_pct(now, before):
    if now is None or not before:
        return None
    return round((before - now) / before * 100, 1)


def build_card(m, fetch_json, fetch_text):
    notes, sources = [], [f"https://inference-api.nousresearch.com/v1/models ({m['id']})"]
    prompt, completion, oprompt, ocompletion = prices(m)
    pricing = m.get("pricing") or {}
    cache_read = to_float(pricing.get("input_cache_read"))
    orig = pricing.get("original") if isinstance(pricing.get("original"), dict) else {}
    arch = m.get("architecture") or {}
    reasoning = m.get("reasoning") if isinstance(m.get("reasoning"), dict) else {}
    name = m.get("name") or m["id"]

    card = {
        "id": m["id"],
        "name": name,
        "provider": name.split(":", 1)[0].strip() if ":" in name else m["id"].split("/", 1)[0],
        "listed_on_portal": epoch_to_date(m.get("created")),
        "description": (m.get("description") or "").strip() or None,
        "context_length": m.get("context_length"),
        "max_output_tokens": (m.get("top_provider") or {}).get("max_completion_tokens"),
        "input_modalities": arch.get("input_modalities"),
        "output_modalities": arch.get("output_modalities"),
        "reasoning_efforts": reasoning.get("supported_efforts"),
        "knowledge_cutoff": m.get("knowledge_cutoff"),
        "expiration_date": m.get("expiration_date") or None,
        "pricing_usd_per_1m": {
            "input": per_million(prompt),
            "output": per_million(completion),
            "cache_read": per_million(cache_read),
            "list_input": per_million(oprompt),
            "list_output": per_million(ocompletion),
            "list_cache_read": per_million(to_float(orig.get("input_cache_read"))),
            "input_discount_pct": discount_pct(prompt, oprompt),
            "output_discount_pct": discount_pct(completion, ocompletion),
        },
        "discount_end_date": None,
        "hugging_face": None,
        "benchmarks": [],
        "notes": notes,
        "sources": sources,
    }
    if oprompt is not None:
        notes.append("The catalogue gives no end date for this discount.")

    hf_id = clean_hf_id(m.get("hugging_face_id"))
    if hf_id:
        card["hugging_face"] = hugging_face_section(hf_id, fetch_json, fetch_text, notes, sources)
        if card["hugging_face"]:
            card["benchmarks"] = [
                dict(b, source="Hugging Face model card, self-reported by the publisher")
                for b in card["hugging_face"].pop("benchmarks_self_reported")
            ]
    else:
        notes.append("No Hugging Face ID in the catalogue (proprietary or unlisted weights).")
    if not card["benchmarks"]:
        notes.append("No benchmark figures in the sources consulted.")
    return card


# --- Main ------------------------------------------------------------------

def run(part1, raw_catalogue, recently_featured, fetch_json=get_json, fetch_text=get_text):
    """Build Part 2. recently_featured: model IDs from the last EXCLUSION_RUNS briefs."""
    result = {
        "part": 2,
        "title": "Model of the day",
        "status": None,
        "model": None,
        "text": None,
        "notes": [],
    }
    if raw_catalogue is None or part1.get("status") not in ("ok", "baseline"):
        result["status"] = "skipped"
        result["text"] = NO_MODEL_TEXT
        result["notes"].append(
            "Skipped: tonight's catalogue was not usable (see Part 1), so no model was chosen.")
        return result

    models = catalogue_models(raw_catalogue)
    model_id, _basis = select(part1, models, recently_featured[:EXCLUSION_RUNS])
    if model_id is None:
        result["status"] = "none"
        result["text"] = NO_MODEL_TEXT
        result["notes"].append(
            f"No new model tonight and no discounted model outside the last "
            f"{EXCLUSION_RUNS} briefs' picks.")
        return result

    result["status"] = "ok"
    result["model"] = build_card(models[model_id], fetch_json, fetch_text)
    return result


def main():
    parser = argparse.ArgumentParser(description="Daily Hermes Brief, Part 2 (prints only)")
    parser.add_argument("--input", help="read catalogue from a file instead of the portal")
    args = parser.parse_args()
    try:
        if args.input:
            raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        else:
            from daily_hermes_brief_part1 import fetch_catalogue
            raw = fetch_catalogue()
    except (OSError, json.JSONDecodeError, FetchFailure) as e:
        print(f"Catalogue unavailable: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(run({"status": "baseline"}, raw, []), indent=2))


if __name__ == "__main__":
    main()
