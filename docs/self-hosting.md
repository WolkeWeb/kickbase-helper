# Selbst aufsetzen

Benötigt werden Docker mit Compose und ein Kickbase-Account. Die App verwaltet einen
einzelnen Account und liest Kickbase-Daten; sie führt keine Käufe oder Verkäufe aus.
IONOS, WireGuard, ein KI-Anbieter und Telegram sind optional.

## Erster Start ohne Domain oder VPN

In Bash unter Linux/macOS:

```bash
git clone https://github.com/WolkeWeb/kickbase-helper.git
cd kickbase-helper
docker build -f docker/app.Dockerfile -t kickbase-helper-app:latest .
docker run --rm -it --network none --user "$(id -u):$(id -g)" \
  -v "$PWD:/setup" -w /setup \
  kickbase-helper-app:latest python /srv/setup.py
docker compose up -d
```

Im Assistenten den **exakten Liganamen**, Kickbase-E-Mail/-Passwort und ein eigenes
Dashboard-Passwort eingeben. Die Zugangsdaten werden verdeckt abgefragt. Das Script
erzeugt `.env`, einen Passwort-Hash, getrennte zufällige Session-/DB-Schlüssel und lokale
Secret-Dateien. Unter Linux/macOS haben Dateien Modus 0600, das Secret-Verzeichnis 0700. Vorhandene `.env`
oder `secrets/` werden nicht überschrieben. Es erfolgt dabei kein Kickbase-Login.

