"""JioMart adapter -- stub. Client-rendered behind Akamai.

TESTED 2026-08-26: https://www.jiomart.com/search/<q> returns HTTP 200 but a
6.8MB Akamai-fronted shell with NO prices in the HTML (search is Algolia-driven
and client-side). No Algolia appId/searchKey is exposed in the page shell -- the
only "algolia" hit is a config schema, not live keys.
Next step: grep the JS bundles for the Algolia credentials or the JSON search
endpoint (the method that cracked 1mg, Apollo and Netmeds).
"""
from adapters.base import Location, ProductResult

PLATFORM = "JioMart"


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Not implemented -- see the module docstring for what it needs."""
    return []
