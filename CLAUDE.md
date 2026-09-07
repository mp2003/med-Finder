# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the repo root. The venv is `.venv/` — always invoke via
`.venv/bin/python`, not bare `python3` (system Python lacks the deps).

```bash
# Setup
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Run (long polling; needs BOT_TOKEN in .env)
.venv/bin/python bot.py

# Health-check ONE adapter against its live endpoint — this is the test suite
.venv/bin/python -m adapters.onemg      # also exercises the latlng resolver
.venv/bin/python -m adapters.apollo
.venv/bin/python -m adapters.pharmeasy
.venv/bin/python -m adapters.netmeds
.venv/bin/python -m adapters.dmart
.venv/bin/python -m adapters.blinkit    # curl_cffi; TLS-impersonated
.venv/bin/python -m adapters.instamart  # curl_cffi; throttles hard
.venv/bin/python -m parsing             # order-message extraction

# Check all seven (keep stderr; a redirected-output loop can misreport status)
for a in onemg apollo pharmeasy netmeds dmart blinkit instamart; do
  printf "%-10s " $a
  .venv/bin/python -m adapters.$a 2>&1 | tail -1
done
```

There is no pytest, no linter, no build step. Each adapter's `demo()` is its
test: it hits the live endpoint and asserts at least one priced result with a
working URL. Run the relevant adapter's self-check after touching it.

**These hit live third-party APIs.** Running them back-to-back gets you
throttled, and a throttled adapter fails with the same
`AssertionError: no results (endpoint likely re-pointed)` as a genuinely broken
one — so a red check is not evidence an endpoint changed. Pause ~20s and retry,
or confirm with a direct `curl`, before concluding anything. Space the checks out
rather than looping them.

## Architecture

A Telegram bot (python-telegram-bot, long polling) that fans a product query out
to several Indian pharmacy/FMCG platforms concurrently and renders one comparison
board once every platform has answered.

```
bot.py::ask_urgency                 1 item -> search; 2+ -> order-list flow
  → bot.py::do_search
      → db.get_location(chat_id)    SQLite, survives restarts
      → cache check                 key=(normalized_query, pincode), TTL 15min
      → fan out over ADAPTERS       10s total budget, 8s per adapter
      → identity gate + typo gate   drop wrong brands, flag near-misses
      → ONE final message           photo + caption + supplier buttons
```

