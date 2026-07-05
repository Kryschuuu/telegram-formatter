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


# ====================== HTML TAG BALANCER (FIX FÜR FEHLER 1) ======================
def balance_and_track_tags(html: str):
    """
    KORREKTUR FEHLER 1: Prüft und repariert fehlerhaft verschachtelte HTML-Tags
    (z. B. <b><i>Text</b></i> -> <b><i>Text</i></b>).
    Verhindert zuverlässig Telegram API 400 ('Unmatched end tag').
    """
    tokens = re.split(r'(</?[a-zA-Z0-9]+(?: [^>]+)?>)', html)
    stack = []
    result = []
    
    for token in tokens:
        if not token:
            continue
        m = re.match(r'^<(/)?([a-zA-Z0-9]+)( [^>]+)?>$', token)
        if m:
            is_close = bool(m.group(1))
            tag_name = m.group(2).lower()
            attrs = m.group(3) or ""
            
            if is_close:
                if tag_name in [t[0] for t in stack]:
                    # Schließe verdrängte Tags temporär im richtigen Stack-LIFO-Prinzip
                    temp_reopen = []
                    while stack and stack[-1][0] != tag_name:
                        top_tag, top_attrs = stack.pop()
                        result.append(f"</{top_tag}>")
                        temp_reopen.append((top_tag, top_attrs))
                    if stack:
                        stack.pop()
                        result.append(f"</{tag_name}>")
                    # Öffne verdrängte Tags wieder in korrekter Reihenfolge
                    while temp_reopen:
                        top_tag, top_attrs = temp_reopen.pop()
                        stack.append((top_tag, top_attrs))
                        result.append(f"<{top_tag}{top_attrs}>")
            else:
                stack.append((tag_name, attrs))
                result.append(token)
        else:
            result.append(token)
            
    # Noch offene Tags merken (für Nachrichten-Splits)
    next_open_tags = list(stack)
    
    # Am Ende des Chunks alle offenen Tags sauber schließen
    while stack:
        top_tag, _ = stack.pop()
        result.append(f"</{top_tag}>")
        
    return "".join(result), next_open_tags


def balance_html_tags(html: str) -> str:
    balanced, _ = balance_and_track_tags(html)
    return balanced


# ====================== MARKDOWN & LATEX CONVERTER ======================
def format_to_tg_html(text: str) -> str:
    if not text:
        return ""

    # KORREKTUR FEHLER 2: Zeilenumbrüche frühzeitig auf \n vereinheitlichen.
    # Telegram benötigt \n für Umbrüche. <br> ist im Telegram-HTML nicht erlaubt!
    text = text.replace('\r\n', '\n').replace('\r', '\n')

    # 1. Code & Formeln vor Formatierung schützen
    placeholders = {}

    def protect(match, is_block=False):
        key = f"@@PLACEHOLDER_{uuid.uuid4().hex[:8]}@@"
        content = match.group(1)
        content = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
        if is_block:
            placeholders[key] = f"<pre>{content.strip()}</pre>"
        else:
            placeholders[key] = f"<code>{content}</code>"
        return key

    text = re.sub(r'```(.*?)```', lambda m: protect(m, True), text, flags=re.DOTALL)
    text = re.sub(r'`([^`\n]+)`', lambda m: protect(m, False), text)
    text = re.sub(r'\$\$(.*?)\$\$', lambda m: protect(m, True), text, flags=re.DOTALL)
    text = re.sub(r'(?<!\\)\$([^\$\n]+)\$', lambda m: protect(m, False), text)

    # 2. HTML-Sonderzeichen im restlichen Text escapen
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # 3. Tabellen in saubere Listen umwandeln
    def table_replacer(match):
        block = match.group(1).strip()
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if len(lines) < 3:
            return match.group(0)
        headers = [h.strip() for h in lines[0].strip('|').split('|') if h.strip()]
        rows = []
        for line in lines[2:]:
            cols = [c.strip() for c in line.strip('|').split('|') if c.strip()]
            parts = [f"<b>{headers[i]}:</b> {col}" if i < len(headers) else col for i, col in enumerate(cols)]
            rows.append("🔸 " + " • ".join(parts))
        return "\n\n" + "\n".join(rows) + "\n\n"

    text = re.sub(r'((?:^\s*\|.*\|\s*(?:\n|$)){3,})', table_replacer, text, flags=re.MULTILINE)

    # 4. Blockquotes
    lines = text.split('\n')
    new_lines = []
    in_quote = False
    for line in lines:
        m = re.match(r'^\s*&gt;\s?(.*)', line)
        if m:
            if not in_quote:
                new_lines.append('<blockquote>' + m.group(1))
                in_quote = True
            else:
                new_lines.append(m.group(1))
        else:
            if in_quote:
                new_lines[-1] += '</blockquote>'
                in_quote = False
            new_lines.append(line)
    if in_quote:
        new_lines[-1] += '</blockquote>'
    text = '\n'.join(new_lines)

    # 5. Überschriften (in Fettschrift umwandeln)
    text = re.sub(r'^####\s+(.*)$', r'<b>🔸 \1</b>', text, flags=re.M)
    text = re.sub(r'^###\s+(.*)$', r'<b>🔹 \1</b>', text, flags=re.M)
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)

    # 6. Listenpunkte
    text = re.sub(r'^\s*[\*-]\s+', r'• ', text, flags=re.M)

    # 7. Links konvertieren
    text = re.sub(r'\[([^\]]+)\]\((https?://[^\s)]+)\)', r'<a href="\2">\1</a>', text)

    # 8. Inline-Formatierung (ohne re.S, um überlappende Mehrzeilen-Matches zu verhindern)
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'__(.+?)__', r'<b>\1</b>', text)
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    text = re.sub(r'_(.+?)_', r'<i>\1</i>', text)
    text = re.sub(r'~~(.+?)~~', r'<s>\1</s>', text)

    # 9. Geschützte Blöcke wiederherstellen
    for key, value in placeholders.items():
        text = text.replace(key, value)

    # 10. Tags ausbalancieren (garantiert 100% korrekte HTML-Schachtelung für Telegram)
    return balance_html_tags(text.strip())


