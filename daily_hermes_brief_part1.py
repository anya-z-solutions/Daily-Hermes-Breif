#!/usr/bin/env python3
"""
Daily Hermes Brief, Part 1: Nous Portal model catalogue changes.

Fetches the Nous Portal model catalogue, compares it with the last good
snapshot, and writes a dated JSON file describing what changed.

Standard library only. Runs on Linux, macOS and Windows.\nDesigned to run nightly (e.g. 01:00) on a local machine.

Usage:
    python3 daily_hermes_brief_part1.py                # normal nightly run
    python3 daily_hermes_brief_part1.py --dry-run      # print result, write nothing
    python3 daily_hermes_brief_part1.py --input x.json # use a saved catalogue (testing)
"""

import argparse
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# --- Configuration ---------------------------------------------------------

CATALOGUE_URL = "https://inference-api.nousresearch.com/v1/models"
BASE_DIR = Path(__file__).resolve().parent
SNAPSHOT_DIR = BASE_DIR / "snapshots"      # raw catalogue, one file per good run
OUTPUT_DIR = BASE_DIR / "digests"          # Part 1 output, one file per run
LOCK_FILE = BASE_DIR / ".part1.lock"

TIMEOUT_SECONDS = 60
RETRY_WAIT_SECONDS = 120                    # pause before the single retry on 429
MAX_COUNT_DROP = 0.20                       # >20% fewer models than last snapshot = bad fetch
PRICE_INCREASE_THRESHOLD = 0.20             # report increases above 20%


# --- Run lock (Linux, macOS and Windows) -----------------------------------

def acquire_lock(handle):
    """Stop two runs overlapping. Returns False if another run holds the lock."""
    try:
        import fcntl                                   # Linux, macOS
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
    except ImportError:
        import msvcrt                                  # Windows
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False


# --- Helpers ---------------------------------------------------------------

def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def base_key(model):
    """Collapse :batch / :free variants onto one model for 'new model' detection."""
    slug = model.get("canonical_slug") or model.get("id", "")
    return slug.split(":")[0]


def is_variant(model_id):
    return ":" in model_id


