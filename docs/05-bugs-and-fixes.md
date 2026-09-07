# Bugs & Fixes

Defects found during development, each with root cause and the fix. Recorded
because the root causes are non-obvious and would otherwise be re-introduced.

---

## B1 — Hyphenated brand names scored below the cutoff

**Symptom.** Apollo returned "Dolo 500 Tablet" (out of stock) as the best match
for `dolo 650`, while the correct in-stock "Dolo-650 Tablet 15's" was dropped.

**Root cause.** Apollo writes `"Dolo-650"` with a hyphen. `token_set_ratio`
treats `dolo-650` as a **single token**, so the exact match scored **42.9** --
below the 55 cutoff -- while the wrong "Dolo 500" scored **66.7**.

Compounding it, `token_set_ratio` is **case-sensitive**: `"dolo 650"` vs
`"Dolo 650mg Strip Of 15 Tablets"` scores 37 raw but 67 lowercased.

**Fix.** Normalize both sides in `matching.py::normalize` before scoring:
lowercase, split punctuation into token boundaries, and separate letter/digit
runs (`650mg` -> `650 mg`, `dolo650` -> `dolo 650`).

**Result.** Exact matches now score 100 on both platforms' naming styles.

**Why in `normalize`, not the adapter.** The bug was in shared scoring, so a
per-adapter patch would have left every other platform broken. One fix, all
callers.

---

## B2 — Wrong brand shown as a confident match {#b2}

**Symptom.** Reported by the user. Searching `Cristello BRIGHTENING FACE WASH`
returned:

```
✅ 1mg — ₹399 (Get by 11pm, Today) — Glutafine Rich Creamy Face Wash
```

A completely different brand, presented as a match.

**Root cause.** `token_set_ratio` scores on **word overlap**, and the query's
generic words (`brightening`, `face`, `wash`) matched -- while `cristello`, the
only token that identifies the product, appeared nowhere in the result. Measured
scores against that query:

| Result | Score |
|---|---|
| Smart and Handsome Instant Brightening Face Wash | 85.3 |
| Brillare Skin Brightening Face Wash | 78.4 |
| Glutafine Rich Creamy Face Wash | 70.0 |
| **Cristello Instant Brightening Face Wash** | **100.0** |

Every wrong product cleared the 55 cutoff. Medicine searches hid this because
`dolo 650` has distinctive tokens; **branded cosmetics exposed it.**

**Fix.** An identity gate in `matching.py`:
- `key_tokens()` -- strip ~60 generic retail words (`tablet`, `face`, `wash`,
  `mg`, `instant`, `brightening`...), keep brand/molecule/strength tokens.
- `identity_ok()` -- **every** key token must appear in the title. Digits must
  match a whole token (`650` != `6500`); words may match as a prefix.
- `top_matches()` splits results into real matches and, if none, similar items
  flagged `is_match=False`.

**Result, verified live:**
```
❌ 1mg — not found. Similar: Glutafine Rich Creamy Face Wash — ₹399
```
Regression-checked: `dolo 650` and `crocin advance` still return correct `✅`.

**Tuning knob.** `_GENERIC` in `matching.py`. If a legitimate product is ever
rejected, a word in that list is the likely cause.

---

## B3 — 1mg ETA corrupted the Telegram message

**Symptom.** Latent, caught before shipping.

**Root cause.** 1mg's `eta` field ships as **HTML**:
`Get by <b><span style=color:#3B3B3B>7pm, Tomorrow</span></b>`. Injected into a
`parse_mode="HTML"` message, the raw tags corrupt rendering.

**Fix.** `strip_tags()` in `adapters/base.py`, applied in the 1mg adapter.
Product names are also `html.escape`d -- pharmacy titles contain `&` and `'`
(e.g. `Dolo-650 Tablet 15's` -> `15&#x27;s`).

---

## B4 — `discounted_price` null on in-stock items

**Root cause.** 1mg returns `prices.discounted_price = null` for many in-stock
products, so reading it alone showed "price n/a" on available items.

**Fix.** Fall back to `prices.mrp`.

---

## B5 — Wrong field names on PharmEasy and DMart

**Root cause.** Both nest or rename their price fields:
- PharmEasy: `salePriceDecimal`/`mrpDecimal` -- `salePrice`/`mrp` exist but are
  always `null`.
- DMart: prices are one level down in `sKUs[0].priceSALE`/`.priceMRP`.

**Caught by** printing a real product object rather than assuming field names.
Documented in [03-platform-research.md](03-platform-research.md).

---

## B6 — Location picker appeared twice

**Symptom.** User screenshot showed the picker rendered twice in a row.

**Root cause.** **Not a bug in the handler** -- the user sent `/start` twice
(both visible at 2:43 in the screenshot). But each `/start` sent **three**
messages (greeting + picker + live-location prompt), so two starts produced six.

