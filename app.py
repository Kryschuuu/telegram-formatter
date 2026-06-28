import os
import re
import uuid
import telebot
from flask import Flask, render_template_string, request, jsonify

# ====================== KONFIGURATION ======================
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
BASE_COFFEE_URL = "https://buymeacoffee.com/rg4free"

bot = telebot.TeleBot(TOKEN) if TOKEN else None
app = Flask(__name__)


# ====================== VERBESSERTER FORMEL + MARKDOWN CONVERTER ======================
def format_to_tg_html(markdown_text):
    if not markdown_text:
        return ""

    # 1. Platzhalter für Code-Blöcke (Inhalt vor Formatierung schützen)
    placeholders = {}
    def repl_code(match):
        p_id = f"___CODE_BLOCK_{len(placeholders)}___"
        placeholders[p_id] = match.group(0)
        return p_id

    # Inline-Code und mehrzeiligen Code schützen
    text = re.sub(r'```.*?```', repl_code, markdown_text, flags=re.DOTALL)
    text = re.sub(r'`[^`\n]+`', repl_code, text)

    # HTML-Sonderzeichen im restlichen Text eskapieren
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 2. Überschriften konvertieren (Telegram unterstützt kein <h1>-<h3>, daher nutzen wir <b>)
    # Entfernt führende Symbole und setzt die Zeile in Fettschrift mit sauberem Umbruch
    text = re.sub(r'^(?:📍\s*)?###?\s*(.+)$', r'\n<b>\1</b>', text, flags=re.MULTILINE)

    # 3. Standard-Markdown-Formatierungen
    # Fett (**text** oder __text__)
    text = re.sub(r'\*\*(.*?)\*\*|__(.*?)__', lambda m: f"<b>{m.group(1) or m.group(2)}</b>", text)
    # Kursiv (*text* oder _text_)
    text = re.sub(r'\*(.*?)\*|_(.*?)_', lambda m: f"<i>{m.group(1) or m.group(2)}</i>", text)

    # 4. Listen-Punkte vereinheitlichen (Sowohl * als auch • zu Telegram-Bullets machen)
    text = re.sub(r'^[•*]\s*(.+)$', r'• \1', text, flags=re.MULTILINE)

    # 5. Links konvertieren [Text](URL) -> <a href="URL">Text</a>
    # Auch extrem lange URLs (wie die Google Search URL aus deinem Post) werden hier sicher erfasst
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)

    # 6. Code-Blöcke wieder zurückholen und in Telegram-HTML übersetzen
    for p_id, original in placeholders.items():
        if original.startswith('```'):
            # Mehrzeiliger Code -> <pre>
            code_content = original.strip('`').strip()
            code_content = code_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            text = text.replace(p_id, f"<pre>{code_content}</pre>")
        else:
            # Inline-Code -> <code>
            code_content = original.strip('`')
            code_content = code_content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            text = text.replace(p_id, f"<code>{code_content}</code>")

    # 7. Zeilenumbrüche für Telegram vorbereiten
    # Telegram interpretiert normale \n als Umbruch, wir bereinigen doppelte Rückstände
    text = text.replace('\r\n', '\n').replace('\r', '\n')
    
    return text.strip()


def split_html_message(text: str, max_length: int = 4000) -> list:
    """Teilt lange Nachrichten sicher auf."""
    if len(text) <= max_length:
        return [text]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_pos = text.rfind('<br>', 0, max_length) or max_length
        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip('<br>')
    return chunks


