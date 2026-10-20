"""Shared test helpers: a fake network and small catalogue builders. No real fetches."""

import datetime as dt
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brief_common import FetchFailure  # noqa: E402

NOW = dt.datetime(2026, 10, 20, 1, 7, tzinfo=dt.timezone.utc)


class FakeFetch:
    """Answer requests by longest matching URL prefix. A value that is an exception is raised."""

    def __init__(self, routes=None, default=None):
        self.routes = dict(routes or {})
        self.default = default
        self.calls = []

    def __call__(self, url, data=None, timeout=None, retry_wait=None):
        self.calls.append(url)
        matches = [p for p in self.routes if url.startswith(p)]
        if matches:
            value = self.routes[max(matches, key=len)]
        elif self.default is not None:
            value = self.default
        else:
            raise FetchFailure("HTTP 404")
        if isinstance(value, Exception):
            raise value
        return value(url, data) if callable(value) else value


def model(model_id, prompt="0.000001", completion="0.000002", original=None,
          created=1_790_000_000, hf=None, expiry=None, slug=None, **extra):
    pricing = {"prompt": prompt, "completion": completion}
    if original:
        pricing["original"] = {"prompt": original[0], "completion": original[1]}
    entry = {
        "id": model_id,
        "canonical_slug": slug or model_id.split(":")[0],
        "name": f"Vendor: {model_id.split('/')[-1]}",
        "created": created,
        "hugging_face_id": hf,
        "description": f"{model_id} is a test model.",
        "context_length": 128000,
        "architecture": {"input_modalities": ["text"], "output_modalities": ["text"]},
        "pricing": pricing,
        "top_provider": {"max_completion_tokens": 8192},
        "knowledge_cutoff": None,
        "expiration_date": expiry,
        "reasoning": {"supported_efforts": ["high", "low"]},
    }
    entry.update(extra)
    return entry


def catalogue(*models):
    return {"data": list(models)}


def filler(n, prefix="filler/m"):
    """n ordinary undiscounted models, so count-drop checks have something to compare."""
    return [model(f"{prefix}{i}") for i in range(n)]
