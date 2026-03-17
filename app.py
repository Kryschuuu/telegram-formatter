import os
import telebot
import re
from flask import Flask, render_template_string, request, jsonify

# --- KONFIGURATION ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(TOKEN) if TOKEN else None
app = Flask(__name__)

# Der Spenden-Link bleibt wie gewünscht unverändert
BASE_COFFEE_URL = "https://buymeacoffee.com/rg4free"

def format_to_tg_html(text):
    """
    Konvertiert Standard-Markdown in sauberes Telegram-HTML.
    Headlines werden mit Emojis und Bold-Text hervorgehoben.
    """
    if not text:
        return ""

    # 1. HTML-Sonderzeichen escapen (Sicherheit zuerst)
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 2. Headlines formatieren (H1, H2, H3)
    # # Headline -> 🚀 FETTDRUCK
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)
    # ## Headline -> 📍 FETTDRUCK
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    # ### Headline -> 🔹 FETTDRUCK
    text = re.sub(r'^###\s+(.*)$', r'<b>🔹 \1</b>', text, flags=re.M)

    # 3. Fett & Kursiv (Standard MD)
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text) # Fett-Kursiv
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)           # Fett
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)               # Kursiv
    text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)               # Unterstrichen

    # 4. Listen (Bulletpoints & Nummerierung)
    # Bulletpoints: - oder * am Zeilenanfang -> •
    text = re.sub(r'^[*-]\s+', r'• ', text, flags=re.M)

    # 5. Code-Blöcke und Inline-Code
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.S)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)

    return text

