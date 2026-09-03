"""Swiggy Instamart adapter -- quick-commerce, per-dark-store.

The old "202-empty" verdict was only half the story. instamart.in's *website*
sits behind an AWS WAF challenge (challenge.js + gokuProps), which no header
trick defeats. Its *API* does not: with curl_cffi's Chrome TLS fingerprint the
search endpoint answers 200 with full JSON and NO cookies at all -- no
aws-waf-token, no session JWT, no deviceId. All were tested and none are
required.

What IS real is aggressive, non-deterministic rate limiting: a 200 carrying
{"statusCode": 429} and a ~31 byte body. Treated as "no results" rather than an
error, since the bot's cache absorbs it.

Store caveat: storeId picks the dark store, and resolving one from lat/lon
needs a separate handshake we have not cracked, so DEFAULT_STORE (Bengaluru)
is used for every location. Prices and stock are therefore that store's, not
the user's -- so this platform's availability is NOT location-accurate yet.
"""
import asyncio
import json
import logging

from curl_cffi import requests as cffi

from adapters.base import Location, ProductResult, top_matches
from config import ADAPTER_TIMEOUT
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# POST; query goes in the JSON body, store in the query string.
# Products: data.cards[].card.card.gridElements.infoWithStyle.items[]
# ponytail: one hardcoded Bengaluru store. Resolve per-location store ids when
# the handshake is cracked -- loc is already threaded through for that day.
SEARCH_URL = "https://instamart.in/api/instamart/search/v2"
PRODUCT_URL = "https://instamart.in/item/{pid}"
DEFAULT_STORE = "1404884"
PLATFORM = "Instamart"
# ---------------------------------------------------------------------------


def _money(m) -> float | None:
    """Prices are protobuf money: {"units": "60", "nanos": 0}."""
    if not isinstance(m, dict):
        return None
    units = m.get("units")
    return None if units is None else float(units) + (m.get("nanos") or 0) / 1e9


def _items(data: dict):
    """Walk the Gandalf widget tree down to product items."""
    for card in (data.get("cards") or []):
        grid = ((card.get("card") or {}).get("card") or {}).get("gridElements") or {}
        yield from ((grid.get("infoWithStyle") or {}).get("items") or [])


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    r = None
    try:
        headers = {
            "accept": "*/*", "content-type": "application/json",
            "origin": "https://instamart.in",
            "referer": "https://instamart.in/search",
            "x-build-version": "2.370.0",
            "user-agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/152.0.0.0 Safari/537.36"),
        }
        body = json.dumps({
            "facets": [], "sortAttribute": "", "query": query,
            "search_results_offset": "0",
            "page_type": "INSTAMART_AUTO_SUGGEST_PAGE",
            "is_pre_search_tag": False,
        })
        params = {"offset": 0, "ageConsent": "false", "layoutId": 4987,
                  "storeId": DEFAULT_STORE, "primaryStoreId": DEFAULT_STORE,
                  "secondaryStoreId": ""}
        async with cffi.AsyncSession() as s:
            r = await s.post(SEARCH_URL, headers=headers, params=params,
                             data=body, impersonate="chrome",
                             timeout=ADAPTER_TIMEOUT)
        r.raise_for_status()
        payload = r.json()
        # Throttling arrives as HTTP 200 with statusCode 429 and no cards.
        data = payload.get("data") or {}
        if payload.get("statusCode") == 429 or not data.get("cards"):
            log.info("%s throttled or empty for %r", PLATFORM, query)
            return []

        out = []
        for it in _items(data):
            name = it.get("displayName") or ""
            if not name:
                continue
            price = (it.get("variations") or [{}])[0].get("price") or {}
            out.append(ProductResult(
                platform=PLATFORM,
                name=name,
                price=_money(price.get("offerPrice")),
                mrp=_money(price.get("mrp")),
                available=bool(it.get("inStock")),
                # No delivery estimate in the search payload; the contract
                # forbids inventing one.
                eta=None,
                url=PRODUCT_URL.format(pid=it.get("productId") or ""),
                match_score=score(query, name),
            ))
        return top_matches(query, out)
    except Exception as e:
        body_txt = r.text[:500] if r is not None else ""
        log.warning("%s search failed for %r: %s %s", PLATFORM, query, e, body_txt)
        return []


async def demo():
    """Self-check against the live endpoint. Run: python -m adapters.instamart"""
    loc = Location("Koramangala", 12.9352, 77.6245, "560034", "Bangalore")
    # FMCG only: Instamart carries OTC/wellness, not prescription tablets, so
    # a "dolo 650" check would fail on catalogue rather than on plumbing.
    for q in ("maggi noodles", "colgate toothpaste"):
        res = await search(q, loc)
        for r in res:
            print(f"  [{q}] {r.name[:38]:40} ₹{r.price} avail={r.available}")
        assert res, f"no results for {q!r} (429 throttle? retry in ~30s)"
        assert any(r.price and r.url.startswith("http") for r in res), \
            f"no priced result with URL for {q!r}"
        await asyncio.sleep(3)  # this API throttles hard on bursts
    print("instamart OK")


if __name__ == "__main__":
    asyncio.run(demo())
