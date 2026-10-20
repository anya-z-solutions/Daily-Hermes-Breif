#!/usr/bin/env python3
"""
Daily Hermes Brief, Part 1: Nous Portal model catalogue changes.

Fetches the Nous Portal model catalogue, compares it with the last good
snapshot, and returns a dict describing what changed. The nightly entry point
(daily_hermes_brief.py) writes it into the brief and saves the snapshot.

Standard library only. Runs on Linux, macOS and Windows.

Usage (prints Part 1 on its own, writes nothing):
    python3 daily_hermes_brief_part1.py
    python3 daily_hermes_brief_part1.py --input x.json   # use a saved catalogue
    python3 daily_hermes_brief_part1.py --archive DIR    # compare against DIR/state/snapshots
"""

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

from brief_common import FetchFailure, get_json, iso, now_utc, per_million

# --- Configuration ---------------------------------------------------------

CATALOGUE_URL = "https://inference-api.nousresearch.com/v1/models"
TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 120                    # pause before the single retry on 429
MAX_COUNT_DROP = 0.20                       # >20% fewer models than last snapshot = bad fetch
PRICE_INCREASE_THRESHOLD = 0.20             # report increases above 20%


# --- Helpers ---------------------------------------------------------------

def base_key(model):
    """Collapse :batch / :free variants onto one model for 'new model' detection."""
    slug = model.get("canonical_slug") or model.get("id", "")
    return slug.split(":")[0]


def is_variant(model_id):
    return ":" in model_id


def is_alias(model_id):
    """'~vendor/x-latest' entries are floating pointers to another model, not models."""
    return model_id.startswith("~")


