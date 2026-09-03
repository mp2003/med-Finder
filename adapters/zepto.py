"""Zepto adapter -- stub. Blocked by short-lived AWS WAF tokens.

TESTED 2026-09-01, in two rounds.

Round 1 (website): zepto.com / zeptonow.com return HTTP 202 carrying an AWS WAF
challenge page (window.gokuProps + awswaf challenge.js via CloudFront).
curl_cffi's Chrome TLS fingerprint does NOT clear it -- unlike Blinkit, where
the fingerprint was the entire block.

Round 2 (real API, from a browser capture): the app talks to a separate host,
  POST https://bff-gateway.zepto.com/user-search-service/api/v3/search
       {"query": ..., "pageNumber": 0, "mode": "TYPED", "intentId": ...}
  GET  https://bff-gateway.zepto.com/lms/api/v2/get_page?latitude=&longitude=
       (the store resolver -- takes raw lat/lon, which is what we would need)
with store_id/store_ids headers naming dark stores as UUIDs, plus
request-signature, x-csrf-secret and x-xsrf-token headers.

That host answers 202-with-an-EMPTY-body rather than a challenge page, for
every combination tried: minimal headers, +aws-waf-token cookie, +signature and
CSRF headers, and the full captured header set. Replaying the captured curl
VERBATIM (plain curl, every header and cookie) also returned 202/0 bytes, so
the credentials had already expired by the time they were tested -- this is not
a header we are missing.

Why re-capturing will not help: the two captured requests carried DIFFERENT
aws-waf-tokens within one browser session, and the token's middle segment
decodes to a 12-byte structure holding a timestamp. The token rotates and is
short-lived, so it cannot be lifted from a curl and reused by the bot.

Route in: spec Phase 3 (real browser with geolocation) -- it can solve the
challenge, keep tokens fresh and hand over the store UUIDs. loc already carries
lat/lon, so no plumbing changes are needed here.
"""
from adapters.base import Location, ProductResult

PLATFORM = "Zepto"


async def search(query: str, loc: Location) -> list[ProductResult]:
    """Not implemented -- see the module docstring for what it needs."""
    return []
