import os
import sqlite3
import json
import hashlib
import hmac
from datetime import datetime
import telebot
from flask import Flask, render_template, request, jsonify, session
from apscheduler.schedulers.background import BackgroundScheduler
import re

# --- KONFIGURATION ---
TOKEN = os.environ.get("TELEGRAM_TOKEN", "DEIN_BOT_TOKEN_HIER")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "DeinBotName")
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24))

app = Flask(__name__)
app.secret_key = SECRET_KEY

# Bot nur initialisieren, wenn ein echtes Token da ist
bot = telebot.TeleBot(TOKEN) if TOKEN and TOKEN != "DEIN_BOT_TOKEN_HIER" else None

# Absolute Pfade für die Datenbank (Thread-Sicherheit)
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_FILE = os.path.join(BASE_DIR, 'saas_database.db')

# --- DATENBANK INITIALISIERUNG ---
def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users
                     (id INTEGER PRIMARY KEY, first_name TEXT, username TEXT, auth_date INTEGER)''')
        c.execute('''CREATE TABLE IF NOT EXISTS channels
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel_id TEXT, channel_name TEXT)''')
        c.execute('''CREATE TABLE IF NOT EXISTS posts
                     (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, channel_id TEXT,
                      content TEXT, buttons TEXT, schedule_time TEXT, status TEXT)''')
        conn.commit()

init_db()

# --- HILFSFUNKTIONEN ---
def verify_telegram_auth(data):
    """Exakte Telegram-Login-Hash-Verifizierung (offiziell & DSGVO-konform)"""
    if not TOKEN or TOKEN == "DEIN_BOT_TOKEN_HIER":
        return False
    secret_key = hashlib.sha256(TOKEN.encode()).digest()
    data_check_arr = [f"{key}={value}" for key, value in sorted(data.items()) if key != 'hash']
    data_check_string = "\n".join(data_check_arr)
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return expected_hash == data.get('hash')

def format_to_tg_html(text):
    if not text:
        return ""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.S)
    return text

def check_bot_permissions(chat_id):
    """Strikte & stabile Rechte-Prüfung für Kanäle & Gruppen"""
    if not bot:
        return False, "Bot-Token ist nicht konfiguriert."

    try:
        chat_id_val = int(chat_id) if str(chat_id).lstrip('-').isdigit() else chat_id
        chat = bot.get_chat(chat_id_val)
        me = bot.get_me()
        member = bot.get_chat_member(chat_id_val, me.id)

        if chat.type == 'channel':
            if member.status in ['administrator', 'creator'] and member.can_post_messages:
                return True, "OK"
            return False, "Bot hat nicht das Recht 'Nachrichten senden'."
        else:
            if member.status in ['administrator', 'creator', 'member']:
                return True, "OK"
            return False, "Bot hat keine ausreichenden Rechte."

    except telebot.apihelper.ApiTelegramException as e:
        err = e.description.lower()
        if "chat not found" in err:
            return False, "Chat nicht gefunden. Bot wirklich als Admin hinzugefügt?"
        if "not a member" in err or "kicked" in err or "forbidden" in err:
            return False, "Bot ist nicht im Chat oder wurde entfernt."
        return False, f"Telegram-Fehler: {e.description}"
    except Exception as e:
        return False, f"Systemfehler: {str(e)}"

# --- SCHEDULER ---
def check_scheduled_posts():
    if not bot:
        return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, channel_id, content, buttons FROM posts WHERE status='pending' AND schedule_time <= ?", (now,))
        due_posts = c.fetchall()

        for post_id, channel_id, content, buttons_json in due_posts:
            try:
                cid = int(channel_id) if str(channel_id).lstrip('-').isdigit() else channel_id
                html_content = format_to_tg_html(content)

                markup = telebot.types.InlineKeyboardMarkup(row_width=1)
                if buttons_json and buttons_json != "[]":
                    for btn in json.loads(buttons_json):
                        markup.add(telebot.types.InlineKeyboardButton(text=btn['text'], url=btn['url']))

                bot.send_message(chat_id=cid, text=html_content, parse_mode='HTML', reply_markup=markup)
                c.execute("UPDATE posts SET status='sent' WHERE id=?", (post_id,))
            except Exception as e:
                print(f"Sendefehler Post {post_id}: {e}")
                c.execute("UPDATE posts SET status='failed' WHERE id=?", (post_id,))
        conn.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_scheduled_posts, trigger="interval", seconds=30)
scheduler.start()

# --- ROUTEN ---
@app.route('/')
def index():
    user = session.get('user')
    return render_template('index.html', logged_in=bool(user), user=user, bot_username=BOT_USERNAME)

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if verify_telegram_auth(data):
        session['user'] = data
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO users (id, first_name, username, auth_date) VALUES (?, ?, ?, ?)",
                      (data.get('id'), data.get('first_name'), data.get('username'), data.get('auth_date')))
            conn.commit()
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Authentifizierung fehlgeschlagen"}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user', None)
    return jsonify({"success": True})

@app.route('/api/channels', methods=['GET', 'POST'])
def manage_channels():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'GET':
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id, channel_id, channel_name FROM channels WHERE user_id=?", (user['id'],))
            return jsonify([{"id": r[0], "channel_id": r[1], "channel_name": r[2]} for r in c.fetchall()])

    if request.method == 'POST':
        data = request.json
        channel_id = str(data.get('channel_id', '')).strip()
        channel_name = data.get('channel_name', '').strip()

        if not channel_id or not channel_name:
            return jsonify({"success": False, "error": "ID und Name erforderlich"}), 400

        success, msg = check_bot_permissions(channel_id)
        if not success:
            return jsonify({"success": False, "error": msg}), 400

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id FROM channels WHERE user_id=? AND channel_id=?", (user['id'], channel_id))
            if c.fetchone():
                return jsonify({"success": False, "error": "Kanal bereits verknüpft"}), 400

            c.execute("INSERT INTO channels (user_id, channel_id, channel_name) VALUES (?, ?, ?)",
                      (user['id'], channel_id, channel_name))
            conn.commit()
        return jsonify({"success": True})

@app.route('/api/channels/<int:cid>', methods=['DELETE'])
def delete_channel(cid):
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM channels WHERE id=? AND user_id=?", (cid, user['id']))
        conn.commit()
    return jsonify({"success": True})

@app.route('/api/posts', methods=['GET', 'POST'])
def manage_posts():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'GET':
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id, channel_id, content, schedule_time, status FROM posts WHERE user_id=? ORDER BY schedule_time DESC LIMIT 50", (user['id'],))
            return jsonify([{"id": r[0], "channel_id": r[1], "content": r[2], "schedule_time": r[3], "status": r[4]} for r in c.fetchall()])

    if request.method == 'POST':
        data = request.json
        channel_id = data.get('channel_id')
        content = data.get('content', '').strip()
        buttons = data.get('buttons', '[]')
        schedule_time = data.get('schedule_time', '').strip()

        if not channel_id or not content:
            return jsonify({"success": False, "error": "Kanal und Inhalt erforderlich"}), 400

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            # SICHERHEIT: Nur eigener Kanal darf verwendet werden
            c.execute("SELECT 1 FROM channels WHERE user_id=? AND channel_id=?", (user['id'], channel_id))
            if not c.fetchone():
                return jsonify({"success": False, "error": "Kanal gehört nicht zu dir"}), 403

            # SOFORT-SENDEN: Server-Zeit verwenden (keine Zeitzonen-Probleme)
            if not schedule_time:
                schedule_time = datetime.now().strftime("%Y-%m-%d %H:%M")

            c.execute("INSERT INTO posts (user_id, channel_id, content, buttons, schedule_time, status) VALUES (?,?,?,?,?, 'pending')",
                      (user['id'], channel_id, content, buttons, schedule_time))
            conn.commit()
        return jsonify({"success": True})

@app.route('/api/dsgvo_delete', methods=['POST'])
def dsgvo_delete():
    user = session.get('user')
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE id=?", (user['id'],))
        c.execute("DELETE FROM channels WHERE user_id=?", (user['id'],))
        c.execute("DELETE FROM posts WHERE user_id=?", (user['id'],))
        conn.commit()
    session.pop('user', None)
    return jsonify({"success": True})

if __name__ == '__main__':
    if not os.path.exists('templates'):
        os.makedirs('templates')
    app.run(host='0.0.0.0', port=5000, debug=True, use_reloader=False)
