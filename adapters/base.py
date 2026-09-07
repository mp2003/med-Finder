"""Shared dataclasses + HTTP client factory for all platform adapters."""
import asyncio
import logging
import re
from dataclasses import dataclass

import httpx

from config import ADAPTER_TIMEOUT, MAX_RESULTS, MIN_MATCH_SCORE
from matching import identity_ok, normalize

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


@dataclass
class Location:
    name: str
    lat: float
    lon: float
    pincode: str
    city: str
    # Instamart's dark-store id. It cannot be derived from lat/lon without a
    # handshake we have not cracked, so presets carry it explicitly. None for a
    # shared live location, where the adapter falls back to its default store.
    im_store: str | None = None


@dataclass
class ProductResult:
    platform: str
    name: str
    price: float | None
    mrp: float | None
    available: bool
    eta: str | None
    url: str
    match_score: float
    # Product photo, absolute URL. Optional: DMart ships an imageKey whose CDN
    # form we could not resolve, so its results carry None and simply render
    # without a picture.
    image: str | None = None
    # False => shares no brand/molecule token with the query, so it is a
    # SIMILAR item, never "the product you asked for".
    is_match: bool = True


def make_client(**kw) -> httpx.AsyncClient:
    """AsyncClient preloaded with a desktop UA and en-IN, per the 8s timeout."""
    headers = {"User-Agent": UA, "Accept-Language": "en-IN,en;q=0.9"}
    headers.update(kw.pop("headers", {}))
    return httpx.AsyncClient(timeout=ADAPTER_TIMEOUT, headers=headers,
                             follow_redirects=True, **kw)


def why(e: Exception) -> str:
    """Readable cause for an adapter failure.

    httpx's timeout and connection errors carry an EMPTY message, so the
    obvious `log.warning(..., e)` prints "search failed for 'x': " and tells
    you nothing -- in particular it cannot be told apart from a re-pointed
    endpoint, which needs a completely different fix. Always name the class.
    """
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def strip_tags(s: str | None) -> str | None:
    """1mg's eta ships as HTML ('Get by <b><span ...>7pm, Tomorrow</span></b>').
    Raw, it corrupts Telegram's parse_mode=HTML message."""
    if not s:
        return None
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip() or None