def to_float(value):
    """Pricing values arrive as strings. Return float, or None if unparseable."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def parse_model(raw):
    """Extract only the fields Part 1 needs. Raises ValueError if unusable."""
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


# --- Fetching --------------------------------------------------------------

class FetchFailure(Exception):
    pass


def fetch_catalogue():
    """Fetch the catalogue. One retry on HTTP 429. Raises FetchFailure."""
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(
                CATALOGUE_URL, headers={"User-Agent": "daily-hermes-brief/1.0"}
            )
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                time.sleep(RETRY_WAIT_SECONDS)
                continue
            reason = "rate limited (HTTP 429)" if e.code == 429 else f"HTTP {e.code}"
            raise FetchFailure(reason)
        except urllib.error.URLError as e:
            raise FetchFailure(f"portal unreachable ({e.reason})")
        except TimeoutError:
            raise FetchFailure("request timed out")
        except json.JSONDecodeError:
            raise FetchFailure("response was not valid JSON")
    raise FetchFailure("rate limited (HTTP 429) after retry")


# --- Snapshots -------------------------------------------------------------

def latest_snapshot(before_date):
    """Return (date_str, data) for the most recent good snapshot before today."""
    if not SNAPSHOT_DIR.exists():
        return None, None
    candidates = sorted(
        p for p in SNAPSHOT_DIR.glob("*.json") if p.stem < before_date
    )
    for path in reversed(candidates):          # skip corrupt files, fall back further
        try:
            return path.stem, json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
    return None, None


def index_models(raw_catalogue):
    """Parse a raw catalogue into {id: model}, plus a list of unparseable ids."""
    models, unparseable = {}, []
    for raw in raw_catalogue.get("data", []):
        try:
            m = parse_model(raw)
            models[m["id"]] = m
        except ValueError as e:
            unparseable.append({"id": raw.get("id", "<no id>"), "reason": str(e)})
    return models, unparseable


# --- Diffing ---------------------------------------------------------------

def pct_change(old, new):
    if old == 0:
        return None
    return (new - old) / old


def diff_catalogues(old, new):
    """Compare two {id: model} indexes. Returns a list of change records."""
    changes = []

    # New models: keyed on base model, so :batch / :free variants don't count.
    old_bases = {m["base_key"] for m in old.values()}
    reported_bases = set()
    for m in new.values():
        b = m["base_key"]
        if b not in old_bases and b not in reported_bases:
            reported_bases.add(b)
            changes.append({
                "type": "new_model", "tier": "act", "id": m["id"], "name": m["name"],
                "prompt_per_1m": round(m["prompt"] * 1e6, 6),
                "completion_per_1m": round(m["completion"] * 1e6, 6),
            })

    for model_id, m in new.items():
        prev = old.get(model_id)
        if prev is None:
            continue

        # Expiry: newly set, or changed.
        if m["expiration_date"] != prev["expiration_date"] and m["expiration_date"]:
            changes.append({
                "type": "expiry_set" if prev["expiration_date"] is None else "expiry_changed",
                "tier": "act", "id": model_id, "name": m["name"],
                "previous_expiration_date": prev["expiration_date"],
                "expiration_date": m["expiration_date"],
            })

        # Pricing changes: base models only. Variants mirror their base model.
        if is_variant(model_id):
            continue

        if m["discounted"] and not prev["discounted"]:
            changes.append({
                "type": "discount_started", "tier": "note", "id": model_id, "name": m["name"],
                "prompt_per_1m": round(m["prompt"] * 1e6, 6),
                "completion_per_1m": round(m["completion"] * 1e6, 6),
                "original_prompt_per_1m": round((m["original_prompt"] or 0) * 1e6, 6),
                "original_completion_per_1m": round((m["original_completion"] or 0) * 1e6, 6),
            })
        elif prev["discounted"] and not m["discounted"]:
            changes.append({
                "type": "discount_ended", "tier": "note", "id": model_id, "name": m["name"],
                "prompt_per_1m": round(m["prompt"] * 1e6, 6),
                "completion_per_1m": round(m["completion"] * 1e6, 6),
            })
        else:
            # Price increases above threshold (either side).
            for side in ("prompt", "completion"):
                change = pct_change(prev[side], m[side])
                if change is not None and change > PRICE_INCREASE_THRESHOLD:
                    changes.append({
                        "type": "price_increase", "tier": "note", "id": model_id,
                        "name": m["name"], "side": side,
                        "previous_per_1m": round(prev[side] * 1e6, 6),
                        "current_per_1m": round(m[side] * 1e6, 6),
                        "increase_pct": round(change * 100, 1),
                    })

    return changes


def expiry_rollcall(models):
    """Every model currently carrying an expiration date. Dates only."""
    return sorted(
        ({"id": m["id"], "expiration_date": m["expiration_date"]}
         for m in models.values() if m["expiration_date"]),
        key=lambda r: (r["expiration_date"], r["id"]),
    )


# --- Main ------------------------------------------------------------------

def run(input_path=None):
    checked_at = now_utc()
    today = checked_at.strftime("%Y-%m-%d")
    result = {
        "part": 1,
        "title": "Nous Portal model catalogue changes",
        "source": CATALOGUE_URL,
        "checked_at_utc": checked_at.isoformat(timespec="seconds"),
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
        if input_path:
            raw = json.loads(Path(input_path).read_text(encoding="utf-8"))
        else:
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

    # 2. Load the last good snapshot.
    prev_date, prev_raw = latest_snapshot(before_date=today)
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
    if unparseable:
        result["notes"].append(
            f"{len(unparseable)} model(s) could not be parsed; the catalogue format "
            f"may have changed."
        )
    if not result["changes"]:
        result["notes"].append("No changes since the last snapshot.")

    return result, raw, today


def main():
    parser = argparse.ArgumentParser(description="Daily Hermes Brief, Part 1")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the result; write no files")
    parser.add_argument("--input", help="read catalogue from a file instead of the portal")
    args = parser.parse_args()

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "w") as lock:
        if not acquire_lock(lock):
            print("Another run is in progress; exiting.", file=sys.stderr)
            sys.exit(1)

        result, raw_to_save, today = run(args.input)

        if args.dry_run:
            print(json.dumps(result, indent=2))
            return

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        (OUTPUT_DIR / f"{today}-part1.json").write_text(
            json.dumps(result, indent=2), encoding="utf-8"
        )
        # Only good fetches become the next comparison baseline.
        if raw_to_save is not None:
            SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
            (SNAPSHOT_DIR / f"{today}.json").write_text(
                json.dumps(raw_to_save), encoding="utf-8"
            )
        print(f"Part 1 written: status={result['status']}, "
              f"changes={len(result['changes'])}")


if __name__ == "__main__":
    main()
