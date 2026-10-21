# HANDOFF.md

Aktiver Agent: opencode (Review + Fixes + Merge + Deploy für C21)

## Ziel und Stand

Single-User-Dashboard für die eigene Kickbase-Liga, nur lesender Kickbase-Zugriff.
Phasen 1–7b und die Erweiterungen bis C20 sind umgesetzt; Details in `docs/roadmap.md`.

## Aktueller Stand

- C21 umgesetzt und live (#14, Squash-Merge, deployiert auf `main` per
  `docker compose up -d --build app worker`): reine Planlogik
  `build_deadline_plan`, Karte „Deadline-Plan" auf der Teamseite, Telegram-Erinnerung
  stündlich 7–23 Uhr, Schalter `deadline_plan:` in `config/notifications.yaml`,
  Fingerabdruck im KV, C21.5-Review-Fixes (siehe vorigen Eintrag).
- Review-Findings für C21 behoben (Commit „C21.5 Review-Fixes" auf
  `feat/c21-deadline-plan`): Worker-Job `deadline_plan_reminder` fängt jetzt Kickbase-
  Ausfälle breit ab (`except Exception` analog zur Backfill-Konvention), loggt Typ+Detail
  und gibt einen Status mit Exception-Namen zurück; dadurch crasht der stündliche
  Scheduler bei einem API-Blip nicht mehr. `ctx.league.get()` wird nur noch einmal
  aufgerufen, irreführender Kommentar „single Kickbase round trip" → „vier gecachte
  Abrufe – Markt, Kader, Aufstellung, Budget – keine neuen Endpunkte" korrigiert, neue
  Doku-Zeile auf `_DEFICIT_BUCKET_EUR` (Integer-Floor-Rundung verhindert Aufwärts-
  Verschiebung an der Bucketgrenze). Neuer Test
  `test_reminder_skips_quietly_on_kickbase_failure` deckt das Ausfall-Szenario ab.
- C21.4 Doku: C21 in `docs/roadmap.md` als umgesetzt 21.10.2026 markiert (Tabellenzeile,
  Last-Budget, Detailabsatz, Folgevorschläge jetzt nur noch C22 + C15), eigene Zeile
  „Deadline-Plan" in `docs/features.md` plus Telegram-Erinnerung dort verlinkt,
  `close_hours`/`safety_hours` in `config/scoring.yaml` kommentiert. Keine neue
  Umgebungsvariable (Zustand im Key-Value-Speicher, Telegram wie bisher über `secrets/`).
- C21.1–C21.3 Deadline-Plan: reine Planlogik `build_deadline_plan` (`app/services/deadline_plan.py`,
  `sell_by` = Deadline − `trading.safety_hours`, konservative Reserve, `holding_roles`-Schutz,
  datierte `PlanStep`s), Anzeige als Karte auf der Teamseite (`deadline_plan_view`, vorhandene
  Caches, Stale-Markierung) und Telegram-Erinnerung als Worker-Job `deadline_plan_reminder`
  (Cron 7–23 Uhr, Schalter `deadline_plan:` in `config/notifications.yaml`, Fingerabdruck aus
  Deadline/Defizit-Bucket/Verkaufsliste, solved→deficit meldet erneut).
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

## Plan

C21 Deadline-Plan mit Erinnerung (Abnahme siehe `docs/roadmap.md` Zeile „C21") — umgesetzt
und live (PR #14). Kickbase nur lesend, keine Migration nötig (Zustand im bestehenden
Key-Value-Speicher wie `notify.sent`).

- [x] C21.1 Reine Planlogik `app/services/deadline_plan.py` (+ `tests/test_deadline_plan.py`):
  `build_deadline_plan(...)` → `DeadlinePlan` mit echter API-Deadline, `sell_by` =
  Deadline − `trading.safety_hours` (`config/scoring.yaml`), konservativem Budget (offene eigene
  Gebote genau einmal abziehen; bei widersprüchlichem Budget-/Gebotsstand den höheren
  Reservebetrag wie in `transfer_simulation.py`), freien Kaderplätzen, geschützten Spielern
  (`holding_roles`, C17) und nötigen Verkäufen über `sell_guide.build_sell_guide` ohne geschützte
  Spieler. Rückwärtsplan als Liste datierter Schritte (Verkäufe bis `sell_by`, Elf/Gebote bis
  Deadline). Englische Woche/kurze Frist: liegt `sell_by` schon in der Vergangenheit, Hinweis
  „Verkauf vor Deadline nicht mehr sicher" statt Zeitplan. Defizit bleibt sichtbar, wenn nur
  geschützte Spieler es decken könnten. Tests: kein Defizit, Defizit mit/ohne Schutz, doppelte
  Gebote, Deadline in <2 h, keine Deadline.
- [x] C21.2 Laden + Anzeige: async `deadline_plan_view(...)` (vorhandene Caches/Loader aus
  `today.py`, `transfers.load_bid_status`, `holding_roles`, keine neuen Kickbase-Endpunkte) und
  eine Karte „Deadline-Plan" auf der Teamseite unter der Heute-Box (Template unter
  `app/web/templates/`, mobil 375 px, Dark-Mode). Fehlen Daten → „unbekannt", nie alte Werte als
  aktuell. Tests: Route rendert Plan mit Fixtures, Fall ohne Deadline.
- [x] C21.3 Erinnerung: Worker-Job `deadline_plan_reminder` in `app/worker/jobs.py` + Cron in
  `app/worker/main.py` (stündlich im Fenster `trading.close_hours` vor der Deadline, Markt aus
  dem Cache), Schalter `deadline_plan:` in `config/notifications.yaml` + Modell in
  `app/notify/events.py`. Nur bei aktiviertem Telegram; Nachricht nur bei relevanter Änderung
  (Fingerabdruck aus Deadline, Defizit-Stufe, Verkaufsliste) und nur mit offenem Defizit –
  gelöstes Defizit löst keinen neuen Alarm aus, erneutes Defizit schon. Tests mit Fake-Notifier
  + Ausfall-Szenario (KickbaseUnavailable).
- [x] C21.4 Doku: `docs/roadmap.md` C21 als umgesetzt markieren (Datum, Kurzbeschreibung,
  Last-Budget), `docs/features.md` und `.env.example`/Config-Kommentare ergänzen.
- [x] C21.5 Review-Fixes: Worker umhüllt `deadline_plan_view` mit `except Exception` (analog zur
  Backfill-Konvention), `ctx.league.get()` einmalig, Kommentar präzisiert, Bucket-Rundung
  dokumentiert, neuer Test für Ausfall.
- [x] PR #14 geöffnet, einmal Subagent-Review, Findings behoben, CI grün, Squash-Merge,
  Branch gelöscht, deployiert.

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
