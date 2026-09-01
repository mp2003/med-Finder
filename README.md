# MedFinder Bot

> **Full documentation in [`docs/`](docs/README.md)** — architecture, endpoint
> reference, decision log, bugs, testing and roadmap.

Telegram bot: send a medicine name, get back which platforms have it in stock
near your saved location — with price, ETA and a tap-to-open product link
(these open the installed app on phones).

## Setup

1. Create a bot with [@BotFather](https://t.me/BotFather) and copy the token.
2. `cp .env.example .env` and paste the token into it.
3. `python -m venv .venv && .venv/bin/pip install -r requirements.txt`
4. `.venv/bin/python bot.py`

## Use

- `/start` — greet + pick a location (5 presets, or share live location)
- `/location` — change location · `/where` — show saved one · `/help` — usage
- Any other text = a product search, e.g. `dolo 650`

Location is saved in `bot.db` and survives restarts.

## Editing presets

`config.py` → `PRESETS`. Each entry needs `city` **and** `pincode`: 1mg keys on
city, Apollo keys on pincode. Use 1mg's spelling ("Bangalore", not "Bengaluru").

## Adding a platform

Copy an adapter in `adapters/`, implement
`async def search(query, loc) -> list[ProductResult]`, then add the module to
`ADAPTERS` in `adapters/__init__.py`. Adapters must never raise — return `[]`.

Each adapter keeps its endpoint + headers in one constant block at the top marked
`# VERIFY in DevTools if this breaks`. Both live platforms are client-rendered
shells, so these are the JSON APIs their own frontends call — if one breaks,
re-capture it from the DevTools Network tab and update that block.

Check an adapter against the live site any time:

```
.venv/bin/python -m adapters.onemg
.venv/bin/python -m adapters.apollo
```

## Platforms

Live (verified against real endpoints):

| Platform | Type | Auth |
|---|---|---|
| Tata 1mg | pharmacy | bundle token |
| Apollo | pharmacy | bundle token |
| PharmEasy | pharmacy | none |
| Netmeds | pharmacy | none |
| DMart Ready | FMCG/grocery | none |

Stubs, rendered as "coming soon" (each file documents what it needs):

- **BigBasket** — API is live but wants `Mid`/`AddressId`/`lat-long`; needs the
  address-resolution call replicated first.
- **JioMart** — Akamai-fronted, client-rendered, Algolia keys not in the shell;
  needs a bundle-grep.
- **Blinkit** — HTTP 403. Zepto and Instamart return 202 with an empty body.
  These need warm per-location sessions (Phase 3), not a plain HTTP call.

Not pursued: Amazon (bot-detection challenge), Meesho (403), MedPlus and
Wellness Forever (search blocked/empty), PillO (no consumer web catalog --
app-only).

Prices/availability come from the platforms and can be stale — this is a
personal convenience tool.
# med-FInder