def split_html_message(text: str, max_length: int = 4000) -> list:
    """
    Teilt lange Nachrichten sicher an Zeilenumbrüchen auf und garantiert, 
    dass jeder einzelne Chunk für sich ein gültiges, geschlossenes HTML besitzt.
    """
    if len(text) <= max_length:
        return [balance_html_tags(text)]
    
    chunks = []
    open_tags = []
    
    while text:
        if len(text) <= max_length:
            prefix = "".join(f"<{t[0]}{t[1]}>" for t in open_tags)
            chunks.append(balance_html_tags(prefix + text))
            break
            
        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1 or split_pos < max_length // 2:
            split_pos = text.rfind(' ', 0, max_length)
            if split_pos == -1:
                split_pos = max_length
        
        chunk_raw = text[:split_pos]
        text = text[split_pos:].lstrip()
        
        prefix = "".join(f"<{t[0]}{t[1]}>" for t in open_tags)
        full_chunk = prefix + chunk_raw
        
        balanced_chunk, next_open_tags = balance_and_track_tags(full_chunk)
        chunks.append(balanced_chunk)
        open_tags = next_open_tags
        
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
        
        /* KORREKTUR FEHLER 2: white-space: pre-wrap sorgt dafür, dass \\n als echter Umbruch angezeigt wird! */
        .tg-bubble { 
            background: white; 
            padding: 22px; 
            border-radius: 18px; 
            border-bottom-right-radius: 6px; 
            max-width: 460px; 
            box-shadow: 0 10px 25px rgba(0,0,0,0.15); 
            line-height: 1.55; 
            color: #222; 
            white-space: pre-wrap; 
            word-break: break-word; 
        }
        
        .btn-group { display: flex; gap: 12px; margin-top: 10px; }
        .btn { flex: 1; padding: 16px; border: none; border-radius: 12px; font-weight: bold; cursor: pointer; font-size: 15px; display: flex; align-items: center; justify-content: center; gap: 10px; }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-success { background: #34c759; color: white; }
    </style>
</head>
<body>
    <div class="sidebar">
        <div class="logo"><i class="fa-solid fa-paper-plane"></i> Post Master Pro</div>
        <button class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-pen-nib"></i> Editor</button>
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
                    <textarea id="editorInput" placeholder="Schreibe dein Markdown / LaTeX hier..."></textarea>
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
