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
    # Instamart's dark-store id. It cannot be derived from lat/lon without a
    # handshake we have not cracked, so presets carry it explicitly. None for a
    # shared live location, where the adapter falls back to its default store.
    im_store: str | None = None


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


# Sorts last: an unknown ETA must never outrank a known one.
ETA_UNKNOWN = 10 ** 6


def eta_minutes(eta: str | None) -> int:
    """Best-effort delivery time in minutes, for ranking only.

    Every platform words this differently -- '45 mins', 'Get by 7pm, Tomorrow',
    'Delivery by Tue, 2 Sep'. We only need a comparable number, so a same-day
    phrase collapses to a nominal few hours and anything dated to tomorrow or
    later sorts behind it. Returns ETA_UNKNOWN when nothing is parseable, which
    keeps unknown ETAs at the bottom instead of silently winning.
    """
    if not eta:
        return ETA_UNKNOWN
    s = eta.lower()
    m = re.search(r"(\d+)\s*(min|hour|hr|day)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"min": 1, "hour": 60, "hr": 60, "day": 1440}[unit]
        return n * mult
    # Quick-commerce delivery classes carry no number but are the fastest thing
    # here; rank them ahead of any dated delivery without claiming a minute
    # count. Blinkit's eta_identifier varies by store ("express" at one branch,
    # "unicorn" at another), so match its vocabulary, not just one value.
    if any(w in s for w in ("express", "instant", "earliest", "unicorn",
                            "superfast", "rocket", "flash")):
        return 15
    # No explicit number: fall back to the day words these strings all use.
    if "tomorrow" in s:
        return 1440
    if "today" in s or "tonight" in s:
        return 240
    return ETA_UNKNOWN


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
