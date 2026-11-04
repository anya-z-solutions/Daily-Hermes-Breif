"""
Daily Hermes Brief: helpers shared by all three parts.

Standard library only. Every network call goes through get_json or get_text,
so every fetch has a timeout and fails the same way: FetchFailure with a
short reason that can go straight into the brief.
"""

import datetime as dt
import http.client
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

USER_AGENT = "daily-hermes-brief/2.0 (+https://github.com/anya-z-solutions/Daily-Hermes-Breif)"
DEFAULT_TIMEOUT = 30


class FetchFailure(Exception):
    """A source could not be read. The message is safe to show in the brief."""


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


BRIEF_TIMEZONE = "Europe/London"


def brief_date(moment):
    """The brief's date: the UK calendar date at run time, so a midnight UK run is dated that day."""
    try:
        from zoneinfo import ZoneInfo                   # Python 3.9+, needs tz data on the system
        return moment.astimezone(ZoneInfo(BRIEF_TIMEZONE)).strftime("%Y-%m-%d")
    except Exception:                                   # no tz data: fall back to UTC
        return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%d")


def iso(moment):
    return moment.isoformat(timespec="seconds")


def parse_iso(value):
    """Parse an ISO 8601 timestamp from any source. Returns aware UTC datetime or None."""
    if not value or not isinstance(value, str):
        return None
    text = value.strip().replace("Z", "+00:00")
    if "." in text:                                    # trim fractional seconds of any length
        head, _, tail = text.partition(".")
        digits = "".join(ch for ch in tail if ch.isdigit())
        zone = tail[len(digits):]
        text = head + zone
    try:
        moment = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.timezone.utc)
    return moment.astimezone(dt.timezone.utc)


def _request(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10, headers=None, with_headers=False):
    """Return the response body as text (and headers if asked). One retry on HTTP 429. Raises FetchFailure."""
    body = None
    all_headers = {"User-Agent": USER_AGENT}
    if headers:
        all_headers.update(headers)
    if isinstance(data, bytes):                        # raw body, caller sets Content-Type
        body = data
    elif data is not None:
        body = json.dumps(data).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=body, headers=all_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8")
                return (text, resp.headers) if with_headers else text
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt == 1:
                time.sleep(retry_wait)
                continue
            reason = "rate limited (HTTP 429)" if e.code == 429 else f"HTTP {e.code}"
            raise FetchFailure(reason) from None
        except urllib.error.URLError as e:
            raise FetchFailure(f"unreachable ({e.reason})") from None
        except TimeoutError:
            raise FetchFailure("request timed out") from None
        except (http.client.HTTPException, ConnectionError, OSError) as e:
            raise FetchFailure(f"connection failed ({type(e).__name__})") from None
        except UnicodeDecodeError:
            raise FetchFailure("response was not valid text") from None
    raise FetchFailure("rate limited (HTTP 429) after retry")


def get_text(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10, headers=None):
    return _request(url, data=data, timeout=timeout, retry_wait=retry_wait, headers=headers)


def get_json(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10):
    text = _request(url, data=data, timeout=timeout, retry_wait=retry_wait,
                    headers={"Accept": "application/json"})
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise FetchFailure("response was not valid JSON") from None


def cache_dir():
    """Where large, slow-changing downloads are kept between runs. Outside the archive, so the
    briefs branch stays append-only. Override with HERMES_BRIEF_CACHE."""
    return Path(os.environ.get("HERMES_BRIEF_CACHE") or Path.home() / ".cache" / "daily-hermes-brief")


def get_json_cached(url, timeout=DEFAULT_TIMEOUT, retry_wait=10):
    """get_json, but ask the server to send the file only if it changed since the cached copy.

    A missing or unreadable cache just means a full download. A machine that keeps its home
    directory between runs saves the download on nights the file hasn't changed.
    """
    folder = cache_dir()
    name = "".join(ch if ch.isalnum() else "_" for ch in url)[-120:]
    body_path, meta_path = folder / f"{name}.json", folder / f"{name}.meta.json"
    headers = {"Accept": "application/json"}
    cached = None
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        cached = json.loads(body_path.read_text(encoding="utf-8"))
        if meta.get("etag"):
            headers["If-None-Match"] = meta["etag"]
        if meta.get("last_modified"):
            headers["If-Modified-Since"] = meta["last_modified"]
    except (OSError, ValueError):
        cached = None
    try:
        text, resp_headers = _request(url, timeout=timeout, retry_wait=retry_wait,
                                      headers=headers if cached is not None else {"Accept": "application/json"},
                                      with_headers=True)
    except FetchFailure as e:
        if cached is not None and str(e) == "HTTP 304":
            return cached                              # unchanged since the cached copy
        raise
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        raise FetchFailure("response was not valid JSON") from None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        body_path.write_text(text, encoding="utf-8")
        meta_path.write_text(json.dumps({"etag": resp_headers.get("ETag"),
                                         "last_modified": resp_headers.get("Last-Modified")}),
                             encoding="utf-8")
    except OSError:
        pass                                           # caching is an optimisation only
    return data


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


# --- Formatting ------------------------------------------------------------

def per_million(price_per_token):
    """USD per token (float) to USD per million tokens, rounded for display."""
    if price_per_token is None:
        return None
    return round(price_per_token * 1e6, 6)


def usd(value):
    """Format a USD-per-million figure without trailing noise: 0.07, 1.5, 20."""
    if value is None:
        return "not listed"
    text = f"{value:.6f}".rstrip("0").rstrip(".")
    return f"${text}"
