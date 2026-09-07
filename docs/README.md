# MedFinder — Documentation

A Telegram bot that compares product prices and availability across Indian
pharmacy and FMCG platforms.

## Start here

| Doc | Read it when |
|---|---|
| [01 — Architecture](01-architecture.md) | Understanding how the pieces fit, or adding a platform |
| [02 — Setup & Operations](02-setup-and-operations.md) | Running it, configuring it, or something is broken |
| [03 — Platform Research](03-platform-research.md) | **An adapter stopped working.** Every endpoint, field map and trap |
| [04 — Decision Log](04-decision-log.md) | Wondering why something is built the way it is |
| [05 — Bugs & Fixes](05-bugs-and-fixes.md) | Before touching matching or rendering |
| [06 — Testing](06-testing.md) | Verifying a change |
| [07 — Roadmap](07-roadmap.md) | Deciding what to build next |

The repo-root `README.md` is the 10-line quickstart. This set is the depth.

## Current state — 2026-09-07

**7 platforms live**, all re-verified today:

| Platform | Type | Auth | Location-aware |
|---|---|---|---|
| Tata 1mg | pharmacy | bundle token | ✅ city-scoped |
| Apollo | pharmacy | bundle token | ETA only |
| PharmEasy | pharmacy | none | ✗ national |
| Netmeds | pharmacy | none | ✗ national |
| DMart Ready | FMCG | none | ✗ national |
| Blinkit | quick-commerce | bundle token | ✅ lat/lon -> dark store |
| Swiggy Instamart | quick-commerce | none | ✅ per-branch storeId |

Stubbed: BigBasket, JioMart. Blocked: Zepto, Amazon, Meesho.

Blinkit and Instamart were previously recorded as blocked. The block was
**TLS fingerprinting**, not session state -- `curl_cffi` clears both with no
cookies at all. → [03](03-platform-research.md)

~6 runtime dependencies, single-file SQLite.

## The three things worth knowing

1. **The spec's scraping strategy did not survive contact with the live sites.**
   1mg and Apollo search pages are client-rendered empty shells. Every endpoint
   in use was found by grepping the sites' own JS bundles and confirmed with curl
   before a parser was written. → [03](03-platform-research.md)

2. **Fuzzy matching alone shows wrong brands as confident matches.** A
   "Cristello" query returned a Glutafine product as a confident hit. Scoring now
   requires the query's identifying tokens to actually appear -- and a one-letter
   typo is separated from a wrong brand by edit distance, not by score.
   → [B2](05-bugs-and-fixes.md#b2)

3. **A `✅` currently means "in stock", not "deliverable to your pincode"** on
   four of the five platforms. This is the most important open gap.
   → [07](07-roadmap.md)

## Conventions

- Each adapter keeps its URL, headers and tokens in **one constant block** at the
  top, marked `# VERIFY in DevTools if this breaks`.
- Adapters **never raise** -- they log and return `[]`, so one broken platform
  renders `⚠️` without taking down the board.
- **Never invent data.** Missing ETA is `None`, not a guess.
