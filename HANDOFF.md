# HANDOFF.md

Aktiver Agent: keiner (Systemprüfung nach den letzten Patches abgeschlossen)

## Ziel und Stand

Single-User-Dashboard für die eigene Kickbase-Liga, nur lesender Kickbase-Zugriff.
Phasen 1–7b und die Erweiterungen bis C20 sind umgesetzt; Details in `docs/roadmap.md`.

## Aktueller Stand

- README überarbeitet: „Auf einen Blick“ (Voraussetzungen, Abgrenzung), Kurzfunktionen,
  Schnellstart früh; Details in `docs/features.md` und `docs/development.md`. Screenshots mit
  prozentualer Breite, damit alle gleich groß rendern.
- README-Screenshots im iPhone-Rahmen aus synthetischen Daten: `scripts/demo_server.py` +
  `demo_data.py` (Test-Fixtures, erfundene Marktwerte), `scripts/readme_screenshots.py`;
  Bilder nur unter `docs/images/demo/` (`check_public_tree.py` erlaubt nur diesen Ordner).
  Merge-Ziel ist das öffentliche Repo `WolkeWeb/kickbase-helper` (origin geprüft).
- Systemprüfung: lokales `COMPOSE_FILE` um `compose.claude.yaml` ergänzt; Bridge/MCP
  wieder beidseitig erreichbar, HTTPS und letzte Worker-Jobs ok, DB auf 0015.
  Setup weist mehrdeutige Backslashes vor Dateierzeugung ab; Update-Doku ergänzt.
  1372 Tests grün (1 Skip), SQLite + isoliertes PostgreSQL, Ruff und Image-Erststart ok.
- Öffentlicher Betrieb: README-Hinweis (inoffiziell, Sperrrisiko, ohne Gewähr, Drittanbieter-
  Bedingungen); Worker-Port 8768 + `host.docker.internal` nur noch über `compose.claude.yaml`
  (experimentell, in `check_deployment.py` geprüft). Maintainer-Dateien unter `docs/maintainer/`
  und `deploy/maintainer/`, Übersicht in `docs/maintainer/README.md`.
- Community-Dateien: `LICENSE` (MIT), `CODE_OF_CONDUCT.md`, `CONTRIBUTING.md` (Deutsch mit
  englischer Kurzfassung), Issue-Formulare unter `.github/ISSUE_TEMPLATE/` (Blanko-Issues aus,
  Sicherheitsmeldungen verweisen auf private Advisories). README/publication.md angepasst.
- Anbieterneutraler Betrieb: vier Zugriffsvarianten, Docker-Setup ohne Secret-Ausgabe,
  konfigurierbare Pfade/UID/GID und generische Offline-Hinweise. 1369 Tests grün (1 Skip),
  Erststart inkl. Login und alle Compose-Modi geprüft; PR #5 nach grüner CI gemergt/deployt.
  Bestehendes IONOS/VPN mit identischen Secret-Mounts/Volumes/Ports; App-/DB-Health ok.

- Veröffentlichung 14.10.2026: neutrale Beispielkonfiguration, Caddy über `PUBLIC_URL`,
  keine Live-Screenshots und keine vollständigen gescrapten Webseiten im Git-Stand.
  Drittanbieter-Lizenzen und `SECURITY.md` ergänzt; Artefaktpfade werden in CI gesperrt.
  Audit und Begründung des neuen Git-Verlaufs: `docs/publication.md`.
  Die alte Entwicklung samt PRs, Logs und Quoten-Branch bleibt im privaten Archiv.

## Plan

- [x] Letzte Patches und VM-Betrieb prüfen: bestehende Claude-Bridge wieder anbinden,
  Setup-Sonderzeichen absichern und Update-Anweisungen korrigieren.

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
