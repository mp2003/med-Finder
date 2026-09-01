# Architecture

> Status: current as of 2026-08-31 · Phase 1 complete + 4 platforms beyond spec

## What this is

A Telegram bot that takes a product name and reports which Indian platforms have
it, with price, stock status, ETA and a tap-to-open product link. Single owner
plus a few friends; long polling, no webhook, no SSL, no server required.

## Request flow

```
Telegram user
  │  "dolo 650"
  ▼
bot.py :: do_search
  │  1. load saved Location from SQLite (chat_id -> name/lat/lon/pincode/city)
  │  2. cache lookup  key=(normalized_query, pincode)  TTL 15 min
  │  3. post placeholder message
  ▼
asyncio.gather over ADAPTERS ──┬── onemg.py     GET  JSON  (city-scoped)
  10s hard budget              ├── apollo.py    POST JSON
  each adapter 8s timeout      ├── pharmeasy.py GET  HTML -> __NEXT_DATA__
  never raises -> []           ├── netmeds.py   GET  JSON  (no auth)
                               └── dmart.py     GET  JSON  (FMCG)
  │  each returns list[ProductResult]
  ▼
matching.py :: identity gate + rapidfuzz score
  │  drop wrong-brand results, rank, cap at 3
  ▼
bot.py :: render  -> edit the same message as each platform lands
  ▼
Final board (HTML parse_mode)
```

## Module map

| File | Responsibility |
|---|---|
| `bot.py` | Handlers, orchestrator, streaming edits, board rendering, cache |
| `config.py` | Presets, TTLs, timeouts, match threshold. The file an owner edits |
| `db.py` | aiosqlite helpers: `init`, `get_location`, `set_location` |
| `matching.py` | Query normalization, identity gate, rapidfuzz scoring |
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
   the response body, and returns `[]`. One broken platform renders `⚠️` and the
   rest of the board still works.
2. **Top 3 max**, sorted by match score, below-threshold results dropped.
3. **Never invent data.** If the platform response has no ETA, `eta` is `None` --
   we do not synthesise one.
4. **One constant block at the top** holding URL, headers and tokens, marked
   `# VERIFY in DevTools if this breaks`, so re-pointing is a one-place edit.

Adding a platform = write the module, add its name to `_LIVE` in
`adapters/__init__.py`. No other file changes.

## Data model

```python
@dataclass
class Location:
    name: str; lat: float; lon: float; pincode: str; city: str

@dataclass
class ProductResult:
    platform: str; name: str
    price: float | None; mrp: float | None
    available: bool          # in stock per the platform
    eta: str | None          # pass-through, never invented
    url: str                 # canonical product page (acts as app deep link)
    match_score: float       # 0-100, rapidfuzz
    is_match: bool = True    # False => similar item, not the thing asked for
```

`Location` carries **both** `city` and `pincode` because platforms key on
different things: 1mg on city, Apollo on pincode. See
[03-platform-research.md](03-platform-research.md).

## Concurrency and failure

- All adapters run concurrently via `asyncio.gather`.
- Per-adapter HTTP timeout: 8s (`ADAPTER_TIMEOUT`).
- Overall search budget: 10s (`SEARCH_BUDGET`). Whatever has arrived is rendered;
  stragglers are cancelled and shown as `⚠️`.
- The message is edited as each adapter completes, so results appear
  progressively rather than in one late burst.

## Cache

In-process dict, `key=(normalized_query, pincode)`, TTL 15 minutes, footer marks
cached boards. Deliberately not Redis: single process, handful of users.
`ponytail:` swap only if this ever runs multi-process.

## Deliberate non-goals

Ordering/checkout automation, prescription handling, price history, alerts,
group features, monetization. The bot links out; the platform handles the rest.
