import os
import sqlite3
import asyncio
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "@Giadasecret")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "GiadaSecretAccessBot")
PRIVATE_INVITE_URL = os.environ["PRIVATE_INVITE_URL"]
PUBLIC_CHANNEL_URL = os.environ.get("PUBLIC_CHANNEL_URL", "https://t.me/Giadasecret")
PUBLIC_URL = (os.environ.get("PUBLIC_URL") or os.environ.get("RENDER_EXTERNAL_URL", "")).rstrip("/")
if not PUBLIC_URL:
    raise RuntimeError("Render public URL not available; set PUBLIC_URL in environment.")
WEBHOOK_PATH = os.environ.get("WEBHOOK_PATH", "telegram")
PORT = int(os.environ.get("PORT", "10000"))

DB_PATH = Path(os.environ.get("DB_PATH", "referrals.db"))


def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                referrer_id INTEGER,
                referrals INTEGER NOT NULL DEFAULT 0,
                unlocked INTEGER NOT NULL DEFAULT 0,
                referral_credited INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        # Upgrade the DB if this file existed from an earlier version.
        columns = {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        if "referral_credited" not in columns:
            conn.execute(
                "ALTER TABLE users ADD COLUMN referral_credited INTEGER NOT NULL DEFAULT 0"
            )
        conn.commit()


init_db()


def get_user(user_id):
    with db_connect() as conn:
        return conn.execute(
            """
            SELECT user_id, referrer_id, referrals, unlocked, referral_credited
            FROM users WHERE user_id=?
            """,
            (user_id,),
        ).fetchone()


def upsert_user(user_id, referrer_id=None):
    """Create a user, or attach a referral to an existing uncredited user."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT user_id, referrer_id, referrals, unlocked, referral_credited "
            "FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

        if row is None:
            if referrer_id == user_id:
                referrer_id = None
            if referrer_id is not None:
                ref_exists = conn.execute(
                    "SELECT 1 FROM users WHERE user_id=?", (referrer_id,)
                ).fetchone()
                if not ref_exists:
                    referrer_id = None
            conn.execute(
                "INSERT INTO users(user_id, referrer_id) VALUES(?, ?)",
                (user_id, referrer_id),
            )
            conn.commit()
            return conn.execute(
                "SELECT user_id, referrer_id, referrals, unlocked, referral_credited "
                "FROM users WHERE user_id=?",
                (user_id,),
            ).fetchone()

        # A user who opened the bot without a referral can still later arrive via
        # a valid referral link, as long as they have not already been credited.
        if (
            referrer_id is not None
            and referrer_id != user_id
            and row[1] is None
            and row[4] == 0
        ):
            ref_exists = conn.execute(
                "SELECT 1 FROM users WHERE user_id=?", (referrer_id,)
            ).fetchone()
            if ref_exists:
                conn.execute(
                    "UPDATE users SET referrer_id=? WHERE user_id=?",
                    (referrer_id, user_id),
                )
                conn.commit()

        return get_user(user_id)


async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(PUBLIC_CHANNEL, user_id)
        return member.status in {"member", "administrator", "creator"}
    except Exception as exc:
        print(f"PUBLIC CHANNEL CHECK ERROR: {exc!r}")
        return False


def credit_referral(referred_user_id):
    """Credit one referred user once. Returns (referrer_id, count, credited)."""
    with db_connect() as conn:
        row = conn.execute(
            "SELECT referrer_id, referral_credited FROM users WHERE user_id=?",
            (referred_user_id,),
        ).fetchone()
        if not row or not row[0] or row[1] == 1:
            return None, 0, False

        referrer_id = row[0]
        ref_row = conn.execute(
            "SELECT referrals, unlocked FROM users WHERE user_id=?",
            (referrer_id,),
        ).fetchone()
        if not ref_row:
            return None, 0, False

        current_count = ref_row[0]
        if current_count < 3:
            conn.execute(
                "UPDATE users SET referrals=referrals+1 WHERE user_id=?",
                (referrer_id,),
            )
            current_count += 1
            if current_count >= 3:
                conn.execute(
                    "UPDATE users SET unlocked=1 WHERE user_id=?",
                    (referrer_id,),
                )

        # Mark the referred user as consumed so the same person can never count
        # twice for another click/restart.
        conn.execute(
            "UPDATE users SET referral_credited=1, referrer_id=NULL WHERE user_id=?",
            (referred_user_id,),
        )
        conn.commit()
        return referrer_id, current_count, True


def referral_keyboard(user_id):
    link = f"https://t.me/{BOT_USERNAME}?start=ref_{user_id}"
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 INVITA AMICI", url=link)],
            [InlineKeyboardButton("🔄 VERIFICA", callback_data="verify")],
        ]
    )


def join_keyboard():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🐷 UNISCITI AL CANALE", url=PUBLIC_CHANNEL_URL)],
            [InlineKeyboardButton("✅ VERIFICA ACCESSO", callback_data="verify")],
        ]
    )


def private_keyboard():
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🔐 ACCEDI AL CANALE PRIVATO DI GIADA 🐷", url=PRIVATE_INVITE_URL)]]
    )


async def send_access_status(chat_id, user_id, context):
    row = get_user(user_id) or upsert_user(user_id)

    if not await is_member(context.bot, user_id):
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                "🔒 ACCESSO NON ANCORA DISPONIBILE\n\n"
                "Per continuare devi prima unirti al canale di accesso.\n\n"
                "Dopo esserti unito, torna qui e premi «VERIFICA ACCESSO»."
            ),
            reply_markup=join_keyboard(),
        )
        return

    # If this user was brought in through another user's referral link, the
    # referral becomes valid only now, after membership is verified.
    row = get_user(user_id)
    if row and row[1] and row[4] == 0:
        referrer_id, count, credited = credit_referral(user_id)
        if credited and referrer_id:
            try:
                if count >= 3:
                    await context.bot.send_message(
                        referrer_id,
                        "🎉 HAI COMPLETATO 3/3!\n\nIl tuo accesso è stato sbloccato.",
                        reply_markup=private_keyboard(),
                    )
                else:
                    await context.bot.send_message(
                        referrer_id,
                        f"✅ Nuovo accesso verificato!\n\n👥 Inviti completati: {count}/3",
                    )
            except Exception as exc:
                print(f"REFERRER MESSAGE ERROR: {exc!r}")

    row = get_user(user_id)
    if row and row[3]:
        await context.bot.send_message(
            chat_id=chat_id,
            text="🔓 ACCESSO SBLOCCATO!\n\nPuoi entrare nel canale privato di Giada 🐷",
            reply_markup=private_keyboard(),
        )
        return

    count = row[2] if row else 0
    await context.bot.send_message(
        chat_id=chat_id,
        text=(
            "🐷 BENVENUTO!\n\n"
            "L'accesso ai contenuti esclusivi è disponibile dopo aver completato la procedura.\n\n"
            f"👥 Inviti completati: {count}/3\n\n"
            "Condividi il tuo link personale con i tuoi amici. "
            "Per essere conteggiato, ogni invitato deve entrare nel canale di accesso "
            "e confermare l'accesso qui."
        ),
        reply_markup=referral_keyboard(user_id),
    )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrer_id = None
    if context.args:
        value = context.args[0]
        if value.startswith("ref_"):
            try:
                referrer_id = int(value[4:])
            except ValueError:
                referrer_id = None

    upsert_user(user_id, referrer_id)
    await send_access_status(update.effective_chat.id, user_id, context)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await send_access_status(query.message.chat_id, query.from_user.id, context)


async def error_handler(update, context):
    print(f"BOT ERROR: {context.error!r}")


def main():
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .connect_timeout(30)
        .read_timeout(30)
        .write_timeout(30)
        .build()
    )
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(verify, pattern=r"^verify$")
    )
    application.add_error_handler(error_handler)

    webhook_url = f"{PUBLIC_URL}/{WEBHOOK_PATH}"
    print(f"Starting Giada Secret bot webhook at {webhook_url}")

    application.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=WEBHOOK_PATH,
        webhook_url=webhook_url,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
