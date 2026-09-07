"""PharmEasy adapter.

Next.js site whose SEARCH page really is server-rendered (unlike 1mg/Apollo):
products sit in the __NEXT_DATA__ script tag. No auth needed.
"""
import asyncio
import json
import logging
import re

from adapters.base import (Location, ProductResult, make_client, to_float,
                           top_matches)
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# Prices are salePriceDecimal / mrpDecimal -- NOT salePrice/mrp (those are None).
# Stock is productAvailabilityFlags.isAvailable. Results at
# props.pageProps.productList. Availability is national: identical for pincodes
# 560034 and 110001, so we send no location and don't claim pincode accuracy.
SEARCH_URL = "https://pharmeasy.in/search/all"
PRODUCT_URL = "https://pharmeasy.in/online-medicine-order/{slug}"
NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)
PLATFORM = "PharmEasy"
# ---------------------------------------------------------------------------


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    try:
        async with make_client() as c:
            r = await c.get(SEARCH_URL, params={"name": query})
            r.raise_for_status()
            html = r.text

        m = NEXT_DATA_RE.search(html)
        if not m:
            log.warning("%s: __NEXT_DATA__ not found; body starts: %s",
                        PLATFORM, html[:500])
            return []
        products = (json.loads(m.group(1)).get("props", {})
                    .get("pageProps", {}).get("productList") or [])

        out = []
        for it in products:
            name = it.get("name") or ""
            flags = it.get("productAvailabilityFlags") or {}
            out.append(ProductResult(
                platform=PLATFORM,
                name=name,
                price=to_float(it.get("salePriceDecimal")),
                mrp=to_float(it.get("mrpDecimal")),
                available=bool(flags.get("isAvailable")),
                eta=it.get("edd") or None,
                url=PRODUCT_URL.format(slug=it.get("slug") or ""),
                match_score=score(query, name),
                image=it.get("image"),  # absolute cdn01.pharmeasy.in URL
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
        print(f"  {r.name[:40]:42} ₹{r.price} mrp={r.mrp} avail={r.available}")
    assert res, "no results (endpoint likely re-pointed)"
    assert any(r.price and r.url.startswith("http") for r in res), "no priced result with URL"
    print("pharmeasy OK")


if __name__ == "__main__":
    asyncio.run(demo())
