# HANDOFF.md

Aktiver Agent: Claude Code (/auftrag) + opencode

## Ziel und Stand

Single-User-Dashboard für die eigene Kickbase-Liga, nur lesender Kickbase-Zugriff.
Phasen 1–7b und die Erweiterungen bis C20 sind umgesetzt; Details in `docs/roadmap.md`.

## Aktueller Stand

- C21.3 Erinnerung: Worker-Job `deadline_plan_reminder` in `app/worker/jobs.py`,
  Cron `hour="7-23", minute=0` in `app/worker/main.py`, Schalter `deadline_plan:`
  in `config/notifications.yaml` und Modell `DeadlinePlanRule` in `app/notify/events.py`.
  Fingerabdruck (`app/notify/deadline_plan.situation_key`) aus stabiler Spieler-ID (oder Name
  als Fallback), Deadline und 250-k€-Bucket; solved→deficit-Reentry meldet erneut.
  Frühe Bails: kein Telegram, Regel aus, keine Deadline, außerhalb Close-Fenster, gelöstes
  Defizit (committed baseline, keine Nachricht). `test_schedule` und 21 Fälle in
  `test_deadline_plan_reminder.py` grün, Worker-Test 62 grün.
- C21.2 Laden + Anzeige: `deadline_plan_view(...)` in `app/services/deadline_plan.py` zieht
  Squad/Budget/Markt/MyEleven über vorhandene Caches (401-Re-Login, Stale-Markierung),
  `holding_roles` für Schutz-IDs, `_pick_deadline` schluckt vergangene Fristen. Route
  `/deadline_plan` in `app/web/routes/pages.py` (HTMX-Redirect, `_guarded` für Ausfälle).
  Karte unter Heute in `partials/team.html`, partial `partials/deadline_plan.html`
  (Finanzierung, geschützte Stammspieler, Schritte, Stale-Hinweis). Tests in
  `test_deadline_plan_view.py` und `test_deadline_plan_route.py` grün (28 Fälle).
- C21.1 reine Planlogik in `app/services/deadline_plan.py` (`build_deadline_plan` → `DeadlinePlan`):
  echte Deadline, `sell_by = deadline − trading.safety_hours`, konservative Reserve aus
  `pending_offers_budget` und eigenen Geboten (höherer Wert wie in `transfer_simulation`),
  eigene Gebote pro Spieler einmal gezählt, freie Kaderplätze (beste Formation), geschützte
  Spieler (C17 `holding_roles`) aus `build_sell_guide` ausgenommen. Rückwärtsplan aus datierten
  `PlanStep`s: Verkäufe bis `sell_by`, Elf/Gebote bis Deadline. Liegt `sell_by` in der
  Vergangenheit (Englische Woche / kurze Frist) fällt der Zeitplan weg, Hinweis ersetzt ihn.
  Defizit bleibt sichtbar, wenn nur geschützte Stammspieler es decken könnten.
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

C21 Deadline-Plan mit Erinnerung (Branch `feat/c21-deadline-plan`, Abnahme siehe
`docs/roadmap.md` Zeile „C21“). Kickbase nur lesend, keine Migration nötig (Zustand im
bestehenden Key-Value-Speicher wie `notify.sent`).

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
  gelöstes Defizit löst keinen neuen Alarm aus, erneutes Defizit schon. Tests mit Fake-Notifier.
- [ ] C21.4 Doku: `docs/roadmap.md` C21 als umgesetzt markieren (Datum, Kurzbeschreibung,
  Last-Budget), `docs/features.md` und `.env.example`/Config-Kommentare ergänzen.
- [ ] PR öffnen, einmal reviewen, Findings fixen, CI abwarten und mergen, deployen.

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
