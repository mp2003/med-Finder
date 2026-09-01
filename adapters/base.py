"""Shared dataclasses + HTTP client factory for all platform adapters."""
import logging
import re
from dataclasses import dataclass

import httpx

from config import ADAPTER_TIMEOUT, MAX_RESULTS, MIN_MATCH_SCORE
from matching import identity_ok

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@dataclass
class Location:
    name: str
    lat: float
    lon: float
    pincode: str
    city: str


@dataclass
class ProductResult:
    platform: str
    name: str
    price: float | None
    mrp: float | None
    available: bool
    eta: str | None
    url: str
    match_score: float
    # False => shares no brand/molecule token with the query, so it is a
    # SIMILAR item, never "the product you asked for".
    is_match: bool = True


def make_client(**kw) -> httpx.AsyncClient:
    """AsyncClient preloaded with a desktop UA and en-IN, per the 8s timeout."""
    headers = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}
    headers.update(kw.pop("headers", {}))
    return httpx.AsyncClient(timeout=ADAPTER_TIMEOUT, headers=headers,
                             follow_redirects=True, **kw)


def strip_tags(s: str | None) -> str | None:
    """1mg's eta ships as HTML ('Get by <b><span ...>7pm, Tomorrow</span></b>').
    Raw, it corrupts Telegram's parse_mode=HTML message."""
    if not s:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip() or None


def to_float(v) -> float | None:
    """Prices arrive as '₹32.1' or 32.0 or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


def top_matches(query: str, results: list[ProductResult]) -> list[ProductResult]:
    """Real matches first (identity gate + score cutoff), then similar items as
    a labelled fallback.

    The identity gate is what stops a "Cristello" query rendering a Glutafine
    product as a confident hit -- token_set_ratio alone scored that 70.
    """
    for r in results:
        r.is_match = identity_ok(query, r.name)

    real = [r for r in results if r.is_match and r.match_score >= MIN_MATCH_SCORE]
    real.sort(key=lambda r: (r.available, r.match_score), reverse=True)
    if real:
        return real[:MAX_RESULTS]

    # Nothing genuinely matched -> offer the closest in-stock alternatives,
    # clearly flagged so the caller can label them.
    similar = [r for r in results if r.match_score >= MIN_MATCH_SCORE]
    similar.sort(key=lambda r: (r.available, r.match_score), reverse=True)
    return similar[:MAX_RESULTS]
