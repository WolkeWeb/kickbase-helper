# Deployment

> Betriebsnotizen des Maintainers (Updates, Remote Control, Claude-Bridge). Für eine eigene
> Installation reicht [Self-Hosting](../self-hosting.md); Pfade und User hier sind Beispiele.

Erste Einrichtung und Wahl des Zugriffs: [Self-Hosting](../self-hosting.md).
`COMPOSE_FILE` in `.env` legt lokal, Standard-HTTPS, eigenen Proxy oder IONOS fest.

Ausgelagert aus `AGENTS.md`, damit die Details nur geladen werden, wenn wirklich deployt wird.
Die immer gültigen Regeln (Portainer, Secrets in `docker compose config`, Migrationen) stehen
weiter in `AGENTS.md`.

Nach jedem Merge (einmal pro Block) auf der VM:

```bash
git checkout main && git pull
# optionale Secret-Dateien müssen existieren (leer = Funktion aus)
for f in telegram_bot_token llm_api_key claude_bridge_token; do
  test -e secrets/$f || install -m 600 /dev/null secrets/$f
done
docker compose up -d --build app worker
docker compose ps
# /health an der eigenen PUBLIC_URL prüfen (lokal: http://localhost:8080/health)
```

Bei reinen App-/Worker-Änderungen gezielt diese beiden Dienste bauen. Den optionalen
IONOS-Proxy separat nur bei Änderungen an seinem Dockerfile/Caddyfile.ionos bauen;
ein ungebundener neuer Caddy-Builder
kann mehrere GB für Go-Abhängigkeiten und Compiler benötigen. Am 14.10.2026 musste der
Build auf dem 19-GB-Root-Dateisystem abbrechen. Reproduzierbaren Build-Cache bereinigen
(`docker builder prune --force`) ist eine mögliche Entlastung; keine Volumes löschen.

Migration 0015 ergänzt Artikel-/Auswertungsrevisionen, Textbasis und eine dauerhafte
Benachrichtigungsmarkierung; bisher behandelte Meldungen werden aus `notified_at` übernommen.
Artikel, Meldungen und ursprüngliche Veröffentlichungsdaten bleiben erhalten. Beim Rückwechsel
auf ein Image vor 0015 die zusätzlichen Spalten behalten und den alten App-Migrationsschritt
wie unten umgehen; keine Produktions-Downgrades zum Löschen von Revisionsdaten.

Migration 0014 ergänzt nullable `league_transfers.sold_by_me`; Altbestand bleibt unbekannt,
bis Feed/Cache den Verkäufer belegt. 0013 ergänzt die lokale Tabelle `holding_roles`; Daten bleiben
unverändert. Beim Rückwechsel auf ein Image vor 0013 die Tabelle samt Nutzerpräferenzen
behalten: dessen alter Alembic-Code kennt die neue Revision nicht. Dasselbe gilt vor 0014: neue
Spalte behalten und den alten App-Migrationsschritt umgehen. App bei einem solchen
Rückwechsel direkt starten (EntryPoint nimmt beliebige Befehle entgegen), ohne den alten
`web`-Migrationsschritt auszuführen:

```bash
uvicorn --factory app.main:build_app --host 0.0.0.0 --port 8000 \
  --proxy-headers --forwarded-allow-ips "*" --no-server-header --log-level warning
```

Diesen Befehl als App-Command im lokalen Compose-Override für das vorherige Image setzen.
Der zurückgesetzte Worker verwendet seine bisherigen Tabellen; die neue Tabelle bleibt
bestehen. Keine Produktions-Downgrades zum Löschen von Präferenzen.

## Lokale Betriebsvariante: IONOS + VPN (optional)

