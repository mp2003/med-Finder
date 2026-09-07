"""MedFinder -- Telegram bot: which platform has this medicine, near me?

Run: python bot.py   (long polling, no webhook/SSL needed)
"""
import asyncio
import html
import itertools
import logging
import os
import re
import sys
import time

from dotenv import load_dotenv
from telegram import (BotCommand, InlineKeyboardButton, InlineKeyboardMarkup,
                      KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove,
                      Update)
from telegram.error import BadRequest, Conflict
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import db
from adapters import ADAPTERS, COMING_SOON
from adapters.base import (ETA_UNKNOWN, QUICK, SLOW, Location, ProductResult,
                           eta_minutes, probe_name, search_wide)
from adapters.onemg import resolve_latlng
from config import (CACHE_TTL, DB_PATH, DELIVERY_COST, MEANINGFUL_SAVING,
                    PRESETS, SEARCH_BUDGET)
from matching import identity_ok, normalize, respell, typo_ok
from parsing import extract_items, search_term

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("medfinder")

# Conflict repeats every poll; warn once rather than flood the log.
_conflict_warned = False

# key=(normalized_query, pincode) -> (results, timestamp). In-process is enough
# for a handful of users; ponytail: swap for Redis only if this ever runs multi-process.
_cache: dict[tuple[str, str], tuple[list[ProductResult], float]] = {}

DISCLAIMER = ("Prices and availability are read from each platform at the time "
              "you search and can change before you order. Confirm on the "
              "platform's own page before placing an order.")


# ---------------------------------------------------------------- keyboards
def preset_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"{p['name']} — {p['pincode']}",
                                  callback_data=f"loc:{i}")]
            for i, p in enumerate(PRESETS)]
    return InlineKeyboardMarkup(rows)


LIVE_LOCATION_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("Share my current location", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True)

ASK_PRODUCT = ("Send the <b>product name</b> you want to check.\n\n"
               "For example: <code>Dolo 650</code>")


async def ask_location(update: Update, prefix: str = ""):
    """One message with the presets, plus the live-location keyboard.

    Telegram can't put inline buttons and a request_location button on the same
    message, so the second send is unavoidable -- but it carries no visible text
    beyond a hint, and the greeting is folded into the first.
    """
    chat = update.effective_chat
    await chat.send_message(
        f"{prefix}<b>Select a delivery location</b>\n"
        "Stock and delivery times are checked for the branch you pick.",
        parse_mode="HTML", reply_markup=preset_keyboard())
    await chat.send_message(
        "If you are somewhere else, share your current location instead.",
        reply_markup=LIVE_LOCATION_KB)


# ---------------------------------------------------------------- commands
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    loc = await db.get_location(update.effective_chat.id)
    if loc:
        # Already set up: don't re-ask, just confirm and get out of the way.
        return await update.message.reply_text(
            f"Delivery location is set to <b>{html.escape(loc.name)}</b> "
            f"({loc.pincode}).\n\n{ASK_PRODUCT}\n\n"
            "Use /location to deliver to a different branch.",
            parse_mode="HTML")
    await ask_location(
        update,
        "<b>MedFinder</b> checks a product across "
        f"{len(ADAPTERS)} pharmacy and grocery platforms, and reports where it "
        "is in stock, at what price, and how soon it arrives.\n\n")


async def cmd_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/location -- always re-open the picker; new choice overwrites the old."""
    await ask_location(update)


async def cmd_where(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/where -- show the saved location, or prompt if none is set."""
    loc = await db.get_location(update.effective_chat.id)
    if not loc:
        return await ask_location(update, "No delivery location saved yet.\n\n")
    await update.message.reply_text(
        f"Delivering to <b>{html.escape(loc.name)}</b>\n"
        f"{loc.pincode}, {html.escape(loc.city)}\n\n"
        "Use /location to change it.", parse_mode="HTML")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/help -- usage plus the price-staleness disclaimer."""
    await update.message.reply_text(
        "<b>How this works</b>\n\n"
        "Send a product name. You will be asked whether to rank the results by "
        "delivery speed or by price, then shown every platform that has it, "
        "with the price and pack size on each button. Tapping a button opens "
        "that product page.\n\n"
        "<b>Commands</b>\n"
        "/location — change the delivery branch\n"
        "/where — show the current branch\n\n"
        f"<i>{DISCLAIMER}</i>", parse_mode="HTML")


async def unknown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Any unrecognised /command."""
    await update.message.reply_text(
        "That command is not recognised. Send /help to see what is available.")


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Log one line instead of a full traceback, and tell the user.

    Without this, python-telegram-bot dumps the whole stack for every blip --
    a dropped wifi connection while polling filled the log with ~30 lines of
    httpx internals. Adapters already swallow their own failures, so anything
    reaching here is the bot itself: a network drop or a Telegram API error.
    """
    err = ctx.error
    if isinstance(err, Conflict):
        # Two bot processes polling the same token evict each other every few
        # seconds. Say it once, plainly, instead of flooding the log.
        global _conflict_warned
        if not _conflict_warned:
            _conflict_warned = True
            log.error("Another instance of this bot is already polling. "
                      "Stop one of them -- only one can run per token.")
        return
    log.error("handler error: %s: %s", type(err).__name__, err)
    # Best effort -- if the failure WAS the network, this send fails too.
    chat = getattr(update, "effective_chat", None)
    if chat:
        try:
            await chat.send_message(
                "Something went wrong on our side. Please try that again.")
        except Exception:
            pass


# ---------------------------------------------------------------- location
async def on_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    p = PRESETS[int(q.data.split(":")[1])]
    loc = Location(p["name"], p["lat"], p["lon"], p["pincode"], p["city"],
                   p.get("im_store"))
    # Toast at the top of the chat -- instant feedback on the tap itself.
    await q.answer(f"{loc.name} selected", show_alert=False)
    await db.set_location(q.message.chat_id, loc)
    # Arrived here from "Check another branch": re-run that search rather than
    # making the user retype the product name.
    repeat = ctx.user_data.pop("branch_query", None)
    if repeat:
        await q.edit_message_text(
            f"Location selected: <b>{html.escape(loc.name)}</b>",
            parse_mode="HTML")
        await q.message.reply_text(
            f"<b>{html.escape(loc.name)}</b> is now your delivery location.\n"
            f"{loc.pincode}, {html.escape(loc.city)}\n\n"
            f"Re-checking <b>{html.escape(repeat[0])}</b>.", parse_mode="HTML")
        return await do_search(q.message, repeat[0], q.message.chat_id,
                               urgent=repeat[1], ctx=ctx)
    # Retire the picker, then send the confirmation as its OWN message: an
    # in-place edit is easy to miss if the picker has scrolled out of view.
    await q.edit_message_text(
        f"Location selected: <b>{html.escape(loc.name)}</b>", parse_mode="HTML")
    await q.message.reply_text(
        f"<b>{html.escape(loc.name)}</b> is now your delivery location.\n"
        f"{loc.pincode}, {html.escape(loc.city)}\n\n{ASK_PRODUCT}",
        parse_mode="HTML",
        # Retire the "Share my current location" keyboard the picker put up.
        reply_markup=ReplyKeyboardRemove())


async def on_live_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Shared Telegram location -> resolve city+pincode via 1mg's latlng
    endpoint, so the user never has to type a pincode. Falls back to the
    preset picker if resolution fails."""
    lat, lon = update.message.location.latitude, update.message.location.longitude
    resolved = await resolve_latlng(lat, lon)
    if not resolved:
        return await update.message.reply_text(
            "That location could not be resolved. Please choose one of the "
            "branches instead.", reply_markup=preset_keyboard())
    city, pincode = resolved
    loc = Location("Shared location", lat, lon, pincode, city)
    await db.set_location(update.message.chat_id, loc)
    await update.message.reply_text(
        f"Delivery location set to <b>{pincode}, {html.escape(city)}</b>.\n"
        "Quick-commerce stock is approximate for a shared location; choose a "
        f"branch for exact availability.\n\n{ASK_PRODUCT}", parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove())