Danach [http://localhost:8080](http://localhost:8080) öffnen und mit dem **Dashboard-Passwort**
anmelden. Der Worker lädt die Daten im Hintergrund; die erste Synchronisierung kann dauern.
Der lokale Port ist ausschließlich an `127.0.0.1` gebunden. Auf einem entfernten Server
kannst du zunächst per SSH-Tunnel testen:

```bash
ssh -L 8080:127.0.0.1:8080 USER@SERVER
```

Alternativ mit Python 3.12 und `uv` auf dem Host:

```bash
uv run python scripts/setup.py
docker compose up -d --build
```

Diese Variante funktioniert auch unter Windows; der Assistent verwendet den passenden
`COMPOSE_FILE`-Separator. Docker muss Linux-Container ausführen können. Für die Nutzung
am Handy oder eine PWA außerhalb von localhost eine der folgenden HTTPS-Varianten wählen.

## Zugriffsvariante wählen

Der gemeinsame Kern `compose.yaml` enthält nur App, Worker und Datenbank und veröffentlicht
keinen App-/DB-Port. `COMPOSE_FILE` in `.env` ergänzt **genau eine** Zugriffsvariante:

| Variante | `COMPOSE_FILE` (Linux/macOS) | `PUBLIC_URL` | Voraussetzung |
|---|---|---|---|
| Lokal | `compose.yaml:compose.local.yaml` | `http://localhost:8080` | keine Domain |
| Standard-HTTPS | `compose.yaml:compose.https.yaml` | `https://DEINE-DOMAIN` | Domain und erreichbare ACME-Ports; beliebiger DNS-Anbieter |
| Eigener TLS-Proxy | `compose.yaml:compose.external.yaml` | `https://DEINE-DOMAIN` | bestehender Reverse Proxy mit TLS |
| IONOS DNS-01 | `compose.yaml:compose.ionos.yaml` | `https://DEINE-DOMAIN` | IONOS-DNS-API-Token; funktioniert auch bei ausschließlich intern erreichbarer App |

Auf Windows `;` statt `:` verwenden. Alternativ unabhängig vom Separator explizit starten:

```bash
docker compose -f compose.yaml -f compose.https.yaml up -d --build
```

Explizite `-f`-Angaben gehen `COMPOSE_FILE` vor. Beim Wechsel die alte Variante nicht
zusätzlich laden: sonst werden beispielsweise Portlisten zusammengeführt. Falls zuvor
ein Caddy lief, vor dem Wechsel auf lokal/eigenen Proxy `docker compose stop proxy`
mit der bisherigen Variante ausführen. Datenbank- und Zertifikats-Volumes behalten.

### Standard-HTTPS: beliebiger Domain-/DNS-Anbieter

`PUBLIC_URL` auf die eigene HTTPS-Adresse ohne Pfad setzen und `COMPOSE_FILE` auf
`compose.yaml:compose.https.yaml` ändern. A/AAAA-Einträge müssen zum Server zeigen;
Port 80/TCP und 443/TCP müssen für die Zertifikatsprüfung erreichbar sein. 443/UDP ist
für HTTP/3 vorgesehen. Ein eventuell vorhandener AAAA-Eintrag muss ebenfalls funktionieren.
Dann `docker compose up -d --build` ausführen.

Diese Variante nutzt das offizielle Caddy-Image und automatische HTTPS-Zertifikate.
Sie braucht weder ein DNS-Plugin noch ein DNS-API-Token und ist unabhängig von IONOS.
Hat der Server diese Ports bereits belegt, die nächste Variante verwenden.

### Bestehender Reverse Proxy

`COMPOSE_FILE=compose.yaml:compose.external.yaml` und `PUBLIC_URL=https://DEINE-DOMAIN`
setzen. Die App ist am Host unter `http://127.0.0.1:8080` erreichbar; den vorhandenen
TLS-Proxy dorthin weiterleiten lassen und Host-/Forwarded-Header setzen. Der App-Port
bleibt auf Loopback, Session-Cookies bleiben `Secure`. Im Browser die HTTPS-Adresse
des Proxys öffnen, nicht den direkten HTTP-Port.

Bei einem Proxy in einem anderen Docker-Netz eine eigene lokale Compose-Ergänzung
für das gemeinsame Netz verwenden und an `app:8000` weiterleiten. Dabei keine
HTTP-App-Ports ins LAN veröffentlichen. Zusätzliche lokale Overrides gitignorieren.

### Optional: IONOS und privates Netz

`COMPOSE_FILE=compose.yaml:compose.ionos.yaml` und die eigene HTTPS-Adresse setzen.
Das Token als `./secrets/ionos_token` mit Modus 0600 speichern oder `IONOS_TOKEN_FILE`
auf eine vorhandene Datei setzen. Das Token bleibt ein Docker-Secret. Nur dieser
Modus baut das Caddy-Image mit IONOS-Plugin und benötigt die Token-Datei.

DNS-01 benötigt keine von außen erreichbare App für die Zertifikatsprüfung. Der Name
kann intern aufgelöst und die App im LAN, über WireGuard, Tailscale oder ein anderes
VPN erreicht werden. DNS-Umschreibungen sind keine Zugriffssperre; Netzwerkzugriff
über Firewall/VPN festlegen. In der Anwendung ist kein bestimmtes VPN vorausgesetzt.

Die Variablen `PROXY_BIND_ADDRESS`, `PROXY_HTTP_PORT` und `PROXY_HTTPS_PORT` passen
die Proxy-Bindings an. Andere Host-Ports ersetzen nicht die für ACME notwendige
Erreichbarkeit von Standard-Port 80/443 in der Standard-HTTPS-Variante.

## Secrets und optionale Funktionen

| Datei | Verwendung |
|---|---|
| `./secrets/kickbase_email` | Kickbase-E-Mail |
| `./secrets/kickbase_password` | Kickbase-Passwort |
| `./secrets/telegram_bot_token` | optional: Telegram; zusätzlich `TELEGRAM_CHAT_ID` in `.env` |
| `./secrets/llm_api_key` | optional: OpenAI-kompatibler KI-Anbieter; URL/Modell in `.env` |
| `./secrets/claude_bridge_token` | optional: Claude-Bridge; Einrichtung in `deployment.md` |
| `./secrets/ionos_token` | nur bei gewähltem IONOS-Modus |

Der Assistent legt die drei optionalen Basis-Dateien leer an. Leere Dateien schalten
diese Funktionen ab. Die IONOS-Datei wird für den normalen Start nicht angelegt und
nicht benötigt. Nach einer Änderung `docker compose up -d` ausführen.

`KICKBASE_EMAIL_FILE` und `KICKBASE_PASSWORD_FILE` erlauben andere Secret-Pfade,
auch außerhalb des Repos. `APP_UID`/`APP_GID` müssen die Dateien im Container lesen
können; der Assistent übernimmt den ausführenden Host-User. Bestehende Installationen
ohne diese Variablen verwenden weiterhin 1000:1000.

## Bestehende IONOS-Installation übernehmen

Keine Neuinstallation ausführen und keine Secret-Datei verschieben. Zur bisherigen
`.env` folgende Einstellungen ergänzen, falls die Credentials dort bisher lagen:

```dotenv
COMPOSE_FILE=compose.yaml:compose.ionos.yaml
KICKBASE_EMAIL_FILE=/opt/kickbase-helper/secrets/kickbase_email
KICKBASE_PASSWORD_FILE=/opt/kickbase-helper/secrets/kickbase_password
IONOS_TOKEN_FILE=/opt/kickbase-helper/secrets/ionos_token
```

`PUBLIC_URL`, Login-/Session-/DB-Werte und optionale Tokens behalten. Stack-Name sowie
`pgdata`, `caddy_data` und `caddy_config` bleiben gleich; bestehende Daten und
Zertifikate werden weiterverwendet. WireGuard, DNS-Umschreibungen und Firewall bleiben
Teil der lokalen Infrastruktur. Nach erfolgreicher Konfigurationsprüfung ohne Ausgabe
`docker compose config --quiet` gezielt deployen; siehe [Deployment](deployment.md).

## Prüfen und aktualisieren

```bash
docker compose ps
docker compose logs --tail=50 app worker
git pull --ff-only
docker compose up -d --build app worker
```

`/health` prüft App und Datenbank. Bei HTTPS-Modi die eigene HTTPS-Adresse verwenden;
beim lokalen Start `curl -fsS http://localhost:8080/health`.
Den IONOS-Proxy nur bei Änderungen an dessen Image/Konfiguration neu bauen. Das
Standard-Caddy-Image wird ohne Go-Compiler verwendet. Keine Volumes beim Aktualisieren
löschen. Tokens und persönliche Liga-Daten nicht in öffentliche Fehlerberichte kopieren.

Referenzen: [Compose-Dateien kombinieren](https://docs.docker.com/compose/how-tos/multiple-compose-files/merge/),
[Compose-Variablen](https://docs.docker.com/compose/how-tos/environment-variables/envvars/),
[Caddy Automatic HTTPS](https://caddyserver.com/docs/automatic-https).
