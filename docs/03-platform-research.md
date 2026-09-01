# Platform Research & Endpoint Reference

> All findings below were **verified with live requests**, not assumed.
> Last full re-verification: **2026-08-31** (all 5 live adapters passing).

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

## Blocked -- not pursued

| Platform | Observed | Note |
|---|---|---|
| Blinkit | HTTP 403 | Needs warm per-location session (Phase 3) |
| Zepto | HTTP 202, **empty body** | Store-id resolution first (Phase 3) |
| Swiggy Instamart | HTTP 202, **empty body** | Phase 3; historically needs residential IP |
| Amazon.in | `bm-verify` bot challenge | Deliberate bot detection |
| Meesho | HTTP 403 | |
| Wellness Forever | HTTP 403 even with full browser headers | |
| MedPlus | Homepage 200 ✅ but search returns 384 bytes | Search path needs investigation |
| Flipkart | Search returns electronics, not groceries; captcha infra in payload | Wrong shape for this bot |
| PillO | `pillo.in` does not resolve; `pillo.co.in`/`pillo.app`/`trypillo.com` are **114-byte stubs**. Only `evitalrx.in` is real -- and it is *pharmacy billing software*, not a consumer storefront | No public web catalog exists. Would require reverse-engineering the mobile app |

**Policy:** the blocked platforms return 403s and bot-challenges. Working around
those means evading protection, which is out of scope for this project. They stay
unbuilt unless reached the way the spec intends -- a real browser session with
proper geolocation (Phase 3).

## Credentials note

The 1mg and Apollo tokens above are **public web-client constants** shipped in
those sites' own JS bundles -- the same values any browser sends. They are
hardcoded in the adapter constant blocks by design, not stored in `.env`
(which holds `BOT_TOKEN` only).

While reading Apollo's bundle, unrelated third-party keys (Google Maps, Firebase)
were visible in its public env config. They were **not** used or recorded here --
only the search/serviceability endpoints this tool actually needs.