# ---------------------------------------------------------------- search
_PACK = re.compile(r"\(([^)]{1,22})\)\s*$")


def _pack(name: str) -> str:
    """Pack size, when the adapter appended one: 'Dolo 650 (15 tabs)'.

    Matters for comparison -- Rs 25 for 10 tablets is not cheaper than Rs 31
    for 15, but a bare price makes it look that way.
    """
    m = _PACK.search(name or "")
    return m.group(1) if m else ""


def _bucket(query: str, done: dict) -> tuple[list, list, list, list, list]:
    """Split the fan-out into (in stock, closest, missing, pending, out of stock).

    Buckets carry (platform, ProductResult) so the caller can build both the
    button and the summary line from one pass.
    """
    stocked, closest, missing, pending, oos = [], [], [], [], []
    for mod in ADAPTERS:
        p = mod.PLATFORM
        if p not in done:
            pending.append(p)
            continue
        results = done[p]
        if not results:            # None (failed) or [] (nothing returned)
            missing.append(p)
            continue
        r = results[0]
        if r.is_match and r.available:
            stocked.append((p, r))
        elif r.is_match:
            # Carried, just not right now -- distinct from "not stocked".
            oos.append(p)
        elif typo_ok(query, r.name):
            # One edit from the query: a mistyped name, not a different
            # product. Offered separately so it never reads as a confident hit.
            closest.append((p, r))
        else:
            missing.append(p)
    return stocked, closest, missing, pending, oos


def _label(platform: str, r: ProductResult) -> str:
    """Button caption. Telegram truncates long labels, so keep it tight."""
    bits = [platform]
    if r.price is not None:
        bits.append(f"Rs {r.price:g}")
    pack = _pack(r.name)
    if pack:
        bits.append(pack)
    eta = _eta_text(r.eta) if r.eta else ""
    if eta:
        bits.append(eta)
    return "  ".join(bits)


# Blinkit ships an internal delivery-class name rather than minutes. These are
# engineering labels, not user copy. Only the quick-commerce ones say anything
# about speed -- "longtail" and "pharma_rx" are catalogue tags, so they are
# dropped rather than dressed up as a delivery promise.
_FAST_WORDS = {"express", "instant", "earliest", "unicorn", "superfast",
               "rocket", "flash"}
_TAG_WORDS = {"longtail", "pharma_rx", "standard"}


def _eta_text(eta: str) -> str:
    """Human wording for an ETA, without inventing a number we do not have."""
    e = eta.strip().lower()
    if e in _FAST_WORDS:
        return "under 30 min"
    if e in _TAG_WORDS:
        return ""            # says nothing about timing; show no ETA at all
    return eta.strip()


def results_keyboard(query: str, done: dict, urgent: bool) -> InlineKeyboardMarkup:
    """A button per alternative supplier, plus a branch switch.

    The best fit is described in full in the caption, so it is not repeated
    here -- these buttons are the OTHER places you could buy it.
    """
    ranked = _ranked(query, done, urgent)
    rows = []
    if ranked and ranked[0][1].url:
        # The winner still needs a way to open it -- labelled so it is clearly
        # the one the caption just described.
        rows.append([InlineKeyboardButton(
            f"Open on {ranked[0][0]}", url=ranked[0][1].url)])
    rows += [[InlineKeyboardButton(_label(p, r), url=r.url)]
             for p, r in ranked[1:] if r.url]
    rows.append([InlineKeyboardButton("Check another branch",
                                      callback_data="branch")])
    return InlineKeyboardMarkup(rows)


def _sort_key(r: ProductResult, urgent: bool):
    """Soonest-first or cheapest-first, with the other field as tie-break."""
    price = r.price if r.price is not None else float("inf")
    eta = eta_minutes(r.eta, r.platform)
    return (eta, price) if urgent else (price, eta)


CAPTION_LIMIT = 1024   # Telegram's cap on a photo caption (text allows 4096)


def _ranked(query: str, done: dict, urgent: bool):
    """Every offer worth showing, best first: exact hits then near-matches.

    Near-matches trail exact ones so a mistyped query can never promote a
    "did you mean" result above a product we actually matched.
    """
    stocked, closest, _, _, _ = _bucket(query, done)
    stocked.sort(key=lambda pr: _sort_key(pr[1], urgent))
    closest.sort(key=lambda pr: _sort_key(pr[1], urgent))
    return stocked + closest


