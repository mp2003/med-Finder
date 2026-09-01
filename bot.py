"""MedFinder -- Telegram bot: which platform has this medicine, near me?

Run: python bot.py   (long polling, no webhook/SSL needed)
"""
import asyncio
import html
import logging
import os
import sys
import time

from dotenv import load_dotenv
from telegram import (InlineKeyboardButton, InlineKeyboardMarkup, KeyboardButton,
                      ReplyKeyboardMarkup, ReplyKeyboardRemove, Update)
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, MessageHandler, filters)

import db
from adapters import ADAPTERS, COMING_SOON
from adapters.base import Location, ProductResult
from adapters.onemg import resolve_latlng
from config import CACHE_TTL, PRESETS, SEARCH_BUDGET
from matching import normalize

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("medfinder")

# key=(normalized_query, pincode) -> (results, timestamp). In-process is enough
# for a handful of users; ponytail: swap for Redis only if this ever runs multi-process.
_cache: dict[tuple[str, str], tuple[list[ProductResult], float]] = {}

DISCLAIMER = ("Prices/availability come from the platforms and can be stale — "
              "this is a personal convenience tool.")


# ---------------------------------------------------------------- keyboards
def preset_keyboard() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(f"📍 {p['name']} ({p['pincode']})",
                                  callback_data=f"loc:{i}")]
            for i, p in enumerate(PRESETS)]
    return InlineKeyboardMarkup(rows)


LIVE_LOCATION_KB = ReplyKeyboardMarkup(
    [[KeyboardButton("📡 Share my live location", request_location=True)]],
    resize_keyboard=True, one_time_keyboard=True)


async def ask_location(update: Update, prefix: str = ""):
    """One message with the presets, plus the live-location keyboard.

    Telegram can't put inline buttons and a request_location button on the same
    message, so the second send is unavoidable -- but it carries no visible text
    beyond a hint, and the greeting is folded into the first.
    """
    chat = update.effective_chat
    await chat.send_message(f"{prefix}📍 <b>Pick a location</b>",
                            parse_mode="HTML", reply_markup=preset_keyboard())
    await chat.send_message("…or tap below to share your live location 👇",
                            reply_markup=LIVE_LOCATION_KB)


# ---------------------------------------------------------------- commands
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    loc = await db.get_location(update.effective_chat.id)
    if loc:
        # Already set up: don't re-ask, just confirm and get out of the way.
        return await update.message.reply_text(
            f"👋 <b>MedFinder</b>\nSaved location: {html.escape(loc.name)} "
            f"({loc.pincode}).\nSend a product name, or /location to change it.",
            parse_mode="HTML")
    await ask_location(update, "👋 <b>MedFinder</b> — send a product name and "
                               "I'll compare platforms near you.\n\n")


async def cmd_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/location -- always re-open the picker; new choice overwrites the old."""
    await ask_location(update)


async def cmd_where(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/where -- show the saved location, or prompt if none is set."""
    loc = await db.get_location(update.effective_chat.id)
    if not loc:
        return await ask_location(update, "No location saved yet.\n")
    await update.message.reply_text(
        f"📍 {html.escape(loc.name)} — {loc.pincode} ({html.escape(loc.city)})",
        parse_mode="HTML")


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/help -- usage plus the price-staleness disclaimer."""
    await update.message.reply_text(
        "Send any medicine name (e.g. <code>dolo 650</code>) and I'll check "
        "each platform for stock, price and ETA near your saved location.\n\n"
        "/location — change location\n/where — show saved location\n\n"
        f"<i>{DISCLAIMER}</i>", parse_mode="HTML")


async def unknown_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Any unrecognised /command."""
    await update.message.reply_text("Unknown command — try /help")


