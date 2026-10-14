# Sicherheit

Das Dashboard ist für einen einzelnen Account im eigenen Netz/VPN vorgesehen.
Eine öffentliche Code-Veröffentlichung macht die laufende Installation nicht öffentlich.
Die Anwendung hat einen eigenen Login; zusätzlich den Netzwerkzugriff einschränken.

Sicherheitslücken bitte über die aktivierte
[private GitHub-Meldung](https://github.com/WolkeWeb/kickbase-helper/security/advisories/new)
(`Security → Advisories → Report a vulnerability`) melden. Ist sie nicht verfügbar, keine Details in einem
öffentlichen Issue veröffentlichen; zunächst einen privaten Kontaktweg beim Maintainer anfragen.
Keine Zugangsdaten, Cookies, Datenbank-Dumps oder Daten anderer Liga-Manager anhängen.

## Beiträge und Testdaten

- `.env`, `secrets/`, Backups und Live-Screenshots bleiben lokal. Auch Bilder, PR-Texte,
  Commit-Nachrichten und Actions-Logs können private Daten enthalten.
- API-Fixtures mit `scripts/probe.py` anonymisieren; anschließend auch unbekannte Felder
  prüfen. Tests verwenden keine echten Accounts oder Live-API-Aufrufe.
- Screenshots für Beiträge ausschließlich mit synthetischen Daten aufnehmen.
- Vor einem Push `uv run python scripts/check_public_tree.py` und den Secret-Scan aus der
  CI ausführen. Der Dateicheck prüft den Git-Index, nicht ignorierte lokale Dateien.
- Ein bereits eingechecktes echtes Secret zuerst beim Anbieter widerrufen/rotieren.
  Löschen in einem neuen Commit entfernt weder alte Commits noch PR-Referenzen.

## Betrieb

Eigene zufällige Werte für `SESSION_SECRET` und `POSTGRES_PASSWORD` verwenden;
den App-Passwort-Hash mit `scripts/hash_password.py` erzeugen. `.env.example` ist nicht
betriebsfertig. Kickbase-Zugangsdaten bleiben in den vorgesehenen Docker-Secrets.
PostgreSQL nicht am Host veröffentlichen. Optionale Telegram-/KI-Funktionen benötigen
eigene Zugangsdaten und sollten nur bei Bedarf aktiviert werden.

Automatische Updates werden als Dependabot-PRs angeboten; Änderungen vor dem Einsatz
testen. Der aktuelle Stand von `main` ist die gepflegte Version.
