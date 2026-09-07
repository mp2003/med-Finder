"""Netmeds adapter (Reliance-owned pharmacy).

Runs on the Fynd platform. Search is a same-origin extension route that needs
NO auth at all -- a bare GET with zero headers returns priced JSON.
"""
import asyncio
import logging

from adapters.base import (Location, ProductResult, make_client, to_float,
                           top_matches)
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# GET only (POST -> 404). No Authorization/api key/cookie required.
# filters=false drops a ~100KB facet block we never read.
# NOTE the stock Fynd paths (/service/application/catalog/v1.0/products/) return
# the 3.2MB HTML shell -- Netmeds proxies search through /ext/search/ instead.
SEARCH_URL = "https://www.netmeds.com/ext/search/application/api/v1.0/products"
PRODUCT_URL = "https://www.netmeds.com/product/{slug}"
PLATFORM = "Netmeds"
# ---------------------------------------------------------------------------


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    try:
        params = {"q": query, "page_size": 20, "filters": "false"}
        async with make_client(headers={"Accept": "application/json",
                                        "Referer": "https://www.netmeds.com/"}) as c:
            r = await c.get(SEARCH_URL, params=params)
            r.raise_for_status()
            data = r.json()

        out = []
        for it in data.get("items") or []:
            price = it.get("price") or {}
            name = it.get("name") or ""
            # medias[] mixes images and video; take the first image entry.
            img = next((m.get("url") for m in (it.get("medias") or [])
                        if isinstance(m, dict) and m.get("type") == "image"), None)
            out.append(ProductResult(
                platform=PLATFORM,
                name=name,
                price=to_float((price.get("effective") or {}).get("min")),
                mrp=to_float((price.get("marked") or {}).get("min")),
                available=bool(it.get("sellable")),
                eta=None,  # not in the search payload; don't invent one
                url=PRODUCT_URL.format(slug=it.get("slug") or ""),
                match_score=score(query, name),
                image=img,
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
    print("netmeds OK")


if __name__ == "__main__":
    asyncio.run(demo())
