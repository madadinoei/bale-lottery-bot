import logging
import os
import sys
import requests
import psycopg2
from psycopg2.extras import RealDictCursor

# Config logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

TOKEN = os.environ.get("BALE_BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

if not TOKEN:
    logging.critical("BALE_BOT_TOKEN environment variable is not set!")
    sys.exit(1)

if not DATABASE_URL:
    logging.critical("DATABASE_URL environment variable is not set!")
    sys.exit(1)

BASE_URL = f"https://tapi.bale.ai/bot{TOKEN}"


def get_db_connection():
    """ایجاد اتصال به PostgreSQL"""
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    return conn


def init_db():
    """ایجاد جدول کاربران در صورت عدم وجود"""
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    chat_id BIGINT NOT NULL,
                    user_id BIGINT NOT NULL,
                    first_name TEXT,
                    last_name TEXT,
                    username TEXT,
                    PRIMARY KEY (chat_id, user_id)
                );
            """
            )
        conn.commit()
    logging.info("PostgreSQL Database initialized.")


def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        r = requests.post(f"{BASE_URL}/sendMessage", json=payload, timeout=10)
        return r.json()
    except Exception as e:
        logging.error(f"Error sending message: {e}")
        return None


def get_panel_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🟢 عضویت در قرعه‌کشی",
                    "callback_data": "join",
                    "style": "success",
                },
                {
                    "text": "🔴 خروج از قرعه‌کشی",
                    "callback_data": "leave",
                    "style": "danger",
                },
            ]
        ]
    }


def is_user_admin(chat_id, user_id):
    try:
        r = requests.post(
            f"{BASE_URL}/getChatMember",
            json={"chat_id": chat_id, "user_id": user_id},
            timeout=10,
        )
        res = r.json()
        if res.get("ok"):
            status = res.get("result", {}).get("status")
            return status in ("creator", "administrator")
    except Exception as e:
        logging.error(f"Error checking admin: {e}")
    return False


def handle_join(chat_id, user):
    user_id = user.get("id")
    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")
    username = user.get("username", "")

    # ساخت نام نمایشی برای کاربر جدید
    full_name = f"{first_name} {last_name}".strip() or "کاربر"
    user_display = f"{full_name} (@{username})" if username else full_name

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            # بررسی وجود کاربر
            cur.execute(
                "SELECT first_name, last_name, username FROM users WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            )
            existing_user = cur.fetchone()

            if existing_user:
                # فرمت کردن نام کاربر قبلاً ثبت‌نام شده
                ex_name = f"{existing_user['first_name'] or ''} {existing_user['last_name'] or ''}".strip() or "کاربر"
                ex_uname = existing_user["username"]
                ex_display = f"{ex_name} (@{ex_uname})" if ex_uname else ex_name
                return f"کاربر {ex_display}، ثبت‌نام شما قبلاً در قرعه‌کشی انجام شده است! ✅"

            # درج کاربر جدید
            cur.execute(
                """
                INSERT INTO users (chat_id, user_id, first_name, last_name, username)
                VALUES (%s, %s, %s, %s, %s)
            """,
                (chat_id, user_id, first_name, last_name, username),
            )
        conn.commit()
    return f"کاربر {user_display} با موفقیت به قرعه‌کشی اضافه شد 🎉"


def handle_leave(chat_id, user_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM users WHERE chat_id = %s AND user_id = %s",
                (chat_id, user_id),
            )
            deleted = cur.rowcount
        conn.commit()

    if deleted > 0:
        return "شما از لیست قرعه‌کشی خارج شدید. ❌"
    return "شما در لیست قرعه‌کشی حضور نداشتید."


def handle_count(chat_id):
    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) as cnt FROM users WHERE chat_id = %s", (chat_id,)
            )
            count = cur.fetchone()["cnt"]
    return f"تعداد افراد ثبت‌نام‌شده در این گروه: {count} نفر"


def handle_draw(chat_id, count_str):
    try:
        count = int(count_str)
        if count <= 0:
            return "لطفاً یک عدد بزرگتر از صفر وارد کنید."
    except ValueError:
        return "فرمت دستور اشتباه است. مثال: /draw 3"

    with get_db_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT first_name, last_name, username 
                FROM users 
                WHERE chat_id = %s 
                ORDER BY RANDOM() 
                LIMIT %s
            """,
                (chat_id, count),
            )
            winners = cur.fetchall()

    if not winners:
        return "هیچ فردی برای قرعه‌کشی ثبت‌نام نکرده است!"

    text = f"🎉 **برندگان قرعه‌کشی ({len(winners)} نفر):**\n\n"
    for idx, w in enumerate(winners, 1):
        name = f"{w['first_name'] or ''} {w['last_name'] or ''}".strip() or "کاربر"
        uname = f" (@{w['username']})" if w["username"] else ""
        text += f"{idx}. {name}{uname}\n"

    return text


def handle_callback(callback_query):
    query_id = callback_query.get("id")
    from_user = callback_query.get("from", {})
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    data = callback_query.get("data")

    if not chat_id:
        return

    reply_text = ""
    if data == "join":
        reply_text = handle_join(chat_id, from_user)
    elif data == "leave":
        reply_text = handle_leave(chat_id, from_user.get("id"))

    if reply_text:
        send_message(chat_id, reply_text)

    try:
        requests.post(
            f"{BASE_URL}/answerCallbackQuery",
            json={"callback_query_id": query_id},
            timeout=5,
        )
    except Exception as e:
        logging.error(f"Error answering callback query: {e}")


def handle_update(update):
    if "callback_query" in update:
        handle_callback(update["callback_query"])
        return

    message = update.get("message")
    if not message:
        return

    chat = message.get("chat", {})
    chat_id = chat.get("id")
    chat_type = chat.get("type")
    user = message.get("from", {})
    user_id = user.get("id")
    text = message.get("text", "").strip()

    if not text.startswith("/"):
        return

    cmd_parts = text.split(maxsplit=1)
    cmd = cmd_parts[0].lower()
    args = cmd_parts[1] if len(cmd_parts) > 1 else ""

    if "@" in cmd:
        cmd = cmd.split("@")[0]

    if cmd in ("/panel", "/start"):
        send_message(
            chat_id, "پنل قرعه‌کشی گروه:", reply_markup=get_panel_keyboard()
        )

    elif cmd == "/join":
        msg = handle_join(chat_id, user)
        send_message(chat_id, msg)

    elif cmd == "/leave":
        msg = handle_leave(chat_id, user_id)
        send_message(chat_id, msg)

    elif cmd == "/count":
        msg = handle_count(chat_id)
        send_message(chat_id, msg)

    elif cmd == "/draw":
        if chat_type in ("group", "supergroup"):
            if not is_user_admin(chat_id, user_id):
                send_message(
                    chat_id, "⛔ شما دسترسی مدیر برای قرعه‌کشی را ندارید."
                )
                return
        msg = handle_draw(chat_id, args)
        send_message(chat_id, msg)


def main():
    init_db()
    offset = 0
    logging.info("Starting Bale Raffle Bot (PostgreSQL)...")

    while True:
        try:
            r = requests.get(
                f"{BASE_URL}/getUpdates",
                params={"offset": offset, "timeout": 20},
                timeout=25,
            )
            data = r.json()
            if data.get("ok"):
                for update in data.get("result", []):
                    offset = update["update_id"] + 1
                    handle_update(update)
        except Exception as e:
            logging.error(f"Polling error: {e}")
