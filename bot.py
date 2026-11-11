import os
import secrets
import sqlite3
import time
import requests

TOKEN = os.environ["BALE_BOT_TOKEN"]
BASE_URL = f"https://tapi.bale.ai/bot{TOKEN}"
DB_PATH = os.environ.get("DB_PATH", "/data/raffle.sqlite3")

session = requests.Session()

db = sqlite3.connect(DB_PATH)
db.execute("""
    CREATE TABLE IF NOT EXISTS participants (
        chat_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        username TEXT,
        PRIMARY KEY (chat_id, user_id)
    )
""")
db.execute("""
    CREATE TABLE IF NOT EXISTS draws (
        chat_id INTEGER NOT NULL,
        message_id INTEGER NOT NULL,
        result TEXT NOT NULL,
        PRIMARY KEY (chat_id, message_id)
    )
""")
db.execute("""
    CREATE TABLE IF NOT EXISTS state (
        key TEXT PRIMARY KEY,
        value INTEGER NOT NULL
    )
""")
db.commit()


def api(method, **params):
    response = session.post(
        f"{BASE_URL}/{method}",
        json=params,
        timeout=(10, 40),
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"{method}: {data.get('description', data)}")
    return data["result"]


bot = api("getMe")
bot_id = bot["id"]
bot_username = (bot.get("username") or "").lower()


def send(chat_id, text, thread_id=None):
    chunks = []
    current = ""
    for line in text.splitlines(keepends=True):
        if len(current) + len(line) > 3500 and current:
            chunks.append(current.rstrip())
            current = ""
        current += line
    if current:
        chunks.append(current.rstrip())

    for chunk in chunks:
        payload = {"chat_id": chat_id, "text": chunk}
        if thread_id is not None:
            payload["message_thread_id"] = thread_id
        api("sendMessage", **payload)


def display_name(user):
    return (
        " ".join(
            part for part in (user.get("first_name"), user.get("last_name"))
            if part
        ).strip()
        or str(user["id"])
    )


def is_current_member(member):
    status = member.get("status")
    return status in ("creator", "administrator", "member") or (
        status == "restricted" and member.get("is_member") is True
    )


def handle(message):
    chat = message.get("chat") or {}
    if chat.get("type") not in ("group", "supergroup"):
        return

    sender = message.get("from") or {}
    if not sender or sender.get("is_bot") or message.get("sender_chat"):
        return

    text = (message.get("text") or "").strip()
    if not text.startswith("/"):
        return

    parts = text.split()
    command, separator, addressed_to = parts[0].lower().partition("@")
    if separator and addressed_to != bot_username:
        return

    chat_id = chat["id"]
    user_id = sender["id"]
    thread_id = message.get("message_thread_id")

    def reply(t):
        send(chat_id, t, thread_id)

    if command == "/join":
        db.execute(
            """
            INSERT INTO participants (chat_id, user_id, name, username)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                name = excluded.name,
                username = excluded.username
            """,
            (chat_id, user_id, display_name(sender), sender.get("username")),
        )
        db.commit()
        reply(f"{display_name(sender)}، برای قرعه‌کشی ثبت شدی ✅")
        return

    if command == "/leave":
        db.execute(
            "DELETE FROM participants WHERE chat_id = ? AND user_id = ?",
            (chat_id, user_id),
        )
        db.commit()
        reply("از فهرست قرعه‌کشی خارج شدی.")
        return

    if command == "/count":
        count = db.execute(
            "SELECT COUNT(*) FROM participants WHERE chat_id = ?",
            (chat_id,),
        ).fetchone()[0]
        reply(f"تعداد افراد ثبت‌نام‌شده: {count}")
        return

    if command != "/draw":
        return

    admins = api("getChatAdministrators", chat_id=chat_id)
    admin_ids = {item["user"]["id"] for item in admins}
    if user_id not in admin_ids:
        reply("فقط مدیران گروه می‌توانند قرعه‌کشی کنند.")
        return

    if len(parts) != 2 or not parts[1].isdecimal() or int(parts[1]) < 1:
        reply("فرمت دستور: /draw 3")
        return

    count = int(parts[1])
    previous = db.execute(
        "SELECT result FROM draws WHERE chat_id = ? AND message_id = ?",
        (chat_id, message["message_id"]),
    ).fetchone()

    if previous:
        reply(previous[0])
        return

    bot_member = api("getChatMember", chat_id=chat_id, user_id=bot_id)
    if bot_member.get("status") not in ("creator", "administrator"):
        reply("برای بررسی عضویت شرکت‌کنندگان، بازو باید مدیر گروه باشد.")
        return

    registered = db.execute(
        "SELECT user_id, name, username FROM participants WHERE chat_id = ? ORDER BY user_id",
        (chat_id,),
    ).fetchall()

    eligible = []
    for candidate_id, saved_name, saved_username in registered:
        member = api("getChatMember", chat_id=chat_id, user_id=candidate_id)
        if not is_current_member(member):
            continue
        user = member.get("user") or {}
        if user.get("is_bot"):
            continue
        eligible.append((
            candidate_id,
            display_name(user) if user.get("id") else saved_name,
            user.get("username") or saved_username,
        ))

    if count > len(eligible):
        reply(f"تعداد درخواستی {count} نفر است، اما فقط {len(eligible)} نفر واجد شرایط عضو گروه‌اند.")
        return

    winners = secrets.SystemRandom().sample(eligible, count)
    lines = [f"🎉 نتیجهٔ قرعه‌کشی: {count} برنده از {len(eligible)} شرکت‌کننده", ""]
    for idx, (winner_id, name, username) in enumerate(winners, 1):
        handle_text = f" | @{username}" if username else ""
        lines.append(f"{idx}. {name}{handle_text} | شناسه: {winner_id}")

    result = "\n".join(lines)
    db.execute(
        "INSERT INTO draws (chat_id, message_id, result) VALUES (?, ?, ?)",
        (chat_id, message["message_id"], result),
    )
    db.commit()
    reply(result)


def main():
    while True:
        try:
            row = db.execute("SELECT value FROM state WHERE key = 'offset'").fetchone()
            offset = row[0] if row else 0

            updates = api("getUpdates", offset=offset, timeout=25)
            for update in updates:
                if "message" in update:
                    handle(update["message"])

                db.execute(
                    """
                    INSERT INTO state (key, value) VALUES ('offset', ?)
                    ON CONFLICT(key) DO UPDATE SET value = excluded.value
                    """,
                    (update["update_id"] + 1,),
                )
                db.commit()

        except (requests.RequestException, RuntimeError, ValueError) as exc:
            print(f"خطا: {exc}", flush=True)
            time.sleep(3)


if __name__ == "__main__":
    main()