def _best_block(r: ProductResult, exact: bool) -> str:
    """The recommendation, written out. Everything else is a button."""
    head = "BEST OPTION" if exact else "CLOSEST MATCH"
    # Cap the name: some listings run to hundreds of characters, and the whole
    # block has to fit a 1024-char photo caption alongside everything else.
    name = r.name if len(r.name) <= 120 else r.name[:117].rstrip() + "..."
    # Labelled rows, one fact each: the eye can find "Price" or "Delivery"
    # without reading the whole block. Labels stay plain, values carry the
    # emphasis, so the values are what stands out.
    out = [f"<b>{head}</b>",
           f"Product    <b>{html.escape(name)}</b>",
           f"Supplier   <b><u>{html.escape(r.platform)}</u></b>"]

    # Delivery before price: when someone needs a medicine, when it arrives is
    # the deciding fact.
    eta = _eta_text(r.eta) if r.eta else ""
    out.append(f"Delivery   <b>{html.escape(eta)}</b>" if eta
               else "Delivery   <b>not published by this platform</b>")

    if r.price is not None:
        money = f"<b>Rs {r.price:g}</b>"
        # Show the strike-through only for a discount worth noticing; a 3%
        # gap is rounding, and dressing it up as a saving is noise.
        if r.mrp and r.mrp > r.price and (1 - r.price / r.mrp) >= 0.05:
            money += (f"   <s>Rs {r.mrp:g}</s>   "
                      f"<b>{(1 - r.price / r.mrp) * 100:.0f}% off</b>")
        out.append(f"Price      {money}")
    else:
        out.append("Price      <b>not listed</b>")

    pack = _pack(r.name)
    if pack:
        out.append(f"Pack       <b>{html.escape(pack)}</b>")
    out.append("Stock      <b>Available</b>")

    if not exact:
        out.append("\n<i>Not an exact match — check the name before "
                   "ordering.</i>")
    return "\n".join(out)


def render(query: str, loc: Location, done: dict, cached: bool,
           urgent: bool = False) -> str:
    """Board text: the winner in full, everything else summarised.

    Kept under CAPTION_LIMIT because this doubles as a photo caption.
    """
    stocked, closest, missing, pending, oos = _bucket(query, done)
    order = "soonest delivery" if urgent else "lowest price"
    head = (f"<b>{html.escape(query)}</b>\n"
            f"{html.escape(loc.name)} ({loc.pincode}) — ordered by {order}")

    lines = []
    ranked = _ranked(query, done, urgent)
    if ranked:
        best_platform, best = ranked[0]
        lines.append(_best_block(best, exact=bool(stocked)))
        others = len(ranked) - 1
        if others:
            lines.append(f"Also available from {others} other "
                         f"{'supplier' if others == 1 else 'suppliers'} — "
                         "tap to open:")
        priced = [r for _, r in ranked if r.price is not None]
        if len(priced) > 1:
            lo = min(priced, key=lambda r: r.price)
            hi = max(priced, key=lambda r: r.price)
            # Only worth saying when the gap would actually change a decision;
            # "Rs 0.10 below the highest" is noise.
            if (lo.platform != best.platform
                    and hi.price - lo.price >= 5):
                lines.append(f"<u>{html.escape(lo.platform)}</u> is cheapest "
                             f"overall at "
                             f"Rs {lo.price:g}, Rs {hi.price - lo.price:.2f} "
                             "below the highest.")
    if closest and stocked:
        lines.append(f"No exact match on {_join([p for p, _ in closest])}, but a "
                     f"close product was found — check the name before ordering.")
    if not stocked and not closest and not pending:
        lines.append(
            f"Currently out of stock at {_join(oos)}, and not carried elsewhere."
            if oos else
            f"Not found on any of the {len(ADAPTERS)} platforms checked.")
    elif oos:
        lines.append(f"Carried but out of stock at {_join(oos)}.")
    if missing:
        lines.append(f"Not stocked at {_join(missing)}.")
    soon = [m.PLATFORM for m in COMING_SOON]
    if soon:
        lines.append(f"Not yet supported: {_join(soon)}.")

    foot = "\n\nShowing recent results." if cached else ""
    out = head + "\n\n" + "\n\n".join(lines) + foot
    # Drop trailing summary lines rather than cut mid-tag and break the HTML
    # parse -- but never the first line, which carries the recommendation.
    while len(out) > CAPTION_LIMIT and len(lines) > 1:
        lines.pop()
        out = head + "\n\n" + "\n\n".join(lines) + foot
    return out


def _join(names: list[str]) -> str:
    """'A', 'A and B', 'A, B and C' -- reads as prose, not a CSV dump.

    Platform names are underlined wherever they appear, so they stand out from
    the surrounding sentence.
    """
    names = [f"<u>{html.escape(n)}</u>" for n in names]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def best_image(query: str, done: dict, urgent: bool) -> str | None:
    """Photo of the top result, if any platform supplied one.

    Not every product has an image on every platform (PharmEasy's Dolo Xtraa
    has none), so this walks the ranked results and takes the first that does.
    """
    stocked, closest, _, _, _ = _bucket(query, done)
    ranked = sorted(stocked + closest, key=lambda pr: _sort_key(pr[1], urgent))
    return next((r.image for _, r in ranked if r.image), None)


async def ask_urgency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Plain text = a product name. Ask how to rank before searching."""
    query = (update.message.text or "").strip()
    if not query:
        return
    loc = await db.get_location(update.effective_chat.id)
    if not loc:
        return await ask_location(
            update, "Choose a delivery location before searching.\n\n")

    # An order message names several things; a plain search names one. Two or
    # more items takes the list flow, one keeps today's behaviour exactly.
    items = extract_items(query)
    if len(items) > 1:
        return await confirm_list(update, ctx, items)

    # Keyed per chat so a second search cannot answer the first one's prompt.
    ctx.user_data["pending_query"] = query
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Soonest delivery", callback_data="urg:1")],
        [InlineKeyboardButton("Lowest price", callback_data="urg:0")],
    ])
    await update.message.reply_text(
        f"<b>{html.escape(query)}</b>\n\n"
        "How should the results be ordered?",
        reply_markup=kb, parse_mode="HTML")


async def on_urgency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Urgency answered -> run the search with that ranking."""
    q = update.callback_query
    await q.answer()
    query = ctx.user_data.pop("pending_query", None)
    if not query:
        return await q.edit_message_text(
            "That search has expired. Send the product name again.")
    await q.edit_message_reply_markup(reply_markup=None)
    await do_search(q.message, query, update.effective_chat.id,
                    urgent=q.data == "urg:1", ctx=ctx)


