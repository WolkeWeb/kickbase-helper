# HANDOFF.md

Aktiver Agent: Codex (anbieterunabhängige Einrichtung; allein)

## Ziel und Stand

Single-User-Dashboard für die eigene Kickbase-Liga, nur lesender Kickbase-Zugriff.
Phasen 1–7b und die Erweiterungen bis C20 sind umgesetzt; Details in `docs/roadmap.md`.

## Aktueller Stand

- Anbieterneutraler Betrieb: Kern plus vier Zugriffsvarianten; Docker-Setup-Assistent
  ohne Secret-Ausgabe, konfigurierbare Pfade/UID/GID und generische Offline-Hinweise.
  1369 Tests grün (1 Skip), neue Installation inkl. Anmeldung sowie alle Compose-Modi
  geprüft. Bestehende IONOS-Konfiguration lokal übernommen, Ports/Volumes/Mounts exakt
  verglichen; eigenständiger PR, danach CI und Deployment.

- Veröffentlichung 14.10.2026: neutrale Beispielkonfiguration, Caddy über `PUBLIC_URL`,
  keine Live-Screenshots und keine vollständigen gescrapten Webseiten im Git-Stand.
  Drittanbieter-Lizenzen und `SECURITY.md` ergänzt; Artefaktpfade werden in CI gesperrt.
- Audit und Begründung des neuen Git-Verlaufs: `docs/publication.md`.
  Die alte Entwicklung samt PRs, Logs und Quoten-Branch bleibt im privaten Archiv.
- Lokal: 1360 Tests erfolgreich, 1 Skip; SQLite und isoliertes PostgreSQL.
  Ruff, actionlint, zizmor, Secret-Scan ohne Ausnahmen und Caddy-Hostprüfung grün.
  CI und Proxy-Image der neuen Historie erfolgreich; öffentlich unter der bisherigen URL.
  Das Altarchiv ist privat/archiviert. `main`: Pflicht-CI, PRs und kein Force-Push;
  GitHub Secret-Scanning, Push-Schutz und private Sicherheitsmeldungen aktiviert.

## Plan

- [x] L1 Anbieterneutraler Compose-Kern; lokale Einrichtung, Standard-HTTPS und externer
  Proxy sowie IONOS als Option. Sichere Setup-Hilfe, Tests/CI, PR und Release.
  Bestehende Installation behält IONOS/VPN und vorhandene Secret-Dateien.

- [x] Veröffentlichungsstand und saubere neue Historie vorbereiten.
- Weitere Produktfeatures nur auf neuen Auftrag umsetzen.

## Offene fachliche Punkte

- C5/Phase 8: Marktwertprognose erst nach mindestens vier Wochen Backtest.
- C6/Phase 9: Quotenquelle noch ungeklärt; alter Arbeitsbranch nur im privaten Archiv.
- C12: frühere Teilpartien erst nach Vollständigkeitsprüfung gezielt reparieren.
- C13: Aufstellungsindex ist eine relative Kennzahl, keine vorhergesagte Punktzahl.
- Weitere Ideen und Abnahmekriterien: `docs/roadmap.md`.

## Weiterarbeiten

Neue Branches ausschließlich von diesem öffentlichen `main` abzweigen. Alte lokale
Branches nicht mergen oder pushen: sie würden entfernte private Historie zurückbringen.
Alte PR-Nummern in den Entwicklungsdokumenten beziehen sich auf das private Archiv.
Deployte Migrationen nicht ändern; Betriebsdaten und Secrets bleiben lokal.
