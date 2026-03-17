import os
import telebot
import telegramify_markdown
from telebot import util
from flask import Flask, render_template_string, request, jsonify

# --- KONFIGURATION (Sicher über Umgebungsvariablen) ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# Fallback für lokale Tests
if not TOKEN:
    TOKEN = "DEIN_TOKEN_HIER"
if not MY_CHAT_ID:
    MY_CHAT_ID = "DEINE_ID_HIER"

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

def safe_format(text):
    # Überschriften fixieren (# wird zu Fett-Text)
    lines = text.split('\n')
    for i, line in enumerate(lines):
        if line.strip().startswith('#') and not line.strip().startswith('##'):
            lines[i] = f"*{line.replace('#', '').strip()}*"
    text = '\n'.join(lines)

    formatted = telegramify_markdown.markdownify(text)

    # Unbalanced Backticks fixen
    if formatted.count('```') % 2 != 0:
        formatted += '\n```'
    return formatted

def split_message_safely(text, max_length=3500):
    if len(text) <= max_length:
        return [text]
    parts = []
    current_part = ""
    in_code_block = False
    lines = text.split('\n')
    for line in lines:
        if "```" in line:
            in_code_block = not in_code_block
        if len(current_part) + len(line) + 1 > max_length and not in_code_block:
            parts.append(current_part.strip())
            current_part = line + '\n'
        else:
            current_part += line + '\n'
    if current_part:
        parts.append(current_part.strip())
    return [p + '\n```' if p.count('```') % 2 != 0 else p for p in parts]