**Fix.** Greeting folded into the picker message; `/start` with a location
already saved now just confirms it.

| | Before | After |
|---|---|---|
| First `/start` | 3 messages | **2** |
| Repeat `/start` | 3 messages | **1** |

The remaining 2-message split is a **Telegram constraint**: inline buttons and
`request_location` buttons cannot coexist on one message.

---

## B7 — DMart multi-word queries silently returned the wrong payload

**Symptom.** `colgate toothpaste` returned nothing from DMart, while single-word
`maggi` worked. Found while verifying the health-check commands for `CLAUDE.md`.

**Root cause.** DMart puts the query in the **URL path**, not a query parameter.
httpx passed the raw space through, and the server answered **HTTP 200 with a
large unrelated payload** rather than an error -- so the adapter saw a valid
response with no matching products and returned `[]`.

**Fix.** `quote(query, safe="")` before formatting the path, in
`adapters/dmart.py`.

**Verified after fix:** `colgate toothpaste` ₹67, `surf excel` ₹249,
`amul butter` ₹60, `maggi noodles` ₹14.

**Also hardened `demo()`** to check two queries, one of them multi-word, so this
class of bug fails the health check instead of hiding.

---

## Non-bugs (test harness errors, recorded to avoid confusion)

- **"DMart regressed on dolo 650"** -- the regression test asserted *every*
  platform matches a medicine query. DMart is a grocery store and correctly has
  no medicines. The assertion was wrong, not the code.
- **"unescaped &"** -- a crude escaping check flagged `&#x27;`, which is the
  correctly-escaped apostrophe. Escaping was working.
- **MedPlus "403 blocked"** -- an early probe with thin headers returned 403;
  with full browser headers the homepage returns 200. The initial reading was
  wrong.
- **Adapter self-checks reporting FAIL** -- a shell loop misread exit codes when
  stdout was redirected. All five adapters were passing. The loop in
  `CLAUDE.md` now keeps stderr and prints the last line instead.
- **Three adapters "broken" at once** -- Apollo, PharmEasy and Netmeds all failed
  immediately after passing individually. Cause was **rate-limiting from running
  the checks back-to-back**, not API changes; all recovered after a ~20s pause.
  A throttled adapter raises the *same* assertion as a re-pointed endpoint, so
  always pause and retry before concluding drift.

## B8 — "Vigoquin 0.5%" reported not-found on a product that exists

**Symptom.** An extracted item with a strength failed to match a real listing.

**Cause.** `identity_ok` requires digits to match a whole token, and
`Vigoquin 0.5%` yields key tokens `['vigoquin', '0', '5']`. Verified:

| Query | Title | Matches? |
|---|---|---|
| `Vigoquin 0.5%. E/d` | Vigoquin 0.5% Eye Drop 5ml | yes |
| `Vigoquin 0.5%. E/d` | **Vigoquin Eye Drops** | **no** |
| `Vigoquin` | Vigoquin 0.5% Eye Drop 5ml | yes |

**Fix.** `parsing.search_term` strips a percentage strength and trailing dosage
form for the *search* while the confirmation list keeps the original line. Brand
digits survive -- `Dolo 650` keeps its 650, because there the digits identify the
product rather than its strength.

## B9 — A typo reported not-found on the right product

**Symptom.** Searching `BIODEMS-F 10 HAIR LOTION 60ML` (one letter off the real
`BIODENS-F`) returned "not found" from every platform, including one showing the
exact product at Rs 1024.50.

**Cause.** The identity gate demands exact token containment, so `biodems` failed
against `biodens...` despite scoring 96.7.

**Fix that did NOT work.** A score threshold. Scores overlap badly --
`token_set_ratio` rewards shared generic words, so a *wrong* brand ("O3+
Brightening Face Wash" for a Cristello query) scored **91.3** while a real typo
match scored **70.8**. No cutoff separates them; this was tested and discarded.

**Fix.** `matching.typo_ok` uses Levenshtein distance on the *identifying*
tokens: a typo is one edit from the brand, a wrong brand is a different word.
Digits still require exact matching, so 650 never becomes 500. Renders as
`CLOSEST MATCH`, never a confident hit.

## B10 — Blinkit's internal delivery labels leaked into the UI

**Symptom.** Buttons read `Blinkit  Rs 31  unicorn`.

**Cause.** Blinkit ships an `eta_identifier` class name rather than minutes, and
it **varies by store** -- `express` at one branch, `unicorn` at another. The
first fix only knew `express`, so `unicorn` scored as an unknown ETA and sorted
Blinkit *last* under "soonest delivery" despite being fastest.

**Fix.** `_FAST_WORDS` covers the quick-commerce vocabulary and renders "under
30 min". `longtail` and `pharma_rx` are catalogue tags, not speeds, so they show
no ETA rather than being dressed up as a delivery promise.
