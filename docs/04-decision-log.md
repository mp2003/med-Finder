# Decision Log

Why the code looks the way it does. Each entry: the decision, the reason, and
what would make us revisit it.

---

## D1 — JSON APIs instead of HTML scraping

**Decision.** Both Phase 1 adapters call the platforms' own JSON APIs rather than
parsing search-page HTML.

**Why.** The spec assumed 1mg and Apollo were server-rendered for SEO. Live
testing showed both search pages are **client-rendered empty shells**: empty
`<title>`, zero product names, zero prices, no product links. 1mg's
`__INITIAL_STATE__` was 2.3KB of shell config; Apollo's RSC payload had no
`price`/`mrp`/`productName` anywhere. There was nothing to parse.

**Consequence.** Endpoints were found by grepping each site's JS bundles and
confirmed with curl before writing parsers. This also kept the project inside the
"no Playwright" constraint -- no browser automation needed.

**Revisit if.** An endpoint starts returning the HTML shell (the documented
failure mode) -- re-capture from DevTools per
[03-platform-research.md](03-platform-research.md).

---

## D2 — Presets carry `city` as well as `pincode`

**Decision.** Added a `city` field to presets, `Location`, and the `users` table.

**Why.** The spec's presets had only lat/lon/pincode, but **1mg keys on city**,
not pincode. Without it the largest platform cannot be queried correctly. All
Bengaluru presets use `"Bangalore"` -- 1mg's own spelling, not "Bengaluru".

**Revisit if.** 1mg migrates to pincode-based serviceability.

---

## D3 — Live location auto-resolves instead of prompting

**Decision.** When a user shares a Telegram location, resolve city + pincode via
1mg's `location/latlng/<lat>,<lon>` endpoint.

**Why.** The spec said "ask the user to type their 6-digit pincode once". The
endpoint returns both fields directly (verified: `12.9352,77.6245` ->
`Bangalore` / `560034`). Fewer steps for the user *and* less code. Falls back to
the preset picker if resolution fails.

---

## D4 — Identity gate on top of fuzzy matching

**Decision.** A result must contain **every** identifying token from the query
(brand/molecule/strength) to be shown as a match. Score alone is not enough.

**Why.** A real bug. See [05-bugs-and-fixes.md](05-bugs-and-fixes.md#b2).
`token_set_ratio` rewards generic words, so a "Cristello" query returned a
**Glutafine** product as a confident `✅`. Showing the wrong brand as a match is
worse than showing nothing.

**Trade-off, accepted.** Matching is now strict: a genuine typo (`dollo 650`)
reports not-found-with-similar rather than guessing. For a pharmacy tool, never
lying about a match is worth losing typo tolerance.

**Revisit if.** Real usage shows legitimate products being rejected -- the
`_GENERIC` word list in `matching.py` is the tuning knob.

---

## D5 — Three distinct result states

**Decision.** Render match / out-of-stock / no-match differently:

| State | Rendering |
|---|---|
| Real match, in stock | `✅ Platform — ₹32.1 (2 hr) — Product` |
| Real match, out of stock | `❌ Platform — Product is out of stock` + `↳ in stock:` alternative |
| No real match | `❌ Platform — not found. Similar: <product> — ₹399` |

**Why.** Direct user feedback: *"if the product is not available tell user and if
they want give the next similar product"*. Suggestions are never `✅`, so the user
always knows whether they got their product or a substitute.

---

## D6 — Tokens hardcoded in adapters, not `.env`

**Decision.** 1mg and Apollo tokens live in the adapter constant blocks.

**Why.** They are public web-client constants shipped in the sites' own JS --
the same values any browser sends, not user secrets. Treating them as config adds
setup friction for zero security gain. `.env` holds `BOT_TOKEN` only.
*(User-confirmed.)*

---

## D7 — bs4/lxml dropped

**Decision.** Removed from `requirements.txt`.

**Why.** Both live adapters return JSON; nothing imports them. YAGNI. Add back if
an adapter ever degrades to HTML parsing. *(User-confirmed.)*

Note PharmEasy parses HTML but needs only a regex for the `__NEXT_DATA__` tag
plus `json` -- still no BeautifulSoup.

---

## D8 — Blocked platforms stay unbuilt

**Decision.** Blinkit, Zepto, Instamart, Amazon and Meesho are not implemented.

**Why.** They return 403s and bot-detection challenges. Working around those means
evading protection, which is out of scope. The spec's own Phase 3 path -- a real
browser session with geolocation -- remains the legitimate route.

**Also.** BigBasket and JioMart are *reachable* but need real work (a store
handshake and a bundle-grep). They are stubbed with findings recorded, rather
than shipped as adapters that silently return nothing.

---

## D9 — Stubs render "coming soon", not "not found"

**Decision.** `COMING_SOON` modules are excluded from the fan-out and rendered as
`⚠️ coming soon`.

**Why.** A stub returning `[]` would render as `❌ not found`, which is a lie --
it implies the platform was checked and lacked the product.

---

## D10 — `/start` is idempotent

**Decision.** `/start` with a location already saved confirms it in one message
instead of re-showing the picker.

**Why.** User reported the picker appearing twice. Root cause was two `/start`
messages, each producing three replies. Reduced first-run to 2 messages and
repeat to 1. The remaining 2-message split is a Telegram constraint: inline
buttons and `request_location` buttons cannot share one message.
