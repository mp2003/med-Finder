# Architecture

> Status: current as of 2026-09-07 · 7 live platforms · order lists supported

## What this is

A Telegram bot that takes a product name -- or a whole order message -- and
reports which Indian platforms have it, with price, stock status, ETA and a
tap-to-open product link. Built for a pharmacy: searches run against the branch
the stock is being delivered **to**, not "near me". Single owner plus a few
people; long polling, no webhook, no SSL, no server required.

## Two request flows

A message naming **one** product takes the search flow. A message naming **two or
more** takes the order-list flow. `parsing.extract_items` decides which.

### Single product

```
Telegram user
  │  "dolo 650"
  ▼
bot.py :: ask_urgency          "Soonest delivery" or "Lowest price"
  ▼
bot.py :: do_search
  │  1. load saved Location from SQLite (chat_id -> name/lat/lon/pincode/city/im_store)
  │  2. cache lookup  key=(normalized_query, pincode)  TTL 15 min
  │  3. post ONE static loading notice
  ▼
asyncio fan-out over ADAPTERS ─┬── onemg.py      GET  JSON  (city-scoped)
  10s hard budget              ├── apollo.py     POST JSON  (pincode-scoped)
  each adapter 8s timeout      ├── pharmeasy.py  GET  HTML -> __NEXT_DATA__
  never raises -> []           ├── netmeds.py    GET  JSON  (no auth)
                               ├── dmart.py      GET  JSON  (FMCG)
                               ├── blinkit.py    POST JSON  (curl_cffi, lat/lon)
                               └── instamart.py  POST JSON  (curl_cffi, storeId)
  ▼
matching.py :: identity gate + rapidfuzz score, then typo_ok for near-misses
  ▼
bot.py :: render + results_keyboard
  ▼
ONE final message: photo, caption, supplier buttons
```

### Order list

```
"sir can u please order dolo 650, eno and colgate"
  ▼
parsing.extract_items          ->  ["dolo 650", "eno", "colgate"]
  ▼
confirm_list                   ->  "Found 3 items - is that right?"
  ▼
on_list_urgency                ->  one ranking question for the whole list
  ▼
_search_one per item, SEQUENTIALLY (platforms in parallel within each item)
  ▼
complete_baskets               ->  platforms carrying EVERY item
  │
  ├── basket exists  -> recommend the winner on the chosen axis, links, total
  └── none           -> walk item by item, record a pick each, then summarise
```

One order beats three, so a platform holding the whole list wins even when
another is cheaper on a single line. Items run sequentially because these APIs
throttle: N x 7 simultaneous calls gets rate-limited.

## Module map

| File | Responsibility |
|---|---|
| `bot.py` | Handlers, both flows, board rendering, basket scoring, cache |
| `parsing.py` | Pull item names out of a human order message |
| `config.py` | Presets, TTLs, timeouts, match threshold. The file an owner edits |
| `db.py` | aiosqlite: locations (`get/set_location`) and picks (`save/recent_pick`) |
| `matching.py` | Query normalization, identity gate, typo gate, rapidfuzz scoring |
| `adapters/base.py` | `Location`, `ProductResult`, client factory, shared parse helpers |
| `adapters/<platform>.py` | One module per platform, all implementing the same contract |
| `adapters/__init__.py` | Registry: `ADAPTERS` (live) and `COMING_SOON` (stubs) |

## The adapter contract

Every platform is one module exposing:

```python
PLATFORM: str
async def search(query: str, loc: Location) -> list[ProductResult]
```

Rules, enforced by convention and by `top_matches`:

1. **Never raise.** Any exception is caught, logged with the first 500 chars of
   the response body, and returns `[]`. One broken platform is summarised as
   "not stocked"; the rest of the board still works.
2. **Top 3 max**, sorted by match score, below-threshold results dropped.
3. **Never invent data.** No ETA in the response means `eta is None`. No
   resolvable image means `image is None`. We do not synthesise either.
4. **One constant block at the top** holding URL, headers and tokens, marked
   `# VERIFY in DevTools if this breaks`, so re-pointing is a one-place edit.

Adding a platform = write the module, add its name to `_LIVE` in
`adapters/__init__.py`. No other file changes.

## Data model

```python
@dataclass
class Location:
    name: str; lat: float; lon: float; pincode: str; city: str
    im_store: str | None = None   # Instamart dark-store id; see below

@dataclass
class ProductResult:
    platform: str; name: str
    price: float | None; mrp: float | None
    available: bool          # in stock per the platform
    eta: str | None          # pass-through, never invented
    url: str                 # canonical product page (acts as app deep link)
    match_score: float       # 0-100, rapidfuzz
    image: str | None = None # absolute URL; None where unresolvable (DMart)
    is_match: bool = True    # False => similar item, not the thing asked for
```

`Location` carries several location keys because platforms want different ones:
1mg keys on **city**, Apollo on **pincode**, Blinkit on **lat/lon** headers, and
Instamart on an **`im_store`** id that cannot be derived from coordinates -- it is
captured per branch and recorded in `config.PRESETS`. See
[03-platform-research.md](03-platform-research.md).

## Concurrency and failure

- Within one search, all adapters run concurrently.
- Per-adapter HTTP timeout: 8s (`ADAPTER_TIMEOUT`).
- Overall search budget: 10s (`SEARCH_BUDGET`). Whatever has arrived is rendered;
  stragglers are cancelled and reported as not found.
- **The board is rendered once, at the end.** It used to be re-rendered on every
  adapter that landed, which made rows reshuffle under the reader; a single
  static notice now stands until every platform has answered.

## Rendering

The final board is **one message**: a photo carrying the caption and the buttons.
Telegram allows a caption and `reply_markup` on `reply_photo`, so the image sits
above the text structurally rather than by send-order luck. Constraints that
follow: captions cap at **1024 characters** (text allows 4096), and a text
message cannot be edited into a photo one -- hence delete-and-resend at the end,
with a text-only fallback when no platform supplied an image.

## Cache

In-process dict, `key=(normalized_query, pincode)`, TTL 15 minutes. Deliberately
not Redis: single process, handful of users. `ponytail:` swap only if this ever
runs multi-process.

Picks are **not** cached -- they go to SQLite, because they are order history
worth querying later rather than transient state.

## Deliberate non-goals

Ordering/checkout automation, prescription handling, price history, alerts,
group features, monetization, OCR of forwarded images, quantity handling. The bot
reports availability and links out; the platform handles the rest.
