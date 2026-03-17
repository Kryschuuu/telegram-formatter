import os
import telebot
import telegramify_markdown
from flask import Flask, render_template_string, request, jsonify

# --- KONFIGURATION ---
# Die Tokens werden aus den Umgebungsvariablen von Render bezogen
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Bot Initialisierung
bot = telebot.TeleBot(TOKEN) if TOKEN else None
app = Flask(__name__)

# Wir definieren die URL hier in Python, um Kopierfehler im HTML zu vermeiden
# Dies verhindert die fehlerhafte "Markdown-Link" Interpretation im Browser
BASE_COFFEE_URL = "https://buymeacoffee.com/rg4free"

def safe_format(text):
    """Formatiert den Text für Telegram MarkdownV2."""
    if not text:
        return ""
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if line.strip().startswith('#') and not line.strip().startswith('##'):
            lines[i] = f"*{line.replace('#', '').strip()}*"
    text = '\n'.join(lines)
    formatted = telegramify_markdown.markdownify(text)
    # Sicherstellen, dass Code-Blöcke geschlossen sind
    if formatted.count('```') % 2 != 0:
        formatted += '\n```'
    return formatted

def split_message_safely(text, max_length=3500):
    """Teilt lange Nachrichten in telegram-konforme Häppchen."""
    if len(text) <= max_length:
        return [text]
    parts, current_part, in_code_block = [], "", False
    for line in text.split('\n'):
        if "```" in line:
            in_code_block = not in_code_block
        if len(current_part) + len(line) + 1 > max_length and not in_code_block:
            parts.append(current_part.strip())
            current_part = line + '\n'
        else:
            current_part += line + '\n'
    if current_part:
        parts.append(current_part.strip())
    # Code-Blöcke in jedem Teil korrekt schließen/öffnen
    return [p + '\n```' if p.count('```') % 2 != 0 else p for p in parts]

