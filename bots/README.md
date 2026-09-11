# `bots/` — eigene (Nutzer-)Bots

Ablage für **eigene Bot-Implementierungen**, die den Baukasten
`telegram_formatter/botkit` nutzen. Dieser Ordner ist durch
[`.github/CODEOWNERS`](../.github/CODEOWNERS) und den CI-Job `bot-gate` in
[`.github/workflows/bot-review.yml`](../.github/workflows/bot-review.yml)
geschützt: jede Datei hier muss das Review-Gate durchlaufen, bevor sie gemergt
wird.

## Ablauf

```bash
# 1) Statik (BK001–BK012) + Ticket
python -m telegram_formatter.botctl review bots/mein_bot.py --bot-id <ID>

# 2) Zwei Freigaben (Vier-Augen-Prinzip, ≥1 Maintainer)
python -m telegram_formatter.botctl approve <TICKET> --reviewer <handle> \
    --role maintainer --checks C1,C2,C3,C4,C5,C6,C7,C8,C9

# 3) Tor vor dem Deployment
python -m telegram_formatter.botctl verify bots/mein_bot.py --bot-id <ID>
```

Vorlage und Referenz: [`examples/own_bot/minimal_bot.py`](../examples/own_bot/minimal_bot.py)
(besteht alle Regeln). Architektur & Regeln:
[`docs/DECENTRAL_BOT_ARCHITECTURE.md`](../docs/DECENTRAL_BOT_ARCHITECTURE.md).

## Harte Regeln (Kurzfassung)

- Kein Token im Quelltext — nur `BotToken` + Environment/`getpass`.
- Keine Persistenz (Dateien, DB, Cache), keine Fremd-Hosts, kein `eval`/`exec`,
  keine Shell, keine Inhalte in Logs.
- Session-Ende räumt auf (`deleteWebhook`, `drop_pending_updates`).
