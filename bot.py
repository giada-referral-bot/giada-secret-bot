import os
import sqlite3
import threading
import asyncio
from urllib.parse import quote

from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

BOT_TOKEN = os.environ["BOT_TOKEN"]
PUBLIC_CHANNEL = os.environ.get("PUBLIC_CHANNEL", "@Giadasecret")
PRIVATE_INVITE_URL = os.environ["PRIVATE_INVITE_URL"]

app = Flask(__name__)
DB = "referrals.db"
PHOTO_PATH = os.path.join(os.path.dirname(__file__), "giada.jpg")
db_lock = threading.Lock()

conn = sqlite3.connect(DB, check_same_thread=False)
with db_lock:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            referrer_id INTEGER,
            referrals INTEGER NOT NULL DEFAULT 0,
            unlocked INTEGER NOT NULL DEFAULT 0
        )
    """)
    conn.commit()


def get_user(user_id):
    with db_lock:
        return conn.execute(
            "SELECT user_id, referrer_id, referrals, unlocked FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()


def ensure_user(user_id, referrer_id=None):
    with db_lock:
        row = conn.execute(
            "SELECT user_id, referrer_id, referrals, unlocked FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

        if row:
            return row

        if referrer_id == user_id:
            referrer_id = None

        conn.execute(
            "INSERT INTO users(user_id, referrer_id) VALUES(?, ?)",
            (user_id, referrer_id),
        )
        conn.commit()

    return get_user(user_id)


async def is_member(bot, user_id):
    try:
        member = await bot.get_chat_member(PUBLIC_CHANNEL, user_id)
        return member.status in ("member", "administrator", "creator")
    except Exception as exc:
        print("MEMBERSHIP CHECK ERROR:", repr(exc))
        return False


def credit_referral(user_id):
    """
    Credit the user to the referrer exactly once.
    The referrer_id is cleared after crediting so the same user
    cannot be counted twice for the same inviter.
    """
    with db_lock:
        row = conn.execute(
            "SELECT referrer_id FROM users WHERE user_id=?",
            (user_id,),
        ).fetchone()

        if not row or not row[0]:
            return None, 0

        referrer_id = row[0]

        conn.execute(
            """
            UPDATE users
            SET referrals = MIN(referrals + 1, 3),
                referrer_id = NULL
            WHERE user_id=?
            """,
            (referrer_id,),
        )
        conn.execute(
            "UPDATE users SET referrer_id=NULL WHERE user_id=?",
            (user_id,),
        )
        conn.commit()

        count = conn.execute(
            "SELECT referrals FROM users WHERE user_id=?",
            (referrer_id,),
        ).fetchone()[0]

    return referrer_id, count


def unlock_user(user_id):
    with db_lock:
        conn.execute(
            "UPDATE users SET unlocked=1 WHERE user_id=?",
            (user_id,),
        )
        conn.commit()


def referral_link(user_id):
    return f"https://t.me/GiadaSecretAccessBot?start=ref_{user_id}"


def share_link(user_id):
    link = referral_link(user_id)
    text = (
        "💋 Accedi a Giada: foto, video e contenuti esclusivi. "
        "Entra qui per continuare 👇"
    )
    return (
        "https://t.me/share/url?url="
        + quote(link, safe="")
        + "&text="
        + quote(text, safe="")
    )


def invite_keyboard(user_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💌 INVITA I MIEI AMICI", url=share_link(user_id))],
        [InlineKeyboardButton("🔄 VERIFICA", callback_data="verify")],
    ])


async def send_invitation_card(chat_id, context, user_id):
    text = (
        "💋 <b>ACCEDI A GIADA</b> 🐷\n\n"
        "Ti aspettano <b>foto, video e contenuti esclusivi</b> di Giada.\n\n"
        "🔐 Premi qui sotto per continuare."
    )
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("💋 ACCEDI A GIADA", url=referral_link(user_id))
    ]])

    with open(PHOTO_PATH, "rb") as photo:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(photo, filename="giada.jpg"),
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def send_access_gate(chat_id, context):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("💋 UNISCITI AL CANALE", url="https://t.me/Giadasecret")],
        [InlineKeyboardButton("🔄 VERIFICA ACCESSO", callback_data="verify_channel")],
    ])
    text = (
        "🔒 <b>ACCESSO NON ANCORA DISPONIBILE</b>\n\n"
        "Per continuare devi prima unirti al canale di accesso di Giada 🐷.\n\n"
        "Dopo esserti unito, torna qui e premi <b>VERIFICA ACCESSO</b>."
    )
    with open(PHOTO_PATH, "rb") as photo:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(photo, filename="giada.jpg"),
            caption=text,
            parse_mode="HTML",
            reply_markup=keyboard,
        )


async def send_main_access(chat_id, context, user_id):
    row = get_user(user_id) or ensure_user(user_id)
    count = row[2]

    text = (
        "🐷 <b>BENVENUTO/A</b>\n\n"
        "Per ottenere l'accesso ai contenuti esclusivi di Giada, "
        "completa la procedura.\n\n"
        f"👥 <b>Inviti completati: {count}/3</b>\n\n"
        "Invita 3 amici usando il pulsante qui sotto. "
        "Ogni accesso verificato farà avanzare il tuo contatore."
    )

    with open(PHOTO_PATH, "rb") as photo:
        await context.bot.send_photo(
            chat_id=chat_id,
            photo=InputFile(photo, filename="giada.jpg"),
            caption=text,
            parse_mode="HTML",
            reply_markup=invite_keyboard(user_id),
        )


async def process_start(chat_id, user_id, context):
    row = get_user(user_id)
    if not row:
        row = ensure_user(user_id)

    # The user must belong to the public access channel.
    if not await is_member(context.bot, user_id):
        await send_access_gate(chat_id, context)
        return

    # A referral is credited only after the referred user has joined
    # the public channel and started the bot.
    if row[1]:
        referrer_id, count = credit_referral(user_id)
        if referrer_id:
            try:
                if count >= 3:
                    unlock_user(referrer_id)
                    await context.bot.send_message(
                        referrer_id,
                        "🔥 <b>3/3 COMPLETATO</b>\n\n"
                        "Il tuo accesso privato è stato sbloccato.",
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton(
                                "🐷 ACCEDI AL CANALE PRIVATO",
                                url=PRIVATE_INVITE_URL,
                            )
                        ]]),
                    )
                else:
                    await context.bot.send_message(
                        referrer_id,
                        f"✅ <b>Nuovo accesso verificato</b>\n\n"
                        f"👥 Inviti completati: {count}/3",
                        parse_mode="HTML",
                    )
            except Exception as exc:
                print("REFERRER MESSAGE ERROR:", repr(exc))

    row = get_user(user_id) or ensure_user(user_id)

    if row[3]:
        await context.bot.send_message(
            chat_id,
            "🔓 <b>ACCESSO SBLOCCATO</b>\n\n"
            "Puoi entrare nel canale privato di Giada 🐷.",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(
                    "🐷 ACCEDI AL CANALE PRIVATO",
                    url=PRIVATE_INVITE_URL,
                )
            ]]),
        )
        return

    await send_main_access(chat_id, context, user_id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    referrer_id = None

    if context.args and context.args[0].startswith("ref_"):
        try:
            referrer_id = int(context.args[0][4:])
        except ValueError:
            referrer_id = None

    row = get_user(user_id)
    if not row:
        ensure_user(user_id, referrer_id)

    await process_start(update.effective_chat.id, user_id, context)


async def verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await process_start(query.message.chat_id, query.from_user.id, context)


async def error_handler(update, context):
    print("BOT ERROR:", repr(context.error))


async def run_bot_async():
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(
        CallbackQueryHandler(
            verify,
            pattern=r"^(verify|verify_channel)$",
        )
    )
    application.add_error_handler(error_handler)

    await application.initialize()
    await application.start()
    await application.updater.start_polling(allowed_updates=Update.ALL_TYPES)
    await asyncio.Event().wait()


def run_bot():
    asyncio.run(run_bot_async())


@app.get("/")
def health():
    return "Giada Secret bot OK", 200


if __name__ == "__main__":
    threading.Thread(target=run_bot, daemon=True).start()
    port = int(os.environ.get("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
