import os
import sqlite3
import json
import hashlib
import hmac
from datetime import datetime
import telebot
from flask import Flask, render_template_string, request, jsonify, session
from apscheduler.schedulers.background import BackgroundScheduler
import re

# --- KONFIGURATION ---
TOKEN = os.environ.get("TELEGRAM_TOKEN", "DEIN_BOT_TOKEN_HIER")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "mdtotxt_bot")
SECRET_KEY = os.environ.get("SECRET_KEY", os.urandom(24))

app = Flask(__name__)
app.secret_key = SECRET_KEY
bot = telebot.TeleBot(TOKEN) if TOKEN else None

# --- DATENBANK INITIALISIERUNG ---
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

# --- HILFSFUNKTIONEN ---
def verify_telegram_auth(data):
    if not TOKEN: return False
    secret_key = hashlib.sha256(TOKEN.encode()).digest()
    data_check_arr = [f"{key}={value}" for key, value in sorted(data.items()) if key != 'hash']
    data_check_string = "\n".join(data_check_arr)
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return expected_hash == data.get('hash')

def format_to_tg_html(text):
    if not text: return ""
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.S)
    return text

def build_inline_keyboard(buttons_json):
    if not buttons_json or buttons_json == "[]": return None
    try:
        buttons = json.loads(buttons_json)
        markup = telebot.types.InlineKeyboardMarkup()
        for btn in buttons:
            if btn.get('text') and btn.get('url'):
                markup.add(telebot.types.InlineKeyboardButton(text=btn['text'], url=btn['url']))
        return markup
    except:
        return None

# --- SCHEDULER (Hintergrund-Job) ---
def check_scheduled_posts():
    if not bot: return
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("SELECT id, channel_id, content, buttons FROM posts WHERE status='pending' AND schedule_time <= ?", (now,))
        due_posts = c.fetchall()

        for post in due_posts:
            post_id, channel_id, content, buttons_json = post
            try:
                html_content = format_to_tg_html(content)
                markup = build_inline_keyboard(buttons_json)
                bot.send_message(channel_id, html_content, parse_mode='HTML', reply_markup=markup)
                c.execute("UPDATE posts SET status='sent' WHERE id=?", (post_id,))
            except Exception as e:
                print(f"Fehler bei Post {post_id}: {e}")
                c.execute("UPDATE posts SET status='failed' WHERE id=?", (post_id,))
        conn.commit()

scheduler = BackgroundScheduler()
scheduler.add_job(func=check_scheduled_posts, trigger="interval", seconds=60)
scheduler.start()