def to_float(v) -> float | None:
    """Prices arrive as '₹32.1' or 32.0 or None."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v).replace(",", ""))
    return float(m.group()) if m else None


# Sorts last: an unknown ETA must never outrank a known one.
ETA_UNKNOWN = 10 ** 6

# Delivery speed per platform, because it CANNOT be read off the eta field.
# Only 1mg, Apollo, Blinkit and PharmEasy publish an eta at all; Instamart,
# DMart and Netmeds always send None. Instamart is 30-minute quick-commerce
# yet eta_minutes() scores it ETA_UNKNOWN -- the slowest value there is -- so
# anything derived from eta alone calls Blinkit+Instamart a mixed-speed order,
# which is exactly backwards. Keyed on PLATFORM.
#
# 1mg, Apollo and PharmEasy are deliberately in neither set: they publish a
# real eta, so they are classified from data instead of guessed here.
# ponytail: two sets beat an enum for 7 platforms. Widen only if a third class
# (same-day slots) ever needs its own copy.
QUICK = {"Blinkit", "Instamart"}      # ~30 min
QUICK_MINUTES = 20   # what a QUICK platform is worth when it publishes no eta
SLOW = {"Netmeds", "DMart"}           # days, or slot-based with no eta


def eta_minutes(eta: str | None, platform: str | None = None) -> int:
    """Best-effort delivery time in minutes, for ranking only.

    Pass `platform` wherever it is known. Blinkit tags some catalogue lines
    `pharma_rx`/`longtail` -- classifications, not speeds -- and Instamart
    sends no eta at all, so string-only parsing scores a 15-minute order
    ETA_UNKNOWN and sinks it behind a next-day courier. QUICK is the floor for
    those platforms, not a guess about the individual line.

    Every platform words this differently -- '45 mins', 'Get by 7pm, Tomorrow',
    'Delivery by Tue, 2 Sep'. We only need a comparable number, so a same-day
    phrase collapses to a nominal few hours and anything dated to tomorrow or
    later sorts behind it. Returns ETA_UNKNOWN when nothing is parseable, which
    keeps unknown ETAs at the bottom instead of silently winning.
    """
    if not eta:
        return QUICK_MINUTES if platform in QUICK else ETA_UNKNOWN
    s = eta.lower()
    m = re.search(r"(\d+)\s*(min|hour|hr|day)", s)
    if m:
        n, unit = int(m.group(1)), m.group(2)
        mult = {"min": 1, "hour": 60, "hr": 60, "day": 1440}[unit]
        return n * mult
    # Quick-commerce delivery classes carry no number but are the fastest thing
    # here; rank them ahead of any dated delivery without claiming a minute
    # count. Blinkit's eta_identifier varies by store ("express" at one branch,
    # "unicorn" at another), so match its vocabulary, not just one value.
    if any(w in s for w in ("express", "instant", "earliest", "unicorn",
                            "superfast", "rocket", "flash")):
        return 15
    # No explicit number: fall back to the day words these strings all use.
    if "tomorrow" in s:
        return 1440
    if "today" in s or "tonight" in s:
        return 240
    return QUICK_MINUTES if platform in QUICK else ETA_UNKNOWN


def top_matches(query: str, results: list[ProductResult]) -> list[ProductResult]:
    """Real matches first (identity gate + score cutoff), then similar items as
    a labelled fallback.

    The identity gate is what stops a "Cristello" query rendering a Glutafine
    product as a confident hit -- token_set_ratio alone scored that 70.
    """
    for r in results:
        r.is_match = identity_ok(query, r.name)

    real = [r for r in results if r.is_match and r.match_score >= MIN_MATCH_SCORE]
    real.sort(key=lambda r: (r.available, r.match_score), reverse=True)
    if real:
        return real[:MAX_RESULTS]

    # Nothing genuinely matched -> offer the closest in-stock alternatives,
    # clearly flagged so the caller can label them.
    similar = [r for r in results if r.match_score >= MIN_MATCH_SCORE]
    similar.sort(key=lambda r: (r.available, r.match_score), reverse=True)
    return similar[:MAX_RESULTS]


# Brand names split and join freely: users type "Eco Sprin", the catalogue
# lists "Ecosprin", and vice versa. Only 1mg and DMart tolerate the mismatch;
# Apollo, PharmEasy, Netmeds and Blinkit all return ZERO results for the
# spelling they do not hold, which reads as "not stocked" and pushes the order
# onto an extra platform.
#
# Neither spelling wins everywhere -- "Dolo 650" beats "dolo650" on 1mg (3
# results vs 1) while "ecosprin" beats "Eco Sprin" on Blinkit (3 vs 0) -- so
# this cannot be a one-way normalisation. Try the query as typed, then the
# variants, and stop at the first that finds anything.
def variants(query: str) -> list[str]:
    """Spellings to try, best first. Always starts with the query as typed."""
    out = [query]

    def add(s):
        if s and s.lower() != query.lower() and s not in out:
            out.append(s)

    # "Eco Sprin 75" -> "EcoSprin 75": join the letter-only run that starts the
    # name, but leave the strength alone -- "EcoSprin75" matches nothing.
    add(re.sub(r"\b([a-zA-Z]+)\s+([a-zA-Z]+)\b", r"\1\2", query, count=1))
    # The reverse: a digit run glued to the brand. dolo650 -> dolo 650.
    add(re.sub(r"([a-zA-Z])(\d)", r"\1 \2", query))
    return out


# Minimum brand length worth probing, and the cap past which the split count
# stops being cheap. "b12"/"ab" are real queries but too short to split
# meaningfully; nothing sensible is 25 characters of unbroken brand.
_PROBE_MIN, _PROBE_MAX = 5, 24


def splits(query: str) -> list[str]:
    """Space-insertion candidates for a glued brand name.

    Empty -- meaning "do not probe" -- when the query already carries a space
    (the user told us where the boundary is), or is too short/long to be a
    glued brand. Length is linear in the query, not combinatorial: 9 characters
    give 6 candidates, the 24-character cap gives 21.
    """
    q = query.strip()
    if " " in q or not (_PROBE_MIN <= len(q) <= _PROBE_MAX):
        return []
    return [q[:i] + " " + q[i:] for i in range(2, len(q) - 1)]


async def probe_name(mod, query: str, loc) -> str | None:
    """Ask ONE platform every split at once; return the canonical product name.

    Runs only when every platform found nothing, so the cost is paid on a
    search that was going to fail anyway. Roughly 0.5s measured against 1mg.

    The signal is the returned TITLE, not the hit count: 1mg's search ignores
    spaces, so "augmen tin" and "ec osprin" return results too and counting
    hits would accept nonsense splits. Every split that finds the product
    reports the same canonical name, so the correct split never has to be
    identified -- we just need one title that identity_ok accepts.
    """
    cands = splits(query)
    if not cands:
        return None
    got = await asyncio.gather(*(mod.search(c, loc) for c in cands),
                               return_exceptions=True)
    for res in got:
        if isinstance(res, BaseException) or not res:
            continue
        for r in res:
            if identity_ok(query, r.name):
                return _brand(query, r.name)
    return None


def _brand(query: str, title: str) -> str:
    """The leading words of a title that cover the query -- "Lido-Plast
    Lidocaine 350mg/12h Transdermal Patch | Arthritis..." -> "lido plast".

    Re-searching the FULL title over-constrains the other platforms' search
    engines: measured 6 results against 9 for the trimmed brand.
    """
    glued = normalize(query).replace(" ", "")
    out = []
    for w in normalize(title).split():
        out.append(w)
        if len("".join(out)) >= len(glued):
            break
    return " ".join(out)


async def search_wide(mod, query: str, loc) -> list[ProductResult]:
    """One adapter, retried on a respelt query when the first attempt is empty.

    Results are scored against the ORIGINAL query, never the variant that
    found them: identity_ok("ecosprin", "Eco Sprin 150 Tablet") is False, so
    re-gating on the variant would throw away the very products the retry
    exists to find. top_matches inside the adapter already gated on the
    variant, hence the re-run here.

    Only fires when a platform returned nothing, so a hit on the first
    spelling costs no extra request -- which is what keeps this inside
    SEARCH_BUDGET.
    """
    tried = variants(query)
    first = await mod.search(tried[0], loc)
    if first:
        return first
    for alt in tried[1:]:
        out = await mod.search(alt, loc)
        if out:
            return top_matches(query, out)
    return []

def demo():
    """Self-check for the query variants. Run: python -m adapters.base"""
    assert variants("Eco Sprin") == ["Eco Sprin", "EcoSprin"]
    assert variants("Eco Sprin 75") == ["Eco Sprin 75", "EcoSprin 75"], \
        "joined the strength into the brand"
    assert variants("dolo650") == ["dolo650", "dolo 650"]
    # No variant means no second request: the common case must stay free.
    assert variants("ecosprin") == ["ecosprin"]
    assert variants("Shelcal 500") == ["Shelcal 500"], "split a strength"
    assert variants("Eno") == ["Eno"]

    # splits(): probe only what is worth probing.
    assert len(splits("lidoplast")) == 6
    assert splits("dolo 650") == [], "probed a query that already has a space"
    assert splits("b12") == [] and splits("ab") == [], "probed too-short query"
    assert splits("a" * 30) == [], "probed an implausibly long query"
    assert "lido plast" in splits("lidoplast")
    # Linear, not combinatorial -- the reason this is affordable at all.
    assert len(splits("a" * 24)) == 21

    # A QUICK platform with no published eta must not sort as unknown.
    assert eta_minutes(None, "Instamart") < ETA_UNKNOWN
    assert eta_minutes(None, "Netmeds") == ETA_UNKNOWN
    assert why(Exception()) == "Exception", "empty message lost the class name"
    print("base OK")


if __name__ == "__main__":
    demo()
