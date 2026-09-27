# Runbook — Betrieb und Diagnose

Was tun, wenn etwas nicht geht. Geordnet nach dem ersten sichtbaren Symptom.

**Zwei Sprachen, jeder Befehl doppelt.** fish und bash unterscheiden sich bei
`set`, `source`, Variablenzuweisung und Schleifen. Beide Varianten stehen bei
jedem Befehl; die fish-Variante zuerst, weil fish auf diesem Setup die
Standard-Shell ist.

**Wo der Stack liegt:** `/home/kris/nas-server` (Compose-Datei + Caddyfile),
Quellcode in `/home/kris/GITHUB/telegram-formatter`. Die App lauscht **nicht**
auf einem Host-Port — nur Caddy ist von außen erreichbar.

---

## 0. Die drei Fragen, die 90 % aller Fälle klären

Bevor Sie irgendetwas ändern, beantworten Sie diese drei Fragen. Fast jeder
Fehlerfall ist genau eine davon.

```bash
# fish
echo "--- 1. Läuft der Container? ---"
docker compose ps telegram-formatter

echo "--- 2. Antwortet die App direkt? (im Netz, nicht am Host) ---"
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- 3. Sieht der Container die aktuelle Caddyfile? ---"
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

```bash
# bash
echo "--- 1. Läuft der Container? ---"
docker compose ps telegram-formatter

echo "--- 2. Antwortet die App direkt? (im Netz, nicht am Host) ---"
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- 3. Sieht der Container die aktuelle Caddyfile? ---"
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

Erwartete Antworten: **1.** `Up ... (healthy)`, **2.** `200` mit
`{"status":"ok"}`, **3.** zwei gleiche Prüfsummen.