# ---------------------------------------------------------------- location
async def on_preset(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    p = PRESETS[int(q.data.split(":")[1])]
    loc = Location(p["name"], p["lat"], p["lon"], p["pincode"], p["city"])
    await db.set_location(q.message.chat_id, loc)
    await q.edit_message_text(
        f"✅ Location set: <b>{html.escape(loc.name)}</b> — {loc.pincode}\n"
        "Now send me a medicine name.", parse_mode="HTML")


async def on_live_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Shared Telegram location -> resolve city+pincode via 1mg's latlng
    endpoint, so the user never has to type a pincode. Falls back to the
    preset picker if resolution fails."""
    lat, lon = update.message.location.latitude, update.message.location.longitude
    resolved = await resolve_latlng(lat, lon)
    if not resolved:
        return await update.message.reply_text(
            "Couldn't resolve that location. Please pick a preset instead:",
            reply_markup=preset_keyboard())
    city, pincode = resolved
    loc = Location("My location", lat, lon, pincode, city)
    await db.set_location(update.message.chat_id, loc)
    await update.message.reply_text(
        f"✅ Location set: <b>My location</b> — {pincode} ({html.escape(city)})\n"
        "Now send me a medicine name.", parse_mode="HTML",
        reply_markup=ReplyKeyboardRemove())


# ---------------------------------------------------------------- search
def render(query: str, loc: Location, done: dict, cached: bool) -> str:
    head = (f"🔎 <b>{html.escape(query)}</b> — {html.escape(loc.name)} "
            f"({loc.pincode})")
    lines = []
    for mod in ADAPTERS:
        name = html.escape(mod.PLATFORM)
        if mod.PLATFORM not in done:
            lines.append(f"⏳ {name} — checking…")
            continue
        results = done[mod.PLATFORM]
        if results is None:
            lines.append(f"⚠️ {name} — check failed")
        elif not results:
            lines.append(f"❌ {name} — not found")
        else:
            r = results[0]
            link = (f'<a href="{html.escape(r.url, quote=True)}">'
                    f'{html.escape(r.name)}</a>')
            price = f"₹{r.price:g}" if r.price is not None else "price n/a"
            eta = f" ({html.escape(r.eta)})" if r.eta else ""
            if not r.is_match:
                # No real match: say so plainly, then offer the closest item.
                lines.append(f"❌ {name} — not found. Similar: {link} — "
                             f"{price}{eta}")
            elif not r.available:
                lines.append(f"❌ {name} — {link} is out of stock")
                alt = next((a for a in results[1:] if a.available), None)
                if alt:
                    ap = f"₹{alt.price:g}" if alt.price is not None else "price n/a"
                    lines.append(f"   ↳ in stock: "
                                 f'<a href="{html.escape(alt.url, quote=True)}">'
                                 f'{html.escape(alt.name)}</a> — {ap}')
            else:
                lines.append(f"✅ {name} — {price}{eta} — {link}")
    for mod in COMING_SOON:
        lines.append(f"⚠️ {html.escape(mod.PLATFORM)} — coming soon")
    foot = "\n\n<i>cached</i>" if cached else ""
    return head + "\n" + "\n".join(lines) + foot


async def do_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Default handler: treat plain text as a product search.

    Serves from cache when fresh, else fans out to every live adapter and
    edits the placeholder message as each one lands.
    """
    query = (update.message.text or "").strip()
    if not query:
        return
    loc = await db.get_location(update.effective_chat.id)
    if not loc:
        return await ask_location(update, "Pick a location first.\n")

    key = (normalize(query), loc.pincode)
    hit = _cache.get(key)
    if hit and time.time() - hit[1] < CACHE_TTL:
        done = {m.PLATFORM: [r for r in hit[0] if r.platform == m.PLATFORM]
                for m in ADAPTERS}
        return await update.message.reply_text(
            render(query, loc, done, cached=True), parse_mode="HTML",
            disable_web_page_preview=True)

    msg = await update.message.reply_text(
        render(query, loc, {}, cached=False), parse_mode="HTML",
        disable_web_page_preview=True)

    done: dict[str, list | None] = {}
    tasks = {asyncio.create_task(m.search(query, loc)): m for m in ADAPTERS}
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
        try:  # progressive edit; ignore "message is not modified"
            await msg.edit_text(render(query, loc, done, cached=False),
                                parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            pass

    for t in pending:  # over budget -> render as failed
        t.cancel()
        done.setdefault(tasks[t].PLATFORM, None)

    ok = [r for v in done.values() if v for r in v]
    if ok:
        _cache[key] = (ok, time.time())
    try:
        await msg.edit_text(render(query, loc, done, cached=False),
                            parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass


# ---------------------------------------------------------------- main
def main():
    load_dotenv()
    token = (os.getenv("BOT_TOKEN") or "").strip().strip('"\'')
    if not token or token == "paste-here":
        sys.exit("BOT_TOKEN not set. Edit .env and replace paste-here with the "
                 "token from @BotFather, e.g.\n  BOT_TOKEN=8123456789:AAF...")
    if ":" not in token:
        sys.exit(f"BOT_TOKEN looks malformed ({token[:6]}...). Expected "
                 "<digits>:<letters>, e.g. 8123456789:AAF...")

    app = Application.builder().token(token).post_init(
        lambda _: db.init()).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("location", cmd_location))
    app.add_handler(CommandHandler("where", cmd_where))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(on_preset, pattern=r"^loc:"))
    app.add_handler(MessageHandler(filters.LOCATION, on_live_location))
    app.add_handler(MessageHandler(filters.COMMAND, unknown_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, do_search))
    log.info("MedFinder up — polling…")
    app.run_polling()


if __name__ == "__main__":
    main()
