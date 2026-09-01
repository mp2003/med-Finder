"""Tata 1mg adapter.

The public search PAGE is a client-rendered empty shell (no products in the HTML),
so we call the JSON API the site's own frontend calls.
"""
import asyncio
import logging

from adapters.base import (Location, ProductResult, make_client, strip_tags,
                           to_float, top_matches)
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# Gotchas, all confirmed live: param is q= (not name=); types=sku is REQUIRED
# (blank/all/drug -> HTTP 400); and WITHOUT the Authorization/X-City headers the
# same path silently returns the HTML shell instead of JSON.
SEARCH_URL = "https://www.1mg.com/pwa-dweb-api/api/v4/search/all"
LATLNG_URL = "https://www.1mg.com/pwa-dweb-api/location/latlng/{lat},{lon}"
# Public web-client token hardcoded in 1mg's own JS bundle (not a user secret).
AUTH = "Token token=3769e1fd4435b207522343256042a9d4490b147d51f2770553d5c019414f"
BASE = "https://www.1mg.com"
PLATFORM = "1mg"
# ---------------------------------------------------------------------------


def _headers(city: str) -> dict:
    """Auth + city headers. Without these the API returns the HTML shell."""
    return {"Authorization": AUTH, "X-City": city,
            "x-platform": "desktop-0.0.1", "locale": "en",
            "Accept": "application/json", "Referer": f"{BASE}/"}


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    try:
        params = {"q": query, "city": loc.city, "filter": "", "page_number": 1,
                  "scroll_id": "", "per_page": 10, "types": "sku", "sort": "",
                  "fetch_eta": "true", "is_city_serviceable": "true"}
        async with make_client(headers=_headers(loc.city)) as c:
            r = await c.get(SEARCH_URL, params=params)
            r.raise_for_status()
            data = r.json()

        out = []
        for it in (data.get("data") or {}).get("search_results") or []:
            prices = it.get("prices") or {}
            # discounted_price is often null even on in-stock items -> use mrp
            price = to_float(prices.get("discounted_price")) or to_float(prices.get("mrp"))
            url = it.get("url") or ""
            out.append(ProductResult(
                platform=PLATFORM,
                name=it.get("name") or "",
                price=price,
                mrp=to_float(prices.get("mrp")),
                available=bool(it.get("available")),
                eta=strip_tags(it.get("eta")),
                url=url if url.startswith("http") else BASE + url,
                match_score=score(query, it.get("name") or ""),
            ))
        return top_matches(query, out)
    except Exception as e:
        body = ""
        try:
            body = r.text[:500]  # helps re-point selectors later
        except Exception:
            pass
        log.warning("%s search failed for %r: %s %s", PLATFORM, query, e, body)
        return []


async def resolve_latlng(lat: float, lon: float) -> tuple[str, str] | None:
    """(city, pincode) for a shared Telegram location. None if it fails."""
    try:
        async with make_client(headers=_headers("Bangalore")) as c:
            r = await c.get(LATLNG_URL.format(lat=lat, lon=lon))
            r.raise_for_status()
            first = (r.json().get("result") or [None])[0]
        if not first or not first.get("city"):
            return None
        return first["city"], str(first.get("zipcode") or "")
    except Exception as e:
        log.warning("1mg latlng failed for %s,%s: %s", lat, lon, e)
        return None


async def demo():
    """Self-check against the live endpoint. Run: python -m adapters.<name>"""
    loc = Location("Koramangala", 12.9352, 77.6245, "560034", "Bangalore")
    res = await search("dolo 650", loc)
    for r in res:
        print(f"  {r.name[:40]:42} ₹{r.price} avail={r.available} eta={r.eta}")
    assert res, "no results (endpoint likely re-pointed)"
    assert any(r.price and r.url.startswith("http") for r in res), "no priced result with URL"
    print(f"  latlng -> {await resolve_latlng(12.9352, 77.6245)}")
    print("onemg OK")


if __name__ == "__main__":
    asyncio.run(demo())