async def on_branch(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """'Check another branch' -> pick a location, then re-run the same query."""
    q = update.callback_query
    await q.answer()
    last = ctx.user_data.get("last_search")
    if not last:
        return await q.message.reply_text(
            "Send the product name again to run a new search.")
    ctx.user_data["branch_query"] = last
    await q.message.reply_text(
        f"Checking <b>{html.escape(last[0])}</b> at a different branch.\n"
        "Select the delivery location.",
        parse_mode="HTML", reply_markup=preset_keyboard())


async def do_search(message, query: str, chat_id: int, urgent: bool, ctx=None):
    """Fan out to every live adapter and edit the board as each one lands.

    Serves from cache when fresh. Takes an explicit message/query rather than
    an Update, since it is driven by the urgency callback, not a raw message.
    ctx is optional so the "check another branch" button can remember what to
    re-run.
    """
    loc = await db.get_location(chat_id)
    if not loc:
        return
    if ctx is not None:
        ctx.user_data["last_search"] = (query, urgent)

    async def finish(done: dict, cached: bool):
        """Replace the placeholder with the final board.

        Sent as ONE photo message when an image exists -- caption and buttons
        ride along, so the picture is always above the text. A text message
        cannot be edited into a photo one, hence the delete-and-resend.
        """
        text = render(query, loc, done, cached=cached, urgent=urgent)
        kb = results_keyboard(query, done, urgent)
        img = best_image(query, done, urgent)
        if img:
            try:
                await message.reply_photo(img, caption=text, parse_mode="HTML",
                                          reply_markup=kb)
                try:
                    await msg.delete()
                except Exception:
                    pass            # placeholder too old to delete; harmless
                return
            except Exception as e:  # dead CDN link must not lose the board
                log.info("photo send failed, falling back to text: %s", e)
        try:
            await msg.edit_text(text, parse_mode="HTML",
                                disable_web_page_preview=True, reply_markup=kb)
        except BadRequest as e:
            # "Message is not modified" -- a platform returned nothing, so the
            # board is byte-identical to the last edit. Expected, not an error.
            if "not modified" not in str(e).lower():
                raise

    key = (normalize(query), loc.pincode)
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < CACHE_TTL:
        done = {m.PLATFORM: [r for r in hit[0] if r.platform == m.PLATFORM]
                for m in ADAPTERS}
        msg = await message.reply_text(
            render(query, loc, done, cached=True, urgent=urgent),
            parse_mode="HTML", disable_web_page_preview=True)
        return await finish(done, cached=True)

    # A single static notice while the fan-out runs. The board used to be
    # re-rendered on every adapter that landed, which made the message twitch
    # and reshuffle under the reader -- now it is drawn once, at the end.
    msg = await message.reply_text(
        f"Checking <b>{html.escape(query)}</b> across {len(ADAPTERS)} "
        f"platforms for {html.escape(loc.name)}.\nOne moment.",
        parse_mode="HTML", disable_web_page_preview=True)

    done: dict[str, list | None] = {}
    tasks = {asyncio.create_task(search_wide(m, query, loc)): m
             for m in ADAPTERS}
    deadline = time.time() + SEARCH_BUDGET
    pending = set(tasks)
    while pending:
        left = deadline - time.time()
        if left <= 0:
            break
        finished, pending = await asyncio.wait(
            pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        if not finished:
            break
        for t in finished:
            mod = tasks[t]
            try:
                done[mod.PLATFORM] = t.result()
            except Exception as e:
                log.warning("%s task failed: %s", mod.PLATFORM, e)
                done[mod.PLATFORM] = None
    for t in pending:  # over budget -> render as failed
        t.cancel()
        done.setdefault(tasks[t].PLATFORM, None)
    done = await _probe_retry(done, query, loc, deadline)
    done = await _respell_retry(done, query, loc, deadline)

    ok = [r for v in done.values() if v for r in v]
    if ok:
        _cache[key] = (ok, time.time())
    try:
        await finish(done, cached=False)
    except Exception as e:
        log.warning("final render failed: %s", e)


# ---------------------------------------------------------------- order lists
async def confirm_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE,
                       items: list[str]):
    """Show what was extracted before spending a fan-out on it.

    Extraction is a heuristic over human prose, so the user confirms before
    N items x 7 platforms of live calls run.
    """
    ctx.user_data["batch"] = items
    lines = []
    for i, it in enumerate(items, 1):
        term = search_term(it)
        # Say when the search differs from what was written -- a stripped
        # strength changes what comes back, and the user should see that.
        extra = f"   (searching \"{html.escape(term)}\")" if term != it else ""
        lines.append(f"{i}. <b>{html.escape(it)}</b>{extra}")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"Yes, check all {len(items)}",
                              callback_data="list:go")],
        [InlineKeyboardButton("Cancel", callback_data="list:no")],
    ])
    await update.message.reply_text(
        f"Found <b>{len(items)} items</b> in that message:\n\n"
        + "\n".join(lines) + "\n\nIs that right?",
        parse_mode="HTML", reply_markup=kb)


