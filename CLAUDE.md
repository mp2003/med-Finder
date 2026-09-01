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

# Check all five (keep stderr; a redirected-output loop can misreport status)
for a in onemg apollo pharmeasy netmeds dmart; do
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
one. Before concluding an endpoint changed, pause ~20s and retry, or confirm with
a direct `curl`. Space the checks out rather than looping them.

## Architecture

A Telegram bot (python-telegram-bot, long polling) that fans a product query out
to several Indian pharmacy/FMCG platforms concurrently and renders one comparison
board, editing the message as each platform lands.

```
bot.py::do_search
  → db.get_location(chat_id)        SQLite, survives restarts
  → cache check                     key=(normalized_query, pincode), TTL 15min
  → asyncio.gather over ADAPTERS    10s total budget, 8s per adapter
  → matching identity gate + score  drop wrong-brand results
  → render()                        progressive message edits
```

**The adapter contract** is the core abstraction. Each `adapters/<platform>.py`
exposes `PLATFORM: str` and `async def search(query, loc) -> list[ProductResult]`,
and must:

- **Never raise.** Catch everything, log with the first 500 chars of the response
  body, return `[]`. One broken platform renders `⚠️`; the board still works.
- End by returning `top_matches(query, out)` — this applies the identity gate,
  ranking and the 3-result cap. Do not hand-roll filtering.
- Set `eta=None` when the platform gives no delivery estimate. **Never synthesize
  prices or ETAs.**
- Keep URL/headers/tokens in one constant block at the top marked
  `# VERIFY in DevTools if this breaks`.

Adding a platform: write the module, add its name to `_LIVE` in
`adapters/__init__.py`. Nothing else changes. `_SOON` holds stubs, which are
excluded from the fan-out and rendered `⚠️ coming soon` — a stub returning `[]`
would otherwise render as a misleading `❌ not found`.

`adapters/__init__.py` builds the registry lazily via module `__getattr__` so
`python -m adapters.<name>` does not trip runpy's double-import warning.

## Two things that will bite you

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
a *Glutafine* product at 70 and rendered it `✅`. `matching.py` therefore layers
an **identity gate** over the score: `key_tokens()` strips ~60 generic retail
words (`tablet`, `face`, `wash`, `mg`, `brightening`…), and `identity_ok()`
requires **every** remaining token to appear in the title. `normalize()` also
splits punctuation and letter/digit runs, because Apollo writes `Dolo-650` (one
token, scored 42.9 — below the cutoff) while the wrong `Dolo 500` scored 66.7.

Consequence: matching is deliberately strict. A typo reports not-found-with-
similar rather than guessing. Tune via `_GENERIC` in `matching.py`, and re-check
that a wrong-brand query still fails before committing.

## Result semantics

`render()` distinguishes three states, and the distinction is the point:

| State | Rendering |
|---|---|
| `is_match` + available | `✅ Platform — ₹32.1 (eta) — link` |
| `is_match` + out of stock | `❌ … is out of stock` + `↳ in stock:` alternative |
| `is_match=False` | `❌ … not found. Similar: <product>` |

Suggestions must never render as `✅`. Product names are `html.escape`d
(`parse_mode="HTML"`; pharmacy titles contain `&` and `'`).

**Caveat worth preserving:** only 1mg varies by location. Apollo's price,
PharmEasy, Netmeds and DMart are national, so `✅` means "in stock", not
"deliverable to this pincode". Do not write copy that claims otherwise.

## Config and secrets

`config.py` holds everything an owner edits: `PRESETS`, TTLs, timeouts,
`MIN_MATCH_SCORE`, `MAX_RESULTS`. Presets need **both** `city` and `pincode` —
1mg keys on city (use its spelling, `"Bangalore"`), Apollo on pincode.

`.env` holds `BOT_TOKEN` only. The 1mg/Apollo tokens hardcoded in the adapters
are public web-client constants shipped in those sites' own JS bundles, not user
secrets — that placement is deliberate.

## Scope

Blinkit/Zepto/Instamart return 403 / 202-empty; Amazon serves a bot challenge.
These stay unbuilt — the legitimate route is the spec's Phase 3 (a real browser
session with geolocation), not working around bot protection. BigBasket and
JioMart are reachable but need a store handshake / bundle-grep respectively.

Out of scope: ordering or checkout automation, prescription handling, price
history, alerts.

## Documentation

`docs/` carries the depth: architecture (01), operations (02), **endpoint
reference (03 — read this first when an adapter breaks)**, decision log (04),
bugs with root causes (05), testing (06), roadmap (07). Keep them current when
changing endpoints or matching behaviour.
