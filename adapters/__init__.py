"""Registry of enabled adapters. Adding a platform = write the module, import it
here, add its name below."""
import importlib

# Verified working against live endpoints (2026-08-26).
#   Pharmacy: 1mg, Apollo, PharmEasy, Netmeds
#   FMCG:     DMart
#   Quick-commerce: Blinkit, Instamart -- both need curl_cffi. Blinkit is
#                   blocked on TLS fingerprint; Instamart's *website* sits
#                   behind an AWS WAF challenge but its API does not.
_LIVE = ("onemg", "apollo", "pharmeasy", "netmeds", "dmart", "blinkit",
         "instamart")

# Reachable-but-unbuilt (BigBasket/JioMart need a handshake or bundle-grep) and
# Zepto, whose site AND api host are both behind the AWS WAF challenge.
# Rendered as "coming soon" rather than a misleading "not found".
_SOON = ("bigbasket", "jiomart", "zepto")


def _load(names):
    """Import adapter modules by name."""
    # Imported lazily so `python -m adapters.onemg` self-checks don't trip
    # runpy's double-import warning.
    return [importlib.import_module(f"adapters.{n}") for n in names]


def __getattr__(name):
    """Resolve ADAPTERS / COMING_SOON lazily on first access."""
    if name == "ADAPTERS":
        return _load(_LIVE)
    if name == "COMING_SOON":
        return _load(_SOON)
    raise AttributeError(name)
