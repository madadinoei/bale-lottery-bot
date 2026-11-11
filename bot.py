import os
import sqlite3
import secrets
import logging
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

TOKEN = os.environ.get("BALE_BOT_TOKEN")
if not TOKEN:
    raise RuntimeError("متغیر محیطی BALE_BOT_TOKEN تنظیم نشده است.")

BASE_URL = f"https://tapi.bale.ai/bot{TOKEN}"
DB_PATH = os.environ.get("DB_PATH", "/data/raffle.sqlite3")

os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
db = sqlite3.connect(DB_PATH, check_same_thread=False)
db.row_factory = sqlite3.Row

with db:
    db.execute("""
        CREATE TABLE IF NOT EXISTS participants (
            chat_id INTEGER,
            user_id INTEGER,
            name TEXT,
            username TEXT,
            PRIMARY KEY (chat_id, user_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS draws (
            chat_id INTEGER,
            message_id INTEGER,
            result TEXT,
            PRIMARY KEY (chat_id, message_id)
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            val TEXT
        )
    """)


def api(method: str, **params):
    url = f"{BASE_URL}/{method}"
    res = requests.post(url, json=params, timeout=(10, 40))
    res.raise_for_status()
    data = res.json()
    if not data.get("ok"):
        raise RuntimeError(f"خطای API بله در {method}: {data}")
    return data.get("result")


def send(chat_id: int, text: str, thread_id: int = None, reply_markup: dict = None):
    url = f"{BASE_URL}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    if thread_id:
        payload["message_thread_id"] = thread_id
    if reply_markup:
        payload["reply_markup"] = reply_markup
    res = requests.post(url, json=payload, timeout=(10, 30))
    res.raise_for_status()
    return res.json().get("result")


def answer_callback(callback_query_id: str, text: str):
    try:
        api("answerCallbackQuery", callback_query_id=callback_query_id, text=text, show_alert=True)
    except Exception as e:
        logging.warning("خطا در پاسخ به callback_query: %s", e)


def display_name(user: dict) -> str:
    parts = [user.get("first_name", ""), user.get("last_name", "")]
    name = " ".join(p for p in parts if p).strip()
    return name or user.get("username") or f"کاربر {user.get('id')}"


def is_current_member(member: dict) -> bool:
    status = member.get("status")
    return status in ("creator", "administrator", "member") or (
        status == "restricted" and member.get("is_member") is True
    )


def get_raffle_keyboard():
    return {
        "inline_keyboard": [
            [
                {"text": "ثبت‌نام در قرعه‌کشی 🎟", "callback_data": "join", "style": "success"},
                {"text": "انصراف ❌", "callback_data": "leave", "style": "danger"}
            ]
        ]
    }


def handle_callback(cq: dict):
    cq_id = cq.get("id")
    from_user = cq.get("from", {})
    user_id = from_user.get("id")
    msg = cq.get("message", {})
    chat = msg.get("chat", {})
    chat_id = chat.get("id")
    data = cq.get("data")

    if not chat_id or not user_id:
        return

    name = display_name(from_user)
    username = from_user.get("username") or ""

    if data == "join":
        with db:
            db.execute(
                "INSERT OR REPLACE INTO participants (chat_id, user_id, name, username) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, name, username)
            )
        answer_callback(cq_id, f"{name} عزیز، با موفقیت ثبت‌نام شدی! ✅")
        logging.info("کاربر %s در گروه %s ثبت‌نام کرد (دکمه شیشه‌ای)", user_id, chat_id)

    elif data == "leave":
        with db:
            cur = db.execute(
                "DELETE FROM participants WHERE chat_id = ? AND user_id = ?",
                (chat_id, user_id)
            )
            deleted = cur.rowcount
        if deleted > 0:
            answer_callback(cq_id, "از فهرست قرعه‌کشی خارج شدی. ❌")
        else:
            answer_callback(cq_id, "شما قبلاً در فهرست قرعه‌کشی نبودید.")
        logging.info("کاربر %s از گروه %s انصراف داد (دکمه شیشه‌ای)", user_id, chat_id)


