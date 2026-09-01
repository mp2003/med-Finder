# Roadmap

Current state: **5 platforms live**, well past the original Phase 1 scope
(bot shell + 1mg + Apollo).

| Platform | Type | Status |
|---|---|---|
| 1mg | pharmacy | ✅ live |
| Apollo | pharmacy | ✅ live |
| PharmEasy | pharmacy | ✅ live |
| Netmeds | pharmacy | ✅ live |
| DMart Ready | FMCG | ✅ live |
| BigBasket | FMCG | stub -- needs store handshake |
| JioMart | FMCG | stub -- needs bundle-grep |
| Blinkit / Zepto / Instamart | quick-commerce | blocked (Phase 3) |

---

## Next: serviceability (recommended)

**The problem.** Only 1mg varies by location. Apollo's price, PharmEasy, Netmeds
and DMart are all **national** -- a `✅` currently means "in stock nationally",
not "deliverable to your pincode".

For a tool whose job is telling you where to buy something *near you*, that is
the weakest claim on the board. A wrong `✅` costs a wasted trip.

**Work.** Capture the "deliver to `<pincode>`" XHR each platform fires on a
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

## Phase 3: quick-commerce

Blinkit (403), Zepto and Instamart (202, empty body). These need **warm
per-location sessions**, exactly as the original spec predicted:

1. A real browser context with geolocation set per preset location, kept warm and
   reused
2. Or replicating the lat/lon-header XHRs if they are reachable without
   defeating protection

This is a separate session's work and pulls in a browser dependency the MVP
deliberately avoided. **Not** to be attempted by working around bot protection.

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
