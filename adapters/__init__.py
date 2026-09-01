"""Registry of enabled adapters. Adding a platform = write the module, import it
here, add its name below."""
import importlib

# Verified working against live endpoints (2026-08-26).
#   Pharmacy: 1mg, Apollo, PharmEasy, Netmeds
#   FMCG:     DMart
_LIVE = ("onemg", "apollo", "pharmeasy", "netmeds", "dmart")

# Reachable-but-unbuilt (BigBasket/JioMart need a handshake or bundle-grep) and
# hard-blocked (Blinkit 403, Zepto/Instamart 202-empty). Rendered as
# "coming soon" rather than a misleading "not found".
_SOON = ("bigbasket", "jiomart", "blinkit")


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