def handle(message: dict):
    chat = message.get("chat", {})
    chat_type = chat.get("type", "")
    if chat_type not in ("group", "supergroup"):
        return

    from_user = message.get("from", {})
    if from_user.get("is_bot"):
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    chat_id = chat.get("id")
    thread_id = message.get("message_thread_id")
    parts = text.split()
    cmd = parts[0].split("@")[0].lower()

    # ارسال پنل شیشه‌ای قرعه‌کشی با دکمه‌ها
    if cmd in ("/panel", "/raffle", "/start"):
        send(
            chat_id,
            "🎉 **پنل ثبت‌نام در قرعه‌کشی**\nبرای شرکت یا انصراف، از دکمه‌های زیر استفاده کنید:",
            thread_id=thread_id,
            reply_markup=get_raffle_keyboard()
        )
        return

    if cmd == "/join":
        user_id = from_user.get("id")
        name = display_name(from_user)
        username = from_user.get("username") or ""
        with db:
            db.execute(
                "INSERT OR REPLACE INTO participants (chat_id, user_id, name, username) VALUES (?, ?, ?, ?)",
                (chat_id, user_id, name, username)
            )
        send(chat_id, f"«{name}»، برای قرعه‌کشی ثبت شدی ✅", thread_id=thread_id)

    elif cmd == "/leave":
        user_id = from_user.get("id")
        with db:
            db.execute("DELETE FROM participants WHERE chat_id = ? AND user_id = ?", (chat_id, user_id))
        send(chat_id, "از فهرست قرعه‌کشی خارج شدی.", thread_id=thread_id)

    elif cmd == "/count":
        cur = db.execute("SELECT COUNT(*) as c FROM participants WHERE chat_id = ?", (chat_id,))
        count = cur.fetchone()["c"]
        send(chat_id, f"تعداد افراد ثبت‌نام‌شده: {count} نفر", thread_id=thread_id)

    elif cmd == "/draw":
        msg_id = message.get("message_id")
        user_id = from_user.get("id")

        admins = api("getChatAdministrators", chat_id=chat_id)
        admin_ids = {a.get("user", {}).get("id") for a in admins}
        if user_id not in admin_ids:
            send(chat_id, "فقط مدیران گروه می‌توانند قرعه‌کشی را اجرا کنند.", thread_id=thread_id)
            return

        if len(parts) < 2 or not parts[1].isdigit():
            send(chat_id, "فرمت دستور نادرست است.\nنمونه: `/draw 3`", thread_id=thread_id)
            return

        count_req = int(parts[1])
        if count_req <= 0:
            send(chat_id, "تعداد برندگان باید حداقل ۱ باشد.", thread_id=thread_id)
            return

        cur = db.execute("SELECT result FROM draws WHERE chat_id = ? AND message_id = ?", (chat_id, msg_id))
        prev = cur.fetchone()
        if prev:
            send(chat_id, prev["result"], thread_id=thread_id)
            return

        cur = db.execute("SELECT user_id, name, username FROM participants WHERE chat_id = ?", (chat_id,))
        registered = cur.fetchall()

        eligible = []
        for p in registered:
            try:
                m = api("getChatMember", chat_id=chat_id, user_id=p["user_id"])
                if is_current_member(m) and not m.get("user", {}).get("is_bot"):
                    eligible.append(p)
            except Exception:
                continue

        total = len(eligible)
        if total == 0:
            send(chat_id, "هیچ شرکت‌کننده واجد شرایطی یافت نشد.", thread_id=thread_id)
            return

        if count_req > total:
            send(chat_id, f"تعداد درخواستی ({count_req}) بیشتر از کل شرکت‌کنندگان واجد شرایط ({total}) است.", thread_id=thread_id)
            return

        winners = secrets.SystemRandom().sample(eligible, count_req)

        lines = [f"🎉 نتیجهٔ قرعه‌کشی: {count_req} برنده از {total} شرکت‌کننده:"]
        for idx, w in enumerate(winners, start=1):
            uname = f"@{w['username']}" if w["username"] else "بدون نام‌کاربری"
            lines.append(f"{idx}. {w['name']} | {uname} | شناسه: {w['user_id']}")

        result_text = "\n".join(lines)
        with db:
            db.execute("INSERT OR REPLACE INTO draws (chat_id, message_id, result) VALUES (?, ?, ?)",
                       (chat_id, msg_id, result_text))

        send(chat_id, result_text, thread_id=thread_id)


def main():
    cur = db.execute("SELECT val FROM state WHERE key = 'offset'")
    row = cur.fetchone()
    offset = int(row["val"]) if row else 0

    logging.info("بات قرعه‌کشی با پشتیبانی از دکمه‌های شیشه‌ای شروع به کار کرد...")

    while True:
        try:
            updates = api("getUpdates", offset=offset, timeout=25)
            for upd in updates:
                offset = upd["update_id"] + 1
                with db:
                    db.execute("INSERT OR REPLACE INTO state (key, val) VALUES ('offset', ?)", (str(offset),))

                if "message" in upd:
                    handle(upd["message"])
                elif "callback_query" in upd:
                    handle_callback(upd["callback_query"])

        except requests.RequestException as e:
            logging.warning("خطای شبکه: %s", e)
        except Exception as e:
            logging.error("خطای غیرمنتظره: %s", e)


if __name__ == "__main__":
    main()
