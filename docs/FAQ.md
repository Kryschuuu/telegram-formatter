# FAQ — kurze Antworten

Bei **„tut nicht"** → [RUNBOOK.md](RUNBOOK.md). Hier nur die häufigsten Fragen
in Stichworten. Befehle in **fish** zuerst, darunter **bash** — beide sind
identisch, außer an den im RUNBOOK §9 genannten Stellen.

---

## Netzwerk & Erreichbarkeit

### Warum ist die App unter `telegram-formatter.local` nicht erreichbar, obwohl der Container läuft und gesund ist?

Weil Caddy den **falschen Upstream** hat: `host.docker.internal:5000`. Der
Dienst veröffentlicht bewusst **keinen** Host-Port (nur `expose:`), damit weder
Portkonflikte entstehen noch die App am Caddy vorbei erreichbar wird. Auf dem
Host lauscht also nichts auf 5000.

**Richtig ist der Dienstname**, weil Caddy und die App im selben Docker-Netz
hängen:

```caddy
telegram-formatter.local {
	tls internal
	reverse_proxy telegram-formatter:5000
}
```

Nachweisen, welcher Upstream funktioniert:

```bash
# fish
docker compose exec proxy sh -c 'nc -z -w3 telegram-formatter 5000 && echo OPEN || echo CLOSED'
docker compose exec proxy sh -c 'nc -z -w3 host.docker.internal 5000 && echo OPEN || echo CLOSED'
```

```bash
# bash
docker compose exec proxy sh -c 'nc -z -w3 telegram-formatter 5000 && echo OPEN || echo CLOSED'
docker compose exec proxy sh -c 'nc -z -w3 host.docker.internal 5000 && echo OPEN || echo CLOSED'
```