def split_html_message(text, max_length=4000):
    """Teilt die HTML-Nachricht sicher auf, ohne Tags zu zerschneiden."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        # Suche nach dem letzten Zeilenumbruch innerhalb des Limits
        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    return chunks

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Post Master Pro</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css">
    <style>
        :root { --tg-blue: #24A1DE; --tg-bg: #547594; --sidebar-width: 260px; }
        * { box-sizing: border-box; transition: all 0.2s ease; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; display: flex; height: 100vh; background: #f0f2f5; }

        .sidebar { width: var(--sidebar-width); background: white; border-right: 1px solid #e0e0e0; display: flex; flex-direction: column; padding: 25px; }
        .logo { font-size: 22px; font-weight: 800; color: var(--tg-blue); margin-bottom: 40px; display: flex; align-items: center; gap: 12px; }

        .nav-item {
            padding: 14px 18px; margin-bottom: 8px; border-radius: 12px; cursor: pointer;
            display: flex; align-items: center; gap: 12px; color: #5f6368; text-decoration: none; font-weight: 500;
        }
        .nav-item:hover { background: #f8f9fa; color: var(--tg-blue); }
        .nav-item.active { background: var(--tg-blue); color: white; box-shadow: 0 4px 10px rgba(36, 161, 222, 0.3); }
        .sidebar-footer { margin-top: auto; padding-top: 20px; border-top: 1px solid #eee; }

        .main-content { flex: 1; display: flex; flex-direction: column; padding: 30px; overflow: hidden; }
        .view { display: none; height: 100%; flex-direction: column; gap: 20px; }
        .view.active { display: flex; }

        .editor-container { display: flex; gap: 25px; flex: 1; min-height: 0; }
        .panel { flex: 1; display: flex; flex-direction: column; gap: 10px; }
        .panel-label { font-weight: bold; font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: 1px; }

        textarea {
            flex: 1; border: 2px solid #e0e0e0; border-radius: 16px; padding: 20px;
            font-family: 'Fira Code', 'Consolas', monospace; resize: none; outline: none; font-size: 15px;
            background: white; line-height: 1.6;
        }
        textarea:focus { border-color: var(--tg-blue); }

        .preview-box {
            flex: 1; background: var(--tg-bg); border-radius: 16px;
            display: flex; justify-content: center; padding: 25px; overflow-y: auto;
            background-image: url('https://www.transparenttextures.com/patterns/cubes.png');
        }
        .tg-bubble {
            background: white; padding: 18px; border-radius: 18px; border-bottom-right-radius: 4px;
            max-width: 450px; width: 100%; height: fit-content;
            box-shadow: 0 10px 25px rgba(0,0,0,0.1); font-size: 15px; line-height: 1.5;
            color: #222;
        }
        .tg-bubble b { color: #000; }

        .btn-group { display: flex; gap: 15px; }
        .btn {
            padding: 14px 28px; border: none; border-radius: 12px;
            font-weight: bold; cursor: pointer; display: flex;
            align-items: center; gap: 10px; font-size: 15px;
        }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-success { background: #34c759; color: white; }
        .btn-danger { background: #ff3b30; color: white; }
        .btn:hover { filter: brightness(1.1); transform: translateY(-2px); }
        .btn:active { transform: translateY(0); }
    </style>
</head>
<body>

<div class="sidebar">
    <div class="logo"><i class="fa-solid fa-paper-plane"></i> Post Master</div>
    <div class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-magic"></i> Formatter</div>
    <div class="nav-item" onclick="showView('help', this)"><i class="fa-solid fa-lightbulb"></i> Tipps</div>

    <div class="sidebar-footer">
        <a href="mailto:kris@deine-domain.de" class="nav-item"><i class="fa-solid fa-envelope"></i> Support</a>
        <a href="{{ coffee_link }}" target="_blank" class="nav-item" style="color: #f39c12; font-weight: bold;">
            <i class="fa-solid fa-mug-hot"></i> Spenden
        </a>
    </div>
</div>

<div class="main-content">
    <div id="editor" class="view active">
        <div class="editor-container">
            <div class="panel">
                <div class="panel-label">Markdown Input</div>
                <textarea id="editorInput" placeholder="# Überschrift&#10;- Punkt 1&#10;**Fett**"></textarea>
            </div>
            <div class="panel">
                <div class="panel-label">Telegram Vorschau</div>
                <div class="preview-box">
                    <div class="tg-bubble" id="previewBubble">Schreibe etwas...</div>
                </div>
            </div>
        </div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="send()"><i class="fa-solid fa-paper-plane"></i> Senden</button>
            <button class="btn btn-success" id="copyBtn" onclick="copy()"><i class="fa-solid fa-copy"></i> Kopieren</button>
            <button class="btn btn-danger" onclick="reset()"><i class="fa-solid fa-trash-can"></i> Reset</button>
        </div>
    </div>

    <div id="help" class="view">
        <div style="background:white; padding:40px; border-radius:20px; box-shadow: 0 4px 15px rgba(0,0,0,0.05);">
            <h2>Profi-Tipps für die Formatierung</h2>
            <p>Verwende Standard-Markdown:</p>
            <ul>
                <li><code># Überschrift</code> für große Ankündigungen</li>
                <li><code>**Text**</code> für wichtigen Fettdruck</li>
                <li><code>- Punkt</code> für Listen</li>
            </ul>
        </div>
    </div>
</div>

<script>
    const input = document.getElementById('editorInput');
    const preview = document.getElementById('previewBubble');

    function showView(id, el) {
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.getElementById(id).classList.add('active');
        el.classList.add('active');
    }

    function reset() {
        if(confirm("Wirklich alles löschen?")) {
            input.value = "";
            preview.innerHTML = "Schreibe etwas...";
        }
    }

    async function copy() {
        // Wir kopieren den Text so, wie er in der Sprechblase steht
        const text = preview.innerText;
        if(!text || text === "Schreibe etwas...") return;
        try {
            await navigator.clipboard.writeText(text);
            const btn = document.getElementById('copyBtn');
            btn.innerHTML = '<i class="fa-solid fa-check"></i> Kopiert!';
            setTimeout(() => btn.innerHTML = '<i class="fa-solid fa-copy"></i> Kopieren', 2000);
        } catch (err) { alert("Fehler beim Kopieren."); }
    }

    async function send() {
        if(!input.value) return;
        const res = await fetch('/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'content=' + encodeURIComponent(input.value)
        });
        const result = await res.text();
        if(result === "OK") alert("🚀 Erfolgreich gesendet!");
        else alert("❌ Fehler: " + result);
    }

    let timer;
    input.addEventListener('input', () => {
        clearTimeout(timer);
        timer = setTimeout(async () => {
            const res = await fetch('/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: input.value})
            });
            const data = await res.json();
            preview.innerHTML = data.html;
        }, 300);
    });
</script>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        content = request.form.get('content')
        if not bot: return "Bot nicht konfiguriert (Token fehlt)", 500
        if content:
            try:
                # Wir nutzen HTML Parse Mode für maximale Stabilität
                formatted_html = format_to_tg_html(content)
                chunks = split_html_message(formatted_html)
                for chunk in chunks:
                    bot.send_message(MY_CHAT_ID, chunk, parse_mode='HTML')
                return "OK"
            except Exception as e:
                return f"API Fehler: {str(e)}", 500
    return render_template_string(HTML_TEMPLATE, coffee_link=BASE_COFFEE_URL)

@app.route('/preview', methods=['POST'])
def preview_api():
    data = request.json
    html_version = format_to_tg_html(data.get('text', ''))
    # Im Browser-Vorschaufenster wandeln wir <pre> und <code> optisch um
    return jsonify({'html': html_version.replace('\n', '<br>')})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
