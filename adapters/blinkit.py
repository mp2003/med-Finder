"""Blinkit adapter -- quick-commerce, genuinely per-dark-store.

The block here was NEVER headers or cookies: an identical curl with the full
browser cookie jar still gets 403. Cloudflare rejects on TLS handshake
fingerprint, before a single header is read. curl_cffi replays Chrome's real
fingerprint and the same request returns 200 -- with NO cookies at all, so
there is no session to warm or expire.

Unlike our other adapters this one is truly location-scoped: lat/lon headers
select a dark store (merchant_id), so `available` means "deliverable to this
location", not the "in stock nationally" caveat that applies elsewhere.
"""
import asyncio
import logging

from curl_cffi import requests as cffi

from adapters.base import Location, ProductResult, to_float, top_matches
from config import ADAPTER_TIMEOUT
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# POST (GET -> 405). Body is literally "{}"; the query lives in the URL.
# auth_key is a public web-client constant from blinkit.com's own bundle.
# Products are at response.snippets[].data, mixed in with layout snippets that
# have no product_id -- skip those rather than assuming a fixed index.
SEARCH_URL = "https://blinkit.com/v1/layout/search"
PRODUCT_URL = "https://blinkit.com/prn/x/prid/{prid}"
AUTH_KEY = "c761ec3633c22afad934fb17a66385c1c06c5472b4898b866b7306186d0bb477"
PLATFORM = "Blinkit"
# ponytail: one impersonation target. If Cloudflare tightens, curl_cffi ships
# other profiles (chrome131, safari17_0) -- all three returned 200 in testing.
IMPERSONATE = "chrome"
# ---------------------------------------------------------------------------


def _text(v):
    """Blinkit wraps display strings as {"text": ..., "font": {...}}."""
    return v.get("text") if isinstance(v, dict) else v


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    r = None
    try:
        headers = {
            "accept": "*/*",
            "app_client": "consumer_web",
            "app_version": "1010101010",
            "auth_key": AUTH_KEY,
            "content-type": "application/json",
            "origin": "https://blinkit.com",
            "referer": "https://blinkit.com/s/",
            "web_app_version": "1008010016",
            # The whole point: these pick the dark store.
            "lat": str(loc.lat),
            "lon": str(loc.lon),
        }
        async with cffi.AsyncSession() as s:
            r = await s.post(SEARCH_URL, headers=headers,
                             params={"q": query, "search_type": "type_to_search"},
                             data="{}", impersonate=IMPERSONATE,
                             timeout=ADAPTER_TIMEOUT)
        r.raise_for_status()
        data = r.json()

        out = []
        for snip in (data.get("response") or {}).get("snippets") or []:
            c = snip.get("data") or {}
            prid = c.get("product_id")
            if not prid:
                continue  # layout/banner snippet, not a product
            name = _text(c.get("name")) or ""
            unit = _text(c.get("variant")) or ""
            out.append(ProductResult(
                platform=PLATFORM,
                name=f"{name} ({unit})" if unit else name,
                # mrp is frequently null on discounted items; price is the
                # reliable one, so never fall back the other way round.
                price=to_float(_text(c.get("normal_price"))),
                mrp=to_float(_text(c.get("mrp"))),
                available=not c.get("is_sold_out") and bool(c.get("inventory")),
                # No minutes anywhere in this payload: eta_tag is only
                # {"text": "earliest"} and v1/actions/get_updated_eta returns a
                # merchant map that excludes these stores. eta_identifier is
                # the platform's own delivery class, so pass that through
                # verbatim rather than inventing "10 mins".
                eta=_text(c.get("eta_identifier")) or None,
                url=PRODUCT_URL.format(prid=prid),
                match_score=score(query, name),
            ))
        return top_matches(query, out)
    except Exception as e:
        body = r.text[:500] if r is not None else ""
        log.warning("%s search failed for %r: %s %s", PLATFORM, query, e, body)
        return []


async def demo():
    """Self-check against the live endpoint. Run: python -m adapters.blinkit"""
    loc = Location("Koramangala", 12.9352, 77.6245, "560034", "Bangalore")
    # Both must be BRAND names. A molecule ("paracetamol") returns HTTP 200
    # with real products, but the identity gate correctly rejects them all --
    # Blinkit stocks Dolo and Saridon, nothing titled "paracetamol".
    for q in ("dolo 650", "crocin"):
        res = await search(q, loc)
        for r in res:
            print(f"  [{q}] {r.name[:38]:40} ₹{r.price} avail={r.available}")
        assert res, f"no results for {q!r} (403 => TLS profile likely stale)"
        assert any(r.price and r.url.startswith("http") for r in res), \
            f"no priced result with URL for {q!r}"
    print("blinkit OK")


if __name__ == "__main__":
    asyncio.run(demo())