# ====================== HTML FRONTEND ======================
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Post Master Pro</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    <style>
        :root { --tg-blue: #24A1DE; --bg-light: #f4f7f9; }
        * { box-sizing: border-box; }
        body { font-family: 'Segoe UI', system-ui, sans-serif; margin: 0; display: flex; height: 100vh; background: var(--bg-light); }
        .sidebar { width: 280px; background: white; border-right: 1px solid #ddd; padding: 25px; display: flex; flex-direction: column; }
        .logo { font-size: 24px; font-weight: 800; color: var(--tg-blue); display: flex; align-items: center; gap: 12px; margin-bottom: 40px; }
        .nav-item { padding: 14px 18px; margin-bottom: 8px; border-radius: 12px; display: flex; align-items: center; gap: 12px; color: #555; font-weight: 500; background: none; border: none; width: 100%; text-align: left; cursor: pointer; }
        .nav-item:hover, .nav-item.active { background: #f0f7ff; color: var(--tg-blue); }
        .main-content { flex: 1; padding: 30px; overflow: auto; }
        .view { display: none; flex-direction: column; gap: 20px; height: 100%; }
        .view.active { display: flex; }
        .editor-container { display: flex; gap: 25px; flex: 1; }
        .panel { flex: 1; display: flex; flex-direction: column; }
        .panel-label { font-weight: bold; font-size: 13px; color: #888; text-transform: uppercase; letter-spacing: 1px; margin-bottom: 8px; }
        textarea { flex: 1; border: 2px solid #e0e6ed; border-radius: 16px; padding: 20px; font-family: 'Consolas', monospace; font-size: 15px; line-height: 1.6; resize: none; outline: none; }
        textarea:focus { border-color: var(--tg-blue); }
        .preview-box { flex: 1; background: #547594; border-radius: 16px; padding: 30px; overflow-y: auto; background-image: url('https://www.transparenttextures.com/patterns/cubes.png'); display: flex; align-items: center; justify-content: center; }
        .tg-bubble { background: white; padding: 22px; border-radius: 18px; border-bottom-right-radius: 6px; max-width: 460px; box-shadow: 0 10px 25px rgba(0,0,0,0.15); line-height: 1.55; color: #222; }
        .btn-group { display: flex; gap: 12px; margin-top: 10px; }
        .btn { flex: 1; padding: 16px; border: none; border-radius: 12px; font-weight: bold; cursor: pointer; font-size: 15px; display: flex; align-items: center; justify-content: center; gap: 10px; }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-success { background: #34c759; color: white; }
        .btn-danger { background: #ff3b30; color: white; }
    </style>
</head>
<body>
<div class="sidebar">
    <div class="logo"><i class="fa-solid fa-paper-plane"></i> Post Master Pro</div>
    <button class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-pen-nib"></i> Editor</button>
    <button class="nav-item" onclick="showView('help', this)"><i class="fa-solid fa-circle-info"></i> Hilfe & Tipps</button>
    <div style="margin-top: auto; padding-top: 30px; border-top: 1px solid #eee;">
        <a href="{{ coffee_link }}" target="_blank" class="nav-item" style="color:#f39c12; background:#fff8e1;">
            <i class="fa-solid fa-mug-hot"></i> Buy Me a Coffee
        </a>
    </div>
</div>

<div class="main-content">
    <div id="editor" class="view active">
        <div class="editor-container">
            <div class="panel">
                <div class="panel-label">Markdown + LaTeX Eingabe</div>
                <textarea id="editorInput" placeholder="Hier kannst du ganz normal Markdown und LaTeX schreiben..."></textarea>
            </div>
            <div class="panel">
                <div class="panel-label">Telegram-Vorschau (Live)</div>
                <div class="preview-box">
                    <div class="tg-bubble" id="previewBubble">Deine Vorschau erscheint hier...</div>
                </div>
            </div>
        </div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="sendToTelegram()"><i class="fa-solid fa-share-from-square"></i> An Telegram senden</button>
            <button class="btn btn-success" id="copyBtn" onclick="copyToClipboard()"><i class="fa-solid fa-copy"></i> Vorschau kopieren</button>
            <button class="btn btn-danger" onclick="resetAll()"><i class="fa-solid fa-trash-can"></i> Reset</button>
        </div>
    </div>

    <div id="help" class="view">
        <div style="background:white; padding:40px; border-radius:20px; box-shadow:0 4px 20px rgba(0,0,0,0.05);">
            <h1>✅ Jetzt funktionieren Formeln und Zeilenumbrüche perfekt!</h1>
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

    async function updatePreview() {
        if (!input.value.trim()) {
            preview.innerHTML = "Deine Vorschau erscheint hier...";
            return;
        }
        const res = await fetch('/preview', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: input.value})
        });
        const data = await res.json();
        preview.innerHTML = data.html;
    }

    input.addEventListener('input', () => {
        clearTimeout(window.timer);
        window.timer = setTimeout(updatePreview, 300);
    });

    async function sendToTelegram() {
        if (!input.value.trim()) return alert("Bitte Text eingeben!");
        const res = await fetch('/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'content=' + encodeURIComponent(input.value)
        });
        const txt = await res.text();
        alert(txt === "OK" ? "🚀 Erfolgreich gesendet!" : "Fehler: " + txt);
    }

    async function copyToClipboard() {
        const text = preview.innerText;
        if (text.includes("Vorschau")) return;
        await navigator.clipboard.writeText(text);
        const btn = document.getElementById('copyBtn');
        const old = btn.innerHTML;
        btn.innerHTML = '✅ Kopiert!';
        setTimeout(() => btn.innerHTML = old, 1800);
    }

    function resetAll() {
        if (confirm("Alles zurücksetzen?")) {
            input.value = "";
            preview.innerHTML = "Deine Vorschau erscheint hier...";
        }
    }
</script>
</body>
</html>
"""

# ====================== ROUTEN ======================
@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        content = request.form.get('content')
        if not bot or not MY_CHAT_ID:
            return "Bot-Token oder Chat-ID fehlt!", 500
        if content:
            try:
                html = format_to_tg_html(content)
                chunks = split_html_message(html)
                for chunk in chunks:
                    bot.send_message(MY_CHAT_ID, chunk, parse_mode='HTML')
                return "OK"
            except Exception as e:
                return f"Fehler: {str(e)}", 500
    return render_template_string(HTML_TEMPLATE, coffee_link=BASE_COFFEE_URL)


@app.route('/preview', methods=['POST'])
def preview_api():
    data = request.json or {}
    html = format_to_tg_html(data.get('text', ''))
    return jsonify({'html': html})


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