# --- HTML TEMPLATE (SaaS Frontend) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>PostMaster Pro SaaS</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        .tg-bubble { background: white; padding: 15px; border-radius: 12px; border-bottom-right-radius: 4px; box-shadow: 0 4px 6px rgba(0,0,0,0.1); width: 100%; word-wrap: break-word; }
        .tg-button { display: block; text-align: center; background: #e0f2fe; color: #0284c7; padding: 8px; border-radius: 8px; margin-top: 8px; text-decoration: none; font-weight: bold; }
        .tab-content { display: none; }
        .tab-content.active { display: block; }
    </style>
</head>
<body class="bg-slate-50 text-slate-800 font-sans h-screen flex overflow-hidden">

    <aside class="w-64 bg-white border-r border-slate-200 flex flex-col z-10">
        <div class="p-6 border-b border-slate-100">
            <h1 class="text-2xl font-black text-sky-600 flex items-center gap-2"><i class="fa-solid fa-rocket"></i> PostMaster</h1>
            <p class="text-xs text-slate-400 mt-1 uppercase font-bold tracking-wider">Pro Edition</p>
        </div>

        {% if logged_in %}
        <div class="p-4 border-b border-slate-100 flex items-center gap-3">
            <div class="w-10 h-10 rounded-full bg-sky-100 flex items-center justify-center text-sky-600 font-bold">
                {{ user.first_name[0] }}
            </div>
            <div class="overflow-hidden">
                <p class="font-bold text-sm truncate">{{ user.first_name }}</p>
                <button onclick="logout()" class="text-xs text-red-500 hover:underline">Abmelden</button>
            </div>
        </div>

        <nav class="flex-1 p-4 space-y-2 overflow-y-auto">
            <button onclick="switchTab('editor')" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left font-medium hover:bg-sky-50 text-slate-600 hover:text-sky-600 transition-colors" id="nav-editor"><i class="fa-solid fa-pen-nib w-5"></i> Post erstellen</button>
            <button onclick="switchTab('channels')" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left font-medium hover:bg-sky-50 text-slate-600 hover:text-sky-600 transition-colors" id="nav-channels"><i class="fa-solid fa-tower-broadcast w-5"></i> Meine Kanäle</button>
            <button onclick="switchTab('history')" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left font-medium hover:bg-sky-50 text-slate-600 hover:text-sky-600 transition-colors" id="nav-history"><i class="fa-solid fa-calendar-check w-5"></i> Geplant & Historie</button>
            <button onclick="switchTab('dsgvo')" class="w-full flex items-center gap-3 px-4 py-3 rounded-xl text-left font-medium hover:bg-sky-50 text-slate-600 hover:text-sky-600 transition-colors" id="nav-dsgvo"><i class="fa-solid fa-shield-halved w-5"></i> Manual & DSGVO</button>
        </nav>
        {% else %}
        <div class="flex-1 flex items-center justify-center p-6 text-center">
            <div>
                <i class="fa-brands fa-telegram text-5xl text-sky-500 mb-4"></i>
                <h2 class="font-bold text-lg mb-2">Bitte anmelden</h2>
                <p class="text-sm text-slate-500 mb-6">Logge dich via Telegram ein, um Kanäle zu verwalten.</p>
                <script async src="https://telegram.org/js/telegram-widget.js?22" data-telegram-login="{{ bot_username }}" data-size="large" data-onauth="onTelegramAuth(user)" data-request-access="write"></script>
            </div>
        </div>
        {% endif %}
    </aside>

    <main class="flex-1 overflow-y-auto bg-slate-50 relative">
        {% if logged_in %}

        <!-- EDITOR TAB -->
        <div id="tab-editor" class="tab-content active max-w-6xl mx-auto p-8">
            <h2 class="text-2xl font-bold mb-6">Neuen Post erstellen</h2>

            <div class="grid grid-cols-1 lg:grid-cols-2 gap-8">
                <div class="space-y-6">
                    <!-- Kanal Auswahl -->
                    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
                        <label class="block text-sm font-bold text-slate-700 mb-2">Ziel-Kanal</label>
                        <select id="postChannel" class="w-full p-3 border border-slate-300 rounded-xl outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100 bg-slate-50">
                            <!-- Wird per JS befüllt. Standardanzeige: Lade... -->
                            <option value="">Lade Kanäle...</option>
                        </select>
                    </div>

                    <!-- Text Editor -->
                    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm flex flex-col h-64">
                        <label class="block text-sm font-bold text-slate-700 mb-2">Inhalt (Markdown unterstützt)</label>
                        <textarea id="postContent" oninput="updatePreview()" class="flex-1 w-full p-3 border border-slate-300 rounded-xl outline-none focus:border-sky-500 focus:ring-2 focus:ring-sky-100 resize-none font-mono text-sm" placeholder="# Mega Angebot\n\nHier kommt der Text..."></textarea>
                    </div>

                    <!-- Inline Buttons Builder -->
                    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
                        <div class="flex justify-between items-center mb-3">
                            <label class="block text-sm font-bold text-slate-700">Inline Buttons (optional)</label>
                            <button onclick="addButtonField()" class="text-sky-600 text-sm font-bold hover:underline"><i class="fa-solid fa-plus"></i> Button anlegen</button>
                        </div>
                        <div id="buttonsContainer" class="space-y-3"></div>
                    </div>

                    <!-- Scheduling -->
                    <div class="bg-white p-5 rounded-2xl border border-slate-200 shadow-sm">
                        <label class="block text-sm font-bold text-slate-700 mb-2">Wann posten?</label>
                        <input type="datetime-local" id="postSchedule" class="w-full p-3 border border-slate-300 rounded-xl outline-none focus:border-sky-500">
                        <p class="text-xs text-slate-500 mt-2">Leer lassen für sofortigen Versand.</p>
                    </div>

                    <button onclick="submitPost()" id="submitBtn" class="w-full bg-sky-600 hover:bg-sky-700 text-white font-bold py-4 rounded-xl shadow-lg shadow-sky-200 transition-all flex justify-center items-center gap-2">
                        <i class="fa-solid fa-paper-plane"></i> Post einplanen / senden
                    </button>
                </div>

                <!-- Live Preview -->
                <div>
                    <div class="sticky top-8 bg-[#8ab4f8]/20 p-8 rounded-3xl h-[600px] flex justify-center items-center overflow-y-auto shadow-inner" style="background-image: url('https://www.transparenttextures.com/patterns/cubes.png');">
                        <div class="max-w-sm w-full">
                            <div class="tg-bubble shadow-xl" id="previewBox">
                                <div id="previewText" class="text-sm">Vorschau deines Textes...</div>
                                <div id="previewButtons" class="mt-3 space-y-2 border-t pt-3 border-slate-100 empty:hidden"></div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <!-- CHANNELS TAB -->
        <div id="tab-channels" class="tab-content max-w-4xl mx-auto p-8">
            <h2 class="text-2xl font-bold mb-6">Deine Kanäle verwalten</h2>

            <div class="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm mb-8">
                <h3 class="font-bold text-lg mb-2">Neuen Kanal verknüpfen</h3>
                <p class="text-sm text-slate-500 mb-4">WICHTIG: Füge deinen Bot <b>zuerst</b> als Administrator (mit Post-Rechten) in deinen Telegram-Kanal ein. Trage dann hier die ID oder den öffentlichen @Namen ein.</p>
                <div class="flex gap-3">
                    <input type="text" id="newChannelId" placeholder="z.B. @mein_kanal oder -100123..." class="flex-1 p-3 border border-slate-300 rounded-xl outline-none focus:border-sky-500">
                    <input type="text" id="newChannelName" placeholder="Interner Anzeigename (z.B. Main Channel)" class="flex-1 p-3 border border-slate-300 rounded-xl outline-none focus:border-sky-500">
                    <button onclick="addChannel()" id="addChannelBtn" class="bg-slate-800 hover:bg-slate-900 text-white px-6 rounded-xl font-bold flex items-center gap-2">Hinzufügen</button>
                </div>
            </div>

            <h3 class="font-bold text-lg mb-4">Verknüpfte Kanäle</h3>
            <div id="channelsList" class="space-y-3">
                <!-- Wird per JS befüllt -->
            </div>
        </div>

        <!-- HISTORY TAB -->
        <div id="tab-history" class="tab-content max-w-5xl mx-auto p-8">
            <h2 class="text-2xl font-bold mb-6">Geplant & Historie</h2>
            <div class="bg-white rounded-2xl border border-slate-200 shadow-sm overflow-hidden">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-slate-50 border-b border-slate-200">
                            <th class="p-4 font-bold text-slate-600">Datum / Zeit</th>
                            <th class="p-4 font-bold text-slate-600">Kanal</th>
                            <th class="p-4 font-bold text-slate-600">Vorschau</th>
                            <th class="p-4 font-bold text-slate-600">Status</th>
                        </tr>
                    </thead>
                    <tbody id="historyTableBody" class="divide-y divide-slate-100">
                        <!-- Wird per JS befüllt -->
                    </tbody>
                </table>
            </div>
        </div>

        <!-- DSGVO & MANUAL TAB -->
        <div id="tab-dsgvo" class="tab-content max-w-4xl mx-auto p-8">
            <div class="bg-white p-8 rounded-2xl border border-slate-200 shadow-sm prose max-w-none text-slate-600">
                <h2 class="text-sky-600">📖 Anleitung: So verknüpfst du einen Kanal</h2>
                <ol>
                    <li><b>Bot einladen:</b> Öffne deinen Telegram Kanal. Gehe in die Kanal-Einstellungen &rarr; Administratoren &rarr; Administrator hinzufügen.</li>
                    <li><b>Suchen:</b> Suche nach <code>@{{ bot_username }}</code> und füge den Bot hinzu.</li>
                    <li><b>Rechte geben:</b> Achte darauf, dass der Schalter bei "Nachrichten senden" (Post Messages) aktiviert ist.</li>
                    <li><b>Hier eintragen:</b> Gehe im Tool auf den Reiter "Meine Kanäle". Trage dort den Handle deines Kanals ein (z.B. <code>@mein_cooler_kanal</code>). Falls es ein privater Kanal ist, nutze die interne ID (beginnt meist mit <code>-100...</code>).</li>
                </ol>

                <h2 class="text-sky-600 mt-8">🛡️ DSGVO & Datenschutz</h2>
                <p>Diese SaaS-Anwendung speichert nur die Daten, die für die Bereitstellung des Dienstes absolut notwendig sind:</p>
                <ul>
                    <li>Deine Telegram Benutzer-ID und dein Name.</li>
                    <li>Die IDs der verknüpften Telegram-Kanäle.</li>
                    <li>Die Inhalte der von dir geplanten Posts in der Datenbank, bis sie versendet wurden.</li>
                </ul>

                <div class="mt-8 p-6 bg-red-50 border border-red-200 rounded-xl">
                    <h3 class="text-red-700 m-0">Gefahrzone: Account löschen</h3>
                    <button onclick="deleteAccountData()" class="mt-4 bg-red-600 hover:bg-red-700 text-white font-bold py-2 px-6 rounded-lg transition-colors">Alle meine Daten endgültig löschen</button>
                </div>
            </div>
        </div>

        {% endif %}
    </main>

    <script>
        function onTelegramAuth(user) {
            fetch('/api/auth', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(user)
            }).then(res => res.json()).then(data => {
                if (data.success) window.location.reload();
                else alert('Login fehlgeschlagen: Hash ungültig.');
            });
        }

        function logout() {
            fetch('/api/logout', {method: 'POST'}).then(() => window.location.reload());
        }

        {% if logged_in %}
        function switchTab(tabId) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('[id^="nav-"]').forEach(el => {
                el.classList.remove('bg-sky-50', 'text-sky-600');
                el.classList.add('text-slate-600');
            });
            document.getElementById('tab-' + tabId).classList.add('active');
            document.getElementById('nav-' + tabId).classList.add('bg-sky-50', 'text-sky-600');
            document.getElementById('nav-' + tabId).classList.remove('text-slate-600');

            if(tabId === 'channels') loadChannels();
            if(tabId === 'editor') populateEditorChannels();
            if(tabId === 'history') loadHistory();
        }

        async function loadChannels() {
            const res = await fetch('/api/channels');
            const channels = await res.json();
            const list = document.getElementById('channelsList');
            list.innerHTML = channels.length === 0 ? '<p class="text-slate-500 italic">Noch keine Kanäle verknüpft.</p>' : '';
            channels.forEach(ch => {
                list.innerHTML += `
                    <div class="flex justify-between items-center p-4 bg-slate-50 border border-slate-200 rounded-xl">
                        <div><p class="font-bold">${ch.channel_name}</p><p class="text-xs text-slate-500 font-mono">${ch.channel_id}</p></div>
                        <button onclick="deleteChannel(${ch.id})" class="text-red-500 hover:bg-red-50 p-2 rounded-lg"><i class="fa-solid fa-trash"></i></button>
                    </div>`;
            });
        }

        async function addChannel() {
            const cId = document.getElementById('newChannelId').value.trim();
            const cName = document.getElementById('newChannelName').value.trim();
            const btn = document.getElementById('addChannelBtn');

            if(!cId || !cName) return alert('Bitte Kanal-ID/Name und einen Anzeigenamen ausfüllen.');

            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i> Prüfe...';
            btn.disabled = true;

            try {
                const res = await fetch('/api/channels', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel_id: cId, channel_name: cName })
                });
                const data = await res.json();

                if(data.success) {
                    document.getElementById('newChannelId').value = '';
                    document.getElementById('newChannelName').value = '';
                    loadChannels();
                } else {
                    alert('FEHLER: ' + data.error);
                }
            } catch (err) {
                alert('Netzwerkfehler. Bitte versuche es nochmal.');
            } finally {
                btn.innerHTML = 'Hinzufügen';
                btn.disabled = false;
            }
        }

        async function deleteChannel(id) {
            if(!confirm('Kanal wirklich entfernen?')) return;
            await fetch('/api/channels/' + id, {method: 'DELETE'});
            loadChannels();
        }

        async function populateEditorChannels() {
            const sel = document.getElementById('postChannel');
            const submitBtn = document.getElementById('submitBtn');

            try {
                const res = await fetch('/api/channels');
                const channels = await res.json();

                if (channels.length === 0) {
                    // UX VERBESSERUNG HIER: Zeige an, wenn leer
                    sel.innerHTML = '<option value="">⚠️ Bitte erst im Tab "Meine Kanäle" einen Kanal hinzufügen!</option>';
                    sel.disabled = true;
                    sel.classList.add('bg-red-50', 'border-red-300', 'text-red-700');
                    submitBtn.disabled = true;
                    submitBtn.classList.replace('bg-sky-600', 'bg-slate-400');
                } else {
                    sel.disabled = false;
                    sel.classList.remove('bg-red-50', 'border-red-300', 'text-red-700');
                    sel.innerHTML = channels.map(ch => `<option value="${ch.channel_id}">${ch.channel_name} (${ch.channel_id})</option>`).join('');
                    submitBtn.disabled = false;
                    submitBtn.classList.replace('bg-slate-400', 'bg-sky-600');
                }
            } catch (e) {
                sel.innerHTML = '<option value="">Fehler beim Laden der Kanäle</option>';
            }
        }

        function addButtonField() {
            const container = document.getElementById('buttonsContainer');
            const div = document.createElement('div');
            div.className = 'flex gap-2 items-center button-row';
            div.innerHTML = `
                <input type="text" placeholder="Button Text" class="btn-text flex-1 p-2 border border-slate-300 rounded-lg text-sm outline-none focus:border-sky-500">
                <input type="url" placeholder="URL (https://...)" class="btn-url flex-1 p-2 border border-slate-300 rounded-lg text-sm outline-none focus:border-sky-500">
                <button onclick="this.parentElement.remove(); updatePreview();" class="text-red-500 px-2"><i class="fa-solid fa-xmark"></i></button>
            `;
            container.appendChild(div);
            div.querySelectorAll('input').forEach(inp => inp.addEventListener('input', updatePreview));
        }

        function getButtonsData() {
            const rows = document.querySelectorAll('.button-row');
            let btns = [];
            rows.forEach(row => {
                const text = row.querySelector('.btn-text').value.trim();
                const url = row.querySelector('.btn-url').value.trim();
                if(text && url) btns.push({text, url});
            });
            return btns;
        }

        async function updatePreview() {
            const text = document.getElementById('postContent').value;
            const res = await fetch('/api/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: text})
            });
            const data = await res.json();
            document.getElementById('previewText').innerHTML = data.html.replace(/\\n/g, '<br>') || 'Vorschau deines Textes...';

            const btns = getButtonsData();
            const btnContainer = document.getElementById('previewButtons');
            btnContainer.innerHTML = '';
            btns.forEach(btn => {
                btnContainer.innerHTML += `<a href="#" onclick="return false;" class="tg-button">${btn.text}</a>`;
            });
        }

        async function submitPost() {
            const channelId = document.getElementById('postChannel').value;
            const content = document.getElementById('postContent').value;
            const schedule = document.getElementById('postSchedule').value;
            const buttons = getButtonsData();

            if(!channelId) return alert('Bitte wähle einen Kanal aus.');
            if(!content) return alert('Bitte Text eingeben.');

            let scheduleTime = schedule ? schedule.replace('T', ' ') : new Date().toISOString().slice(0, 16).replace('T', ' ');

            const res = await fetch('/api/posts', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    channel_id: channelId,
                    content: content,
                    buttons: JSON.stringify(buttons),
                    schedule_time: scheduleTime
                })
            });
            const data = await res.json();
            if(data.success) {
                alert(schedule ? 'Post erfolgreich geplant! 🕒' : 'Post wurde sofort zum Versand freigegeben! 🚀');
                document.getElementById('postContent').value = '';
                document.getElementById('buttonsContainer').innerHTML = '';
                document.getElementById('postSchedule').value = '';
                updatePreview();
                switchTab('history');
            } else {
                alert('Fehler: ' + data.error);
            }
        }

        async function loadHistory() {
            const res = await fetch('/api/posts');
            const posts = await res.json();
            const tbody = document.getElementById('historyTableBody');
            tbody.innerHTML = posts.length === 0 ? '<tr><td colspan="4" class="p-4 text-center text-slate-500">Keine Posts vorhanden.</td></tr>' : '';

            posts.forEach(p => {
                let statusBadge = '';
                if(p.status === 'pending') statusBadge = '<span class="bg-yellow-100 text-yellow-800 text-xs font-bold px-2 py-1 rounded-full">Ausstehend</span>';
                else if(p.status === 'sent') statusBadge = '<span class="bg-green-100 text-green-800 text-xs font-bold px-2 py-1 rounded-full">Gesendet</span>';
                else statusBadge = '<span class="bg-red-100 text-red-800 text-xs font-bold px-2 py-1 rounded-full">Fehler</span>';

                tbody.innerHTML += `
                    <tr>
                        <td class="p-4 text-sm whitespace-nowrap">${p.schedule_time}</td>
                        <td class="p-4 text-sm font-mono text-sky-600">${p.channel_id}</td>
                        <td class="p-4 text-sm truncate max-w-xs">${p.content.substring(0, 40)}...</td>
                        <td class="p-4">${statusBadge}</td>
                    </tr>`;
            });
        }

        async function deleteAccountData() {
            if(confirm('ACHTUNG! Willst du wirklich deinen Account und alle verknüpften Daten unwiderruflich löschen?')) {
                const res = await fetch('/api/dsgvo_delete', { method: 'POST' });
                if(res.ok) {
                    alert('Deine Daten wurden restlos gelöscht.');
                    window.location.reload();
                }
            }
        }

        // Init
        switchTab('editor');
        {% endif %}
    </script>