# Das HTML Template als String
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Post Master Pro</title>
    <link rel="stylesheet" href="[https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css](https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.0.0/css/all.min.css)">
    <style>
        :root {
            --tg-blue: #24A1DE;
            --tg-bg: #547594;
            --sidebar-width: 260px;
            --dark-text: #2c3e50;
        }
        * { box-sizing: border-box; }
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0; display: flex; height: 100vh; background: #f4f7f9;
        }

        /* Sidebar Styling */
        .sidebar {
            width: var(--sidebar-width);
            background: white;
            border-right: 1px solid #ddd;
            display: flex;
            flex-direction: column;
            padding: 20px;
        }
        .logo {
            font-size: 20px;
            font-weight: bold;
            color: var(--tg-blue);
            margin-bottom: 30px;
            display: flex;
            align-items: center;
            gap: 10px;
        }
        .nav-item {
            padding: 12px 15px;
            margin-bottom: 5px;
            border-radius: 8px;
            cursor: pointer;
            display: flex;
            align-items: center;
            gap: 10px;
            color: #666;
            text-decoration: none;
            border: none;
            background: none;
            width: 100%;
            font-size: 16px;
            transition: 0.2s;
        }
        .nav-item:hover { background: #f0f7ff; color: var(--tg-blue); }
        .nav-item.active { background: var(--tg-blue); color: white; }
        .sidebar-footer { margin-top: auto; border-top: 1px solid #eee; padding-top: 15px; }

        /* Main Content */
        .main-content { flex: 1; display: flex; flex-direction: column; padding: 25px; overflow: hidden; }
        .view { display: none; height: 100%; flex-direction: column; }
        .view.active { display: flex; }

        .editor-layout { display: flex; gap: 20px; flex: 1; min-height: 0; }
        textarea {
            flex: 1; border: 1px solid #ccc; border-radius: 12px; padding: 15px;
            font-family: 'Consolas', monospace; resize: none; outline: none; font-size: 14px;
        }
        .preview-box {
            flex: 1; background: var(--tg-bg); border-radius: 12px;
            display: flex; justify-content: center; padding: 20px; overflow-y: auto;
        }
        .tg-bubble {
            background: white; padding: 15px; border-radius: 15px;
            max-width: 450px; width: 100%; height: fit-content;
            white-space: pre-wrap; box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            font-size: 14px; line-height: 1.4;
        }

        /* Buttons */
        .btn-group { display: flex; gap: 10px; margin-top: 15px; }
        .btn {
            padding: 12px 24px; border: none; border-radius: 8px;
            font-weight: bold; cursor: pointer; display: flex;
            align-items: center; gap: 8px; transition: 0.2s;
        }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-success { background: #2ecc71; color: white; }
        .btn-danger { background: #e74c3c; color: white; }
        .btn:hover { opacity: 0.9; transform: translateY(-1px); }
    </style>
</head>
<body>

<div class="sidebar">
    <div class="logo"><i class="fa-solid fa-paper-plane"></i> TG Master</div>
    <div class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-pen-to-square"></i> Editor</div>
    <div class="nav-item" onclick="showView('help', this)"><i class="fa-solid fa-circle-question"></i> Hilfe</div>
    <div class="nav-item" onclick="showView('impressum', this)"><i class="fa-solid fa-file-contract"></i> Impressum</div>

    <div class="sidebar-footer">
        <a href="mailto:kris@deine-domain.de" class="nav-item"><i class="fa-solid fa-envelope"></i> Kontakt</a>
        <!-- Dynamischer Link von Python eingefügt -->
        <a href="{{ coffee_link }}" target="_blank" class="nav-item" style="color: #e67e22; font-weight: bold;">
            <i class="fa-solid fa-coffee"></i> Spenden
        </a>
    </div>
</div>

<div class="main-content">
    <!-- Editor Bereich -->
    <div id="editor" class="view active">
        <div class="editor-layout">
            <textarea id="editorInput" placeholder="Füge hier deinen Text ein..."></textarea>
            <div class="preview-box">
                <div class="tg-bubble" id="previewBubble">Vorschau wird hier geladen...</div>
            </div>
        </div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="send()"><i class="fa-solid fa-share"></i> Senden</button>
            <button class="btn btn-success" onclick="copy()"><i class="fa-solid fa-copy"></i> Kopieren</button>
            <button class="btn btn-danger" onclick="reset()"><i class="fa-solid fa-trash"></i> Reset</button>
        </div>
    </div>

    <!-- Hilfe Bereich -->
    <div id="help" class="view">
        <div style="background:white; padding:30px; border-radius:15px; max-width: 700px;">
            <h1>Anleitung</h1>
            <p>1. Text im Editor links eingeben.<br>
               2. Rechts die Live-Vorschau prüfen.<br>
               3. "Senden" drückt den Post direkt in deinen Kanal.</p>
        </div>
    </div>

    <!-- Impressum Bereich -->
    <div id="impressum" class="view">
        <div style="background:white; padding:30px; border-radius:15px; max-width: 700px;">
            <h1>Impressum</h1>
            <p><strong>Betreiber:</strong> Kris<br>
               Musterstraße 1<br>
               12345 Musterstadt</p>
        </div>
    </div>
</div>

<script>
    function showView(viewId, el) {
        document.querySelectorAll('.view').forEach(x => x.classList.remove('active'));
        document.querySelectorAll('.nav-item').forEach(x => x.classList.remove('active'));
        document.getElementById(viewId).classList.add('active');
        el.classList.add('active');
    }

    function reset() {
        if(confirm("Möchtest du wirklich alles löschen?")) {
            document.getElementById('editorInput').value = "";
            document.getElementById('previewBubble').innerText = "Vorschau...";
        }
    }

    async function copy() {
        const text = document.getElementById('previewBubble').innerText;
        if(text === "Vorschau...") return;
        try {
            await navigator.clipboard.writeText(text);
            alert("In Zwischenablage kopiert!");
        } catch (err) {
            alert("Fehler beim Kopieren.");
        }
    }

    async function send() {
        const val = document.getElementById('editorInput').value;
        if(!val) return;
        const res = await fetch('/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'content=' + encodeURIComponent(val)
        });
        if(await res.text() === "OK") {
            alert("Erfolgreich an Telegram gesendet!");
        } else {
            alert("Fehler beim Senden.");
        }
    }

    // Live-Vorschau Logik
    let timeout;
    document.getElementById('editorInput').addEventListener('input', (e) => {
        clearTimeout(timeout);
        timeout = setTimeout(async () => {
            const res = await fetch('/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: e.target.value})
            });
            const d = await res.json();
            // Bereinigung von Escape-Backslashes für die HTML-Anzeige
            document.getElementById('previewBubble').innerText = d.raw.replace(/\\\\/g, '');
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
        if content and bot:
            try:
                formatted = safe_format(content)
                chunks = split_message_safely(formatted)
                for chunk in chunks:
                    bot.send_message(MY_CHAT_ID, chunk, parse_mode='MarkdownV2')
                return "OK"
            except Exception as e:
                return str(e), 500
    # Wir übergeben die URL hier sicher an das Template
    return render_template_string(HTML_TEMPLATE, coffee_link=BASE_COFFEE_URL)

@app.route('/preview', methods=['POST'])
def preview_api():
    data = request.json
    return jsonify({'raw': safe_format(data.get('text', ''))})

if __name__ == '__main__':
    # Render nutzt den Port 5000 standardmäßig oder über env
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
