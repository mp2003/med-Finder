"""Owner-editable constants. Presets carry city AND pincode: 1mg keys on city,
Apollo keys on pincode."""

# name, lat, lon, pincode, city  ("Bangalore" is 1mg's spelling, not "Bengaluru")
PRESETS = [
    {"name": "Koramangala", "lat": 12.9352, "lon": 77.6245, "pincode": "560034", "city": "Bangalore"},
    {"name": "Indiranagar", "lat": 12.9719, "lon": 77.6412, "pincode": "560038", "city": "Bangalore"},
    {"name": "HSR Layout",  "lat": 12.9116, "lon": 77.6446, "pincode": "560102", "city": "Bangalore"},
    {"name": "Whitefield",  "lat": 12.9698, "lon": 77.7500, "pincode": "560066", "city": "Bangalore"},
    {"name": "Jayanagar",   "lat": 12.9308, "lon": 77.5838, "pincode": "560041", "city": "Bangalore"},
]

DB_PATH = "bot.db"
CACHE_TTL = 15 * 60      # seconds
ADAPTER_TIMEOUT = 8.0    # per-platform HTTP timeout
SEARCH_BUDGET = 10.0     # overall fan-out cap; render whatever arrived
MIN_MATCH_SCORE = 55     # rapidfuzz cutoff
MAX_RESULTS = 3          # per platform