</body>
</html>
"""

# --- ROUTEN / API ---
@app.route('/')
def index():
    user_id = session.get('user_id')
    user = None
    if user_id:
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("SELECT first_name, username FROM users WHERE id=?", (user_id,))
            row = c.fetchone()
            if row: user = {'first_name': row[0], 'username': row[1]}
            else: session.pop('user_id', None)

    return render_template_string(HTML_TEMPLATE, logged_in=bool(user), user=user, bot_username=BOT_USERNAME)

@app.route('/api/auth', methods=['POST'])
def auth():
    data = request.json
    if verify_telegram_auth(data):
        user_id = data.get('id')
        with sqlite3.connect(DB_FILE) as conn:
            c = conn.cursor()
            c.execute("INSERT OR REPLACE INTO users (id, first_name, username, auth_date) VALUES (?, ?, ?, ?)",
                      (user_id, data.get('first_name'), data.get('username'), data.get('auth_date')))
            conn.commit()
        session['user_id'] = user_id
        return jsonify({'success': True})
    return jsonify({'success': False}), 401

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_id', None)
    return jsonify({'success': True})

@app.route('/api/channels', methods=['GET', 'POST'])
def manage_channels():
    if not session.get('user_id'): return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']

    if request.method == 'GET':
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.cursor().execute("SELECT id, channel_id, channel_name FROM channels WHERE user_id=?", (user_id,)).fetchall()
            return jsonify([dict(ix) for ix in rows])

    if request.method == 'POST':
        data = request.json
        c_id = data.get('channel_id')

        try:
            # 1. Prüfen, ob der Chat überhaupt existiert (und Bot reingelassen wurde)
            chat = bot.get_chat(c_id)

            # 2. Prüfen, ob der Bot Admin-Rechte hat
            bot_me = bot.get_me()
            member = bot.get_chat_member(c_id, bot_me.id)

            if member.status not in ['administrator', 'creator']:
                return jsonify({'success': False, 'error': f'Der Bot ist kein Administrator im Kanal {c_id}. Bitte in Telegram die Rechte anpassen.'})
            if not member.can_post_messages:
                return jsonify({'success': False, 'error': f'Der Bot hat nicht die Berechtigung "Nachrichten senden" im Kanal {c_id}.'})

        except telebot.apihelper.ApiTelegramException as e:
            error_msg = str(e)
            if "chat not found" in error_msg.lower():
                return jsonify({'success': False, 'error': f'Kanal {c_id} wurde nicht gefunden. Wenn es ein privater Kanal ist, nutze die ID (z.B. -100...). Sonst das @Handle.'})
            elif "bot is not a member" in error_msg.lower():
                return jsonify({'success': False, 'error': 'Der Bot muss zuerst als Administrator in den Kanal eingeladen werden!'})
            else:
                return jsonify({'success': False, 'error': f'Telegram Fehler: {error_msg}'})
        except Exception as e:
            return jsonify({'success': False, 'error': f'Unbekannter Fehler beim Verifizieren: {str(e)}'})

        # Wenn wir hier sind, ist alles okay -> in Datenbank speichern
        with sqlite3.connect(DB_FILE) as conn:
            conn.cursor().execute("INSERT INTO channels (user_id, channel_id, channel_name) VALUES (?, ?, ?)",
                                  (user_id, c_id, data.get('channel_name')))
            conn.commit()
        return jsonify({'success': True})

@app.route('/api/channels/<int:cid>', methods=['DELETE'])
def delete_channel(cid):
    if not session.get('user_id'): return jsonify({'error': 'Unauthorized'}), 401
    with sqlite3.connect(DB_FILE) as conn:
        conn.cursor().execute("DELETE FROM channels WHERE id=? AND user_id=?", (cid, session['user_id']))
        conn.commit()
    return jsonify({'success': True})

@app.route('/api/preview', methods=['POST'])
def preview():
    data = request.json
    return jsonify({'html': format_to_tg_html(data.get('text', ''))})

@app.route('/api/posts', methods=['GET', 'POST'])
def manage_posts():
    if not session.get('user_id'): return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']

    if request.method == 'GET':
        with sqlite3.connect(DB_FILE) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.cursor().execute("SELECT * FROM posts WHERE user_id=? ORDER BY schedule_time DESC LIMIT 50", (user_id,)).fetchall()
            return jsonify([dict(ix) for ix in rows])

    if request.method == 'POST':
        data = request.json
        with sqlite3.connect(DB_FILE) as conn:
            conn.cursor().execute("INSERT INTO posts (user_id, channel_id, content, buttons, schedule_time, status) VALUES (?, ?, ?, ?, ?, ?)",
                                  (user_id, data['channel_id'], data['content'], data['buttons'], data['schedule_time'], 'pending'))
            conn.commit()
        return jsonify({'success': True})

@app.route('/api/dsgvo_delete', methods=['POST'])
def dsgvo_delete():
    if not session.get('user_id'): return jsonify({'error': 'Unauthorized'}), 401
    user_id = session['user_id']
    with sqlite3.connect(DB_FILE) as conn:
        c = conn.cursor()
        c.execute("DELETE FROM users WHERE id=?", (user_id,))
        c.execute("DELETE FROM channels WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM posts WHERE user_id=?", (user_id,))
        conn.commit()
    session.pop('user_id', None)
    return jsonify({'success': True})

if __name__ == '__main__':
    import atexit
    atexit.register(lambda: scheduler.shutdown())
    app.run(host='0.0.0.0', port=5000)