# --- MODERNER UI CODE ---
HTML_TEMPLATE = r"""
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
            font-family: 'Segoe UI', Roboto, sans-serif;
            margin: 0; display: flex; height: 100vh; background: #f4f7f9; color: var(--dark-text);
        }

        /* Sidebar */
        .sidebar {
            width: var(--sidebar-width); background: white; border-right: 1px solid #ddd;
            display: flex; flex-direction: column; padding: 20px; z-index: 100;
        }
        .logo { font-size: 20px; font-weight: bold; color: var(--tg-blue); margin-bottom: 30px; display: flex; align-items: center; gap: 10px; }
        .nav-item {
            padding: 12px 15px; margin-bottom: 5px; border-radius: 8px; cursor: pointer;
            display: flex; align-items: center; gap: 10px; transition: 0.2s; color: #666;
            text-decoration: none; /* Wichtig für <a> Tags */
        }
        .nav-item:hover { background: #f0f7ff; color: var(--tg-blue); }
        .nav-item.active { background: var(--tg-blue); color: white; }
        .sidebar-footer { margin-top: auto; font-size: 13px; }

        /* Main Area */
        .main-content { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .view { display: none; height: 100%; padding: 25px; overflow-y: auto; }
        .view.active { display: flex; flex-direction: column; }

        /* Editor View */
        .editor-layout { display: flex; gap: 20px; flex: 1; min-height: 0; }
        .editor-box { flex: 1; display: flex; flex-direction: column; gap: 10px; }
        textarea {
            flex: 1; width: 100%; border: 1px solid #ccc; border-radius: 12px; padding: 15px;
            font-family: 'Consolas', monospace; font-size: 14px; resize: none; outline: none;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.05);
        }

        /* Vorschau & Sprechblase */
        .preview-box {
            flex: 1; background: var(--tg-bg); border-radius: 12px;
            display: flex; justify-content: center; padding: 20px; overflow-y: auto;
        }
        .tg-bubble {
            background: white; padding: 15px; border-radius: 15px; max-width: 450px; width: 100%; height: fit-content;
            box-shadow: 0 4px 15px rgba(0,0,0,0.2); white-space: pre-wrap; font-size: 14.5px; line-height: 1.5;
        }

        /* Buttons */
        .btn-group { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 10px; }
        .btn {
            padding: 12px 20px; border: none; border-radius: 8px; font-weight: bold; cursor: pointer;
            display: flex; align-items: center; gap: 8px; transition: 0.2s; font-size: 14px;
        }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-primary:hover { background: #1e87bb; transform: translateY(-1px); }
        .btn-success { background: #2ecc71; color: white; }
        .btn-success:hover { background: #27ae60; transform: translateY(-1px); }
        .btn-danger { background: #e74c3c; color: white; }
        .btn-danger:hover { background: #c0392b; }

        /* Info Cards */
        .content-card { background: white; padding: 30px; border-radius: 15px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); max-width: 800px; line-height: 1.6; }
        h1 { margin-top: 0; color: var(--tg-blue); }
        code { background: #eee; padding: 2px 5px; border-radius: 4px; }
    </style>
</head>
<body>

<div class="sidebar">
    <div class="logo"><i class="fa-solid fa-paper-plane"></i> TG Master</div>
    <div class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-pen-to-square"></i> Editor</div>
    <div class="nav-item" onclick="showView('help', this)"><i class="fa-solid fa-circle-question"></i> Hilfe</div>
    <div class="nav-item" onclick="showView('impressum', this)"><i class="fa-solid fa-file-contract"></i> Impressum</div>

    <div class="sidebar-footer">
        <hr>
        <a href="mailto:kris@deine-domain.de" class="nav-item" style="color: #666;">
            <i class="fa-solid fa-envelope"></i> Kontakt
        </a>
        <a href="[https://buymeacoffee.com/rg4free](https://buymeacoffee.com/rg4free)" target="_blank" class="nav-item" style="color: #e67e22;">
            <i class="fa-solid fa-coffee"></i> Spenden
        </a>
    </div>
</div>

<div class="main-content">
    <div id="editor" class="view active">
        <div class="editor-layout">
            <div class="editor-box">
                <textarea id="editorInput" placeholder="Füge hier deinen KI-Text ein..."></textarea>
                <div class="btn-group">
                    <button class="btn btn-primary" onclick="sendToTelegram()"><i class="fa-solid fa-share"></i> Senden</button>
                    <button class="btn btn-success" id="copyBtn" onclick="copyToClipboard()"><i class="fa-solid fa-copy"></i> Text Kopieren</button>
                    <button class="btn btn-danger" onclick="resetEditor()"><i class="fa-solid fa-trash"></i> Reset</button>
                </div>
            </div>
            <div class="preview-box">
                <div class="tg-bubble" id="previewBubble">Warte auf Eingabe...</div>
            </div>
        </div>
    </div>

    <div id="help" class="view">
        <div class="content-card">
            <h1>Hilfe & Anleitung</h1>
            <p>Dieser Formatter bändigt KI-Texte für Telegram.</p>
            <ul>
                <li><strong>Markdown:</strong> Standard-Markdown wird in Telegram MarkdownV2 übersetzt.</li>
                <li><strong>Automatischer Split:</strong> Texte über 4096 Zeichen werden intelligent aufgeteilt.</li>
            </ul>
        </div>
    </div>

    <div id="impressum" class="view">
        <div class="content-card">
            <h1>Impressum</h1>
            <p>Betreiber der Webseite:<br>Kris<br>Musterstr. 123<br>Deutschland</p>
            <p>E-Mail: kris@deine-domain.de</p>
        </div>
    </div>
</div>

<script>
    const editor = document.getElementById('editorInput');
    const preview = document.getElementById('previewBubble');

    function showView(viewId, element) {
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.getElementById(viewId).classList.add('active');
        element.classList.add('active');
    }

    function resetEditor() {
        if(confirm("Alles löschen?")) {
            editor.value = "";
            preview.innerText = "Warte auf Eingabe...";
        }
    }

    async function copyToClipboard() {
        const textToCopy = preview.innerText;
        if(textToCopy === "Warte auf Eingabe..." || textToCopy.trim() === "") {
            alert("Nichts zum Kopieren da!");
            return;
        }

        try {
            await navigator.clipboard.writeText(textToCopy);
            const copyBtn = document.getElementById('copyBtn');
            const originalHTML = copyBtn.innerHTML;

            copyBtn.innerHTML = '<i class="fa-solid fa-check"></i> Kopiert!';
            copyBtn.style.background = "#27ae60";

            setTimeout(() => {
                copyBtn.innerHTML = originalHTML;
                copyBtn.style.background = "#2ecc71";
            }, 2000);
        } catch (err) {
            alert('Fehler beim Kopieren: ' + err);
        }
    }

    async function updatePreview() {
        if (!editor.value) { preview.innerText = "Warte auf Eingabe..."; return; }
        const res = await fetch('/preview', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({text: editor.value})
        });
        const data = await res.json();
        preview.innerText = data.raw.replace(/\\/g, '');
    }

    async function sendToTelegram() {
        if(!editor.value) return;
        const res = await fetch('/', {
            method: 'POST',
            headers: {'Content-Type': 'application/x-www-form-urlencoded'},
            body: 'content=' + encodeURIComponent(editor.value)
        });
        const status = await res.text();
        if(status === "OK") {
            alert("🚀 Gesendet!");
        } else {
            alert("Fehler: " + status);
        }
    }

    let timeout;
    editor.addEventListener('input', () => {
        clearTimeout(timeout);
        timeout = setTimeout(updatePreview, 400);
    });
</script>

</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        content = request.form.get('content')
        if content:
            try:
                formatted = safe_format(content)
                chunks = split_message_safely(formatted)
                for chunk in chunks:
                    bot.send_message(MY_CHAT_ID, chunk, parse_mode='MarkdownV2')
                return "OK"
            except Exception as e:
                return str(e), 500
    return render_template_string(HTML_TEMPLATE)

@app.route('/preview', methods=['POST'])
def preview_api():
    data = request.json
    formatted = safe_format(data.get('text', ''))
    return jsonify({'raw': formatted})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
