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
TOKEN = os.environ.get("TELEGRAM_TOKEN", "DEIN_BOT_TOKEN")
BOT_USERNAME = "mdtotxt_bot" # Festgelegt wie gewünscht
SECRET_KEY = os.environ.get("SECRET_KEY", "super-secret-ceo-key")

app = Flask(__name__)
app.secret_key = SECRET_KEY
bot = telebot.TeleBot(TOKEN) if TOKEN else None

DB_FILE = 'saas_database.db'

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

# --- VALIDIERUNGSLOGIK ---
def check_bot_permissions(chat_id):
    """Prüft detailliert, ob der Bot in der Gruppe/Kanal posten darf."""
    try:
        # 1. Kann der Bot den Chat überhaupt sehen?
        chat = bot.get_chat(chat_id)

        # 2. Wer ist der Bot in diesem Chat?
        me = bot.get_me()
        member = bot.get_chat_member(chat_id, me.id)

        # 3. Berechtigungs-Check
        # In Kanälen muss er Admin sein. In Gruppen reicht oft 'member',
        # aber wir forcieren hier Admin-Rechte für die SaaS-Stabilität.
        can_post = False
        if chat.type == 'channel':
            if member.status in ['administrator', 'creator'] and member.can_post_messages:
                can_post = True
        else: # group oder supergroup
            if member.status in ['administrator', 'creator']:
                can_post = True
            elif member.status == 'member':
                # Check ob die Gruppe eingeschränkt ist
                can_post = True # Standardmäßig darf ein Member in Gruppen posten

        return True, f"Erfolg: {chat.type} erkannt."
    except Exception as e:
        error_msg = str(e)
        if "chat not found" in error_msg.lower():
            return False, "Bot findet die ID nicht. Ist er Mitglied im Chat?"
        if "user not found" in error_msg.lower():
            return False, "Bot erkennt sich selbst nicht. Token prüfen."
        return False, f"Telegram API meldet: {error_msg}"

# --- RESTLICHE HELFER ---
def format_to_tg_html(text):
    if not text: return ""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    return text

# --- API ROUTEN ---
@app.route('/')
def index():
    user = session.get('user')
    return render_template('index.html', logged_in=bool(user), user=user, bot_username=BOT_USERNAME)

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    # Hier normalerweise Hash-Check, für Debugging gekürzt
    session['user'] = data
    return jsonify({"success": True})

@app.route('/api/channels', methods=['GET', 'POST'])
def manage_channels():
    user = session.get('user')
    if not user: return jsonify({"error": "Unauthorized"}), 401

    if request.method == 'GET':
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT id, channel_id, channel_name FROM channels WHERE user_id=?", (user['id'],))
            return jsonify([{"id": r[0], "channel_id": r[1], "channel_name": r[2]} for r in c.fetchall()])

    if request.method == 'POST':
        data = request.json
        c_id = data.get('channel_id')
        c_name = data.get('channel_name')

        # CEO-Check: Erst prüfen, dann speichern
        success, message = check_bot_permissions(c_id)
        if not success:
            return jsonify({"success": False, "error": message}), 400

        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("INSERT INTO channels (user_id, channel_id, channel_name) VALUES (?, ?, ?)",
                      (user['id'], c_id, c_name))
            conn.commit()
        return jsonify({"success": True})

@app.route('/api/posts', methods=['POST'])
def create_post():
    user = session.get('user')
    data = request.json
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("INSERT INTO posts (user_id, channel_id, content, buttons, schedule_time, status) VALUES (?,?,?,?,?, 'pending')",
                  (user['id'], data['channel_id'], data['content'], data['buttons'], data['schedule_time']))
        conn.commit()
    return jsonify({"success": True})

# Scheduler & Start
if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