async def on_confirm(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Confirmation answered -> ask the one ranking question, or drop it."""
    q = update.callback_query
    await q.answer()
    if q.data == "list:no":
        ctx.user_data.pop("batch", None)
        return await q.edit_message_text(
            "Cancelled. Send the list again, or one product name.")
    items = ctx.user_data.get("batch")
    if not items:
        return await q.edit_message_text(
            "That list has expired. Send the message again.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("Soonest delivery", callback_data="lurg:1")],
        [InlineKeyboardButton("Lowest price", callback_data="lurg:0")],
    ])
    await q.edit_message_text(
        f"Checking <b>{len(items)} items</b>.\n\n"
        "How should the results be ordered?",
        parse_mode="HTML", reply_markup=kb)


async def on_list_urgency(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ranking chosen -> run the fan-out for every item."""
    q = update.callback_query
    await q.answer()
    items = ctx.user_data.get("batch")
    if not items:
        return await q.edit_message_text(
            "That list has expired. Send the message again.")
    urgent = q.data == "lurg:1"
    ctx.user_data["batch_urgent"] = urgent
    loc = await db.get_location(q.message.chat_id)
    if not loc:
        return await q.edit_message_text("Set a delivery location first.")

    await q.edit_message_text(
        f"Checking <b>{len(items)} items</b> across {len(ADAPTERS)} platforms "
        f"for {html.escape(loc.name)}.\nThis takes a moment.",
        parse_mode="HTML")

    # Items sequentially, platforms in parallel: these APIs throttle hard and a
    # burst of N x 7 simultaneous calls gets us rate-limited.
    results = {}
    for it in items:
        results[it] = await _search_one(search_term(it), loc)

    ctx.user_data["batch_results"] = results
    await _report(q.message, ctx, items, results, urgent)


async def _probe_retry(done: dict, query: str, loc: Location, deadline: float):
    """Second chance when EVERY platform came back empty.

    A glued brand ("lidoplast" for Lido-Plast) is a query no platform matches,
    so there is nothing for matching.py to rescue -- the fix has to happen
    before the fan-out. One platform is asked every split spelling at once; if
    it names the product, that name is re-fanned to all adapters.

    Costs nothing on a normal search: it only runs when the search had already
    failed completely, and it respects the caller's remaining budget.
    """
    if any(done.get(m.PLATFORM) for m in ADAPTERS):
        return done
    if deadline - time.time() < 2:          # no room left to be useful
        return done
    probe = ADAPTERS[0]                     # 1mg: fastest and most tolerant
    try:
        name = await probe_name(probe, query, loc)
    except Exception as e:
        log.warning("probe failed for %r: %s", query, e)
        return done
    if not name:
        return done
    log.info("probe resolved %r -> %r", query, name)
    return await _refan(done, query, name, loc, deadline)


async def _refan(done: dict, query: str, better: str, loc: Location,
                 deadline: float):
    """Re-ask every platform with a corrected query, keeping what improves.

    Results are gated against the ORIGINAL query, never the corrected one:
    top_matches inside the adapter judged against `better`, and the user asked
    for `query`. A platform that already had results keeps them unless the
    retry does strictly better, so a correction can only add.
    """
    retry = {asyncio.create_task(m.search(better, loc)): m for m in ADAPTERS}
    left = max(0.0, deadline - time.time())
    finished, pending = await asyncio.wait(retry, timeout=left)
    for t in pending:
        t.cancel()
    for t in finished:
        mod = retry[t]
        try:
            got = [r for r in t.result()
                   if identity_ok(query, r.name) or typo_ok(query, r.name)]
        except Exception:
            continue
        if got and len(got) > len(done.get(mod.PLATFORM) or []):
            done[mod.PLATFORM] = got
    return done


async def _respell_retry(done: dict, query: str, loc: Location,
                         deadline: float):
    """Second chance when the platforms returned the product under a DIFFERENT
    spelling -- "paracitamol" answered by "Paracetamol 500mg Tablet".

    Unlike the probe this fires on a PARTIAL result: one tolerant platform
    found it and six did not, so there is a title to learn the real spelling
    from. Correcting and re-asking took "crocine" from 2 results to 15.
    """
    titles = [r.name for v in done.values() if v for r in v]
    if not titles or deadline - time.time() < 2:
        return done
    better = respell(query, titles)
    if not better:
        return done
    log.info("respelt %r -> %r", query, better)
    return await _refan(done, query, better, loc, deadline)


async def _search_one(term: str, loc: Location) -> dict:
    """One item across every adapter. Mirrors do_search's fan-out and budget."""
    done: dict[str, list | None] = {}
    tasks = {asyncio.create_task(search_wide(m, term, loc)): m
             for m in ADAPTERS}
    deadline = time.time() + SEARCH_BUDGET
    pending = set(tasks)
    while pending:
        left = deadline - time.time()
        if left <= 0:
            break
        finished, pending = await asyncio.wait(
            pending, timeout=left, return_when=asyncio.FIRST_COMPLETED)
        if not finished:
            break
        for t in finished:
            mod = tasks[t]
            try:
                done[mod.PLATFORM] = t.result()
            except Exception as e:
                log.warning("%s failed for %r: %s", mod.PLATFORM, term, e)
                done[mod.PLATFORM] = None
    for t in pending:
        t.cancel()
        done.setdefault(tasks[t].PLATFORM, None)
    done = await _probe_retry(done, term, loc, deadline)
    return await _respell_retry(done, term, loc, deadline)


def _offers(item: str, done: dict) -> dict:
    """platform -> the best in-stock ProductResult it has for this item."""
    out = {}
    for platform, results in (done or {}).items():
        if not results:
            continue
        r = results[0]
        if r.is_match and r.available:
            out[platform] = r
    return out


def _plan_cost(picked: dict) -> tuple[int, float]:
    """(unpriced lines, rupees). A missing price is NOT free: the old
    `sum(r.price or 0)` scored it as 0, so an item with no published price
    made a whole basket look cheapest and win. _sort_key calls the same None
    float("inf"); ranking on the count first keeps the two consistent without
    letting an infinity swallow every comparison."""
    rs = sum(r.price for _, r in picked.values() if r.price is not None)
    return sum(r.price is None for _, r in picked.values()), rs


def _plan_eta(picked: dict) -> int:
    """Slowest KNOWN eta. eta_minutes returns ETA_UNKNOWN for anything it
    cannot parse, and three live adapters never send one, so a plain max()
    sinks an otherwise-fine plan on a single unpublished eta."""
    known = [e for e in (eta_minutes(r.eta, r.platform)
                         for _, r in picked.values())
             if e < ETA_UNKNOWN]
    return max(known) if known else ETA_UNKNOWN


def _mixed_speed(platforms) -> bool:
    """True when one leg is 30-minute and another is days away."""
    used = set(platforms)
    return bool(QUICK & used) and bool(SLOW & used)


def plans(items: list[str], results: dict, urgent: bool):
    """Every way to buy the stocked items, best first.

    Returns [(platforms, {item: (platform, result)}, n_unpriced, rupees)].
    Items stocked nowhere are dropped rather than making the order impossible.

    Exhaustive over every platform subset -- 2**7 = 128 of them, ~33us -- so
    this is the exact optimum. Greedy set-cover is a ln(n) approximation and
    is wrong on cases this small: it takes the platform covering the most
    items, which can force a third order where two sufficed.
    ponytail: 2**len(ADAPTERS). Fine to ~14 platforms, then go greedy.
    """
    per_item = {it: _offers(it, results.get(it, {})) for it in items}
    per_item = {it: o for it, o in per_item.items() if o}
    universe = sorted({p for o in per_item.values() for p in o})
    out = []
    for k in range(1, len(universe) + 1):
        for combo in itertools.combinations(universe, k):
            picked = {}
            for it, offers in per_item.items():
                cand = [(p, offers[p]) for p in combo if p in offers]
                if not cand:
                    break                      # this subset misses an item
                picked[it] = min(cand, key=lambda pr: _sort_key(pr[1], urgent))
            else:
                used = sorted({p for p, _ in picked.values()})
                # Drop subsets where a platform won nothing: that plan buys
                # exactly what a smaller one does but gets charged another
                # DELIVERY_COST, and without this the "other plans" list fills
                # with near-duplicates.
                if len(used) == k:
                    n_none, rs = _plan_cost(picked)
                    out.append((used, picked, n_none, rs))
    if urgent:
        out.sort(key=lambda pl: (_plan_eta(pl[1]), pl[2], _allin(pl),
                                 len(pl[0]), pl[0]))
    else:
        out.sort(key=lambda pl: (pl[2], _allin(pl), len(pl[0]), pl[0]))
    return out


def _allin(plan) -> float:
    """Item prices plus one delivery per order -- the number the user pays."""
    return plan[3] + DELIVERY_COST * len(plan[0])


def best_plan(all_plans: list, urgent: bool = False):
    """Fewest orders, unless splitting saves real money.

    Pure cost would split a Rs 540 single order to save Rs 10, which is not
    worth a second delivery to receive and track. So: among plans within
    MEANINGFUL_SAVING of the cheapest, take the one with the fewest orders.

    When the user asked for soonest delivery, plans() has already sorted on
    eta and its answer stands -- applying the cost rule here too would hand
    back a 3-hour order when they said they were in a hurry.
    """
    if not all_plans:
        return None
    if urgent:
        return all_plans[0]
    # Compare on cost only among plans that price everything: an unpriced line
    # contributes 0 rupees, so a plan carrying one looks cheaper than it is and
    # would win a comparison it never earned. plans() already sorts those last.
    fewest_unknown = min(pl[2] for pl in all_plans)
    pool = [pl for pl in all_plans if pl[2] == fewest_unknown]
    cheapest = min(_allin(pl) for pl in pool)
    near = [pl for pl in pool if _allin(pl) <= cheapest + MEANINGFUL_SAVING]
    return min(near, key=lambda pl: (len(pl[0]), _allin(pl), pl[0]))


def _plan_lines(plan, items, urgent) -> list[str]:
    """One plan, rendered. Shared by the first report and every re-render from
    'Show other plans' -- two renderers would drift apart."""
    used, picked, n_none, rupees = plan
    order = "soonest delivery" if urgent else "lowest price"
    found = len(picked)
    scope = f"{found} of {len(items)} items, " if found != len(items) else ""
    lines = [f"<b>BEST PLAN — {len(used)} "
             f"{'order' if len(used) == 1 else 'orders'}</b>"
             f"   ({scope}by {order})", ""]

    for platform in used:
        mine = [(it, r) for it, (p, r) in picked.items() if p == platform]
        # Apollo prices delivery PER PRODUCT ("29 mins" on one line, "3 hr" on
        # the next), so the first line's eta is not the order's. Show the
        # slowest -- that is when the order actually lands.
        etas = [(eta_minutes(r.eta, r.platform), _eta_text(r.eta))
                for _, r in mine if r.eta]
        etas = [e for e in etas if e[1]]        # catalogue tags say nothing
        # Blinkit/Instamart publish tags or nothing at all, but we know the
        # delivery model, so name it rather than leaving the order undated.
        eta = max(etas)[1] if etas else (
            "under 30 min" if platform in QUICK else "")
        lines.append(f"<b><u>{html.escape(platform)}</u></b>   {len(mine)} "
                     f"{'item' if len(mine) == 1 else 'items'}"
                     + (f"   {html.escape(eta)}" if eta else ""))
        for it, r in mine:
            price = f"Rs {r.price:g}" if r.price is not None else "price n/a"
            lines.append(f"{html.escape(it)} — <b>{price}</b>")
            lines.append(html.escape(r.url))
        lines.append("")

    if _mixed_speed(used):
        fast = _join([html.escape(p) for p in used if p in QUICK])
        slow = _join([html.escape(p) for p in used if p in SLOW])
        lines.append(f"<b>Note</b>   These arrive at different times — {fast} "
                     f"in about 30 minutes, {slow} in a few days. Order "
                     "separately if you need one sooner.")
        lines.append("")

    missing = [it for it in items if it not in picked]
    if missing:
        lines.append("Not stocked anywhere: "
                     + _join([html.escape(m) for m in missing]) + ".")
        lines.append("")

    # DELIVERY_COST still drives the grouping -- it is just not shown, since
    # it is our estimate rather than a fee any platform quoted.
    lines.append(f"<b>Items   Rs {rupees:g}</b>"
                 + (f"   ({n_none} without a published price)" if n_none else ""))
    return lines


def _plan_rows(ctx) -> list[list[InlineKeyboardButton]]:
    """Buttons under a plan. 'Item by item' reuses the existing walk handler."""
    rows = []
    if len(ctx.user_data.get("batch_plans") or []) > 1:
        rows.append([InlineKeyboardButton("Show other plans",
                                          callback_data="plan:list")])
    rows.append([InlineKeyboardButton("Item by item", callback_data="walk:y")])
    return rows


def _alt_rows(ctx) -> list[list[InlineKeyboardButton]]:
    """The alternatives keyboard. Capped at 5 -- nobody reads a sixth."""
    rows = []
    for i, pl in enumerate((ctx.user_data.get("batch_plans") or [])[:5]):
        # Item price only, and never the all-in figure: these are ranked by
        # cost INCLUDING delivery, so showing item totals makes the winning
        # plan look dearer than the ones it beats.
        label = (f"{len(pl[0])} {'order' if len(pl[0]) == 1 else 'orders'} — "
                 f"{', '.join(pl[0])}   items Rs {pl[3]:g}")
        rows.append([InlineKeyboardButton(label[:60], callback_data=f"plan:{i}")])
    return rows


async def _report(message, ctx, items, results, urgent):
    """Recommend the cheapest grouping, counting one delivery per platform."""
    all_plans = plans(items, results, urgent)
    ctx.user_data["batch_plans"] = all_plans

    if not all_plans:
        ctx.user_data.pop("batch", None)
        return await message.reply_text(
            f"None of those {len(items)} items are stocked at any platform "
            "we can reach. Check the spelling, or send them one at a time.")

    top = best_plan(all_plans, urgent)
    ctx.user_data["batch_plan_i"] = all_plans.index(top)
    for it, (platform, r) in top[1].items():
        await db.save_pick(message.chat_id, it, platform, r.price, r.url)
    await message.reply_text(
        "\n".join(_plan_lines(top, items, urgent)), parse_mode="HTML",
        disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(_plan_rows(ctx)))


async def on_plan(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """'Show other plans', and switching between them. Re-renders in place --
    a new message per tap would bury the plan the user is comparing against."""
    q = update.callback_query
    await q.answer()
    all_plans = ctx.user_data.get("batch_plans")
    if not all_plans:
        return await q.edit_message_text("That list has expired. "
                                         "Send the message again.")
    if q.data == "plan:list":
        return await q.edit_message_reply_markup(
            reply_markup=InlineKeyboardMarkup(_alt_rows(ctx)))

    i = int(q.data.split(":")[1])
    if i >= len(all_plans):
        return await q.edit_message_text("That plan is no longer available.")
    ctx.user_data["batch_plan_i"] = i
    items = ctx.user_data.get("batch") or []
    urgent = ctx.user_data.get("batch_urgent", False)
    await q.edit_message_text(
        "\n".join(_plan_lines(all_plans[i], items, urgent)),
        parse_mode="HTML", disable_web_page_preview=True,
        reply_markup=InlineKeyboardMarkup(_plan_rows(ctx)))


async def on_walk(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Answer to 'want to see every supplier?' -- start the walk or stop."""
    q = update.callback_query
    await q.answer()
    await q.edit_message_reply_markup(reply_markup=None)   # spend the buttons
    if q.data == "walk:n" or not ctx.user_data.get("batch"):
        for k in ("batch", "batch_i", "batch_picks", "batch_results",
                  "batch_offers", "batch_urgent", "batch_plans",
                  "batch_plan_i"):
            ctx.user_data.pop(k, None)
        return await q.message.reply_text(
            "Links above are the best option for each item. "
            "Send another list any time.")

    ctx.user_data["batch_i"] = 0
    ctx.user_data["batch_picks"] = {}
    await q.message.reply_text("Going through them one at a time.")
    await _next_item(q.message, ctx)


async def _next_item(message, ctx):
    """Show the next item's board, or the summary when the list is done."""
    items = ctx.user_data.get("batch") or []
    i = ctx.user_data.get("batch_i", 0)
    results = ctx.user_data.get("batch_results") or {}
    urgent = ctx.user_data.get("batch_urgent", False)

    while i < len(items) and not _offers(items[i], results.get(items[i], {})):
        i += 1                      # nothing stocks it; nothing to pick
    if i >= len(items):
        return await _summary(message, ctx)

    ctx.user_data["batch_i"] = i
    item = items[i]
    offers = _offers(item, results.get(item, {}))
    ranked = sorted(offers.items(), key=lambda kv: _sort_key(kv[1], urgent))
    ctx.user_data["batch_offers"] = [p for p, _ in ranked]

    rows = [[InlineKeyboardButton(_label(p, r), callback_data=f"pick:{i}:{n}")]
            for n, (p, r) in enumerate(ranked)]
    rows.append([InlineKeyboardButton("Skip this item",
                                      callback_data=f"pick:{i}:x")])
    await message.reply_text(
        f"<b>Item {i + 1} of {len(items)}: {html.escape(item)}</b>\n"
        "Which supplier?", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(rows))


async def on_pick(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """A supplier chosen for one item -> record it and move on."""
    q = update.callback_query
    _, idx, choice = q.data.split(":")
    i = int(idx)
    items = ctx.user_data.get("batch") or []
    if i >= len(items):
        await q.answer()
        return await q.edit_message_text("That list has expired.")
    item = items[i]

    if choice == "x":
        await q.answer("Skipped")
        await q.edit_message_text(f"{html.escape(item)} — skipped.",
                                  parse_mode="HTML")
    else:
        platform = (ctx.user_data.get("batch_offers") or [])[int(choice)]
        r = _offers(item, (ctx.user_data.get("batch_results") or {})
                    .get(item, {}))[platform]
        ctx.user_data.setdefault("batch_picks", {})[item] = (platform, r)
        await db.save_pick(q.message.chat_id, item, platform, r.price, r.url)
        await q.answer(f"{platform} selected")
        await q.edit_message_text(
            f"{html.escape(item)} — <b><u>{html.escape(platform)}</u></b>",
            parse_mode="HTML")

    ctx.user_data["batch_i"] = i + 1
    await _next_item(q.message, ctx)


async def _summary(message, ctx):
    """Plain-text recap of what was chosen. No buttons, per the user."""
    items = ctx.user_data.get("batch") or []
    picks = ctx.user_data.get("batch_picks") or {}
    lines = [f"<b>ORDER SUMMARY — {len(items)} items</b>", ""]
    total = 0.0
    for n, it in enumerate(items, 1):
        got = picks.get(it)
        if not got:
            lines.append(f"{n}. {html.escape(it)}\n   Not ordered.")
            continue
        platform, r = got
        price = f"Rs {r.price:g}" if r.price is not None else "price n/a"
        eta = _eta_text(r.eta) if r.eta else ""
        total += r.price or 0
        lines.append(f"{n}. <b>{html.escape(it)}</b>\n"
                     f"   <u>{html.escape(platform)}</u> — {price}"
                     + (f" — {html.escape(eta)}" if eta else "")
                     + f"\n   {html.escape(r.url)}")
        lines.append("")
    if total:
        lines.append(f"<b>Total   Rs {total:.2f}</b>")
        lines.append("<i>Across different platforms — these cannot be "
                     "ordered together.</i>")
    for k in ("batch", "batch_i", "batch_picks", "batch_results",
              "batch_offers", "batch_urgent", "batch_plans", "batch_plan_i"):
        ctx.user_data.pop(k, None)
    await message.reply_text("\n".join(lines), parse_mode="HTML",
                             disable_web_page_preview=True)


# ---------------------------------------------------------------- main
# Shown in Telegram's blue Menu button and command autocomplete. Registering
# handlers is NOT enough -- Telegram only lists what is set here. Keep this in
# step with the CommandHandlers below.
COMMANDS = [
    BotCommand("start", "Set up and pick a delivery location"),
    BotCommand("location", "Change delivery location"),
    BotCommand("where", "Show the saved location"),
    BotCommand("help", "How this works"),
]


async def post_init(app: Application) -> None:
    """Open the SQLite store and publish the command menu."""
    await db.init()
    await app.bot.set_my_commands(COMMANDS)


def main():
    load_dotenv()
    # Telegram allows ONE poller per token, so a local run and the deployed one
    # evict each other every few seconds. --dev points at a second bot from
    # @BotFather instead, letting both run at once.
    dev = "--dev" in sys.argv
    var = "DEV_BOT_TOKEN" if dev else "BOT_TOKEN"
    token = (os.getenv(var) or "").strip().strip('"\'')
    if not token or token == "paste-here":
        # Name both fixes: on a host there is no .env, and pointing at one
        # sends you looking for a file that does not exist.
        sys.exit(
            f"{var} not set.\n"
            f"  Locally: put it in .env  ->  {var}=8123456789:AAF...\n"
            "  On a host (Railway/Fly): set BOT_TOKEN as a service environment\n"
            "  variable, then redeploy -- variables are injected at container\n"
            "  start, so an already-running container will not pick it up."
            + ("\n  --dev needs a SECOND bot from @BotFather, not the same "
               "token." if dev else ""))
    if ":" not in token:
        sys.exit(f"{var} looks malformed ({token[:6]}...). Expected "
                 "<digits>:<letters>, e.g. 8123456789:AAF...")
    if dev:
        log.info("DEV MODE — using DEV_BOT_TOKEN, db=%s", DB_PATH)

    app = Application.builder().token(token).post_init(post_init).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("location", cmd_location))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_preset, pattern=r"^loc:"))
    app.add_handler(CallbackQueryHandler(on_urgency, pattern=r"^urg:"))
    app.add_handler(CallbackQueryHandler(on_branch, pattern=r"^branch$"))
    app.add_handler(CallbackQueryHandler(on_confirm, pattern=r"^list:"))
    app.add_handler(CallbackQueryHandler(on_list_urgency, pattern=r"^lurg:"))
    app.add_handler(CallbackQueryHandler(on_pick, pattern=r"^pick:"))
    app.add_handler(CallbackQueryHandler(on_walk, pattern=r"^walk:"))
    app.add_handler(CallbackQueryHandler(on_plan, pattern=r"^plan:"))
    app.add_handler(MessageHandler(filters.LOCATION, on_live_location))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ask_urgency))
    app.add_error_handler(on_error)
    log.info("MedFinder up — polling…")
    app.run_polling()


