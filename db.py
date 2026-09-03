"""SQLite persistence: one saved location per chat. Survives bot restarts."""
import datetime

import aiosqlite

from adapters.base import Location
from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    chat_id    INTEGER PRIMARY KEY,
    loc_name   TEXT,
    lat        REAL,
    lon        REAL,
    pincode    TEXT,
    city       TEXT,
    im_store   TEXT,
    updated_at TEXT
)"""


async def init():
    """Create the users table if absent, and add columns older DBs lack."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(_SCHEMA)
        # bot.db predates im_store; CREATE TABLE IF NOT EXISTS will not add it.
        try:
            await db.execute("ALTER TABLE users ADD COLUMN im_store TEXT")
        except Exception:
            pass  # already present
        await db.commit()


async def set_location(chat_id: int, loc: Location):
    """Upsert this chat's saved location, stamping updated_at."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT INTO users (chat_id, loc_name, lat, lon, pincode, city,"
            " im_store, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(chat_id) DO UPDATE SET"
            " loc_name=excluded.loc_name, lat=excluded.lat, lon=excluded.lon,"
            " pincode=excluded.pincode, city=excluded.city,"
            " im_store=excluded.im_store, updated_at=excluded.updated_at",
            (chat_id, loc.name, loc.lat, loc.lon, loc.pincode, loc.city,
             loc.im_store,
             datetime.datetime.now().isoformat(timespec="seconds")))
        await db.commit()


async def get_location(chat_id: int) -> Location | None:
    """Return the chat's saved Location, or None if never set."""
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT loc_name, lat, lon, pincode, city, im_store"
            " FROM users WHERE chat_id=?",
            (chat_id,)
        ) as cur:
            row = await cur.fetchone()
    return Location(*row) if row else None
