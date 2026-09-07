# Platform Research & Endpoint Reference

> All findings below were **verified with live requests**, not assumed.
> Last full re-verification: **2026-09-07** (all 7 live adapters passing).

## Why this document exists

The original spec proposed HTML-scraping strategies for 1mg and Apollo. Both were
**wrong against the live sites** -- their search pages are client-rendered empty
shells. Every endpoint here was found by grepping the sites' own JS bundles and
confirmed with curl before any parser was written. If an adapter breaks, this is
the file to read first.

## Live platforms

### 1mg (pharmacy) -- GET

```
https://www.1mg.com/pwa-dweb-api/api/v4/search/all
  ?q=<query>&city=<City>&filter=&page_number=1&scroll_id=
  &per_page=10&types=sku&sort=&fetch_eta=true&is_city_serviceable=true
```

Headers (all required):
```
Authorization: Token token=3769e1fd4435b207522343256042a9d4490b147d51f2770553d5c019414f
X-City: <City>          x-platform: desktop-0.0.1        locale: en
```

**Three traps, all confirmed:**
- Param is `q=`, **not** `name=`.
- `types=sku` is **mandatory** -- blank/`all`/`drug` return HTTP 400
  `"Parameter types cannot be blank"`.
- **Without the auth headers the same path silently returns the 300KB HTML
  shell instead of JSON.** A 200 does not mean success -- check content-type.

| Field | Path |
|---|---|
| name | `data.search_results[].name` |
| in stock | `.available` (real bool) |
| price | `.prices.discounted_price` -- **often null even in stock, fall back to `.prices.mrp`** |
| MRP | `.prices.mrp` (strings like `"₹32.12"`) |
| ETA | `.eta` -- **contains HTML tags**, must be stripped |
| URL | `.url` (relative, prefix `https://www.1mg.com`) |

Location-sensitive: Bangalore ₹32.10 / "7pm, Tomorrow" vs Delhi ₹30.30 / "45 mins".

**Bonus endpoint** -- reverse geocode, same headers:
`GET /pwa-dweb-api/location/latlng/<lat>,<lon>` -> `result[0].city` + `.zipcode`.
Verified `12.9352,77.6245` -> `Bangalore` / `560034`. This is what makes
live-location sharing work without asking the user to type a pincode.

### Apollo (pharmacy) -- POST

```
POST https://search.apollo247.com/v4/fullSearch
Authorization: Oeu324WMvfKOj5KMJh2Lkf00eW1
x-source-service: PHARMA_AP_IN
Content-Type: application/json

{"query":"<q>","page":1,"productsPerPage":10,"pincode":"<pincode>"}
```

**GET returns HTTP 405** -- must be POST.

| Field | Path |
|---|---|
| name | `data.productDetails.products[].name` |
| in stock | `.status == "in-stock"` (the bundle's own constant) |
| price / MRP | `.specialPrice` / `.price` |
| ETA | `.deliveryTime` |
| URL | `https://www.apollopharmacy.in/otc/<urlKey>` (verified 200; `/medicine/` 308-redirects here) |

`pincode` affects **`deliveryTime` only** -- price is national.
`apollo247.com` shares this same backend; it is one platform, not two.

### PharmEasy (pharmacy) -- GET, no auth

```
GET https://pharmeasy.in/search/all?name=<q>
```
Parse the `__NEXT_DATA__` script tag. This is the one site where the spec's
SSR assumption actually held.

| Field | Path |
|---|---|
| products | `props.pageProps.productList[]` |
| price / MRP | **`.salePriceDecimal` / `.mrpDecimal`** -- NOT `salePrice`/`mrp`, those are null |
| in stock | `.productAvailabilityFlags.isAvailable` |
| URL | `https://pharmeasy.in/online-medicine-order/<slug>` (verified 200) |

National: identical results for pincodes 560034 and 110001.

### Netmeds (pharmacy) -- GET, **zero auth**

```
GET https://www.netmeds.com/ext/search/application/api/v1.0/products
      ?q=<query>&page_size=20&filters=false
```
A bare curl with **no headers at all** returns priced JSON. GET only (POST -> 404).

| Field | Path |
|---|---|
| name | `items[].name` |
| price / MRP | `items[].price.effective.min` / `.marked.min` |
| in stock | `items[].sellable` |
| URL | `https://www.netmeds.com/product/<slug>` |

Runs on the Fynd platform. **Dead end for the record:** the stock Fynd paths
(`/service/application/catalog/v1.0/products/`) return the 3.2MB HTML shell --
Netmeds proxies search through `/ext/search/` instead. No Algolia anywhere.

### DMart Ready (FMCG) -- GET, no auth

```
GET https://digital.dmart.in/api/v2/search/<query>
```

| Field | Path |
|---|---|
| products | `products[]`, prices one level down in `.sKUs[0]` |
| price / MRP | `.sKUs[0].priceSALE` / `.priceMRP` (strings) |
| in stock | `.sKUs[0].buyable == "true"` |
| pack size | `.sKUs[0].variantTextValue` (worth showing -- makes prices comparable) |
| URL | `https://www.dmart.in/pdp/<productId>` |

Use **v2**: v1 returns `suggestionView` instead of `products`. National pricing
(identical across 560034 / 400001 / 110001), so no store session needed.

## Stubbed -- reachable but unfinished

| Platform | Status | Next step |
|---|---|---|
| **BigBasket** | API live, returns `400 "Missing either Mid or AddressId or lat-long"`. Guessed lat/long cookies -> `404` error 5012 | Replicate the address/store-resolution call the site makes *before* search, then pass `Mid`/`AddressId` through |
| **JioMart** | HTTP 200 but a 6.8MB Akamai-fronted shell, **no prices in HTML**. Algolia keys are *not* in the page shell (the one "algolia" hit is a config schema) | Bundle-grep for the Algolia credentials or JSON search endpoint |

## The TLS-fingerprint finding (how Blinkit and Instamart were unblocked)

Both were previously recorded here as blocked. That verdict was **wrong about the
cause**, and the correction is worth keeping.

Blinkit returned 403 to a curl carrying the *full browser cookie jar* -- identical
headers, identical cookies, same 403. Cloudflare was not reading them: it rejects
on the **TLS handshake fingerprint**, before a single header is parsed. `httpx`
and `curl` do not negotiate like Chrome does.

`curl_cffi`, which replays Chrome's real TLS fingerprint, gets **HTTP 200 from
both -- with no cookies at all**. No session to warm, nothing to expire.

```python
from curl_cffi import requests as cffi
async with cffi.AsyncSession() as s:
    r = await s.post(URL, headers=h, data="{}", impersonate="chrome")
```

| Platform | What the block actually was | Key |
|---|---|---|
| **Blinkit** | TLS fingerprint only | `lat`/`lon` **headers** select the dark store |
| **Instamart** | WAF on the *website*; the **API is open** | `storeId` query param, not derivable from lat/lon |
| **Zepto** | Rotating `aws-waf-token` on the API host | Not beatable this way -- see below |

Traps found while doing this:

- Blinkit's `eta_identifier` is an internal class name (`express`, `unicorn`,
  `longtail`), not minutes, and **varies by store**. No minutes exist anywhere in
  the payload; `v1/actions/get_updated_eta` returns a merchant map that excludes
  the pharmacy stores.
