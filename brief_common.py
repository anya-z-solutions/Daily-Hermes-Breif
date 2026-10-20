"""
Daily Hermes Brief: helpers shared by all three parts.

Standard library only. Every network call goes through get_json or get_text,
so every fetch has a timeout and fails the same way: FetchFailure with a
short reason that can go straight into the brief.
"""

import datetime as dt
import http.client
import json
import time
import urllib.error
import urllib.request

USER_AGENT = "daily-hermes-brief/2.0 (+https://github.com/anya-z-solutions/Daily-Hermes-Breif)"
DEFAULT_TIMEOUT = 30


class FetchFailure(Exception):
    """A source could not be read. The message is safe to show in the brief."""


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


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


def _request(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10, headers=None):
    """Return the response body as text. One retry on HTTP 429. Raises FetchFailure."""
    body = None
    all_headers = {"User-Agent": USER_AGENT}
    if headers:
        all_headers.update(headers)
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        all_headers["Content-Type"] = "application/json"
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, data=body, headers=all_headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
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


def get_text(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10):
    return _request(url, data=data, timeout=timeout, retry_wait=retry_wait)


def get_json(url, data=None, timeout=DEFAULT_TIMEOUT, retry_wait=10):
    text = _request(url, data=data, timeout=timeout, retry_wait=retry_wait,
                    headers={"Accept": "application/json"})
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise FetchFailure("response was not valid JSON") from None


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
