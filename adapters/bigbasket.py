"""BigBasket adapter -- stub. Needs a location handshake first.

TESTED 2026-08-26: the listing API is LIVE and returns JSON, but rejects
anonymous calls:
  GET https://www.bigbasket.com/listing-svc/v2/products?type=ps&slug=<q>&page=1
  -> 400 {"msg":"Missing either Mid or AddressId or lat-long"}
Guessed lat-long cookies -> 404 error 5012. So the real work is replicating the
address/store resolution call the site makes BEFORE search, then passing that
Mid/AddressId through. Capture it in DevTools with a delivery address set.
loc already carries lat/lon, so no plumbing changes are needed here.
"""
from adapters.base import Location, ProductResult

PLATFORM = "BigBasket"


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Not implemented -- see the module docstring for what it needs."""
    return []
