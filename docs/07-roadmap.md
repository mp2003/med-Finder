# Roadmap

Current state: **7 platforms live**, well past the original Phase 1 scope
(bot shell + 1mg + Apollo). Quick-commerce, once written off as blocked, turned
out to be a TLS-fingerprint problem rather than a session problem --
see [03-platform-research.md](03-platform-research.md).

| Platform | Type | Status |
|---|---|---|
| 1mg | pharmacy | ✅ live |
| Apollo | pharmacy | ✅ live |
| PharmEasy | pharmacy | ✅ live |
| Netmeds | pharmacy | ✅ live |
| DMart Ready | FMCG | ✅ live (no product image) |
| Blinkit | quick-commerce | ✅ live -- lat/lon, genuinely per-store |
| Swiggy Instamart | quick-commerce | ✅ live -- per-branch `storeId` |
| BigBasket | FMCG | stub -- needs store handshake |
| JioMart | FMCG | stub -- needs bundle-grep |
| Zepto | quick-commerce | blocked -- rotating WAF token (Phase 3) |

---

## Next: serviceability (recommended)

**The problem.** Location accuracy is now split. Blinkit resolves a dark store
from lat/lon and Instamart uses a per-branch `storeId`, so for those two a
result genuinely means "deliverable to this branch". 1mg varies by city. But
**Apollo's price, PharmEasy, Netmeds and DMart remain national** -- for those,
"in stock" still means "in stock somewhere", not "deliverable here".

For a tool whose job is telling you where to buy something for a specific
branch, that is the weakest claim on the board.

**Work.** Capture the "deliver to `<pincode>`" XHR each of those four fires on a
product page and replicate it; tighten `available` accordingly. The spec always
flagged the current behaviour as an MVP simplification.

**Why before more platforms.** Five sources that are honest beat seven that are
vague.

---

## Then: BigBasket and JioMart

Two more FMCG sources. Both reachable, neither quick:

- **BigBasket** -- API returns `400 "Missing either Mid or AddressId or
  lat-long"`. Needs the address/store-resolution call replicated first. Since
  it is store-scoped, this one would give *genuinely* location-accurate stock.
- **JioMart** -- Akamai-fronted, client-rendered, Algolia keys not in the shell.
  Needs a bundle-grep. **Note this contradicts the common assumption that JioMart
  is an easy win** -- it is the same class of work as Netmeds was.

---

## Phase 3: Zepto only

Blinkit and Instamart are **done** -- option 2 below turned out to be reachable
after all, because the block was TLS fingerprinting rather than session state.
The spec's prediction of "warm per-location sessions" was wrong for those two.

Zepto still needs it. Its API host rotates a short-lived `aws-waf-token`, proven
by replaying a captured curl verbatim and still getting 202/empty. That leaves:

1. A real browser context with geolocation set per branch, kept warm and reused,
   solving the challenge and keeping tokens fresh
2. ~~Replicating the lat/lon XHRs~~ -- tried and documented as a dead end

This pulls in a browser dependency the MVP deliberately avoided, for one
platform. **Not** to be attempted by working around bot protection.

## Smaller open items

- **DMart images.** Its `sKUs[0].imageKey` has no CDN form that resolves; every
  pattern tried 404s and the product page 404s too. Results carry no picture.
- **Instamart for a shared live location.** A shared GPS pin has no `im_store`,
  so it falls back to a default store and availability is approximate. Presets
  are exact.
- **ETA coverage.** Only 3 of 7 platforms publish a delivery estimate, so
  "soonest delivery" ranks the other four last regardless of reality. Netmeds and
  PharmEasy likely expose it on *product* pages -- a second call per result.

---

## Product direction: open question

The bot began as a **medicine** finder; DMart added FMCG. That raises a question
the code has not answered:

- **Medicine-first**, with FMCG as a bonus? (current shape)
- **A general price-comparison bot** across both categories?

It matters because matching is currently tuned for pharmacy naming -- `_GENERIC`
holds words like `tablet`, `syrup`, `mg`. Groceries have different conventions
(pack sizes, variants, multipacks), and the Colgate search already showed the
seam: ₹69 at DMart vs ₹455 at Netmeds, both correct, because the pack sizes
differ. A general-purpose bot would need variant-aware comparison.

---

## Smaller items

- **Multi-line search** -- treat each line as a separate product. Stretch goal
  from the original spec, never started.
- **`_GENERIC` tuning** -- driven by real usage; the list is a heuristic.
- **Rotate the bot token** if it has been exposed.
- **Netmeds ETA** -- the search payload has no delivery estimate; it would need a
  second call, so `eta` is honestly `None` today.

---

## Explicitly out of scope

Ordering/checkout automation, prescription handling, price history, alerts, group
features, monetization. Unchanged from the original spec.
