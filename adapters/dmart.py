"""DMart Ready adapter -- FMCG / groceries (not medicines).

Single unauthenticated GET returns names + prices. National pricing: identical
across pincodes 560034 / 400001 / 110001, so no store session is needed.
"""
import asyncio
import logging
from urllib.parse import quote

from adapters.base import (Location, ProductResult, make_client, to_float,
                           top_matches)
from matching import score

log = logging.getLogger(__name__)

# --- VERIFY in DevTools if this breaks -------------------------------------
# Prices live one level down in sKUs[0]: priceSALE / priceMRP (strings).
# v1 of this path returns "suggestionView" instead of "products"; keep v2.
SEARCH_URL = "https://digital.dmart.in/api/v2/search/{query}"
PRODUCT_URL = "https://www.dmart.in/pdp/{pid}"
PLATFORM = "DMart"
# ---------------------------------------------------------------------------


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Query the platform and return up to 3 ranked matches. Never raises."""
    try:
        async with make_client(headers={"Accept": "application/json",
                                        "Referer": "https://www.dmart.in/"}) as c:
            # Query goes in the PATH, so it must be percent-encoded: a raw
            # space yields a 200 with a large unrelated payload, not an error.
            r = await c.get(SEARCH_URL.format(query=quote(query, safe="")))
            r.raise_for_status()
            data = r.json()

        out = []
        for p in data.get("products") or []:
            skus = p.get("sKUs") or []
            if not skus:
                continue
            s = skus[0]
            name = p.get("name") or ""
            size = s.get("variantTextValue") or ""
            out.append(ProductResult(
                platform=PLATFORM,
                name=f"{name} ({size})" if size else name,
                price=to_float(s.get("priceSALE")),
                mrp=to_float(s.get("priceMRP")),
                available=str(s.get("buyable")).lower() == "true",
                eta=None,  # slot-based delivery; nothing reliable in this payload
                url=PRODUCT_URL.format(pid=p.get("productId") or ""),
                match_score=score(query, name),
            ))
        return top_matches(query, out)
    except Exception as e:
        body = ""
        try:
            body = r.text[:500]
        except Exception:
            pass
        log.warning("%s search failed for %r: %s %s",
                    PLATFORM, query, why(e), body)
        return []


async def demo():
    """Self-check against the live endpoint. Run: python -m adapters.<name>"""
    loc = Location("Koramangala", 12.9352, 77.6245, "560034", "Bangalore")
    # Two queries: a multi-word one guards the path-encoding bug (a raw space
    # returns 200 with an unrelated payload), and catalogue reshuffles upstream
    # shouldn't fail the check on a single unlucky term.
    for q in ("colgate toothpaste", "maggi noodles"):
        res = await search(q, loc)
        for r in res:
            print(f"  [{q}] {r.name[:38]:40} ₹{r.price} avail={r.available}")
        assert res, f"no results for {q!r} (endpoint likely re-pointed)"
        assert any(r.price and r.url.startswith("http") for r in res), \
            f"no priced result with URL for {q!r}"
    print("dmart OK")


if __name__ == "__main__":
    asyncio.run(demo())