Erwartet: Dienstname `OPEN`, Host-Port `CLOSED`. Details: [RUNBOOK §3](RUNBOOK.md#3-die-app-ist-nicht-erreichbar-nicht-nur-der-proxy).

---

### `ERR_SSL_PROTOCOL_ERROR` im Browser, aber die App läuft

Das ist **kein** Netz- und **kein** Zertifikatsproblem. Es ist ein
Konfigurationsproblem: Caddy sieht eine **veraltete Caddyfile**.

Der Mount `./caddy/Caddyfile:/etc/caddy/Caddyfile` friert die Inode der Datei
im Container ein. Wird sie auf dem Host atomar ersetzt — der Standard vieler
Editoren (temp schreiben + `rename`) —, sieht der Container den alten Inhalt
weiter. Caddy kennt Ihren Hostnamen dann nicht, stellt **kein** Zertifikat aus
und bricht den TLS-Handshake ab.

```bash
# fish
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

```bash
# bash
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

**Verschiedene Prüfsumme = stale Mount.**

Sofort beheben:

```bash
# fish
docker compose up -d --force-recreate proxy
```

```bash
# bash
docker compose up -d --force-recreate proxy
```

Dauerhaft beheben — Mount auf das Verzeichnis umstellen:

```yaml
- ./caddy:/etc/caddy:ro     # Verzeichnis, NICHT die einzelne Datei
```

Ein Verzeichnis-Mount ist immun, weil der Verzeichnis-Inode nicht wechselt.
Details: [RUNBOOK §4](RUNBOOK.md#4-err_ssl_protocol_error-tls-handshake-abbruch).

---

### Muss ich nach jeder Caddyfile-Änderung `docker restart` machen?

**Nein**, nicht mit Verzeichnis-Mount. Caddy erkennt Änderungen an der Datei
automatisch und lädt neu. Mit Einzeldatei-Mount ist ein Neustart des
Containers nötig, damit der Mount die neue Datei sieht — und selbst das hilft
nur, bis der Editor das nächste Mal atomar ersetzt.

Prüfen, ob die neue Konfiguration aktiv ist:

```bash
# fish
docker compose exec proxy curl -s http://127.0.0.1:2019/config/apps/http/servers/srv0/routes \
  | python3 -c "import json,sys; [print(r.get('match',[{}])[0].get('host',['-'])) for r in json.load(sys.stdin)]"
```

```bash
# bash
docker compose exec proxy curl -s http://127.0.0.1:2019/config/apps/http/servers/srv0/routes \
  | python3 -c "import json,sys; [print(r.get('match',[{}])[0].get('host',['-'])) for r in json.load(sys.stdin)]"
```

---

### Warum ist der Host-Port nicht einfach offen?

Weil ein offener Port die App **auch ohne Caddy** erreichbar machen würde —
ohne TLS, ohne ACL, ohne Rate-Limits. Der Caddy ist der einzige Einstiegspunkt.
`expose:` genügt für den Dienstverkehr im Docker-Netz; veröffentlicht wird
nur, was von außen gebraucht wird.

Wer `ports: "5000:5000"` ergänzt, bekommt zusätzlich Portkonflikte und eine
Umgehung aller Zugriffskontrollen. Details: [DOCKER.md §8a](DOCKER.md#8a-geteilter-proxy-mehrere-dienste-hinter-einem-caddy).

---

### `502 Bad Gateway` — was nun?

In dieser Reihenfolge:

```bash
# fish
docker compose ps telegram-formatter
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz
docker compose logs --tail 30 proxy
```

```bash
# bash
docker compose ps telegram-formatter
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz
docker compose logs --tail 30 proxy
```

Läuft die App und antwortet `/healthz` mit `200`, war der 502 ein
Start-Race — `docker compose restart proxy`. Details: [RUNBOOK §6](RUNBOOK.md#6-502-bad-gateway).

---

### Der Container startet nicht: `read-only file system`

Ein Mount zeigt in ein Verzeichnis, das selbst `:ro` eingehängt ist. runc kann
dort keinen Mountpoint anlegen. Der Fall im aktuellen Setup: `browse.html` war
zusätzlich einzeln nach `/etc/caddy/` gemountet, obwohl `/srv/ftp` bereits
komplett eingehängt ist.

**Regel:** in ein `:ro`-Verzeichnis nie zusätzlich etwas hineinmounten.
Details: [RUNBOOK §7](RUNBOOK.md#7-mount-fehler-beim-start).

---

## TLS & Zertifikate

### Wie bekomme ich das CA-Zertifikat auf den Client?

```bash
# fish — auf dem SERVER
cd /home/kris/nas-server
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
scp caddy-root-ca.crt kris@192.168.0.20:/tmp/
```

```bash
# bash — auf dem SERVER
cd /home/kris/nas-server
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
scp caddy-root-ca.crt kris@192.168.0.20:/tmp/
```

Dann **auf dem Client** 192.168.0.20:

```bash
# Linux (fish oder bash)
sudo cp /tmp/caddy-root-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

| Client | Import |
|---|---|
| Linux | `sudo cp … /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| Windows | Doppelklick → „Installieren" → „Vertrauenswürdige Stammzertifizierungsstellen". Firefox separat: Einstellungen → Zertifikate → Importieren |
| macOS | Doppelklick → Schlüsselbund „System" → „Immer vertrauen" |

---

### Warum ist `curl -k` erfolgreich, der Browser aber nicht?

`-k` überspringt die Zertifikatsprüfung vollständig und beweist damit nichts.
Verwenden Sie `--cacert`:

```bash
# fish
curl -sS -o /dev/null -w "%{http_code} tls=%{ssl_verify_result}\n" \
  --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

```bash
# bash
curl -sS -o /dev/null -w "%{http_code} tls=%{ssl_verify_result}\n" \
  --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

`tls=0` = verifiziert, `tls=1` = CA fehlt dem Client → importieren.

---

### Wie oft laufen die Zertifikate ab?

Gar nicht — Caddy erneuert sie automatisch, Tage vor Ablauf, ohne Zutun.
`tls internal` betreibt eine eigene CA im Verzeichnis `caddy_data`. Es gibt
kein Certbot und kein Ablauf-Risiko. **Der einzige mögliche Ausfall** ist
`docker compose down -v`, das diese Daten löscht.

---

### Der Browser warnt nach `docker compose down -v` wieder — schlimm?

Nein, nur ärgerlich. Die CA wurde gelöscht, es gibt eine neue. Die Clients
brauchen einen erneuten Import. `docker compose down` (ohne `-v`) lässt die
Zertifikate unangetastet.

---

## Betrieb

### Wie aktualisiere ich auf eine neue Version?

```bash
# fish
cd /home/kris/GITHUB/telegram-formatter && git pull
cd /home/kris/nas-server
docker compose up -d --build telegram-formatter
curl -s --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

```bash
# bash
cd /home/kris/GITHUB/telegram-formatter && git pull
cd /home/kris/nas-server
docker compose up -d --build telegram-formatter
curl -s --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

Das `version`-Feld der Antwort muss die neue Version zeigen.

---

### Was muss ich sichern?

| Was | Warum | Wie |
|---|---|---|
| `caddy_data/` | CA + Zertifikate, sonst neue Warnungen | [RUNBOOK §1](RUNBOOK.md#1-normalbetrieb) |
| `docker-compose.yml`, `caddy/Caddyfile` | Konfiguration | im Git-Repository versioniert |
| `paperclip-config/config.json` | Paperclip-Konfiguration | im Git-Repository versioniert |

**Nicht** zu sichern: die App selbst (zustandslos) und BYOB-Sessions (leben
nur im RAM, enden beim Neustart).

---

### Warum verlieren alle meine Sessions bei jedem Neustart?

Weil sie absichtlich nur im Arbeitsspeicher liegen — das ist der Zweck: keine
Datei, keine Datenbank, kein Redis, kein Log. Nach dem Neustart die Session
einfach neu öffnen (30 min TTL, 10 min Leerlauf-Timeout).

---

### Sind meine Telegram-Bot-Tokens verschlüsselt gespeichert?

**Nein.** Es gibt keine Verschlüsselung im Ruhezustand — weder hier noch
anderswo im Projekt. Die Zusage lautet: das Token liegt **nur im
Arbeitsspeicher** und wird auf kein dauerhaftes Medium geschrieben. Es
verschwindet beim Prozessende, beim Schließen der Session und nach der TTL.

Wer den Bot-Token in einer `.env` liegen hat, hat es sehr wohl auf einem
dauerhaften Medium — das ist eine Konfigurationsentscheidung, keine
Eigenschaft der App. Details: [../security/README.md](../security/README.md).

---

## BYOB (eigener Bot)

### Meine BYOB-Session ist mit einem 500er gestorben — warum?

Falls die Fehlermeldung `Interner Fehler` lautete: In v2.12.0 genügte ein
einziges Byte `0xE4` im `X-Auth-Token`-Header, um jeden token-geschützten
Aufruf in einen 500er zu verwandeln (`hmac.compare_digest` wirft für
Nicht-ASCII-Strings). Seit **2.13.0** behoben (401 statt 500). Falls die Meldung
`400` lautet: der Text enthielt einen unvollständigen Unicode-Zeichen
(`\ud800`) — auch seit 2.13.0 abgefangen.

---

### Telegram antwortet 429 — was tun?

Die Antwort enthält ein `retry_after`. Die App wartet seit **2.13.0** selbst
(maximal 3 Versuche, maximal 5 s) und wiederholt denselben Teil. Kommt die
Meldung trotzdem, ist die Wartezeit von Telegram länger als 5 s — dann ist
Warten die richtige Antwort, und `retry_after` steht in der Fehlermeldung.

Prüfen, ob der Bot überhaupt im Zielchat ist: der Bot muss Mitglied sein und
das Recht *Nachrichten senden* haben.

---

### Warum ist die Nachricht in 20 Teile zerfallen?

Der Chunker teilt an Absatz-, Zeilen- und Wortgrenzen, nie mitten in einer
Formatierung. 20 Teile entsprechen ~78 000 Zeichen — deutlich über der
Standardgrenze. Für lange Texte über `TELEGRAM_FORMATTER_SHARED_WEB_MAX_INPUT_CHARS`
(Sprung: `MAX_CHUNKS_PER_REQUEST`, 400 „Zu viele Teile"). Details:
[API.md](API.md).

---

## Entwicklung

### Wie führe ich die Tests aus?

```bash
# fish / bash gleich
cd /home/kris/GITHUB/telegram-formatter
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest -q
.venv/bin/python -m ruff check .
```

Aktueller Stand: **609 Tests, 1 übersprungen** (ein jsdom-Smoke-Test, der Node
im PATH braucht), `ruff` ohne Beanstandung.

---

### Welche Datei ändere ich für was?

| Thema | Datei |
|---|---|
| Konvertierungslogik, Splitting, Chunk-Grenzen | `telegram_formatter/utils.py` |
| HTTP-Endpunkte, Rate-Limits, Auth | `telegram_formatter/app.py` |
| Telegram-Versand, Fehlerbehandlung | `telegram_formatter/sender.py` |
| BYOB-Sessions, TTL, Rate-Budget | `telegram_formatter/botkit/session.py` |
| Bot-Token-Verwaltung | `telegram_formatter/botkit/tokens.py` |
| Review-Tor, statische Analyse (BK001–BK012) | `telegram_formatter/botkit/review.py` |
| CLI (`botctl`, `telegram-formatter`) | `telegram_formatter/botctl.py`, `cli.py` |
| Version | `telegram_formatter/__init__.py` |
| Proxy-Routen | `/home/kris/nas-server/caddy/Caddyfile` |
| Dienste, Netz, Mounts | `/home/kris/nas-server/docker-compose.yml` |

---

### Welche Nummer hat eine Version?

```bash
# fish / bash gleich
.venv/bin/python -c "from telegram_formatter import __version__; print(__version__)"
curl -s --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

Beide müssen übereinstimmen. Änderungen an `__init__.py` **und** `CHANGELOG.md`
gehören in denselben Commit.

---

### Welche Nummer hat die Regel BK004?

Im [../peer-review/CODE_REVIEW.md](../peer-review/CODE_REVIEW.md) und in
`botkit/review.py` (`RULES`). Kurz: BK004 ist die Regel, die ausgehende
HTTP-Aufrufe auf `api.telegram.org` beschränkt — sie verhindert, dass ein
„geprüfter" Bot Nachrichten nach außen schickt.