**Order lists.** `parsing.extract_items` turns a human message ("sir can u please
order dolo, eno and colgate") into items. Two or more takes the list flow:
confirm → one ranking question → search each item → `plans()` groups the order
into the **fewest platforms**, because each extra platform is another delivery
fee. `[Item by item]` still offers the per-item walk with a recorded pick each.

`plans()` enumerates all 2^7 platform subsets (~33us) rather than using greedy
set-cover, so the grouping is the exact optimum -- greedy takes the platform
covering the most items and can force a third order where two sufficed. The
objective is `item prices + DELIVERY_COST per order`, which is why no separate
"consolidation tolerance" knob exists: `DELIVERY_COST` already decides, in
rupees, whether paying more on one line to collapse an order is worth it.
`best_plan()` then prefers fewer orders unless splitting saves at least
`MEANINGFUL_SAVING`.

**The adapter contract** is the core abstraction. Each `adapters/<platform>.py`
exposes `PLATFORM: str` and `async def search(query, loc) -> list[ProductResult]`,
and must:

- **Never raise.** Catch everything, log with the first 500 chars of the response
  body, return `[]`. One broken platform is summarised as "not stocked"; the
  board still works.
- End by returning `top_matches(query, out)` — this applies the identity gate,
  ranking and the 3-result cap. Do not hand-roll filtering.
- Set `eta=None` when the platform gives no delivery estimate. **Never synthesize
  prices or ETAs.**
- Keep URL/headers/tokens in one constant block at the top marked
  `# VERIFY in DevTools if this breaks`.

Adding a platform: write the module, add its name to `_LIVE` in
`adapters/__init__.py`. Nothing else changes. `_SOON` holds stubs, which are
excluded from the fan-out and listed under "Not yet supported" — a stub returning
`[]` would otherwise be reported as genuinely not stocked.

`adapters/__init__.py` builds the registry lazily via module `__getattr__` so
`python -m adapters.<name>` does not trip runpy's double-import warning.

## Persistence

`db.py` holds two tables in `bot.db`:

- **`users`** — one saved location per chat. `init()` runs `ALTER TABLE` in a
  `try/except` because `CREATE TABLE IF NOT EXISTS` will not add a column to an
  existing DB; that is how `im_store` reached older files. Add future columns the
  same way.
- **`picks`** — which supplier was chosen for an item (`save_pick` /
  `recent_pick`). Telegram **never reports a tap on a `url=` button**, so a
  choice can only be known by asking — this is where the answer lives. Order
  history, so rows accumulate; not a cache.

## Four things that will bite you

**1. A 200 response is not success.** 1mg and Apollo search pages, and several
guessed API paths, return the full HTML *page shell* via a catch-all rewrite
instead of JSON. Always check content-type and that the body starts with `{`.
Missing auth headers on 1mg produce this silently. Every endpoint in use was
found by grepping the sites' own JS bundles and confirmed with curl —
`docs/03-platform-research.md` has each URL, required header, field map and trap
(including field names that look right but are always null, e.g. PharmEasy's
`salePrice` vs the real `salePriceDecimal`).

**2. Fuzzy scoring alone shows wrong brands as confident matches.** `rapidfuzz`
`token_set_ratio` rewards generic words, so a "Cristello face wash" query scored
a *Glutafine* product at 70 and presented it as a confident hit. `matching.py`
therefore layers an **identity gate** over the score: `key_tokens()` strips ~60 generic retail
words (`tablet`, `face`, `wash`, `mg`, `brightening`…), and `identity_ok()`
requires **every** remaining token to appear in the title. `normalize()` also
splits punctuation and letter/digit runs, because Apollo writes `Dolo-650` (one
token, scored 42.9 — below the cutoff) while the wrong `Dolo 500` scored 66.7.

Consequence: matching is deliberately strict. Tune via `_GENERIC` in
`matching.py`, and re-check that a wrong-brand query still fails before
committing.

**A score threshold cannot separate a typo from a wrong brand — this was tried
and discarded.** Scores overlap: a wrong brand ("O3+ Brightening Face Wash" for a
Cristello query) scored **91.3** while a real typo match scored **70.8**.
`typo_ok()` uses Levenshtein distance on the identifying tokens instead — a typo
is one edit from the brand, a wrong brand is a different word. Digits still need
exact matches, so 650 never becomes 500.

**3. A block is not always what it looks like.** Blinkit and Instamart were
recorded as blocked for weeks on the strength of a 403 and a 202. Both were
wrong about the *cause*: Cloudflare rejects on **TLS handshake fingerprint**
before reading a single header, which is why a curl carrying the full browser
cookie jar still got 403. `curl_cffi` with `impersonate="chrome"` gets 200 from
both, with **no cookies at all**. Before concluding a platform is unreachable,
check whether the rejection even depends on what you sent.

Instamart also throttles as **HTTP 200 with a ~31-byte `{"statusCode":429}`
body** — it looks like success and parses as empty.

**4. Only one bot process may poll at a time.** Telegram allows a single
`getUpdates` consumer per token; a second instance evicts the first every few
seconds, and both log `Conflict: terminated by other getUpdates request`. If you
are already running the bot in a terminal, do not start another — check first:

```bash
ps -eo pid,etime,command | grep "[b]ot.py"
```

`on_error` logs this once rather than every poll, so a quiet log does not mean
only one is running.

## Result semantics

`_bucket()` splits the fan-out five ways, and the distinctions are the point:

| Bucket | Rendering |
|---|---|
| in stock | best one written out as `BEST OPTION`; rest become buttons |
| carried, out of stock | "Carried but out of stock at X" — distinct from not-stocked |
| near-match (`typo_ok`) | `CLOSEST MATCH` + "check the name before ordering" |
| no result / failed | folded into one "Not stocked at …" line |
| coming soon | "Not yet supported: …" |

A near-match must never be presented as the recommendation when an exact match
exists. Product names are `html.escape`d (`parse_mode="HTML"`; pharmacy titles
contain `&` and `'`).

**No emojis in user-facing copy.** Deliberate, and easy to reintroduce by
habit — `grep -nP '[\x{1F300}-\x{1FAFF}\x{2600}-\x{27BF}]' bot.py parsing.py`
should stay empty.

**The board is one photo message** carrying caption and buttons, so captions cap
at **1024 characters** (text allows 4096) and a text message cannot be edited
into a photo one. `render()` trims trailing summary lines to fit but never the
recommendation itself.

**Caveat worth preserving:** location accuracy is split. Blinkit (lat/lon) and
Instamart (per-branch `storeId`) are genuinely store-scoped; 1mg is city-scoped;
**Apollo's price, PharmEasy, Netmeds and DMart are national**, so for those "in
stock" means "in stock somewhere", not "deliverable to this branch". Do not
write copy that claims otherwise.

## Config and secrets

`config.py` holds everything an owner edits: `PRESETS`, TTLs, timeouts,
`MIN_MATCH_SCORE`, `MAX_RESULTS`. Presets need **both** `city` and `pincode` —
1mg keys on city (use its spelling, `"Bangalore"`), Apollo on pincode.

They also need **`im_store`**, Instamart's dark-store id, which **cannot be
derived from lat/lon**: set the branch address on instamart.in and read
`storeId=` out of any request. A preset without one silently falls back to a
default store, so that branch's Instamart stock is somebody else's.

`.env` holds `BOT_TOKEN` and optionally `DB_PATH`. **`config.py` calls
`load_dotenv()` itself** -- it is imported at module scope long before
`bot.main()` runs, so anything read there must be loaded there or it is silently
ignored. On a hosted container set `DB_PATH=/data/bot.db` (a mounted volume);
the default relative path is wiped by every redeploy.

The 1mg/Apollo tokens hardcoded in the adapters are public web-client constants
shipped in those sites' own JS bundles, not user secrets — that placement is
deliberate.

## Git identity

Three identities, and they do not agree by default:

```bash
git config user.email          # this repo: personal (repo-local override)
git config --global user.email # everywhere else: work
gh auth status                 # which account PUSHES
```

The repo-local override keeps commits personal without touching the global work
identity. But `gh` is **global** — pushing while the work account is active fails
on permissions even though the commits are authored correctly. Fix:

```bash
gh auth switch --user mp2003
```

`git config` decides the name recorded in the commit; `gh auth` decides which
account authenticates the push. They are independent.

## Scope

**Blinkit and Instamart are live** — see trap 3 above; the block was TLS
fingerprinting, not session state.

Still unbuilt: **Zepto** (its API host rotates a short-lived `aws-waf-token`;
replaying a captured curl verbatim still returns 202/empty, so re-capturing does
not help) and **Amazon** (bot challenge). The legitimate route for those is the
spec's Phase 3 — a real browser session with geolocation — not working around bot
protection. BigBasket and JioMart are reachable but need a store handshake /
bundle-grep respectively.

Out of scope: ordering or checkout automation, prescription handling, price
history, alerts, OCR of forwarded images, quantity handling.

## Documentation

`docs/` carries the depth: architecture (01), operations (02), **endpoint
reference (03 — read this first when an adapter breaks)**, decision log (04),
bugs with root causes (05), testing (06), roadmap (07). Keep them current when
changing endpoints or matching behaviour.
