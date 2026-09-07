# Setup & Operations

## Prerequisites

- **Python 3.11+** (developed and running on 3.14).
- **A Telegram bot token** from [@BotFather](https://t.me/BotFather) -- the only
  credential you need to supply. Platform tokens are already in the adapters.
- **A residential internet connection.** All endpoints respond from a home/office
  IP. Datacenter/VPS IPs are more likely to be blocked -- relevant if you move
  this to a VPS.
- No inbound ports, SSL, or domain: long polling works behind NAT.

## First run

```bash
cd medfinder
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Get a token from @BotFather, then:
printf 'BOT_TOKEN=8123456789:AAF-your-token\n' > .env

.venv/bin/python bot.py
```

Expect `MedFinder up — polling…` then `Application started`.

**Verify the token was written** (a real token is ~46 chars: 10 digits, `:`,
~35 chars):

```bash
awk -F= '/BOT_TOKEN/{print "length:", length($2)}' .env
```

No quotes, no spaces around `=`. The startup guard strips them anyway and exits
with a clear message if the value is missing, still `paste-here`, or malformed.

> **`cp .env.example .env` caveat.** If `.env.example` disappears after that
> command it was moved, not copied -- restore it with
> `printf 'BOT_TOKEN=paste-here\n' > .env.example`.

## Commands

| Command | Behaviour |
|---|---|
| `/start` | Greeting + location picker. If a location is already saved, just confirms it |
| `/location` | Always re-opens the picker; new choice overwrites the old |
| `/where` | Shows the saved location name, pincode and city |
| `/help` | Usage + the staleness disclaimer |
| *any other text* | Product search against the saved location |

Location is stored in `bot.db` and survives restarts.

## Configuration

Everything an owner edits is in `config.py`:

| Constant | Default | Meaning |
|---|---|---|
| `PRESETS` | 3 delivery destinations | Each needs `name`, `lat`, `lon`, `pincode`, **`city`**, and `im_store` for exact Instamart stock |
| `DB_PATH` | `bot.db` | SQLite file. **Override via env on a host** -- see Deployment |
| `CACHE_TTL` | 900s | In-process cache lifetime |
| `ADAPTER_TIMEOUT` | 8.0s | Per-platform HTTP timeout |
| `SEARCH_BUDGET` | 10.0s | Overall fan-out cap |
| `MIN_MATCH_SCORE` | 55 | rapidfuzz cutoff |
| `MAX_RESULTS` | 3 | Per platform |

**Adding a preset:** use the platform's own city spelling -- `"Bangalore"`, not
`"Bengaluru"`. Both `city` and `pincode` are required (1mg keys on city, Apollo
on pincode). Blinkit uses `lat`/`lon` directly, so verify those resolve -- 1mg's
`resolve_latlng` is a free check that the coordinates match the postal pincode.

`im_store` is Instamart's dark-store id and **cannot be derived from
coordinates**: set the branch address on instamart.in, then read `storeId=` out
of any request. Without it that branch falls back to a default store and its
availability is approximate. Re-capture if a branch's stock stops matching the
app -- Instamart reassigns stores.

Presets are the pharmacy branches and office that stock is delivered **to**, not
generic neighbourhoods. Every pincode was confirmed against 1mg's own resolver.

## Deployment

Currently running in demo mode. Three things constrain where this can go:

**It must never sleep.** Long polling holds an open connection to Telegram, and
the bot receives no inbound HTTP at all. Any host that sleeps a service after
idle HTTP traffic will kill it -- which rules out most "free web service" tiers.
Render's free tier no longer exists; paid plans start at $7/mo per service.

**`curl_cffi` is a compiled dependency.** Blinkit and Instamart need it for TLS
impersonation, so the host must run a real container or VM, not a
pure-Python-wheels sandbox.

**SQLite needs a persistent disk.** `DB_PATH` defaults to a relative `bot.db`,
which on a container is wiped by every redeploy -- taking the saved branches,
Instamart store ids and pick history with it. Set it to a mounted volume:

```bash
DB_PATH=/data/bot.db
```

`config.py` calls `load_dotenv()` itself rather than relying on `bot.main()`,
because config is imported at module scope long before main runs -- a `DB_PATH`
in `.env` would otherwise be read too late and silently ignored.

**Region matters.** Every adapter call goes to an Indian platform inside a 10s
budget, and some behave differently for foreign IPs. Prefer an Indian region.

Reasonable fits: **Fly.io** (free allowance, real containers, volumes, Indian
region) or **Oracle Cloud Free Tier** (genuinely free always-on VM, more setup).
Railway works but is not free beyond trial credit.

Deferred deliberately: webhooks. Long polling needs no public URL or TLS, and
the switch is contained to the last line of `main()` -- no reason to pay that
cost before it is needed.

## Health checks

Each adapter self-checks against its live endpoint:

```bash
.venv/bin/python -m adapters.onemg      # also exercises the latlng resolver
.venv/bin/python -m adapters.apollo
.venv/bin/python -m adapters.pharmeasy
.venv/bin/python -m adapters.netmeds
.venv/bin/python -m adapters.dmart
.venv/bin/python -m adapters.blinkit    # needs curl_cffi
.venv/bin/python -m adapters.instamart  # needs curl_cffi; throttles hard
```

Each prints its top results and asserts at least one priced result with a working
URL. **This is the fastest way to tell whether a platform changed its API.**

> These call live third-party APIs. Running them in a tight loop gets you
> throttled, and a throttled adapter raises the *same* "no results" assertion as
> a re-pointed endpoint. Pause and retry before concluding an API changed.

Run all seven:
```bash
for a in onemg apollo pharmeasy netmeds dmart blinkit instamart; do
  printf "%-10s " $a
  .venv/bin/python -m adapters.$a >/dev/null 2>&1 && echo OK || echo FAIL
done
```

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `InvalidToken: token 'paste-here' rejected` | `.env` still has the placeholder | Write the real token; verify with the `awk` check above |
| One platform shows `⚠️` | Adapter raised -- endpoint changed or network blip | Run that adapter's self-check; log has the error + first 500 chars of the body |
| Several adapters fail at once right after passing | **Rate-limiting from repeated runs**, not an API change | Pause ~20s and retry; confirm with a direct `curl` before assuming drift |
| A platform 200s but returns nothing | **The HTML-shell trap** -- endpoint returned the page shell, not JSON | Check content-type. Re-capture the endpoint per [03-platform-research.md](03-platform-research.md) |
| Everything `⚠️` | Network down, or running from a blocked datacenter IP | Test from a residential connection |
| Right product reported "not found" | Identity gate too strict for this query | Check `_GENERIC` in `matching.py` -- see [B2](05-bugs-and-fixes.md#b2) |
| Board is slow | A platform is timing out | Budget caps at 10s; the slow one renders `⚠️` |

## Logs

`INFO` on startup and per update; adapter failures log at `WARNING` with the
exception **and the first 500 chars of the response body** -- which is what tells
you whether you got JSON, an HTML shell, or a block page.

`httpx` is pinned to `WARNING` to keep per-request noise out.

## Security notes

- **Rotate the bot token if it is ever pasted into a chat, screenshot, or issue.**
  `/revoke` in @BotFather invalidates the old one.
- `.env` and `bot.db` are gitignored. Keep it that way -- `bot.db` contains user
  chat IDs and locations.
- Platform tokens in the adapters are public web-client constants (see
  [D6](04-decision-log.md)), not secrets.

## Politeness

One user query = at most one request per platform. No background crawling, no
retries beyond one, 8s timeout. Traffic is indistinguishable from a human
shopper, which is the intended posture for a personal tool.