def to_float(value):
    """Pricing values arrive as strings. Return float, or None if unparseable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_model(raw):
    """Extract only the fields Part 1 needs. Raises ValueError if unusable."""
    if not isinstance(raw, dict):
        raise ValueError("entry is not an object")
    model_id = raw.get("id")
    pricing = raw.get("pricing")
    if not model_id or not isinstance(pricing, dict):
        raise ValueError("missing id or pricing")

    prompt = to_float(pricing.get("prompt"))
    completion = to_float(pricing.get("completion"))
    if prompt is None or completion is None:
        raise ValueError("unparseable prompt/completion price")

    original = pricing.get("original")
    orig_prompt = orig_completion = None
    if isinstance(original, dict):
        orig_prompt = to_float(original.get("prompt"))
        orig_completion = to_float(original.get("completion"))

    return {
        "id": model_id,
        "name": raw.get("name"),
        "base_key": base_key(raw),
        "prompt": prompt,
        "completion": completion,
        "discounted": isinstance(original, dict),
        "original_prompt": orig_prompt,
        "original_completion": orig_completion,
        "expiration_date": raw.get("expiration_date") or None,
    }


def compact_catalogue(raw):
    """The subset of the catalogue Part 1 needs to diff tomorrow. About a fifth of the size."""
    models = raw.get("data", raw) if isinstance(raw, dict) else raw
    keep = []
    for m in models if isinstance(models, list) else []:
        if not isinstance(m, dict):
            continue
        pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
        slim_pricing = {k: pricing.get(k) for k in ("prompt", "completion") if k in pricing}
        if isinstance(pricing.get("original"), dict):
            slim_pricing["original"] = {
                k: pricing["original"].get(k) for k in ("prompt", "completion")
            }
        keep.append({
            "id": m.get("id"),
            "name": m.get("name"),
            "canonical_slug": m.get("canonical_slug"),
            "pricing": slim_pricing,
            "expiration_date": m.get("expiration_date"),
        })
    return {"data": keep}


# --- Fetching --------------------------------------------------------------

def fetch_catalogue():
    """Fetch the catalogue. One retry on HTTP 429. Raises FetchFailure."""
    try:
        return get_json(CATALOGUE_URL, timeout=TIMEOUT_SECONDS, retry_wait=RETRY_WAIT_SECONDS)
    except FetchFailure as e:
        raise FetchFailure(f"portal {e}") from None


# --- Snapshots -------------------------------------------------------------

def latest_snapshot(snapshot_dir, before_date):
    """Return (date_str, data) for the most recent good snapshot before today."""
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.exists():
        return None, None
    candidates = sorted(
        p for p in snapshot_dir.glob("*.json") if p.stem < before_date
    )
    for path in reversed(candidates):          # skip corrupt files, fall back further
        try:
            return path.stem, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


def index_models(raw_catalogue):
    """Return ({id: parsed_model}, [unparseable ids or reasons])."""
    models = raw_catalogue.get("data", raw_catalogue) if isinstance(raw_catalogue, dict) else raw_catalogue
    index, unparseable = {}, []
    if not isinstance(models, list):
        return index, ["catalogue is not a list of models"]
    for raw in models:
        try:
            m = parse_model(raw)
            index[m["id"]] = m
        except ValueError as e:
            label = raw.get("id", "<no id>") if isinstance(raw, dict) else "<not an object>"
            unparseable.append(f"{label}: {e}")
    return index, unparseable


def pct_change(old, new):
    if not old:
        return None
    return (new - old) / old


def representative(models):
    """Pick the entry that stands for a base model: the plain ID, not a variant or alias."""
    return sorted(models, key=lambda m: (is_alias(m["id"]), is_variant(m["id"]), m["id"]))[0]


def diff_catalogues(old, new):
    """Compare two {id: model} indexes. Returns a list of change records."""
    changes = []

    # New models: keyed on base model, so :batch / :free variants and ~aliases don't count.
    old_bases = {m["base_key"] for m in old.values()}
    groups = {}
    for m in new.values():
        if is_alias(m["id"]):
            continue
        groups.setdefault(m["base_key"], []).append(m)
    for b in sorted(groups):
        if b in old_bases:
            continue
        m = representative(groups[b])
        changes.append({
            "type": "new_model", "tier": "act", "id": m["id"], "name": m["name"],
            "prompt_per_1m": per_million(m["prompt"]),
            "completion_per_1m": per_million(m["completion"]),
            "original_prompt_per_1m": per_million(m["original_prompt"]),
            "original_completion_per_1m": per_million(m["original_completion"]),
        })

    expiry_reported = set()
    for model_id in sorted(new, key=lambda i: (is_variant(i), i)):   # base models first
        m = new[model_id]
        prev = old.get(model_id)
        if prev is None:
            continue

        # Expiry: newly set, or changed. A variant repeating its base model's change is skipped.
        if m["expiration_date"] != prev["expiration_date"] and m["expiration_date"]:
            key = (m["base_key"], prev["expiration_date"], m["expiration_date"])
            if not (is_variant(model_id) and key in expiry_reported):
                expiry_reported.add(key)
                changes.append({
                    "type": "expiry_set" if prev["expiration_date"] is None else "expiry_changed",
                    "tier": "act", "id": model_id, "name": m["name"],
                    "previous_expiration_date": prev["expiration_date"],
                    "expiration_date": m["expiration_date"],
                })

        # Pricing changes: base models only. Variants and aliases mirror their base model.
        if is_variant(model_id) or is_alias(model_id):
            continue

        if m["discounted"] and not prev["discounted"]:
            changes.append({
                "type": "discount_started", "tier": "note", "id": model_id, "name": m["name"],
                "prompt_per_1m": per_million(m["prompt"]),
                "completion_per_1m": per_million(m["completion"]),
                "original_prompt_per_1m": per_million(m["original_prompt"]),
                "original_completion_per_1m": per_million(m["original_completion"]),
            })
        elif prev["discounted"] and not m["discounted"]:
            changes.append({
                "type": "discount_ended", "tier": "note", "id": model_id, "name": m["name"],
                "prompt_per_1m": per_million(m["prompt"]),
                "completion_per_1m": per_million(m["completion"]),
            })
        else:
            # Price increases above threshold (either side).
            for side in ("prompt", "completion"):
                change = pct_change(prev[side], m[side])
                if change is not None and change > PRICE_INCREASE_THRESHOLD:
                    changes.append({
                        "type": "price_increase", "tier": "note", "id": model_id,
                        "name": m["name"], "side": side,
                        "previous_per_1m": per_million(prev[side]),
                        "current_per_1m": per_million(m[side]),
                        "increase_pct": round(change * 100, 1),
                    })

    return changes


def expiry_rollcall(models):
    """Every model with a non-null expiry date. ID and date only, no interpretation."""
    return sorted(
        ({"id": m["id"], "expiration_date": m["expiration_date"]}
         for m in models.values() if m["expiration_date"]),
        key=lambda r: (r["expiration_date"], r["id"]),
    )


# --- Main ------------------------------------------------------------------

def run(snapshot_dir, raw=None, checked_at=None):
    """
    Build Part 1. Returns (result, raw_to_save, today).

    raw_to_save is the catalogue to keep as tomorrow's baseline, or None when
    tonight's fetch must not become one. Pass raw to skip the network (tests).
    """
    checked_at = checked_at or now_utc()
    today = checked_at.strftime("%Y-%m-%d")
    result = {
        "part": 1,
        "title": "Nous Portal model catalogue changes",
        "source": CATALOGUE_URL,
        "checked_at_utc": iso(checked_at),
        "status": None,
        "compared_against": None,
        "model_count": None,
        "changes": [],
        "expiry_rollcall": [],
        "unparseable_models": [],
        "notes": [],
    }

    # 1. Fetch.
    try:
        if raw is None:
            raw = fetch_catalogue()
    except FetchFailure as e:
        result["status"] = "fetch_failed"
        result["notes"].append(
            f"Nous Portal catalogue could not be retrieved at "
            f"{result['checked_at_utc']}: {e}. No comparison made tonight."
        )
        return result, None, today

    new_models, unparseable = index_models(raw)
    result["unparseable_models"] = unparseable
    result["model_count"] = len(new_models)

    if not new_models:
        result["status"] = "fetch_failed"
        result["notes"].append(
            f"Nous Portal returned an empty or unreadable model list at "
            f"{result['checked_at_utc']}. No comparison made tonight."
        )
        return result, None, today

    if unparseable:
        result["notes"].append(
            f"{len(unparseable)} model(s) could not be parsed; the catalogue format "
            f"may have changed."
        )

    # 2. Load the last good snapshot.
    prev_date, prev_raw = latest_snapshot(snapshot_dir, before_date=today)
    result["expiry_rollcall"] = expiry_rollcall(new_models)

    if prev_raw is None:
        result["status"] = "baseline"
        result["notes"].append(
            "No previous snapshot available. This run establishes the baseline; "
            "changes will be reported from the next run."
        )
        return result, raw, today

    old_models, _ = index_models(prev_raw)

    # 3. Sanity check: a sharp drop in model count suggests a partial response.
    if old_models and len(new_models) < len(old_models) * (1 - MAX_COUNT_DROP):
        result["status"] = "suspect_response"
        result["notes"].append(
            f"Catalogue returned {len(new_models)} models against {len(old_models)} "
            f"on {prev_date}, a drop of more than {int(MAX_COUNT_DROP * 100)}%. "
            f"Treated as a partial response: no comparison made, snapshot not saved."
        )
        return result, None, today

    # 4. Diff.
    result["status"] = "ok"
    result["compared_against"] = prev_date
    result["changes"] = diff_catalogues(old_models, new_models)

    gap_days = (dt.date.fromisoformat(today) - dt.date.fromisoformat(prev_date)).days
    if gap_days > 1:
        result["notes"].append(
            f"Compared against {prev_date}, {gap_days} days ago. Changes may span "
            f"more than one day."
        )
    if not result["changes"]:
        result["notes"].append("No changes since the last snapshot.")

    return result, raw, today


def main():
    parser = argparse.ArgumentParser(description="Daily Hermes Brief, Part 1 (prints only)")
    parser.add_argument("--input", help="read catalogue from a file instead of the portal")
    parser.add_argument("--archive", default="archive",
                        help="archive folder holding state/snapshots (default: ./archive)")
    args = parser.parse_args()

    raw = None
    if args.input:
        try:
            raw = json.loads(Path(args.input).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"Could not read {args.input}: {e}", file=sys.stderr)
            sys.exit(1)
    result, _, _ = run(Path(args.archive) / "state" / "snapshots", raw=raw)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
