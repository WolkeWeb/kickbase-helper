# HANDOFF.md

Aktiver Agent: Claude Code (Briefing-Fakten)

## Ziel und Stand

Single-User-Dashboard für die eigene Kickbase-Liga, nur lesender Kickbase-Zugriff.
Phasen 1–7b und die Erweiterungen bis C20 sind umgesetzt; Details in `docs/roadmap.md`.

## Aktueller Stand

- Morgen-Briefing neu (`app/services/briefing.py`): statt Kader-JSON fertige Fakten mit id
  aus `today_view` (neu: `buys`, optional `basis`), Kaufliste, Verkauf, Meldungen, Teamwert.
  KI-Zeilen nennen ihre Fakten; Zahlen/Spieler/Handlungen werden je Zeile geprüft, sonst
  Fakten-Satz statt Zeile; ausgelassene wichtige Fakten werden vorangestellt. Nach Review:
  Werte/Handlungen je Spieler (Satzteil), Tag+Uhrzeit exakt, Umlaut-/Synonym-Handlungen,
  Genitiv-Namen, Gedankenstrich als Minus, Nachricht in ganzen Zeilen auf 3500 Zeichen gekürzt.
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

## Plan

- [x] Morgen-Briefing: KI-Text an die Dashboard-Fakten binden (Heute-Box, Kaufliste mit Gebot,
  exakte Tage), je Zeile prüfen statt nur global Zahlen.

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
