# Lokales Produktions-Deployment mit Docker Compose + Caddy

Diese Anleitung bringt telegram-formatter als **produktionsreifen Stack** in
ein lokales Netzwerk — mit TLS, automatischem Zertifikat-Management und
IP-Zugriffskontrolle. Alle Adressen sind **konkret** (keine Platzhalter):

| Rolle | Adresse | Aufgabe |
|---|---|---|
| Server (Docker-Host) | `192.168.0.10` | führt `docker compose up` aus |
| Client (freigegeben) | `192.168.0.20` | einziger erlaubter Browser/Client |
| Zugriff | `https://192.168.0.10` | TLS via Caddy (interne CA) |
| Standard-Output-Kanal | `t.me/mdtotxt_bot_web` | öffentlicher Demo-Kanal des geteilten Bots |

Eigenes Netz? Dann die beiden IPs in `Caddyfile` (Site-Adresse + `remote_ip`)
ersetzen — alles andere bleibt gleich (siehe „Anpassen").

## 1. Architektur

```
┌──────────────┐  HTTPS :443   ┌─────────────────────────────┐  HTTP :5000  ┌───────────┐
│ 192.168.0.20 │ ────────────► │ Caddy (Edge: TLS + ACL)     │ ───────────► │ App       │
│ (Client)     │ ◄──────────── │  192.168.0.10, Container    │ ◄─────────── │ (Container│
└──────────────┘  403 für alle │  Ports 80/443 veröffentlicht │  appnet     │  kein Port│
 andere Adressen │ anderen     └─────────────────────────────┘ (intern)    └───────────┘
```

* **Caddy** ist der einzige Einstiegspunkt: terminiert TLS (eigene lokale CA,
  Ausstellung + Erneuerung vollautomatisch), prüft die Client-IP per
  Zugriffskontrolle (`remote_ip`) und proxyt erlaubte Anfragen an `app:5000`.
* **App** (Gunicorn, genau 1 Worker + 8 Threads) veröffentlicht **keine**
  Host-Ports — sie ist nur über das interne Docker-Netz `appnet` erreichbar.
  Direkter Zugriff am Caddy vorbei ist damit unmöglich.
* **BYOB-Sessions** leben prozesslokal im RAM — deshalb ein Worker (mehrere
  Worker ohne Sticky-Routing würden Sessions „verlieren", Client bekäme 410).

## 2. Voraussetzungen (Server 192.168.0.10)

1. **Docker Engine + Compose-Plugin** (Compose v2):
   `docker --version` und `docker compose version` müssen antworten.
2. **Bot-Token** von [@BotFather](https://t.me/BotFather) (`/newbot`).
3. **Zielchat** für den Demo-Betrieb: öffentliche Supergroup oder öffentlicher
   Kanal (z. B. `t.me/mdtotxt_bot_web`), Bot als Mitglied (im Kanal mit Recht
   *Nachrichten senden*), automatisches Löschen auf *1 Monat* — und die
   numerische Chat-ID (negativ, meist `-100…`). Details: Schritt 4b in
   [DEPLOYMENT.md](DEPLOYMENT.md).
4. **Freie Ports 80/443** auf dem Server (kein anderer Webserver aktiv).

## 3. Start in 5 Minuten

```bash
# 1) Repository auf den Server holen (einmalig)
git clone https://github.com/Kryschuuu/telegram-formatter.git
cd telegram-formatter

# 2) Umgebung anlegen und Pflichtwerte eintragen
cp .env.example .env
$EDITOR .env
```

In `.env` mindestens setzen (der Rest hat LAN-sinnvolle Standards):

```bash
TELEGRAM_BOT_TOKEN=<token-von-botfather>   # PFLICHT
TELEGRAM_CHAT_ID=-1001234567890             # PFLICHT (ID deines Demo-Kanals)
```

```bash
# 3) Bauen + starten (im Hintergrund)
docker compose up --build -d

# 4) Start verfolgen — gesund, sobald die App "healthy" meldet
docker compose ps
docker compose logs -f app     # Strg+C zum Verlassen
```

## 4. Verifizieren (vom Client 192.168.0.20)

> Hinweis: Beim allerersten Aufruf warnt der Browser vor dem Zertifikat —
> das ist erwartet (interne CA, noch nicht importiert). Entweder vorab
> Abschnitt 5 ausführen oder die Ausnahme zum Testen einmal bestätigen.

```bash
# 1) Liveness-Probe (muss {"status":"ok","version":"2.12.0"} liefern)
curl -k https://192.168.0.10/healthz

# 2) Editor-Seite im Browser öffnen
#    https://192.168.0.10  → Titel „Telegram Formatter — Markdown + LaTeX"

# 3) Konvertierung per API (rein lesend, sendet nichts)
curl -k -X POST https://192.168.0.10/api/convert \
  -H 'Content-Type: application/json' \
  --data '{"text":"**fett** und $x^2$"}'
```

**Zugriffskontrolle prüfen** (vom Client UND von einer anderen Adresse):

```bash
# Vom freigegebenen Client (192.168.0.20): 200 erwartet
curl -k -o /dev/null -w "%{http_code}\n" https://192.168.0.10/healthz

# Von jeder anderen Adresse (z. B. 192.168.0.30): 403 erwartet
curl -k https://192.168.0.10/healthz   # → "Forbidden"
```

**Versand testen** (öffentlich sichtbar im Demo-Kanal!):

```bash
curl -k -X POST https://192.168.0.10/api/send \
  -H 'Content-Type: application/json' \
  --data '{"text":"Testnachricht vom LAN-Stack","confirm_public":true}'
```

Danach im Kanal `t.me/mdtotxt_bot_web` prüfen, ob die Nachricht ankam —
vollständige Endpoint-Referenz: [API.md](API.md).

## 5. TLS-Vertrauen auf dem Client einrichten (einmalig)

Für private IPs gibt es keine öffentlichen Zertifikate — Caddy betreibt
deshalb eine **eigene lokale CA** und verwaltet alle Zertifikate automatisch
(Ausstellung + Erneuerung, kein Certbot, kein Ablauf-Risiko). Einmalig muss
der Client dieser CA vertrauen, sonst warnt jeder Browser:

```bash
# Auf dem SERVER: Wurzelzertifikat exportieren …
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt

# … auf den CLIENT übertragen (z. B. scp) und dort importieren:
#   Linux:   sudo cp caddy-root.crt /usr/local/share/ca-certificates/ && sudo update-ca-certificates
#   Windows: Doppelklick → „Installieren" → Speicherort „Vertrauenswürdige
#            Stammzertifizierungsstellen" (für Chrome/Edge; Firefox: Einstellungen
#            → Zertifikate → Importieren → „Websites vertrauen")
#   macOS:   Doppelklick → Schlüsselbund „System" → Vertrauen: „Immer vertrauen"
```

Danach lädt `https://192.168.0.10` ohne Warnung (ggf. Browser neu starten).
Ohne Import funktioniert alles trotzdem — nur mit Klick-Warnung pro Sitzung.

## 6. Betrieb

```bash
docker compose ps                  # Status + Gesundheitszustand
docker compose logs -f             # alle Logs live (app + caddy)
docker compose logs -f app         # nur App-Logs
docker compose restart app         # App neu starten (Caddy läuft weiter)
docker compose down                # alles stoppen (Zertifikate bleiben im Volume)
docker compose down -v             # ACHTUNG: löscht auch CA + Zertifikate
```

**Update** (neue Version einspielen):

```bash
git pull
docker compose up --build -d
curl -k https://192.168.0.10/healthz   # Versionsfeld prüfen
```

**Backup**: Die Volumes `telegram-formatter-caddy-data` (CA + Zertifikate)
und die `.env` sichern — die App selbst ist zustandslos (BYOB-Sessions leben
nur im RAM und enden beim Neustart):

```bash
docker run --rm -v telegram-formatter-caddy-data:/data -v "$PWD":/bak \
  alpine tar czf /bak/caddy-data-backup.tar.gz -C /data .
cp .env /sicherer/ort/.env.backup   # enthält Geheimnisse — sicher verwahren!
```

## 7. Konfigurationsreferenz

Alle Variablen mit Erklärung stehen kommentiert in [`.env.example`](../.env.example)
(kopieren nach `.env`, nie committen — `.env` ist git-ignoriert). Die
wichtigsten Entscheidungen für den LAN-Betrieb:

| Variable | LAN-Empfehlung | Wirkung |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Token von BotFather | geteilter Bot (Pflicht) |
| `TELEGRAM_CHAT_ID` | ID des Demo-Kanals | pinnt `/api/send` auf diesen Chat |
| `TELEGRAM_FORMATTER_SHARED_CHAT_URL` | `https://t.me/mdtotxt_bot_web` | öffentlich genannter Kanal (muss zur ID gehören) |
| `TELEGRAM_FORMATTER_SHARED_RETENTION_DAYS` | `30` | Löschfrist = Kanal-Einstellung „1 Monat" |
| `TELEGRAM_FORMATTER_SHARED_WEB_SEND` | `1` | Browser-Versand ohne eigenes Setup an |
| `TELEGRAM_FORMATTER_API_TOKEN` | leer (Demo) | nur für authentifizierte API-Nutzung setzen |
| `TELEGRAM_FORMATTER_TRUSTED_PROXY_HOPS` | `1` | wird von Compose erzwungen (genau ein Hop: Caddy) |
| `TELEGRAM_FORMATTER_BYOB_ENABLED` | `1` | private Sessions mit eigenem Bot erlauben |

### Anpassen (eigenes Netz, mehrere Clients)

* **Andere Adressen:** In `Caddyfile` die Site-Zeile (`https://…`) und die
  `remote_ip`-Liste ändern, dann `docker compose up -d caddy`.
* **Mehrere Clients / ganzes Subnetz:** Adressen durch Leerzeichen getrennt
  anhängen (`remote_ip 192.168.0.20 192.168.0.21 …`) oder CIDR nutzen
  (`remote_ip 192.168.0.0/24`).
* **Eigener DNS-Name** (z. B. `formatter.lan` via Router/hosts-Datei):
  Site-Zeile auf den Namen ändern — `tls internal` stellt dafür automatisch
  ein passendes Zertifikat aus.

## 8. Troubleshooting

| Symptom | Ursache & Abhilfe |
|---|---|
| Browser: „unsicher / Zertifikat ungültig" | CA noch nicht importiert → Abschnitt 5. Mit `curl -k` testen (überspringt die Prüfung). |
| `403 Forbidden` vom eigenen Client | Client-IP stimmt nicht mit der ACL überein: tatsächliche IP prüfen (`ip addr`), ggf. `remote_ip` in `Caddyfile` ergänzen + `docker compose up -d caddy`. |
| `502 Bad Gateway` | App (noch) nicht gesund: `docker compose ps` / `docker compose logs app` — meist fehlende `.env`-Pflichtwerte oder Tippfehler. |
| Port-Konflikt beim Start (`address already in use`) | Port 80/443 belegt (Apache/nginx/anderer Caddy): Fremddienst stoppen oder dessen Ports umlegen. |
| Endlosschleife / Login unmöglich | Hier nicht anwendbar — die App hat kein Login; BYOB-Sessions enden beim App-Neustart (RAM), danach einfach neu öffnen. |
| Telegram antwortet 429/502 | Rate-Limit oder Bot nicht im Kanal: Antwort-`retry_after` abwarten, Bot-Mitgliedschaft + Recht *Nachrichten senden* prüfen. |
| `docker compose config` meckert | `.env` fehlt oder YAML-Syntaxfehler: `cp .env.example .env` bzw. Einrückung (2 Leerzeichen) prüfen. |

## 9. Sicherheits-Checkliste (vor dem produktiven Einsatz)

- [ ] `.env` enthält echte Secrets und ist **nicht** im Git (`git status` sauber).
- [ ] `Caddyfile`-ACL enthält genau die gewünschten Clients; 403 von einer
      fremden Adresse verifiziert (Abschnitt 4).
- [ ] Client(s) vertrauen der Caddy-CA (Abschnitt 5) — kein Dauer-Klick auf
      Warnungen (Training gegen Warnmüdigkeit).
- [ ] `TELEGRAM_CHAT_ID` + `SHARED_CHAT_URL` + `RETENTION_DAYS` beschreiben
      denselben Kanal (falsche Kanal-Aussage ist schlimmer als keine).
- [ ] `TELEGRAM_FORMATTER_API_TOKEN` gesetzt, sobald Dritte die API direkt
      nutzen dürfen (Browser-Weg bleibt davon unberührt).
- [ ] Backup von `caddy_data`-Volume + `.env` eingerichtet (Abschnitt 6).
- [ ] Updates: `git pull && docker compose up --build -d` + `/healthz`-Check.
