# Formatierung: Telegram × LLMs × Implementierungsstand

> Stand: v2.7.0 (2026-09-14). Dieses Dokument stellt drei Seiten gegenüber:
> **was Telegram an Formatierung anbietet**, **womit gängige LLMs arbeiten**
> (Ausgabeformate) und **was dieses Projekt bereits wie gut umsetzt**.
> Quellen für die Telegram-Fähigkeiten: die offizielle Bot-API-Dokumentation
> ([core.telegram.org/bots/api](https://core.telegram.org/bots/api),
> Abschnitte „Formatting options“ und „Rich messages“).

## 1. Die drei Formatier-Welten von Telegram

Telegram hat für Bot-Nachrichten **drei Syntaxfamilien**, die sich in
Mächtigkeit und Limits unterscheiden:

| Welt | Aufruf | Eingabe | Limit | Besonderheiten |
|---|---|---|---|---|
| **Regular HTML** | `sendMessage` + `parse_mode="HTML"` | Teilmenge HTML | 4096 Zeichen | `<b> <i> <u> <s> <code> <pre> <a> <blockquote> <tg-spoiler> <details> <tg-emoji>`; kein LaTeX, keine Tabellen |
| **Regular MarkdownV2** | `sendMessage` + `parse_mode="MarkdownV2"` | eigene Syntax | 4096 Zeichen | `*fett*`, `_kursiv_`, `__unter__`, `~durch~`, `\|\|spoiler\|\|`, `[text](url)`, `>Zitat`, `**>aufklappbares Zitat…\|\|`, Zeitstempel `1234567890\|T`; sehr strenge Escape-Regeln |
| **Rich Messages** (Bot API 10.1+) | `sendRichMessage` | GFM-Markdown, HTML oder Blöcke | 32768 Zeichen, 500 Blöcke | natives **LaTeX** (`$…$`/`$$…$$`, Formelquelle = rohes LaTeX), **GFM-Tabellen**, Blockquote-Blöcke inkl. aufklappbarer Zitate und `details`-Blöcke |

Ergänzend kennt Telegram automatisch erkannte Entitäten, die kein Formatter
erzeugen muss: `@mentions`, `#hashtags`, `$CASHTAGS`, `/bot_commands`, nackte
URLs, E-Mails und Telefonnummern.

**Pfadwahl dieses Projekts** (siehe [ARCHITECTURE.md](ARCHITECTURE.md)):
Inhalte mit LaTeX oder Tabellen → Rich-Pfad (`sendRichMessage`, Feld
`markdown`); alles andere → Regular-Pfad (`sendMessage`, `parse_mode="HTML"`).

## 2. Womit arbeiten die gängigen LLMs?

Fast alle großen Modelle geben **GFM-artiges Markdown** aus; die Unterschiede
liegen bei LaTeX-Delimitern, Tabellen und Zitaten/Links:

| LLM/Tool | Markdown-Basis | LaTeX-Ausgabe | Tabellen | Links/Zitate |
|---|---|---|---|---|
| **ChatGPT** (OpenAI) | GFM (Headings, fett/kursiv, Listen, Code, Tabellen) | meist `$$…$$` und `\[…\]`/`\(…\)` | ja (GFM-Pipes) | nackte URLs + `[text](url)`; Deep Research erzeugt intern kodierte Zitatmarker |
| **Claude** (Anthropic) | GFM, nutzt häufig Blockquotes | selten; wenn, dann `$…$`/`$$…$$` | ja | `[text](url)`, nackte URLs |
| **Gemini** (Google) | GFM | `\(…\)`/`\[…\]` und `$…$` gemischt | ja | Zitate teils als Marker (`[cite:…]`), Links teils über Redirect-Wrapper |
| **DeepSeek** | GFM | `\(…\)`/`\[…\]` (Backslash-Delimiter) | ja | `[text](url)` |
| **Perplexity** u. a. Such-KIs | GFM | `\(…\)`/`\[…\]` | ja | **Zitate als Redirect-Links** (z. B. `google.com/search?q=<kodiertes Ziel>`) und verschachtelte Artefakte wie `[text]([url](url))` |
| **Mistral/Llama** (API-roh) | GFM-Basis | `$…$` | ja | `[text](url)` |

Konsequenz für den Formatter: Er muss die **Backslash-Delimiter** von
DeepSeek/Gemini in die Telegram-Dollar-Syntax übersetzen (seit v1.x
implementiert, `convert_deepseek_latex_syntax`) und seit v2.7.0 die
**Redirect-Wrapper der Such-KIs** auf ihre Ziel-URL entpacken.

## 3. Gegenüberstellung: Feature × Telegram × LLM × Status

Legende: ✅ umgesetzt · 🟡 teilweise/Fallback · ❌ nicht umgesetzt

| Feature | Telegram-Fähigkeit | Typische LLM-Syntax | Status hier | Anmerkung |
|---|---|---|---|---|
| Fett | `<b>`/`<strong>`; MDV2 `*x*`; Rich-GFM | `**x**` | ✅ | beide Pfade |
| Kursiv | `<i>`/`<em>`; MDV2 `_x_`; Rich-GFM | `*x*`, `_x_` | ✅ | beide Pfade |
| Unterstrichen | `<u>`/`<ins>`; MDV2 `__x__` | `__x__` | ✅ | Regular: `<u>`; Rich: `__x__`→`<u>x</u>`, URL-geschützt |
| Durchgestrichen | `<s>`/`<strike>`/`<del>`; MDV2 `~x~` | `~~x~~` | ✅ | beide Pfade |
| Inline-Code | `<code>`; MDV2 `` `x` `` | `` `x` `` | ✅ | Platzhalter-Schutz, Escaping |
| Codeblock + Sprache | `<pre><code class="language-…">` | ` ```lang ` | ✅ | Sprache über Allowlist (Attribut-Injection-Schutz) |
| Link | `<a href>`; MDV2 `[t](url)` | `[text](url)` | ✅ | **seit v2.7.0: Redirect-URLs werden auf die Ziel-URL entpackt** |
| Nackte URL | automatische URL-Entität | nackte URLs | ✅ | 1:1 durchgereicht; Redirects werden entpackt (v2.7.0) |
| Überschriften | keine eigenen Entitäten | `#…######` | 🟡 | Regular: Fett + Emoji-Marker (🚀 📍 🔹 🔸); Rich: natives `#` bleibt erhalten |
| Listen | keine Entität (Text) | `- x`, `1. x` | ✅ | Einrückung bleibt erhalten; nummeriert → Bullet |
| Blockquote | `<blockquote>`; MDV2 `>x` | `> x` | ✅ | Regular: zusammenhängende Zeilen → `<blockquote>`; Rich: natives GFM |
| Tabellen | **nur Rich** (GFM) | Pipe-Tabellen | ✅ | Rich: normalisiertes GFM; Regular: Fallback als `Header: Wert`-Zeilen |
| LaTeX | **nur Rich** (`$…$`/`$$…$$`) | `$`, `$$`, `\(…\)`, `\[…\]` | ✅ | Backslash-Delimiter → Dollar-Syntax; Formeln bleiben 1:1 intakt |
| **Spoiler** | `<tg-spoiler>`; MDV2 `\|\|x\|\|` | keine gängige LLM-Syntax | ❌ | Telegram-exklusiv; nachrüstbar (Markdown-Eingang `??x??` o. Ä. wäre Konventionssache) |
| **Aufklappbares Blockquote** | `<blockquote expandable>`; MDV2 `**>…\|\|` | keine LLM-Syntax | ❌ | Telegram-exklusiv; sinnvoll für lange Zitate |
| **Details/Summary** (aufklappbar) | `<details>/<summary>` (HTML-Modus); Rich-Block `details` | keine LLM-Syntax | ❌ | Telegram-exklusiv |
| **Custom Emoji** | `<tg-emoji emoji-id="…">` | keine LLM-Syntax | ❌ | braucht Emoji-ID (Fragment-Kauf); für einen Markdown-Konverter nicht sinnvoll |
| **Zeitstempel-Entität** | `date_time`; MDV2 `1234567890\|T` | keine LLM-Syntax | ❌ | Telegram-exklusiv |
| **Inline-Mentions** | `<a href="tg://user?id=…">` | keine LLM-Syntax | ❌ | braucht User-IDs; `@usernames` erkennt Telegram selbst |
| Verschachtelte Formatierung | erlaubt (außer code/pre) | `**a *b* c**` | 🟡 | einfache Verschachtelungen funktionieren (Reihenfolge der Regex-Ersetzungen); beliebig tiefe/reihenfolge-sensitive Fälle sind durch Regex begrenzt |
| Escaping/Sicherheit | `< > & "` müssen entitisiert werden | beliebige Nutzereingabe | ✅ | volles Escaping + Attribut-Allowlist (Audit M-1); NUL-Filter |
| Syntaxbewusstes Splitting | 4096/32768-Limits | lange Antworten | ✅ | keine zerrissenen Tags/Formeln/Fences an Chunk-Grenzen (Audit B-1) |

## 4. Redirect-URLs (v2.7.0)

Such-KIs und Plattformen legen externe Ziele hinter Tracking-/Redirect-Adressen.
`unwrap_redirect_url()` entpackt sie auf die Ziel-URL:

| Dienst | Muster | Ziel-Parameter |
|---|---|---|
| Google | `google.*/url?q=…` | `q` (oder `url`) |
| Google (Such-KI-Zitat) | `google.*/search?q=<URL>` | `q`, nur wenn Wert eine URL ist |
| YouTube | `youtube.com/redirect?q=…` | `q`, `redirect_url` |
| Facebook | `l.facebook.com/l.php?u=…` | `u` |
| DuckDuckGo | `duckduckgo.com/l/?uddg=…` | `uddg` |
| Reddit | `out.reddit.com/…?url=…` | `url` |
| Steam | `steamcommunity.com/linkfilter/?url=…` | `url`, `u` |
| LinkedIn | `linkedin.com/redir/redirect?url=…` | `url` |
| Bing | `bing.com/ck/a?u=a1<Base64URL>` | `u` (Base64-dekodiert) |

Garantien:

- Das Ziel wird **nur** akzeptiert, wenn es eine absolute `http(s)`-URL ist
  (kein `javascript:`, kein `data:` — Injection-Schutz).
- Normale Suchanfragen (`google.com/search?q=katze`) bleiben unverändert.
- Rekursives Entpacken bis 5 Ebenen (z. B. Facebook → Google → Ziel).
- Mehrfach percent-kodierte Werte werden durchgereicht.
- Link-Artefakte `[text]([label](url))` / `[[text](url)]` werden zu
  `[text](url)` geglättet; wiederholt der Link-Text nur die Redirect-URL,
  wird die Ziel-URL angezeigt.
- Codeblöcke/Inline-Code und Formeln sind über Platzhalter geschützt.

**Offline-Grenze:** Kurz-URL-Dienste (t.co, bit.ly, goo.gl, tinyurl, …)
können ohne Netzwerkaufruf nicht aufgelöst werden und bleiben 1:1 stehen.

## 5. Was kann man noch verbessern? (Ausbau-Kandidaten)

Nach Priorität für den typischen Anwendungsfall „LLM-Antwort → Telegram“:

1. **Spoiler** `||x||` → `<tg-spoiler>` — kleines Feature, klare Syntax.
   Vorsicht: `||` kollidiert nicht mit Tabellen (die leben in eigenen
   Zeilen-Blöcken), müsste aber auf beiden Pfaden getestet werden.
2. **Aufklappbare Blockquotes** für lange Zitate (Regular: Attribut
   `expandable`; Rich: entsprechender Block) — als Opt-in-Konvention, z. B.
   ab N Zeilen Quote-Länge.
3. **`<details>/<summary>`** für lange Code-Anhänge — Regular-Pfad direkt
   möglich, Rich-Pfad über den `details`-Block.
4. **Zeitstempel-Entitäten** (`1234567890|T` bzw. `date_time`) — relevant,
   sobald Termin-/Meeting-Antworten formatiert werden.
5. **Balancierte Klammer-Links** — die aktuelle Link-Regex stoppt am ersten
   `)`; URLs mit Klammern (z. B. Wikipedia `…/Foo_(Bar)`) funktionieren nur
   percent-kodiert. Balanciertes Parsen wäre ein gezieltes Upgrade.
6. **MarkdownV2-Ausgabemodus** als Alternative zu HTML für den Regular-Pfad
   (kleinerer Payload, aber deutlich strengeres Escaping — HTML bleibt vorerst
   die robustere Wahl).

Custom Emoji und User-Mentions per ID sind bewusst **keine** Ausbauziele:
Sie brauchen externe IDs, die aus Markdown-Eingaben nicht hervorgehen.
