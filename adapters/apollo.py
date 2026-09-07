"""Apollo Pharmacy adapter.

Search page is a client-rendered shell (Next.js App Router, no __NEXT_DATA__ and
no products in the RSC payload), so we call the JSON search service directly.
"""
import asyncio
import logging

from adapters.base import (Location, ProductResult, make_client, to_float,
                           top_matches)
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# Must be POST -- GET returns HTTP 405. pincode only affects deliveryTime.
SEARCH_URL = "https://search.apollo247.com/v4/fullSearch"
# Public web-client token from Apollo's own JS bundle (not a user secret).
AUTH = "Oeu324WMvfKOj5KMJh2Lkf00eW1"
SOURCE = "PHARMA_AP_IN"
# /otc/<urlKey> is canonical; /medicine/<urlKey> 308-redirects to it.
PRODUCT_URL = "https://www.apollopharmacy.in/otc/{url_key}"
# thumbnail is a relative catalog path ("/catalog/product/D/O/DOL0026_1_1.jpg").
IMAGE_BASE = "https://newassets.apollo247.com/pub/media"
IN_STOCK = "in-stock"  # the bundle's own SEARCH_ITEM_INSTOCK_STATUS constant
PLATFORM = "Apollo"
# ---------------------------------------------------------------------------


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    try:
        headers = {"Authorization": AUTH, "x-source-service": SOURCE,
                   "Content-Type": "application/json",
                   "Accept": "application/json",
                   "Origin": "https://www.apollopharmacy.in",
                   "Referer": "https://www.apollopharmacy.in/"}
        payload = {"query": query, "page": 1, "productsPerPage": 10,
                   "pincode": loc.pincode}
        async with make_client(headers=headers) as c:
            r = await c.post(SEARCH_URL, json=payload)
            r.raise_for_status()
            data = r.json()

        products = ((data.get("data") or {}).get("productDetails") or {}).get("products") or []
        out = []
        for it in products:
            name = it.get("name") or ""
            thumb = it.get("thumbnail") or ""
            out.append(ProductResult(
                platform=PLATFORM,
                name=name,
                price=to_float(it.get("specialPrice")) or to_float(it.get("price")),
                mrp=to_float(it.get("price")),
                available=(it.get("status") == IN_STOCK),
                eta=it.get("deliveryTime") or None,
                url=PRODUCT_URL.format(url_key=it.get("urlKey") or ""),
                match_score=score(query, name),
                image=f"{IMAGE_BASE}{thumb}" if thumb.startswith("/") else None,
            ))
        return top_matches(query, out)
    except Exception as e:
        body = ""
        try:
            body = r.text[:500]
        except Exception:
            pass
        log.warning("%s search failed for %r: %s %s", PLATFORM, query, e, body)
        return []


async def demo():
    """Self-check against the live endpoint. Run: python -m adapters.<name>"""
    loc = Location("Koramangala", 12.9352, 77.6245, "560034", "Bangalore")
    res = await search("dolo 650", loc)
    for r in res:
        print(f"  {r.name[:40]:42} ₹{r.price} avail={r.available} eta={r.eta}")
    assert res, "no results (endpoint likely re-pointed)"
    assert any(r.price and r.url.startswith("http") for r in res), "no priced result with URL"
    print("apollo OK")


if __name__ == "__main__":
    asyncio.run(demo())
