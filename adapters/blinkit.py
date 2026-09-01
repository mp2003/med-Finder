"""Blinkit adapter -- Phase 3 stub. Not implemented; returns []."""
from adapters.base import Location, ProductResult

PLATFORM = "Blinkit"

# TODO(Phase 3): location-gated quick-commerce. Client-side rendered shell;
# product data comes from internal JSON APIs requiring lat/lon headers plus
# session cookies, and inventory differs per dark store.
#   1. In DevTools on blinkit.com with a location set, capture the search XHR
#      (historically /v*/search products) and replicate its lat/lon headers.
#   2. If blocked or unstable, fall back to Playwright with a geolocation-enabled
#      browser context per preset location, kept warm and reused.
# Note: loc already carries lat/lon, so no extra plumbing is needed here.


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Not implemented -- see the module docstring for what it needs."""
    return []

# TESTED 2026-08-26: https://blinkit.com/s/?q=<q> -> HTTP 403. Zepto and Swiggy
# Instamart both return HTTP 202 with an EMPTY body (bot detection stalls the
# request). All three confirm the Phase 3 assessment: they need warm per-location
# sessions, not a plain HTTP call.