Die ursprüngliche Installation nutzt `compose.ionos.yaml`, eigene Secret-Pfade unter
`/opt/kickbase-helper/secrets/`, interne DNS-Auflösung und WireGuard. Diese Infrastruktur
ist keine Voraussetzung für andere Installationen. Übernahme ohne Änderung der Secrets
oder Volumes: [Migrationsanleitung](../self-hosting.md#bestehende-ionos-installation-übernehmen).

- IONOS-Proxy mit DNS-01 veröffentlicht wie bisher ausschließlich 443/TCP und 443/UDP.
- DNS/Firewall/VPN legen fest, von wo die Installation erreichbar ist.
- App-Login und Secure-Session-Cookies bleiben für alle TLS-Varianten aktiv.

```bash
docker compose config --quiet
docker compose up -d --build app worker
# Nur bei Änderung des optionalen IONOS-Proxys:
docker compose up -d --no-deps --build proxy
```

## Aufträge vom Handy (Claude Code Remote Control)

Optional kann `claude remote-control` als systemd-Dienst laufen
(`deploy/maintainer/claude-remote.service`). In der Claude-App bzw. auf claude.ai/code erscheint die Umgebung
„Kickbase-Helper“; dort eine Session öffnen und z. B. `/auftrag C2 Angebote auf eigene Spieler`
schreiben. Rückfragen und Freigaben kommen im Chat.

Voraussetzungen: Claude Pro/Max-Abo, in Claude Code per `/login` mit dem claude.ai-Konto
angemeldet (API-Keys gehen nicht), `opencode` mit OpenCode-Go-Login.

Die mitgelieferten Units sind Beispiele: User, Arbeitsverzeichnis und CLI-Pfade an die
eigene Installation anpassen. Für das Dashboard werden diese Dienste nicht benötigt.

Einmalig einrichten (im Beispiel als User `claude`):

```bash
cd ~/kickbase-helper
which claude opencode uv gh                  # alle vier müssen gefunden werden
claude remote-control                         # einmal interaktiv: „Enable Remote Control?“ und
                                              # „Trust …?“ mit y bestätigen, dann Strg+C
sudo cp deploy/maintainer/claude-remote.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now claude-remote
systemctl status claude-remote --no-pager    # läuft? Logs: journalctl -u claude-remote -f
```

- Berechtigungen: Sessions starten im Modus `auto` – Claude entscheidet Routine-Befehle selbst und
  fragt nur bei riskanten Aktionen nach. Die `deny`-Regeln in `.claude/settings.json` (gesperrte
  Secrets, Force-Push, Push auf `main`, `down -v`) gelten trotzdem immer.
- Nach Änderungen an der Unit: `sudo systemctl daemon-reload && sudo systemctl restart claude-remote`.
- Empfohlen: unter claude.ai → Einstellungen → Konto „Require trusted devices“ einschalten.
- Kosten: Planen/Prüfen läuft über das Claude-Abo, Programmieren über OpenCode Go. `/auftrag`
  liest deshalb keinen Code selbst. Pro Auftrag eine neue Session.

## Claude im Telegram-Chat

`@claude <Frage>` (oder `/claude`) im Bot-Chat öffnet ein Fenster von 10 Minuten: alle
Nachrichten darin beantwortet Claude statt Mistral, jede Antwort verlängert das Fenster.
`/claude_aus` beendet es, `/claude_model [sonnet|opus|haiku|claude-…]` zeigt bzw. wechselt das
Modell. Fenster, Tageslimit (40 Fragen) und Budget pro Frage: `config/ai.yaml`, Abschnitt `claude`.

Ablauf: Worker → `deploy/claude-bridge/bridge.py` auf dem Host (`172.17.0.1:8767`, systemd) →
`claude -p` mit dem Abo-Login des Users `claude`. Claude hat nur WebSearch/WebFetch und die
lesenden Kickbase-Werkzeuge des Workers (MCP auf `127.0.0.1:8768`, gleicher Token) – keine
Shell, keine Dateien, keine Projekt-Einstellungen. Ist die Bridge weg, antwortet Mistral.

> **Experimentell.** Die Bridge ruft die Claude Code CLI mit dem Abo-Login des Hosts auf. Ob
> diese automatisierte Nutzung durch einen Bot mit dem eigenen Claude-Tarif vereinbar ist, muss
> jeder Betreiber selbst anhand der Anthropic-Bedingungen prüfen. Für das Dashboard wird die
> Bridge nicht benötigt; ohne sie antwortet Mistral bzw. läuft alles ohne KI.

Einmalig einrichten (als User `claude`, Claude Code ist per `/login` angemeldet):

```bash
cd ~/kickbase-helper && git checkout main && git pull
# compose.claude.yaml an COMPOSE_FILE in .env anhängen (Worker-Port 8768 + Host-Zugriff), z. B.
#   COMPOSE_FILE=compose.yaml:compose.local.yaml:compose.claude.yaml
openssl rand -hex 32 > secrets/claude_bridge_token && chmod 600 secrets/claude_bridge_token
sudo install -D -m 755 deploy/claude-bridge/bridge.py /usr/local/lib/kickbase-claude-bridge/bridge.py
sudo cp deploy/claude-bridge/kickbase-claude-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now kickbase-claude-bridge
docker compose up -d --build worker
docker compose port worker 8768                         # 127.0.0.1:8768
curl -fsS http://172.17.0.1:8767/health                 # {"ok": true, "available": true}
docker compose logs worker | grep -i claude              # "Claude im Chat an"
```

- Pfad zu `claude` anders? `which claude` und `CLAUDE_BIN` in der Unit anpassen.
- Firewall (ufw): der Worker muss `172.17.0.1:8767` erreichen, z. B.
  `sudo ufw allow in from 172.16.0.0/12 to 172.17.0.1 port 8767 proto tcp`.
- Der Dienst läuft aus einer installierten Kopie (`/usr/local/lib/kickbase-claude-bridge/`),
  nicht aus dem Arbeitsverzeichnis – dort wechseln Agenten die Branches.
- Logs: `journalctl -u kickbase-claude-bridge -f`. Nach Änderungen an `bridge.py` (auf `main`)
  den `install`- und `cp`-Befehl von oben wiederholen, dann
  `sudo systemctl daemon-reload && sudo systemctl restart kickbase-claude-bridge`.
- Zeitlimit pro Frage: `timeout_seconds` in `config/ai.yaml` (die Bridge deckelt bei 600 s).
- Abschalten: `secrets/claude_bridge_token` leeren, Worker und Dienst neu starten (die Bridge
  beendet sich bei leerem Token ohne Fehler), oder den Dienst stoppen.
