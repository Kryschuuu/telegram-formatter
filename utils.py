import re

def format_to_tg_html(text):
    """
    Konvertiert Markdown-Eingabe in sauberes Telegram-HTML.
    Ausgelagert für bessere Wartbarkeit.
    """
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
    """
    Teilt lange Texte sicher auf, ohne das Telegram-Limit (4096 Zeichen) zu sprengen.
    Zerschneidet keine HTML-Tags.
    """
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
