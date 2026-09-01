# Testing

No test framework. Checks are runnable scripts and `assert`-based self-checks
that fail loudly -- proportionate to a personal tool, and enough to catch the two
things that actually break: **endpoint drift** and **matching regressions**.

## Layers

| Layer | What it proves | How to run |
|---|---|---|
| Adapter self-checks | The live endpoint still returns priced, linkable products | `python -m adapters.<name>` |
| Matching checks | Identity gate accepts the right product, rejects wrong brands | `test_identity.py` |
| Render/orchestrator | All board states render; HTML escaping is correct | `test_render.py` |
| Budget/location | Hung adapter is capped; location changes results | `test_budget.py` |

The `test_*.py` scripts live in the scratchpad, not the repo -- they hit live
endpoints and are diagnostic tools, not a CI suite.

## Adapter self-checks (most important)

Every adapter ends with:

```python
async def demo():
    res = await search("dolo 650", loc)
    assert res, "no results (endpoint likely re-pointed)"
    assert any(r.price and r.url.startswith("http") for r in res)
```

This is the canary for API changes. Run all five after any platform-side breakage
(see [02-setup-and-operations.md](02-setup-and-operations.md#health-checks)).

## What each check covers

**Matching** -- the regression guard for [B1](05-bugs-and-fixes.md) and
[B2](05-bugs-and-fixes.md#b2):
- `cristello ...` -> `is_match=False` on both pharmacy platforms (wrong brand
  must never render `✅`)
- `dolo 650` -> correct brand, `is_match=True` on all four pharmacy platforms
- DMart is excluded from the medicine assertion -- it is a grocery store and
  correctly has no medicines

**Render** -- exercises every board state:
live results · partial (one platform still checking) · one platform failed
(`⚠️` must not break the others) · nothing found · cached footer.
Also asserts no unescaped `&` survives, and that `15's` renders as `&#x27;`.

**Budget** -- injects a deliberately hung adapter and asserts:
- elapsed ≤ `SEARCH_BUDGET` + 1.5s
- the hung platform renders `⚠️` while the others still return
- switching location changes price/ETA (proves the plumbing is live, not
  cosmetic): Koramangala ₹32.10 / "7pm, Tomorrow" vs Delhi ₹30.30 / "45 mins"

**Persistence** -- write a location, re-open the DB in a fresh process, confirm
it survives. Covers the "location survives restart" acceptance criterion.

## Acceptance criteria status

| Criterion | Status |
|---|---|
| `/start` shows presets; tapping saves and confirms | ✅ |
| Location survives restart (SQLite) | ✅ verified in a fresh process |
| `dolo 650` returns a board <10s with 1mg name/price/link | ✅ ~0.7s for five platforms |
| `/location` switches; next search uses the new one | ✅ price and ETA both change |
| A failing platform shows `⚠️` without breaking others | ✅ |
| Runs with `pip install -r requirements.txt && python bot.py` | ✅ verified from a clean venv |

## Known gaps

- **The Telegram UI itself is not automated.** Handlers, keyboards and streaming
  edits are verified at the render level with fake update objects, not against
  the real client. `/start`, button taps and progressive edits were confirmed by
  hand.
- **No mocked-endpoint tests.** Every check hits live platforms, so they fail if
  the network is down or a site is having a bad day. Deliberate: the failure mode
  we actually care about *is* the live endpoint changing.
- **No coverage measurement.** Not warranted at this size.

## Manual smoke test

1. `/start` -> pick a preset -> confirms name + pincode
2. `dolo 650` -> four pharmacy platforms with prices and working links
3. `maggi noodles` -> DMart returns; pharmacy platforms say not-found
4. A junk brand (`cristello face wash`) -> `not found` + labelled `Similar:`
5. `/where` -> saved location; Ctrl-C, restart, `/where` again -> still there
6. `/location` -> switch preset -> re-search -> ETA/price differ