| Was Sie sehen | Bedeutung | Sprung zu |
|---|---|---|
| 1. tot / `Restarting` | App-Prozess stürzt ab | [§2](#2-container-startet-nicht) |
| 2. `000` oder `CLOSED` | Netz/Name falsch | [§3](#3-die-app-ist-nicht-erreichbar-nicht-nur-der-proxy) |
| 3. **verschieden** | veralteter Bind-Mount | [§4](#4-err_ssl_protocol_error-tls-handshake-abbruch) |
| 1–3 ok, Browser kaputt | CA oder ACL | [§5](#5-browser-beklagt-sich-trotz-gutem-curl) |
| 1–3 ok, `502` | App nimmt nicht ab | [§6](#6-502-bad-gateway) |

---

## 1. Normalbetrieb

```bash
# fish
cd /home/kris/nas-server
docker compose ps                                  # Status + Gesundheit
docker compose logs -f telegram-formatter          # App-Log live
docker compose logs -f proxy                       # Caddy-Log live
docker compose restart telegram-formatter          # App neu starten (Caddy läuft weiter)
docker compose restart proxy                       # Proxy neu starten (Zertifikate bleiben)
```

```bash
# bash
cd /home/kris/nas-server
docker compose ps
docker compose logs -f telegram-formatter
docker compose logs -f proxy
docker compose restart telegram-formatter
docker compose restart proxy
```

`docker compose down` stoppt alles, `down -v` **löscht zusätzlich CA und
Zertifikate** — danach warnt jeder Browser erneut und alle Clients brauchen
einen neuen Import.

### Update einspielen

```bash
# fish
cd /home/kris/GITHUB/telegram-formatter
git pull
cd /home/kris/nas-server
docker compose up -d --build telegram-formatter
curl -s --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

```bash
# bash
cd /home/kris/GITHUB/telegram-formatter
git pull
cd /home/kris/nas-server
docker compose up -d --build telegram-formatter
curl -s --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

Prüfen Sie das `version`-Feld der Antwort — es muss der neue Version entsprechen.

### Backup

Die App ist zustandslos (BYOB-Sessions leben nur im RAM). Zu sichern sind die
Zertifikate und die Compose-Konfiguration.

```bash
# fish
cd /home/kris/nas-server
docker run --rm -v ./caddy_data:/data -v "$PWD":/bak \
  alpine tar czf /bak/caddy-data-backup.tar.gz -C /data .
cp docker-compose.yml /sicherer/ort/nas-server-compose.bak

echo "Achtung: /srv/ftp und die Paperclip-Configdaten sind NICHT dabei."
```

```bash
# bash
cd /home/kris/nas-server
docker run --rm -v ./caddy_data:/data -v "$PWD":/bak \
  alpine tar czf /bak/caddy-data-backup.tar.gz -C /data .
cp docker-compose.yml /sicherer/ort/nas-server-compose.bak

echo "Achtung: /srv/ftp und die Paperclip-Configdaten sind NICHT dabei."
```

`caddy_data` gehört `root`; der `docker run` läuft deshalb als root im
Container und braucht kein `sudo` auf dem Host.

---

## 2. Container startet nicht

```bash
# fish
docker compose ps -a telegram-formatter
docker compose logs --tail 50 telegram-formatter
docker inspect telegram-formatter --format '{{json .State.Health}}'
```

```bash
# bash
docker compose ps -a telegram-formatter
docker compose logs --tail 50 telegram-formatter
docker inspect telegram-formatter --format '{{json .State.Health}}'
```

| Ausgabe | Ursache | Abhilfe |
|---|---|---|
| `Restarting (n)` im Sekundentakt | Prozess crasht sofort | Logs lesen — die letzte Zeile nennt den Grund |
| `Exit 1`, `ModuleNotFoundError` | Image kaputt/veraltet | `docker compose up -d --build telegram-formatter` |
| `healthy` fehlt, `unhealthy` | `/healthz` antwortet nicht | App-Logs; läuft der Prozess auf Port 5000? |
| `OCI runtime create failed … read-only file system` | Mount-Konflikt | Ein Mount zielt in ein `:ro`-Verzeichnis — verschachtelte Einzeldatei-Mounts entfernen (siehe §7) |
| `address already in use` | Port belegt | Die App veröffentlicht keinen Host-Port; wer einen sieht, hat `ports:` ergänzt — entfernen |

Nach jeder Konfigurationsänderung an der Compose-Datei:

```bash
# fish
docker compose config --quiet; and echo "Compose-Datei ist gültig"
```

```bash
# bash
docker compose config --quiet && echo "Compose-Datei ist gültig"
```

---

## 3. Die App ist nicht erreichbar (nicht nur der Proxy)

Das ist fast immer der **Upstream-Fehler**: `host.docker.internal:5000` zeigt
ins Leere, weil die App bewusst keinen Host-Port veröffentlicht.

```bash
# fish
echo "--- Dienstname (richtig) ---"
docker compose exec proxy sh -c 'nc -z -w3 telegram-formatter 5000 && echo OPEN || echo CLOSED'
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- Host-Port (das ist die Falle) ---"
docker compose exec proxy sh -c 'nc -z -w3 host.docker.internal 5000 && echo OPEN || echo CLOSED'
```

```bash
# bash
echo "--- Dienstname (richtig) ---"
docker compose exec proxy sh -c 'nc -z -w3 telegram-formatter 5000 && echo OPEN || echo CLOSED'
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- Host-Port (das ist die Falle) ---"
docker compose exec proxy sh -c 'nc -z -w3 host.docker.internal 5000 && echo OPEN || echo CLOSED'
```

**Erwartet:** Dienstname `OPEN` + `200`, Host-Port `CLOSED`.

| Ergebnis | Bedeutung | Fix |
|---|---|---|
| Dienstname `CLOSED` | anderer Dienstname, anderes Netz, oder Container tot | Dienstnamen im Caddyfile gegen `docker compose config --services` prüfen; Netz in beiden Services identisch? |
| Host-Port `OPEN` | da läuft etwas **anderes** auf 5000 | `ss -ltnp \| grep 5000` — Portfreigabe des falschen Dienstes |
| Beide `CLOSED` | App läuft nicht | zurück zu §2 |

Prüfen, ob beide Services im selben Netz hängen:

```bash
# fish
docker inspect telegram-formatter --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect nas-server-proxy-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

```bash
# bash
docker inspect telegram-formatter --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
docker inspect nas-server-proxy-1 --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Beide Zeilen müssen mindestens ein gemeinsames Netz nennen (hier `web`).

---

## 4. ERR_SSL_PROTOCOL_ERROR (TLS-Handshake bricht ab)

Das **ist kein** Netz- und **kein** Zertifikatsproblem. Es ist fast immer ein
veralteter Bind-Mount: der Caddy-Container sieht eine alte Caddyfile, kennt
Ihren Hostnamen nicht, stellt kein Zertifikat aus und bricht den Handshake ab.

### 4.1 Prüfen

```bash
# fish
curl -sv --max-time 8 https://telegram-formatter.local/ 2>&1 | tail -5
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

```bash
# bash
curl -sv --max-time 8 https://telegram-formatter.local/ 2>&1 | tail -5
md5sum ./caddy/Caddyfile
docker compose exec proxy md5sum /etc/caddy/Caddyfile
```

`curl` zeigt `tlsv1 alert internal error` — der Browser zeigt dasselbe als
`ERR_SSL_PROTOCOL_ERROR`.

### 4.2 Läuft der Hostname in der laufenden Konfiguration?

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

Ihr Hostname muss in der Ausgabe stehen. Fehlt er, ist die Konfiguration im
Container veraltet.

### 4.3 Wurde das Zertifikat ausgestellt?

```bash
# fish
docker compose exec proxy ls -1 /data/caddy/certificates/local | grep telegram-formatter
```

```bash
# bash
docker compose exec proxy ls -1 /data/caddy/certificates/local | grep telegram-formatter
```

Fehlt der Ordner, hat Caddy den Namen nie gesehen → Mount ist stale.

### 4.4 Beheben

**Dauerhaft** (die eigentliche Lösung): Mount auf das Verzeichnis umstellen.

```yaml
# docker-compose.yml, unter dem Dienst `proxy`
- ./caddy:/etc/caddy:ro     # Verzeichnis, NICHT die einzelne Datei
```

Warum das hilft: Ein Bind-Mount auf eine einzelne Datei friert deren Inode
ein. Wird die Datei auf dem Host atomar ersetzt (temp schreiben + `rename` —
der Standard vieler Editoren), sieht der Container den alten Inhalt weiter. Ein
Verzeichnis-Mount ist immun, weil der Verzeichnis-Inode nicht wechselt.

**Sofort** (ohne Compose-Änderung): Container neu erstellen, damit der Mount
neu aufgebaut wird.

```bash
# fish
docker compose up -d --force-recreate proxy
```

```bash
# bash
docker compose up -d --force-recreate proxy
```

Nach dem Neustart ist ein Zertifikat vorhanden und die Route aktiv. Prüfen:

```bash
# fish
curl -sS -o /dev/null -w "status=%{http_code} tls=%{ssl_verify_result}\n" \
  --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

```bash
# bash
curl -sS -o /dev/null -w "status=%{http_code} tls=%{ssl_verify_result}\n" \
  --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

`tls=0` bedeutet: Zertifikat gegen die Caddy-CA **verifiziert**. `200` bedeutet:
alles in Ordnung.

### 4.5 Wenn `tls=0`, aber `tls_verify_result=1`

Dann stimmt die Kette, aber der Client vertraut der CA nicht → §5.

---

## 5. Browser beklagt sich trotz gutem curl

| Meldung | Ursache | Abhilfe |
|---|---|---|
| `NET::ERR_CERT_AUTHORITY_INVALID` | CA nicht importiert | [CA importieren](#51-ca-auf-dem-client-importieren) |
| `NET::ERR_CERT_NAME_INVALID` | Zertifikat für anderen Namen | Hostname in Caddyfile und `/etc/hosts` des Clients abgleichen |
| `ERR_CERT_DATE_INVALID` | Systemzeit des Clients falsch | `timedatectl` prüfen |
| `ERR_SSL_PROTOCOL_ERROR` | veralteter Caddy-Mount | [§4](#4-err_ssl_protocol_error-tls-handshake-abbruch) |
| `403 Forbidden` | ACL des Clients | [§5.2](#52-403-forbidden) |

`curl -k` überspringt die Prüfung und ist deshalb **kein** Beweis, dass der
Browser zufrieden sein wird. Nutzen Sie `--cacert` statt `-k`:

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

`tls=0` = verifiziert. `tls=1` (oder ein Fehler) = CA fehlt dem Client.

### 5.1 CA auf dem Client importieren

**Export auf dem Server:**

```bash
# fish
cd /home/kris/nas-server
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
md5sum caddy-root-ca.crt
```

```bash
# bash
cd /home/kris/nas-server
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
md5sum caddy-root-ca.crt
```

**Übertragen und importieren (auf dem Client 192.168.0.20):**

```bash
# fish — vom SERVER aus
scp kris@192.168.0.20:/tmp/caddy-root-ca.crt .

# AUF DEM CLIENT (192.168.0.20)
# Linux:
sudo cp caddy-root-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

```bash
# bash — vom SERVER aus
scp caddy-root-ca.crt kris@192.168.0.20:/tmp/

# AUF DEM CLIENT (192.168.0.20)
# Linux:
sudo cp /tmp/caddy-root-ca.crt /usr/local/share/ca-certificates/
sudo update-ca-certificates
```

| Client | Import |
|---|---|
| Linux | `sudo cp … /usr/local/share/ca-certificates/ && sudo update-ca-certificates` |
| Windows | Doppelklick → „Installieren" → „Vertrauenswürdige Stammzertifizierungsstellen" (Chrome/Edge). Firefox: Einstellungen → Zertifikate → Importieren → „Websites vertrauen" |
| macOS | Doppelklick → Schlüsselbund „System" → „Immer vertrauen" |

### 5.2 403 Forbidden

Caddy kennt `remote_ip`-Listen, wenn der geteilte Proxy eine ACL führt. In
diesem Setup ist die ACL optional; 403 kommt dann von einer anderen Stelle.

```bash
# fish
echo "--- eigene IP, wie Caddy sie sieht ---"
ip addr show | grep -oP 'inet \K[\d.]+'
echo "--- Anfrage mit Host-Header ---"
curl -sS -o /dev/null -w "%{http_code}\n" --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

```bash
# bash
echo "--- eigene IP, wie Caddy sie sieht ---"
hostname -I
echo "--- Anfrage mit Host-Header ---"
curl -sS -o /dev/null -w "%{http_code}\n" --cacert caddy-root-ca.crt https://telegram-formatter.local/
```

Bei `403` in den Caddy-Logs nachsehen, welchen Zugriff er ablehnt:

```bash
# fish
docker compose logs --tail 100 proxy | grep -i telegram-formatter
```

```bash
# bash
docker compose logs --tail 100 proxy | grep -i telegram-formatter
```

---

## 6. 502 Bad Gateway

TLS funktioniert, aber der Upstream nimmt nicht ab. In dieser Reihenfolge
prüfen:

```bash
# fish
echo "--- 1. App läuft? ---"
docker compose ps telegram-formatter

echo "--- 2. App im Netz erreichbar? ---"
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- 3. Was sagt Caddy? ---"
docker compose logs --tail 30 proxy
```

```bash
# bash
echo "--- 1. App läuft? ---"
docker compose ps telegram-formatter

echo "--- 2. App im Netz erreichbar? ---"
docker compose exec proxy curl -s -o /dev/null -w "%{http_code}\n" http://telegram-formatter:5000/healthz

echo "--- 3. Was sagt Caddy? ---"
docker compose logs --tail 30 proxy
```

| `ps` sagt | `/healthz` sagt | Ursache |
|---|---|---|
| läuft | `200` | 502 kam vor dem App-Start; warten oder Caddy neu starten |
| läuft | `000` | falscher Dienstname oder kein gemeinsames Netz → [§3](#3-die-app-ist-nicht-erreichbar-nicht-nur-der-proxy) |
| tot | – | [§2](#2-container-startet-nicht) |
| läuft, `000` | Upstream verweigert Verbindung | Port im Caddyfile ≠ Port im Container (`EXPOSE`/Gunicorn-Bind) |

Nach einer App-Änderung genügt meist:

```bash
# fish
docker compose restart proxy
```

```bash
# bash
docker compose restart proxy
```

---

## 7. Mount-Fehler beim Start

```
OCI runtime create failed: runc create failed: … open browse.html: read-only file system
```

Ein Mount zeigt **in ein Verzeichnis, das selbst `:ro` eingehängt ist**. Der
Container kann dort keinen Mountpoint anlegen.

Im aktuellen Setup war das ein *redundanter* Mount: `browse.html` wurde einzeln
nach `/etc/caddy/` gehängt, obwohl `/srv/ftp` bereits als `/srv/www/ftp`
eingehängt ist und die Datei dort bereits vorliegt.

```yaml
# FALSCH — Einzeldatei in ein ro-Verzeichnis:
- ./caddy:/etc/caddy:ro
- /srv/ftp/.../browse.html:/etc/caddy/browse.html:ro

# RICHTIG — nur den übergeordneten Mount, das Template wird mitgelesen:
- ./caddy:/etc/caddy:ro
- /srv/ftp:/srv/www/ftp:ro
```

Gleiches gilt für `caddy_data` / `caddy_config`: **keine** Einzeldatei-Mounts
hinein, das sind reine Verzeichnisse für Caddy selbst.

---

## 8. Notfall

### Alles läuft nicht mehr, Dienst muss sofort zurück

```bash
# fish
cd /home/kris/nas-server
docker compose up -d proxy telegram-formatter
sleep 8
docker compose ps
curl -sS -o /dev/null -w "%{http_code}\n" --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

```bash
# bash
cd /home/kris/nas-server
docker compose up -d proxy telegram-formatter
sleep 8
docker compose ps
curl -sS -o /dev/null -w "%{http_code}\n" --cacert caddy-root-ca.crt https://telegram-formatter.local/healthz
```

### Caddyfile geändert und es ist kaputt

```bash
# fish
cd /home/kris/nas-server
cp caddy/Caddyfile /tmp/Caddyfile.kaputt
docker compose exec proxy caddy validate --config /etc/caddy/Caddyfile
git diff caddy/Caddyfile
git checkout caddy/Caddyfile
docker compose up -d --force-recreate proxy
```

```bash
# bash
cd /home/kris/nas-server
cp caddy/Caddyfile /tmp/Caddyfile.kaputt
docker compose exec proxy caddy validate --config /etc/caddy/Caddyfile
git diff caddy/Caddyfile
git checkout caddy/Caddyfile
docker compose up -d --force-recreate proxy
```

`caddy validate` prüft die Syntax **ohne** die laufende Konfiguration zu
ändern. Vor jedem Speichern benutzen:

```bash
# fish
docker run --rm -v "$PWD/caddy:/etc/caddy:ro" caddy:latest caddy fmt --diff /etc/caddy/Caddyfile
```

```bash
# bash
docker run --rm -v "$PWD/caddy:/etc/caddy:ro" caddy:latest caddy fmt --diff /etc/caddy/Caddyfile
```

### Zertifikate versehentlich gelöscht

`docker compose down -v` löscht die CA. Danach jedes Mal eine neue
Vertrauens-Warnung auf allen Clients — **kein** Sicherheitsproblem, aber
ärgerlich.

```bash
# fish
cd /home/kris/nas-server
docker compose up -d proxy
sleep 5
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
```

```bash
# bash
cd /home/kris/nas-server
docker compose up -d proxy
sleep 5
docker compose cp proxy:/data/caddy/pki/authorities/local/root.crt ./caddy-root-ca.crt
```

Der Import auf dem Client muss danach **nicht** wiederholt werden, solange
`caddy_data` nicht gelöscht wurde — nur wenn die Datei auf dem Client noch die
**alte** Wurzel ist (erkennbar an der abweichenden md5).

### Vor dem Ändern: Zustand festhalten

```bash
# fish
cd /home/kris/nas-server
git status
docker compose ps
docker compose config > /tmp/compose-stand-$(date +%F-%H%M).yml
```

```bash
# bash
cd /home/kris/nas-server
git status
docker compose ps
docker compose config > /tmp/compose-stand-$(date +%F-%H%M).yml
```

---

## 9. fish vs bash — die Unterschiede, die hier auffallen

Damit Sie die Befehle nicht nachschlagen müssen:

| Zweck | bash | fish |
|---|---|---|
| Variable setzen | `FOO=bar cmd` | `FOO=bar cmd` geht auch; dauerhaft `set -x FOO bar` |
| Skript einbinden | `. ./script.sh` | `source ./script.sh` |
| Befehl nur bei Erfolg | `a && b` | `a; and b` |
| Befehl nur bei Fehler | `a \|\| b` | `a; or b` |
| Negation | `! cmd` | `not cmd` |
| Wildcards in Schleifen | `for f in *.py` | `for f in *.py` (gleich) |
| Datei in-place bearbeiten | `sed -i 's/x/y/' f` | `sed -i '' 's/x/y/' f` (**Achtung: leeres Argument nötig!**) |
| Text suchen | `grep -E 'a\|b'` | `grep -E 'a\|b'` (gleich, aber `string match` ist schneller) |
| Rückgabe-Code prüfen | `[ $? -eq 0 ]` | `test $status -eq 0` oder direkt `or`/`and` |
| Historie | `!!` | `(echo $history[1])` |

Die häufigste Fehlerquelle: `sed -i` **ohne** das leere Argument. Unter fish
liest der Interpreter das Sript selbst als Dateinamen und das Original bleibt
unverändert.

```bash
# bash — so
sed -i 's/alt/neu/' datei.txt

# fish — so (Leerargument zwingend)
sed -i '' 's/alt/neu/' datei.txt
```

Für Ersetzungen in vielen Dateien ist `string replace` in fish der bessere Weg
und liest nicht im Halbsatz:

```bash
# fish — in-place über temporäre Datei, ohne sed-Falle
string replace -r 'host\.docker\.internal:5000' 'telegram-formatter:5000' caddy/Caddyfile > /tmp/c && mv /tmp/c caddy/Caddyfile
```

---

## Weiterführend

| Thema | Datei |
|---|---|
| Endpoints, Header, Fehlercodes | [API.md](API.md) |
| Docker-Grundbetrieb, CA-Import, ACL | [DOCKER.md](DOCKER.md) |
| Kurzantworten auf Einzelfragen | [FAQ.md](FAQ.md) |
| Architektur und Grenzen | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Was sich in welcher Version geändert hat | [../CHANGELOG.md](../CHANGELOG.md) |
| Security-Befunde und deren Status | [../peer-review/2026-09_CODE-REVIEW-V2.13.0.md](../peer-review/2026-09_CODE-REVIEW-V2.13.0.md) |
