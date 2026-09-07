"""Owner-editable constants. Presets carry city AND pincode: 1mg keys on city,
Apollo keys on pincode."""
import os

from dotenv import load_dotenv

# Loaded HERE, not in bot.py: config is imported at module scope, long before
# bot.main() runs, so a DB_PATH set in .env would otherwise be read too late
# and silently ignored. Real hosts set env vars directly and have no .env,
# which load_dotenv handles by doing nothing.
load_dotenv()

# Delivery destinations -- the pharmacy branches and office we order TO, not
# generic neighbourhoods. Every pincode below was confirmed against 1mg's own
# latlng resolver (it returned the same one the postal address carries), and
# Blinkit was verified serving all three.
#
# im_store is Instamart's dark-store id, captured per branch from its own web
# app (set the address, then read storeId= out of any request). It cannot be
# resolved from lat/lon, so it is recorded here. Re-capture if a branch starts
# returning stock that does not match the app -- Instamart reassigns stores.
#
# name, lat, lon, pincode, city, im_store
#   ("Bangalore" is 1mg's spelling, not "Bengaluru")
PRESETS = [
    {"name": "UrMedz Gateway", "lat": 13.011895167526944, "lon": 77.55668878203804,
     "pincode": "560055", "city": "Bangalore",       # Brigade Gateway, Malleshwaram
     "im_store": "1396467"},
    {"name": "UrMedz Metropolis", "lat": 12.989707130253173, "lon": 77.7026712127195,
     "pincode": "560048", "city": "Bangalore",       # Brigade Metropolis, Mahadevapura
     "im_store": "1404967"},
    {"name": "1Pharmacy Office", "lat": 13.013148753446263, "lon": 77.54302298203808,
     "pincode": "560096", "city": "Bangalore",       # Nandini Layout
     "im_store": "1404884"},
]

# Relative by default so a local run just works. On a hosted container the
# filesystem is ephemeral -- point this at a mounted volume or every redeploy
# wipes the saved branches, Instamart store ids and pick history.
#   DB_PATH=/data/bot.db
DB_PATH = os.getenv("DB_PATH", "bot.db")
CACHE_TTL = 15 * 60      # seconds
ADAPTER_TIMEOUT = 8.0    # per-platform HTTP timeout
SEARCH_BUDGET = 10.0     # overall fan-out cap; render whatever arrived
MIN_MATCH_SCORE = 55     # rapidfuzz cutoff
MAX_RESULTS = 3          # per platform
