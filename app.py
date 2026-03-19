import os
import telebot
from flask import Flask, render_template, request, jsonify
from utils import format_to_tg_html, split_html_message

# --- KONFIGURATION ---
# Die CHAT_ID wurde entfernt. Diese wird nun vom Frontend übergeben.
TOKEN = os.environ.get("TELEGRAM_TOKEN")
bot = telebot.TeleBot(TOKEN) if TOKEN else None

app = Flask(__name__)

# Dein Buy Me a Coffee Link
BASE_COFFEE_URL = "https://buymeacoffee.com/rg4free"

@app.route('/', methods=['GET'])
def index():
    # Lädt nun sauber das Frontend aus dem templates Ordner
    return render_template('index.html', coffee_link=BASE_COFFEE_URL)

@app.route('/api/send', methods=['POST'])
def send_message():
    """Nimmt Nachrichten und die individuelle Chat-ID vom Frontend entgegen."""
    if not bot:
        return jsonify({"status": "error", "message": "Bot Token fehlt auf dem Server!"}), 500

    data = request.json
    content = data.get('content')
    chat_id = data.get('chat_id')

    if not content:
        return jsonify({"status": "error", "message": "Kein Text eingegeben."}), 400
    if not chat_id:
        return jsonify({"status": "error", "message": "Keine Chat-ID angegeben. Bitte in der Seitenleiste eintragen."}), 400

    try:
        formatted_html = format_to_tg_html(content)
        chunks = split_html_message(formatted_html)

        for chunk in chunks:
            # Sendet spezifisch an den Nutzer, der den Request ausgelöst hat
            bot.send_message(chat_id, chunk, parse_mode='HTML')

        return jsonify({"status": "success", "message": "Nachricht erfolgreich gesendet!"})
    except telebot.apihelper.ApiTelegramException as e:
        if "chat not found" in str(e).lower() or "bot can't initiate conversation" in str(e).lower():
            return jsonify({"status": "error", "message": "Chat-ID ungültig oder du hast den Bot in Telegram noch nicht gestartet (/start)."}), 400
        return jsonify({"status": "error", "message": f"Telegram API Fehler: {str(e)}"}), 500
    except Exception as e:
        return jsonify({"status": "error", "message": f"Server Fehler: {str(e)}"}), 500

@app.route('/api/preview', methods=['POST'])
def preview_api():
    """Generiert die Live-Vorschau."""
    data = request.json
    raw_html = format_to_tg_html(data.get('text', ''))
    return jsonify({'html': raw_html})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))