- Instamart throttles as **HTTP 200 with a ~31-byte `{"statusCode":429}` body** --
  it looks like success. Treated as "no results", absorbed by the cache.
- Instamart's `storeId` **cannot** be resolved from coordinates. Each branch's id
  was captured from its own web app and recorded in `config.PRESETS`. Ids differ
  per branch and so do prices: the same query returned Double Masala at Rs 75
  from one store and Rs 120 from another.

## Product images

Field paths, probed live; every constructed URL verified returning HTTP 200 with
an `image/*` content-type.

| Platform | Field | Form |
|---|---|---|
| 1mg, PharmEasy | `item["image"]` | already absolute |
| Blinkit | `item["image"]["url"]` | already absolute |
| Netmeds | first `medias[]` entry with `type == "image"` | already absolute |
| Apollo | `item["thumbnail"]` | relative; prefix `https://newassets.apollo247.com/pub/media` |
| Instamart | `variations[0].imageIds[0]` | prefix `https://media-assets.swiggy.com/swiggy/image/upload/` |
| **DMart** | `sKUs[0].imageKey` | **unresolved** -- every CDN pattern tried 404s, and the product page 404s too |

Not every product has an image even on a supporting platform, so the renderer
walks the ranked results and takes the first that does.

## Blocked -- not pursued

| Platform | Observed | Note |
|---|---|---|
| Zepto | `bff-gateway.zepto.com` returns 202/empty | See below -- the one that resisted `curl_cffi` |
| Amazon.in | `bm-verify` bot challenge | Deliberate bot detection |
| Meesho | HTTP 403 | |
| Wellness Forever | HTTP 403 even with full browser headers | |
| MedPlus | Homepage 200 ✅ but search returns 384 bytes | Search path needs investigation |
| Flipkart | Search returns electronics, not groceries; captcha infra in payload | Wrong shape for this bot |
| PillO | `pillo.in` does not resolve; `pillo.co.in`/`pillo.app`/`trypillo.com` are **114-byte stubs**. Only `evitalrx.in` is real -- and it is *pharmacy billing software*, not a consumer storefront | No public web catalog exists. Would require reverse-engineering the mobile app |

### Zepto -- why re-capturing will not help

Worth recording so this is not retried from scratch. The app talks to a separate
host from the WAF-challenged website:

```
POST https://bff-gateway.zepto.com/user-search-service/api/v3/search
GET  https://bff-gateway.zepto.com/lms/api/v2/get_page?latitude=&longitude=
     (the store resolver -- takes raw lat/lon, which is what we would need)
```

That host answers **202 with an empty body** for every combination tried:
minimal headers, `+aws-waf-token` cookie, `+request-signature`/CSRF headers, and
the full captured header set. Replaying a browser-captured curl **verbatim**
(plain curl, every header and cookie) also returned 202/0 bytes.

The reason: two requests captured within one browser session carried **different**
`aws-waf-token` values, and the token's middle segment decodes to a 12-byte
structure holding a timestamp. It rotates and is short-lived, so it cannot be
lifted from a capture and reused by a bot -- unlike Blinkit's static `auth_key`.

Dark stores are UUIDs in `store_id`/`store_ids`, and `store_etas` gives real
minutes (`{"...1b2":8,"...1f6":24}`) -- better data than Blinkit's class names,
if it is ever reachable. Product pages are constructible from the search
response: `zepto.com/pn/<slug>/pvid/<uuid>`.

**Policy:** the remaining blocked platforms return bot-challenges. Working around
those means evading protection, which is out of scope. They stay unbuilt unless
reached the way the spec intends -- a real browser session with proper
geolocation (Phase 3), which can solve the challenge and keep tokens fresh.

## Credentials note

The 1mg and Apollo tokens above are **public web-client constants** shipped in
those sites' own JS bundles -- the same values any browser sends. They are
hardcoded in the adapter constant blocks by design, not stored in `.env`
(which holds `BOT_TOKEN` only).

While reading Apollo's bundle, unrelated third-party keys (Google Maps, Firebase)
were visible in its public env config. They were **not** used or recorded here --
only the search/serviceability endpoints this tool actually needs.
