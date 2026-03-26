import os
import telebot
import re
from flask import Flask, render_template_string, request, jsonify

# --- KONFIGURATION ---
TOKEN = os.environ.get("TELEGRAM_TOKEN")
MY_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

bot = telebot.TeleBot(TOKEN) if TOKEN else None
app = Flask(__name__)

# Dein Buy Me a Coffee Link
BASE_COFFEE_URL = "https://buymeacoffee.com/rg4free"

def format_to_tg_html(text):
    """Konvertiert Markdown-Eingabe in sauberes Telegram-HTML."""
    if not text:
        return ""

    # HTML Sonderzeichen escapen
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # Headlines umwandeln
    text = re.sub(r'^#\s+(.*)$', r'<b>🚀 \1</b>', text, flags=re.M)
    text = re.sub(r'^##\s+(.*)$', r'<b>📍 \1</b>', text, flags=re.M)
    text = re.sub(r'^###\s+(.*)$', r'<b>🔹 \1</b>', text, flags=re.M)

    # Fett, Kursiv, Unterstrichen
    text = re.sub(r'\*\*\*(.*?)\*\*\*', r'<b><i>\1</i></b>', text)
    text = re.sub(r'\*\*(.*?)\*\*', r'<b>\1</b>', text)
    text = re.sub(r'\*(.*?)\*', r'<i>\1</i>', text)
    text = re.sub(r'__(.*?)__', r'<u>\1</u>', text)

    # Listen
    text = re.sub(r'^[*-]\s+', r'• ', text, flags=re.M)

    # Code-Bloecke
    text = re.sub(r'```(.*?)```', r'<pre>\1</pre>', text, flags=re.S)
    text = re.sub(r'`(.*?)`', r'<code>\1</code>', text)

    return text