def demo():
    """Self-check for the order grouper. Run: python bot.py --demo

    The grouper decides real money, so it gets the one runnable check: case 6
    brute-forces every item->platform assignment and asserts we found the true
    optimum, which is the only way to catch a bad sort key.
    """
    def R(platform, price, eta="30 mins"):
        return ProductResult(platform, "x", price, None, True, eta,
                             f"http://{platform}/x", 100.0)

    def mk(spec):
        """{item: {platform: price}} -> the results shape plans() expects."""
        return {it: {p: [R(p, pr)] for p, pr in offers.items()}
                for it, offers in spec.items()}

    # 1. Two orders beat four. Per-item-cheapest picks C,D,E,F = Rs 132 of
    #    items but FOUR deliveries (Rs 292 all-in); A+B pays Rs 8 more on
    #    items and lands at Rs 220.
    items = ["i1", "i2", "i3", "i4"]
    res = mk({"i1": {"A": 30, "C": 28}, "i2": {"A": 20, "D": 18},
              "i3": {"B": 50, "E": 48}, "i4": {"B": 40, "F": 38}})
    got = plans(items, res, urgent=False)
    assert [tuple(pl[0]) for pl in got] == sorted(
        {tuple(pl[0]) for pl in got}, key=[tuple(pl[0]) for pl in got].index), \
        "duplicate platform-sets in the plan list"
    top = best_plan(got)
    assert top[0] == ["A", "B"], top[0]
    assert _allin(top) == 220, _allin(top)

    # 2. Fewer orders wins a trivial saving; real money wins the extra order.
    close = plans(["i1", "i2"],
                  mk({"i1": {"Z": 250, "A": 250}, "i2": {"Z": 250, "B": 200}}),
                  urgent=False)
    assert len(best_plan(close)[0]) == 1, "split an order to save Rs 10"
    worth = plans(["i1", "i2"],
                  mk({"i1": {"Z": 250, "A": 250}, "i2": {"Z": 330, "B": 200}}),
                  urgent=False)
    assert len(best_plan(worth)[0]) == 2, "paid Rs 90 to avoid a 2nd order"

    # 3. price=None is not free -- the `r.price or 0` bug.
    p3 = best_plan(plans(["i1", "i2"],
                         mk({"i1": {"A": None, "B": 10}, "i2": {"A": 5, "B": 5}}),
                         urgent=False))
    assert p3[2] == 0, "picked an unpriced offer over a priced one"

    # 4. An item stocked nowhere is excluded, not fatal.
    p4 = best_plan(plans(["i1", "i2"], mk({"i1": {"A": 10}, "i2": {}}),
                         urgent=False))
    assert p4[0] == ["A"] and list(p4[1]) == ["i1"], p4

    # 5. One unpublished eta must not sink a plan in urgent mode.
    res5 = mk({"i1": {"A": 10}, "i2": {"A": 10, "B": 500}})
    res5["i2"]["A"] = [R("A", 10, eta=None)]
    res5["i2"]["B"] = [R("B", 500, eta="10 mins")]
    assert best_plan(plans(["i1", "i2"], res5, urgent=True))[0] == ["A"]

    # 5b. Urgent means urgent: a cheaper-but-slower plan must not win when the
    #     user asked for soonest delivery. Real case -- Blinkit 20min/Rs 126
    #     against Apollo 3hr/Rs 87.
    fast_v_cheap = mk({"i1": {"Blinkit": 31, "Apollo": 32},
                       "i2": {"Blinkit": 50, "Apollo": 10}})
    fast_v_cheap["i1"]["Blinkit"] = [R("Blinkit", 31, eta="unicorn")]
    fast_v_cheap["i2"]["Blinkit"] = [R("Blinkit", 50, eta="express")]
    fast_v_cheap["i1"]["Apollo"] = [R("Apollo", 32, eta="3 hr")]
    fast_v_cheap["i2"]["Apollo"] = [R("Apollo", 10, eta="3 hr")]
    both = plans(["i1", "i2"], fast_v_cheap, urgent=True)
    assert best_plan(both, urgent=True)[0] == ["Blinkit"], "urgent picked slow"
    cheap = plans(["i1", "i2"], fast_v_cheap, urgent=False)
    assert best_plan(cheap, urgent=False)[0] == ["Apollo"], "cheapest picked dear"

    # 5c. A quick-commerce catalogue tag is not an unknown eta. Blinkit labels
    #     some lines "pharma_rx"; scoring that ETA_UNKNOWN sank a 15-minute
    #     order behind a next-day courier.
    assert eta_minutes("pharma_rx", "Blinkit") < ETA_UNKNOWN
    assert eta_minutes(None, "Instamart") < ETA_UNKNOWN
    assert eta_minutes(None, "Netmeds") == ETA_UNKNOWN

    # 6. Speed mixing is a platform fact, not an eta fact. An eta-derived
    #    predicate gets Blinkit+Instamart wrong -- Instamart publishes none.
    assert _mixed_speed(["Blinkit", "Netmeds"])
    assert not _mixed_speed(["Blinkit", "Instamart"])
    assert not _mixed_speed(["1mg", "Apollo"])

    # 7. Exhaustive == optimal, against an independent brute force.
    import random
    random.seed(11)
    for n in range(200):
        its = [f"i{i}" for i in range(random.randint(2, 5))]
        spec = {it: {p: random.randint(5, 300)
                     for p in random.sample("ABCDEFG", random.randint(1, 4))}
                for it in its}
        got = plans(its, mk(spec), urgent=False)
        assert got
        mine = min(_allin(pl) for pl in got)
        brute = min(sum(pr for _, pr in c) + DELIVERY_COST * len({p for p, _ in c})
                    for c in itertools.product(
                        *[[(p, pr) for p, pr in spec[it].items()] for it in its]))
        assert abs(mine - brute) < 1e-9, f"case {n}: got {mine}, optimal {brute}"
    print("  ok  200 random cases match brute-force optimum")
    print("grouper OK")


if __name__ == "__main__":
    demo() if "--demo" in sys.argv else main()