def split_html_message(text, max_length=4000):
    """Teilt lange Texte sicher auf, ohne das Limit von 4096 Zeichen zu sprengen."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break

        split_pos = text.rfind('\n', 0, max_length)
        if split_pos == -1:
            split_pos = max_length

        chunks.append(text[:split_pos])
        text = text[split_pos:].lstrip()
    return chunks

# Das komplette HTML-Frontend als String
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="de">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Telegram Post Master Pro</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        :root { --tg-blue: #24A1DE; --tg-bg: #547594; --sidebar-width: 280px; --bg-light: #f4f7f9; }
        * { box-sizing: border-box; transition: 0.2s ease-in-out; }
        body { font-family: 'Segoe UI', Tahoma, sans-serif; margin: 0; display: flex; height: 100vh; background: var(--bg-light); }

        .sidebar { width: var(--sidebar-width); background: white; border-right: 1px solid #ddd; display: flex; flex-direction: column; padding: 25px; }
        .logo { font-size: 22px; font-weight: 800; color: var(--tg-blue); margin-bottom: 40px; display: flex; align-items: center; gap: 12px; }
        .nav-item { padding: 14px 18px; margin-bottom: 8px; border-radius: 12px; cursor: pointer; display: flex; align-items: center; gap: 12px; color: #555; text-decoration: none; font-weight: 500; font-size: 15px; border: none; background: none; width: 100%; }
        .nav-item:hover { background: #f0f7ff; color: var(--tg-blue); }
        .nav-item.active { background: var(--tg-blue); color: white; box-shadow: 0 4px 12px rgba(36, 161, 222, 0.25); }
        .sidebar-footer { margin-top: auto; padding-top: 20px; border-top: 1px solid #eee; display: flex; flex-direction: column; gap: 5px;}

        .main-content { flex: 1; display: flex; flex-direction: column; padding: 30px; overflow: hidden; }
        .view { display: none; height: 100%; flex-direction: column; gap: 20px; }
        .view.active { display: flex; }

        .editor-container { display: flex; gap: 25px; flex: 1; min-height: 0; }
        .panel { flex: 1; display: flex; flex-direction: column; gap: 10px; }
        .panel-label { font-weight: bold; font-size: 12px; color: #999; text-transform: uppercase; letter-spacing: 1.2px; }

        textarea { flex: 1; border: 2px solid #e0e6ed; border-radius: 16px; padding: 20px; font-family: 'Consolas', monospace; resize: none; outline: none; font-size: 15px; background: white; line-height: 1.6; }
        textarea:focus { border-color: var(--tg-blue); }

        .preview-box { flex: 1; background: var(--tg-bg); border-radius: 16px; display: flex; justify-content: center; padding: 30px; overflow-y: auto; background-image: url('https://www.transparenttextures.com/patterns/cubes.png'); }
        .tg-bubble { background: white; padding: 18px; border-radius: 18px; border-bottom-right-radius: 4px; max-width: 440px; width: 100%; height: fit-content; box-shadow: 0 8px 20px rgba(0,0,0,0.15); font-size: 15px; line-height: 1.5; color: #222; word-wrap: break-word; }

        .btn-group { display: flex; gap: 12px; }
        .btn { padding: 14px 24px; border: none; border-radius: 12px; font-weight: bold; cursor: pointer; display: flex; align-items: center; gap: 10px; font-size: 14px; }
        .btn-primary { background: var(--tg-blue); color: white; }
        .btn-success { background: #34c759; color: white; }
        .btn-danger { background: #ff3b30; color: white; }
        .btn:hover { filter: brightness(1.05); transform: translateY(-1px); }

        .content-card { background: white; padding: 40px; border-radius: 20px; box-shadow: 0 4px 15px rgba(0,0,0,0.05); max-width: 850px; overflow-y: auto; line-height: 1.7; color: #333; }
        .content-card h1, .content-card h2 { color: var(--tg-blue); }
        .content-card h3 { margin-top: 30px; color: #000; border-bottom: 1px solid #eee; padding-bottom: 5px; }

        .crypto-list { display: flex; flex-direction: column; gap: 15px; margin-top: 25px; }
        .crypto-item { display: flex; align-items: center; justify-content: space-between; background: #f8f9fa; padding: 15px 20px; border-radius: 12px; border: 1px solid #e0e6ed; }
        .crypto-item:hover { border-color: var(--tg-blue); background: #f0f7ff; }
        .crypto-info { display: flex; align-items: center; gap: 18px; flex: 1; overflow: hidden; }
        .crypto-icon { font-size: 28px; width: 40px; text-align: center; }
        .crypto-details { flex: 1; min-width: 0; }
        .crypto-name { font-weight: bold; font-size: 16px; color: #222; display: flex; align-items: center; gap: 10px; }
        .crypto-network { font-size: 11px; color: #666; background: #e2e8f0; padding: 3px 8px; border-radius: 6px; font-weight: normal; }
        .crypto-address { font-family: 'Consolas', monospace; color: #555; font-size: 14px; margin-top: 6px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .btn-copy-sm { background: white; border: 1px solid #cbd5e1; padding: 10px 15px; border-radius: 8px; cursor: pointer; color: #333; font-weight: bold; transition: 0.2s; display: flex; align-items: center; gap: 8px; }
        .btn-copy-sm:hover { background: #e2e8f0; border-color: #94a3b8; }
        .btn-copy-sm.success { background: #22c55e; color: white; border-color: #22c55e; }
    </style>
</head>
<body>

<div class="sidebar">
    <div class="logo"><i class="fa-solid fa-paper-plane"></i> Post Master</div>

    <button class="nav-item active" onclick="showView('editor', this)"><i class="fa-solid fa-pen-nib"></i> Editor</button>
    <button class="nav-item" onclick="showView('help', this)"><i class="fa-solid fa-circle-info"></i> Hilfe & Tipps</button>
    <button class="nav-item" onclick="showView('impressum', this)"><i class="fa-solid fa-shield-halved"></i> Impressum</button>

    <div class="sidebar-footer">
        <a href="mailto:deine-email@beispiel.de" class="nav-item"><i class="fa-solid fa-envelope"></i> Support</a>
        <a href="{{ coffee_link }}" target="_blank" class="nav-item" style="color: #f39c12; font-weight: bold; background: #fff8e1;"><i class="fa-solid fa-mug-hot"></i> Buy Me a Coffee</a>
    </div>
</div>

<div class="main-content">
    <!-- Editor Ansicht -->
    <div id="editor" class="view active">
        <div class="editor-container">
            <div class="panel">
                <div class="panel-label">Markdown Eingabe</div>
                <textarea id="editorInput" placeholder="# Deine Überschrift...&#10;&#10;Schreibe hier deinen Text. Nutze **Fett** oder Listen."></textarea>
            </div>
            <div class="panel">
                <div class="panel-label">Vorschau (Telegram Stil)</div>
                <div class="preview-box">
                    <div class="tg-bubble" id="previewBubble">Bereit für deinen Text...</div>
                </div>
            </div>
        </div>
        <div class="btn-group">
            <button class="btn btn-primary" onclick="sendToTelegram()"><i class="fa-solid fa-share-from-square"></i> Senden</button>
            <button class="btn btn-success" id="copyBtn" onclick="copyToClipboard()"><i class="fa-solid fa-copy"></i> Kopieren</button>
            <button class="btn btn-danger" onclick="resetAll()"><i class="fa-solid fa-trash-can"></i> Reset</button>
        </div>
    </div>

    <!-- Hilfe Ansicht -->
    <div id="help" class="view">
        <div class="content-card">
            <h1>Formatierungs-Tipps</h1>
            <ul>
                <li><code># Überschrift</code> &rarr; Wird groß, fett und bekommt eine Rakete 🚀</li>
                <li><code>## Untertitel</code> &rarr; Wird fett und bekommt einen Pin 📍</li>
                <li><code>**Fett**</code> &rarr; <b>Wird fett dargestellt</b></li>
                <li><code>- Liste</code> &rarr; Wird in Aufzählungspunkte • umgewandelt</li>
            </ul>
        </div>
    </div>

    <!-- Impressum Ansicht -->
    <div id="impressum" class="view">
        <div class="content-card">
            <h1>Impressum</h1>

<p><strong>Angaben gemäß § 5 TMG</strong></p>

<p>
[DEIN NAME]<br>
[DEINE STRASSE + HAUSNUMMER]<br>
[PLZ ORT]<br>
Deutschland
</p>

<p><strong>Kontakt:</strong><br>
E-Mail: [DEINE E-MAIL]
</p>

<p><strong>Verantwortlich für den Inhalt nach § 18 Abs. 2 MStV:</strong><br>
[DEIN NAME]<br>
[DEINE ADRESSE]
</p>

<hr>

<h1>Datenschutzerklärung</h1>

<h2>1. Allgemeine Hinweise</h2>
<p>
Diese Website dient als privates Projekt zur Textformatierung für Telegram.
Es werden keine personenbezogenen Daten aktiv gespeichert oder dauerhaft verarbeitet.
</p>

<h2>2. Verantwortlicher</h2>
<p>
[DEIN NAME]<br>
[DEINE ADRESSE]<br>
E-Mail: [DEINE E-MAIL]
</p>

<h2>3. Zugriffsdaten (Server-Logfiles)</h2>
<p>
Der Hosting-Provider dieser Website erhebt und speichert automatisch Informationen in sogenannten Server-Logfiles, die Ihr Browser automatisch übermittelt. Dies sind:
</p>

<ul>
<li>Browsertyp und Browserversion</li>
<li>verwendetes Betriebssystem</li>
<li>Referrer URL</li>
<li>Hostname des zugreifenden Rechners</li>
<li>Uhrzeit der Serveranfrage</li>
<li>IP-Adresse</li>
</ul>

<p>
Diese Daten sind nicht bestimmten Personen zuordenbar und werden nicht mit anderen Datenquellen zusammengeführt.
Die Verarbeitung erfolgt auf Grundlage von Art. 6 Abs. 1 lit. f DSGVO (berechtigtes Interesse an technisch fehlerfreier Darstellung).
</p>

<h2>4. Keine aktive Datenspeicherung</h2>
<p>
Die Nutzung dieser Website ist in der Regel ohne Angabe personenbezogener Daten möglich.
Eingegebene Inhalte (z. B. Text im Formatter) werden nicht gespeichert, sondern ausschließlich lokal im Browser verarbeitet oder temporär zur Verarbeitung verwendet.
</p>

<h2>5. Externes Hosting</h2>
<p>
Diese Website wird bei einem externen Dienstleister (Render) gehostet.
Der Anbieter verarbeitet personenbezogene Daten (z. B. IP-Adressen) zur Bereitstellung der Website.
</p>

<p>
Die Nutzung erfolgt im Interesse einer sicheren und effizienten Bereitstellung (Art. 6 Abs. 1 lit. f DSGVO).
</p>

<h2>6. Cookies</h2>
<p>
Diese Website verwendet keine Cookies zur Nutzerverfolgung oder Analyse.
</p>

<h2>7. Ihre Rechte</h2>
<p>
Sie haben im Rahmen der geltenden gesetzlichen Bestimmungen jederzeit das Recht auf:
</p>

<ul>
<li>Auskunft über Ihre gespeicherten Daten</li>
<li>Berichtigung unrichtiger Daten</li>
<li>Löschung Ihrer Daten</li>
<li>Einschränkung der Verarbeitung</li>
<li>Widerspruch gegen die Verarbeitung</li>
</ul>

<p>
Hierzu können Sie sich jederzeit unter der im Impressum angegebenen Adresse an mich wenden.
</p>

<h2>8. Beschwerderecht</h2>
<p>
Sie haben das Recht, sich bei einer Datenschutz-Aufsichtsbehörde über die Verarbeitung Ihrer personenbezogenen Daten zu beschweren.
</p>

<h2>9. SSL- bzw. TLS-Verschlüsselung</h2>
<p>
Diese Seite nutzt aus Sicherheitsgründen eine SSL- bzw. TLS-Verschlüsselung.
</p>

<h2>10. Stand</h2>
<p>
Stand: März 2026
</p>
        </div>
    </div>
</div>

<script>
    const inputArea = document.getElementById('editorInput');
    const previewBubble = document.getElementById('previewBubble');

    function showView(viewId, navEl) {
        document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
        document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
        document.getElementById(viewId).classList.add('active');
        navEl.classList.add('active');
    }

    function resetAll() {
        if(confirm("Alles löschen?")) {
            inputArea.value = "";
            previewBubble.innerHTML = "Bereit für deinen Text...";
        }
    }

    async function copyToClipboard() {
        const text = previewBubble.innerText;
        if(!text || text === "Bereit für deinen Text...") return;
        try {
            await navigator.clipboard.writeText(text);
            const btn = document.getElementById('copyBtn');
            const old = btn.innerHTML;
            btn.innerHTML = '<i class="fa-solid fa-check"></i> Kopiert!';
            setTimeout(() => btn.innerHTML = old, 2000);
        } catch (e) { alert("Fehler beim Kopieren."); }
    }

    async function copyWallet(elementId, btnElement) {
        const address = document.getElementById(elementId).innerText;
        try {
            await navigator.clipboard.writeText(address);
            const originalHTML = btnElement.innerHTML;
            btnElement.innerHTML = '<i class="fa-solid fa-check"></i> Kopiert!';
            btnElement.classList.add('success');
            setTimeout(() => {
                btnElement.innerHTML = originalHTML;
                btnElement.classList.remove('success');
            }, 2000);
        } catch (e) { alert("Kopieren fehlgeschlagen."); }
    }

    async function sendToTelegram() {
        if(!inputArea.value) { alert("Bitte gib einen Text ein."); return; }
        try {
            const res = await fetch('/', {
                method: 'POST',
                headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                body: 'content=' + encodeURIComponent(inputArea.value)
            });
            const status = await res.text();
            if(status === "OK") alert("🚀 Erfolgreich gesendet!");
            else alert("Fehler: " + status);
        } catch(e) { alert("Netzwerkfehler."); }
    }

    let delayTimer;
    inputArea.addEventListener('input', () => {
        clearTimeout(delayTimer);
        delayTimer = setTimeout(async () => {
            if(!inputArea.value) { previewBubble.innerHTML = "Bereit..."; return; }
            const res = await fetch('/preview', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({text: inputArea.value})
            });
            const data = await res.json();
            previewBubble.innerHTML = data.html.replace(/\\n/g, '<br>');
        }, 350);
    });
</script>
</body>
</html>
"""

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        content = request.form.get('content')
        if not bot: return "Bot Token fehlt!", 500
        if content:
            try:
                formatted_html = format_to_tg_html(content)
                chunks = split_html_message(formatted_html)
                for chunk in chunks:
                    bot.send_message(MY_CHAT_ID, chunk, parse_mode='HTML')
                return "OK"
            except Exception as e:
                return f"Fehler: {str(e)}", 500
    return render_template_string(HTML_TEMPLATE, coffee_link=BASE_COFFEE_URL)

@app.route('/preview', methods=['POST'])
def preview_api():
    data = request.json
    raw_html = format_to_tg_html(data.get('text', ''))
    return jsonify({'html': raw_html})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
