# Ausbauplan ab Phase 6

Stand 07.10.2026. Ziel: Das Dashboard soll einen **messbaren Vorteil** bringen – besser kaufen,
bieten, verkaufen und aufstellen als die Mitspieler. Jede Phase läuft als
Branch → PR → Review → Merge → Deploy.

**Erledigt:** KI-Grundlage (Mistral), Phasen 6a/6b/7/7b, Review-Korrekturen A und UI B,
C1 Merkliste, C2 Angebote auf eigene Spieler, C3 Spieltags-Bilanz, C4 Aufstellungsindex und
Audit/UI D vom 13.10.2026. Befunde: [`maintainer/audit-2026-10-13.md`](maintainer/audit-2026-10-13.md).

**Offen:** Phase 8 bleibt bis zum erfolgreichen Backtest gesperrt. C6/Phase 9 Wettquoten
bleibt geparkt, bis eine Quelle ohne Account gefunden ist. Neue Vorschläge C12–C16 stehen
unten; die Erweiterung des Plans ist beauftragt, ihre Implementierung ist ein Folgeauftrag.

## Festgelegt

- Reihenfolge: **KI-Grundlage → 6 Kaufen-Seite + Gebote → 7 „Wer spielt?“ →
  7b „Frag dein Dashboard“ → 8 Marktwert-Prognose → 9 Quoten**.
- **KI-Grundlage:** **Mistral, Gratis-Plan** (10 $ API-Guthaben pro Monat, ohne Karte;
  ist es aufgebraucht, ist der Zugang bis Monatsende gesperrt, es entstehen keine Kosten). Key
  in `./secrets/llm_api_key`, am 07.10.2026 getestet: `ministral-14b-latest` (Standard) und
  `ministral-8b-latest` (Ausweichmodell) liefern sauberes `json_schema`; `mistral-small-latest`
  gab in der Gratis-Stufe nur 429. Bedarf ~0,15 $/Monat. Keine kostenpflichtigen Tool-APIs von
  Mistral (z. B. Websuche); Daten sammelt der Worker. Seit PR #62 (13.10.2026) gibt es
  zusätzlich die optionale Claude-Bridge für Telegram mit lesenden MCP-Werkzeugen und
  Websuche; Einrichtung und Grenzen stehen in `docs/maintainer/deployment.md` und `config/ai.yaml`.
- **Scraping ist erlaubt** (privates Projekt), mit den Schonregeln aus der CLAUDE.md
  (≤ 1 Request / 2 s pro Domain, Cache, feste Zeitfenster, HTML-Fixtures in Tests, Ausfälle
  sichtbar machen).
- **Gebotsempfehlung:** Ziel ist das Gebot, das **70 %** der vergleichbaren Transfers gewonnen
  hätte (einstellbar in `config/scoring.yaml`).
- **Quoten:** zuerst eine Quelle ohne Account. Ein Token-Dienst ist optional und wird automatisch
  genutzt, sobald `./secrets/odds_api_key` existiert.

## Noch offen

- 8 Marktwert-Prognose (gated auf Backtest) → 9 Wettquoten; Feature-Vorschläge in `HANDOFF.md`
  (Block C) warten auf Freigabe.
- Später je nach Erfahrung: OpenRouter (Krypto-Zahlung möglich) als Wechsel, falls Ministral
  beim Briefing nicht reicht oder Mistral die Gratis-Stufe einschränkt – nur eine Einstellung
  in `.env`.

## Bekannte Datenquellen (Probe vom 07.10.2026)

| Quelle | Inhalt | Phase |
|---|---|---|
| Kickbase `activitiesFeed` (Liga) | Typ 15 = Transfer mit Preis `trp`, Käufer `byr`, Spieler `pi`; Typ 3 = neues Marktangebot | 6 |
| Kickbase `players/{id}/transferHistory` | alle Käufe eines Spielers in der Liga mit Preis/Datum | 6 |
| Kickbase Markt `dt` | nächste Aufstellungs-Deadline (z. B. Fr 20:30) | 6 |
| Kickbase `matches/{id}/details` | Startelf (`t1lp`/`t2lp`), Bank, Ereignisse (Ein-/Auswechslung, Karten, Tore) | 7 |
| Kickbase `base/news` | fast nur Werbung/Teamvorstellungen, kein Text – **nicht genutzt** | – |
| kicker-RSS `newsfeed.kicker.de/news/bundesliga` | 20 aktuelle Meldungen mit Teaser und Schlagwörtern (Verein, Spieler) | KI / 7 |
| Kickbase `base/predictions/teams` | Prognose-Zeitstempel je Verein (eigentliche S11-Prognose noch zu finden) | 7 |
| LigaInsider (Scraping) | voraussichtliche Aufstellungen, Wackelkandidaten | 7 |
| kicker / Transfermarkt (Scraping) | Verletzte und Gesperrte mit voraussichtlicher Rückkehr | 7 |
| Quoten-Vergleichsseite (Scraping) oder The Odds API (Token) | 1X2-Quoten, ggf. Über/Unter | 9 |

---

## KI-Grundlage (vor Phase 6) — ✅ erledigt

**Ziel:** Verletzungs- und Sperrmeldungen automatisch beim Spieler sehen; morgens ein kurzes
Briefing per Telegram, was heute zu tun ist. Alles weiterhin erklärbar – die KI rechnet keine
Scores, sie fasst nur zusammen und zieht Fakten aus Texten.

1. **Client `app/llm/`**, anbieterneutral im OpenAI-kompatiblen Chat-Format mit `httpx` (kein
   SDK). `.env`: `LLM_BASE_URL=https://api.mistral.ai/v1`, `LLM_MODEL=ministral-14b-latest`,
   `LLM_FALLBACK_MODEL=ministral-8b-latest`; Key als Docker Secret `llm_api_key` (Worker; App
   erst ab 7b). `temperature: 0`, `response_format: json_schema` (strict) und Prüfung gegen
   Pydantic-Modelle; Key nie loggen.
2. **Harte Grenzen** in `config/ai.yaml`: Aufrufe pro Tag, Tokens pro Tag und Monat,
   `max_tokens` pro Aufruf, Mindestabstand 2 s zwischen Aufrufen, Monatsbudget nach Preistabelle
   (höchstens 2 $ des Gratis-Guthabens; bei späterem Bezahlmodell höchstens 3 €/Monat). Vor
   jedem Aufruf ungünstigsten Fall prüfen, danach echten Verbrauch aus `usage` in `ai_usage`
   buchen. 429 → einmal Ausweichmodell, dann aufgeben; Guthaben leer / Budget erreicht → Pause
   bis Monatsende, einmal Telegram-Hinweis. Ohne Key oder bei Fehlern läuft alles ohne KI.
3. **News-Auswertung:** kicker-RSS 3× täglich (Kickbase-News enthalten fast nur Werbung;
   nur neue Meldungen der letzten 3 Tage) → gebündelt ans Modell → `news_injuries` (Meldungs-ID, Spielername,
   Verein, Art, Ausfall in Spielen/Wochen, Rückkehr, Quelle, Zeit) → **Namensabgleich** mit den
   Kickbase-Spielern (normalisiert + Verein, Overrides in `config/player_aliases.yaml`, wird in
   Phase 7 weiterverwendet) → Hinweis auf Spieler- und Team-Seite, z. B. „laut News:
   Muskelfaserriss, ca. 3 Wochen“; betrifft es eigene Spieler, Telegram-Nachricht.
4. **Tägliches Briefing** (Telegram, nach dem Morgen-Sammeljob): Eingabe sind fertige Fakten
   mit id aus dem Dashboard – Deadline, „Heute“-Box (leere Plätze, Ausfallrisiken, Minus,
   Kader-Lücken, offene Gebote), Kaufliste wie auf der Kaufen-Seite (mit Gebot und Tag des
   Angebotsendes), Verkaufskandidaten, Meldungen/Status eigener Spieler, Teamwert über Nacht
   (nur Info). Kein kompletter Kader mehr, damit die KI daraus keine Empfehlungen erfindet.
   Jede KI-Zeile nennt ihre Fakten und darf nur deren Namen, Zahlen, Tage/Uhrzeiten und
   Handlungen (kaufen, verkaufen, aufstellen) nutzen – Werte und Handlungen neben einem Spieler
   nur aus den Fakten zu genau diesem Spieler; sonst ersetzen die Fakten-Sätze die Zeile.
   Ausgelassene wichtige Fakten werden vorangestellt; ohne KI gehen die Fakten-Sätze direkt
   raus (in ganzen Zeilen gekürzt, falls die Nachricht zu lang wird).
5. **Datenschutz:** Die Gratis-Stufe darf mit Eingaben trainieren → nur Bundesliga-Daten und
   eigener Kader, nie Namen/IDs anderer Manager. Falls die Mistral-Konsole ein Training-Opt-out
   anbietet: einschalten (macht Arne).

**Tests:** Client mit `httpx.MockTransport` (Erfolg, 429 → Ausweichmodell, Guthaben leer,
kaputtes JSON, Schema-Verstoß, Budget erreicht, Key nicht im Log), Zahlencheck des Briefings,
Namensabgleich mit Fixtures (News anonymisiert nicht nötig – nur Bundesliga-Spieler).
**Abnahme:** Briefing kommt täglich; News-Ausfälle beim Spieler sichtbar; `ai_usage` zeigt den
Verbrauch; ohne Key läuft alles wie vorher.

## Phase 6 – Kaufen-Seite, Transfers, Gebotsempfehlung, Erfolgsmessung — ✅ erledigt

Umgesetzt in zwei PRs: **6a** Punkte 1–5 (Filter, Deadline-Badge, Kaderlücken, Transfer-Sammler
mit `filter=15`, Gebotsmodell), **6b** Punkte 6–7 (eigene Gebote, Erfolgsmessung).

**Ziel:** Auf einen Blick sehen, was vor dem Spieltag noch kommt, was eine Lücke füllt und wie
viel man bieten muss.

1. **Filter und Sortierung** (HTMX, Zustand in der URL): Sortierung nach Score, Score/Mio, Preis,
   Ablauf; Filter Position, „vor Deadline“ (Standard an), „füllt Lücke“, Höchstpreis.
2. **Deadline-Badge:** `expires_at` gegen Markt-`dt` → „rechtzeitig vor dem Spieltag“ /
   „erst danach“; Restzeit sichtbar.
3. **Kaderlücken:** offene Plätze der besten Formation (aus `eleven_view`) → Positionen
   hervorheben und als Filter anbieten.
4. **Transfer-Sammler (Worker, alle 2 h, ~12 Requests/Tag):** `activitiesFeed` paginiert;
   Tabelle `league_transfers` (league_id, activity_id, player_id, price, buyer_id, at,
   market_value_at, overbid_pct). Marktwert am Kauftag aus `market_values` (Markttag-Logik).
   Einmaliger Backfill so weit der Feed zurückreicht; `transferHistory` nur bei Bedarf (Cache 6 h).
5. **Gebotsmodell:** vergleichbare Transfers (Preisklasse, Position, letzte 60 Tage; zu wenige →
   ligaweit) → 70-%-Quantil der Überbietung → „Empfohlenes Gebot 5,6 Mio. (+9 %), hätte 70 % von
   23 vergleichbaren Transfers gewonnen“. Datenbasis (n, Spanne) immer sichtbar; bei n < 10
   breitere Spanne und Hinweis. Prüfen, ob die Zahl der Gebote (`ofc`) den Preis erklärt.
6. **Eigene Gebote auswerten:** `uop` auf dem Markt merken; nach Ablauf gewonnen (Spieler im
   Kader) oder verloren (Siegerpreis aus dem Feed) → fließt ins Modell.
7. **Erfolgsmessung:** `recommendation_log` (Tag, Ansicht, Spieler, Score + Bestandteile, Preis,
   Marktwert); Job bewertet nach 1/3/7 Tagen (Marktwert-Änderung, Punkte). Seite „Wie gut waren
   die Tipps?“ mit Trefferquoten je Ansicht.

**Vorab-Probe:** Feld-Bedeutungen im Feed (Typen, `t` in Typ 15, `filter`-Werte, Paginierung),
anonymisierte Fixtures (Käufer → „Manager N“, Probe-Anonymisierer erweitern).
**Tests:** Quantil-Rechnung, Vergleichsgruppen, Deadline-Badge (Zeitzonen), Filter/Sortierung,
Sammler-Idempotenz, keine Managernamen in Fixtures.
**Abnahme:** Kaufen-Seite filterbar, jedes Angebot mit Deadline-Badge und Gebotsempfehlung samt
Datenbasis; Log läuft.

## Phase 7 – „Wer spielt?“ (Startwahrscheinlichkeit)

**Ziel:** Keine Punkte mehr durch Spieler, die nicht spielen; Wackelkandidaten früh erkennen.

1. **Einsatzhistorie (Kickbase):** `matches/{id}/details` für beendete Spiele (9 Requests pro
   Spieltag, Backfill der Saison ~45) → `match_lineups` (Spieler, Startelf ja/nein, Minuten,
   Ein-/Auswechslung) → Startquote der letzten 5 Spiele.
2. **Voraussichtliche Aufstellungen (LigaInsider, Scraping):** 18 Vereinsseiten, 2× täglich und
   zusätzlich ~3 h vor der Deadline → `lineup_predictions` (Quelle, Abrufzeit, Spieler,
   Startelf / fraglich / Bank).
3. **Verletzungen und Sperren (kicker / Transfermarkt, Scraping):** 1–2 Seiten täglich →
   `injuries` (Art, seit, voraussichtliche Rückkehr, Quelle).
4. **Namensabgleich** aus der KI-Grundlage für die gescrapten Namen wiederverwenden; nicht
   zuordenbare Namen loggen und auf der Seite nennen. Kicker-Texte ggf. ebenfalls über die
   News-Auswertung der KI strukturieren.
5. **Startwahrscheinlichkeit** mit Begründung: Prognose (Startelf 0,85 / fraglich 0,5 / nicht
   genannt 0,15) × Kickbase-Status (verletzt/gesperrt → 0) angepasst an die Startquote; später mit
   den echten Aufstellungen kalibrieren.
6. **Score:** neuer Bestandteil **„Einsatz“** (Gewicht je Profil in `scoring.yaml`); Beste Elf
   stellt Spieler unter 0,5 nicht auf, Kaufen zeigt das Risiko.
7. **Telegram-Deadline-Warnung** ~2 h vor der Deadline: aufgestellte Spieler mit Ausfallrisiko,
   freie Plätze, eigene Gebote, die vor dem Spieltag auslaufen.

**Vorab:** S11-Prognose evtl. auch über Kickbase (`lineup/selection`, `playercenter`) – Probe;
gespeicherte HTML-Seiten als Fixtures.
**Tests:** Parser gegen HTML-Fixtures (inkl. geänderter Seite → Fehler sichtbar), Namensabgleich,
Wahrscheinlichkeits-Regeln, Warnungslogik.
**Abnahme:** Jeder Spieler hat eine Startwahrscheinlichkeit mit Quelle und Stand; Beste Elf und
Warnung nutzen sie.

## Phase 7b – „Frag dein Dashboard“ (Telegram)

**Ziel:** Fragen wie „Soll ich X verkaufen?“ oder „Wen kaufe ich für 5 Mio. im Sturm?“ direkt
im Telegram-Chat stellen; die Antwort stützt sich auf unsere echten Zahlen.

1. **Nachrichten empfangen:** Long-Polling `getUpdates` im Worker; nur Nachrichten aus
   `TELEGRAM_CHAT_ID` beantworten, alles andere ignorieren.
2. **KI mit Tools (Function Calling), nur lesend auf eigene Daten:** `spieler_suchen`,
   `spieler_details` (Score mit Bestandteilen, Form, Trend, Status, Startwahrscheinlichkeit,
   News), `marktangebote` (Filter Position/Preis, Deadline, Gebotsempfehlung), `mein_kader`,
   `budget`. Kein Internet, keine schreibenden Aktionen (Kickbase bleibt nur lesend).
3. **Grenzen:** höchstens 4 Tool-Runden pro Frage, Fragen pro Tag in `config/ai.yaml`
   (z. B. 30), Antwort kurz und mit den Zahlen aus den Tool-Ergebnissen.
4. Später optional als Eingabefeld im Dashboard.

**Tests:** Tool-Schleife mit Fake-Modell, Chat-ID-Filter, Rundenlimit, Tagesgrenze.
**Abnahme:** Fragen im Chat werden mit Begründung aus unseren Daten beantwortet.

## Block C4 – Aufstellungsindex (interner Name xP) — ✅ erledigt

Korrektur aus dem Audit vom 13.10.2026: Das Produkt aus Startchance, positionsbezogenem
Formrang und mittlerer Schwierigkeit mehrerer kommender Gegner ist ein **relativer Index**,
keine Vorhersage von Kickbase-Punkten und keine kalibrierte Wahrscheinlichkeit.

- Darstellung: **0–100 je Spieler**, ohne Prozent-Einheit; „Index pro Mio.“ bei Kaufangeboten.
  Die Beste Elf maximiert die Summe und zeigt „Index-Summe“; die Zusammenfassung für den Bot
  verwendet dieselben Begriffe. Interne `XP`, `xp`, `xp_per_million` bleiben kompatibel.
- Faktoren: Startchance aus LigaInsider/Status/Startquote, Formrang aus `matchday_points`,
  Programm aus `programme.py`. Fehlende Daten werden neutral angesetzt und sichtbar markiert.
- Notbesetzung: Tatsächliche Ausfälle/Startchance null ausschließen. Index null durch schlechte
  Form bedeutet keinen sicheren Ausfall; spielbarer Ersatz kann einen sonst freien Platz füllen.
- Tests: Faktorberechnung, gültige Formationen, Nullindex-Ersatz, Budget mit eigenen Geboten,
  Sortierung pro Mio., ehrliche Einheiten auf Seiten und im Bot.

Eine echte Punkteprognose braucht das getrennte Modell und den Backtest aus C13.

## Neue Vorschläge aus dem Audit (C12–C16, Folgeaufträge)

Priorität: Datenvertrauen vor weiteren Prognosen. Jeder Vorschlag bleibt ein eigener
Branch/PR-Block; zunächst die Datenlage prüfen und die konkreten Checkboxen in HANDOFF planen.

| Vorschlag | Nutzen | Abnahme |
|---|---|---|
| **C12 Datenqualität und gezielte Reparatur** | Quellenalter, letzter erfolgreicher Worker-Lauf, fehlende Mannschaften/Spieltage und neutrale Score-Faktoren sichtbar bündeln. Historische Teilpartien anschließend gezielt nachladen. | Vollständigkeit je Mannschaft prüfen, keine pauschale Löschung von Sync-Zuständen; begrenzte lesende Requests mit bestehendem Takt/Cache. Wiederholung idempotent; Fixtures für Quelle ausgefallen, alt, halbe Partie. |
| **C13 Echte erwartete Kickbase-Punkte** | Absoluten Punktewert samt Unsicherheit für den nächsten Spieltag schätzen; Kauf- und Aufstellungsentscheidungen lassen sich besser vergleichen. | Historische Punkte statt Perzentile; tatsächlich nächster Gegner; Start-/Einwechselchance getrennt. Zeitlich getrennten Backtest ohne zukünftige Daten gegen Saison-Ø und letzten-5-Schnitt durchführen; erst bei belegtem Vorteil als Punkteprognose anzeigen. Index bleibt Fallback. |
| **C14 Transfer-Simulation · umgesetzt 14.10.2026** | Vor einer Entscheidung Verkauf/Kauf gedanklich kombinieren und Budget, offene Gebote, Formation und Kaderlücken danach sehen. | Lokale Was-wäre-wenn-Ansicht ohne Kickbase-Schreibzugriff. Reservierte Gebote nur einmal berücksichtigen; Ausgangskader unverändert; Tests für mehrere Gebote, Verkauf des einzigen Torwarts und Spieler über Budget. |
| **C15 Betriebs- und Ausfallhinweise** | Verpasste Sammelläufe oder lange Quellenausfälle rechtzeitig erkennen, statt mit alten Empfehlungen weiterzuarbeiten. | Worker-Heartbeat auch ohne externe Requests; konfigurierbare Warnschwellen und deduplizierte Benachrichtigung nur bei aktiviertem Telegram. Wiederherstellung sichtbar, kein Alarm beim regulären Neustart. |
| **C16 Prognosegüte und Datenbasis** | Startchancen und Empfehlungen anhand tatsächlicher Einsätze/Ergebnisse beurteilen; systematische Fehleinschätzungen finden. | Stichprobengröße, Datenalter und zeitlich getrennte Auswertung anzeigen; unvollständige Spieltage aus Qualitätsmetriken ausschließen. Gewichtsänderungen nur vorschlagen (C7), nie automatisch aktivieren. |

## Phase 8 – Marktwert-Prognose („wer steigt, wer fällt“)

**Ziel:** Vor dem täglichen Update (~22:00) wissen, wer morgen steigt – kaufen, solange es
billig ist; Fallende rechtzeitig verkaufen.

1. **Backfill:** `/marketValue/365` und Punkte der letzten Saison je Spieler (einmalig ~920
   Requests, 1 req/s).
2. **Merkmale je Spieler und Tag:** Marktwert-Momentum (1/3/7 Tage), Punkte letzter Spieltag vs.
   Schnitt, Startwahrscheinlichkeit, Rückkehr nach Verletzung, Tage bis/nach Spieltag,
   Preisklasse, Position, Programm, Transferaktivität in der Liga.
3. **Ziel:** Marktwert-Änderung am nächsten Tag und über 3 Tage.
4. **Modelle:** Baselines „unverändert“ und „Trend setzt sich fort“; dann Ridge-Regression, dann
   Gradient Boosting (LightGBM) mit Beitrag je Merkmal (Erklärbarkeit, Regel 6).
5. **Backtest** mit rollierendem Startpunkt: Fehler, Richtungs-Trefferquote, Präzision der
   „Top 20 Steiger“. **Nur ausliefern, wenn beide Baselines über ≥ 4 Wochen geschlagen werden.**
6. **Nutzung:** Badge „steigt voraussichtlich +x %“ mit Gründen; Teil „Prognose“ im Trend-
   Bestandteil; Telegram ~21:30 „voraussichtliche Gewinner morgen“; wöchentliches Nachtrainieren
   mit gespeicherten Kennzahlen.

**Abnahme:** Backtest-Bericht im Repo; Prognose nur sichtbar, wenn sie besser als die Baselines
ist.

## Phase 9 – Wettquoten — 🅿 geparkt (13.10.2026)

Geparkt, bis eine Quelle ohne Account gefunden ist. Branch `feat/c6-quoten` enthält die bisherigen
Erkundungen; offene Punkte dort im Abschnitt „Offene Probleme".

1. Zu Beginn kurz bewerten, welche Quelle ohne Account am stabilsten ist (Quoten-Vergleichsseite
   oder öffentliche Buchmacher-Endpunkte); 1–2 Abrufe pro Tag für die nächsten Spieltage.
2. Optional **The Odds API** (Free-Tier): `./secrets/odds_api_key` anlegen → wird automatisch
   bevorzugt, Scraping bleibt Fallback.
3. Nutzung: Wahrscheinlichkeiten ohne Buchmacher-Marge → erwartete Punkte des Vereins
   (ersetzt/ergänzt die Tabellen-Stärke im „Programm"), Chance ohne Gegentor für Torwart/Abwehr,
   Torerwartung für Angreifer. Gewichte in `scoring.yaml`. Schnittstelle in
   `app/services/xp.py::expected_points` ist so vorbereitet, dass ein Quotenfaktor multiplikativ
   eingefügt werden kann.

---

## Startschuss-Checkliste (erledigt)

**KI-Grundlage zuerst:**
1. Branch `feat/ai-basis` von `main`.
2. Compose: Secret `llm_api_key` (Datei `./secrets/llm_api_key`, Besitzer uid 1000, 0600);
   `.env`/`.env.example` um `LLM_*` ergänzen.
3. Probe: kicker-RSS (Felder, Schlagwörter), synthetische Fixture im selben Format.
4. Migration: `ai_usage`, `news_injuries`.
5. Client + Grenzen, dann News-Auswertung, dann Briefing; Tests, PR, Review, Merge, Deploy.

**Danach Phase 6:**
1. Branch `feat/phase-6-buy` von `main`.
2. Probe: `activitiesFeed` (mehrere Seiten, Filter), `transferHistory` für 2–3 Spieler;
   Anonymisierer erweitern, Fixtures speichern.
3. Migration 0003: `league_transfers`, `recommendation_log`, `my_offers`.
4. Sammler-Job + Backfill, dann Gebotsmodell, dann UI (Filter, Badges, Gebot), dann Erfolgsmessung.
5. Tests, Screenshots (Managernamen verpixelt), PR, Review, Merge, Deploy.

## Last-Budget (zusätzlich zu heute)

| Phase | Kickbase | Extern |
|---|---|---|
| KI-Grundlage | ~2 Requests/Tag (News) | Mistral ~3–5 Aufrufe/Tag |
| 6 | ~12 Requests/Tag (Feed), Gebote on demand | – |
| 7 | 9 pro Spieltag (+ ~45 einmalig) | LigaInsider ~40/Tag, kicker/Transfermarkt ~2/Tag |
| 7b | – | Mistral 2–5 Aufrufe pro Frage |
| 8 | einmalig ~920 | – |
| 9 | – | Quoten 1–2 Abrufe/Tag |

## Auftrag 13.10.2026 – Trading, iPhone-Fokus, Nachrichten, Spieltagspunkte

Trading-Block: Tagesbewegung und Gewinn seit Kauf, eigenes Zeitfenster bis zur echten Deadline,
Budget nach allen Geboten, Finanzierung zu heutigen Marktwerten mit Schutz der vorgeschlagenen
Elf. Trading-Käufe berücksichtigen Aufschlag, verlässliches Angebotsende und verbleibende Updates;
Gewinnrechnung ist nur ein Szenario. Details und Quellen in `maintainer/iphone-product-review-2026-10-13.md`.

Umgesetzt: Nachrichten mit Originalartikeln auch ohne KI und besserem
Deadline-Takt; mobile Informationshierarchie straffen; eigene Spieltagspunkte mit 10-min-Cache,
vorläufiger Wertung und klarer Trennung von nächster Aufstellung. Jeder Block hat eigenen PR,
ein lesendes Review, grüne CI und Deployment. Phase 8 bleibt gated.


## Nächste sinnvolle Features nach dem iPhone-/Trading-Auftrag (F3)

Priorität nach Alltagsnutzen: erst C14 Transfer-Simulation und manuell geschützte Stammspieler,
dann tatsächlicher Trading-Erfolg, danach Nachrichten-Verlässlichkeit und gezielte Hinweise.
Umgesetzte Vorschläge sind markiert, die übrigen bleiben geplant. Jeder erhält eigene Checkboxen,
Branch/PR, Fixtures und Abnahme; Kickbase bleibt vollständig lesend.

| Vorschlag | Nutzen und Verhalten | Abnahme |
|---|---|---|
| **C17 Stammkader und Trades selbst markieren · umgesetzt 14.10.2026** | Spieler als „für die Deadline behalten“ oder „Trading“ lokal vormerken. Der Finanzierungsplan respektiert den eigenen Stammkader zusätzlich zur vorgeschlagenen Elf. | Schutz sichtbar und jederzeit aufhebbar; Budgetdefizit bleibt sichtbar, wenn geschützte Spieler zur Finanzierung nötig wären. Keine Kickbase-Aufstellungsänderung; keine versteckten Verkäufe im Plan. |
| **C18 Tatsächlicher Trading-Gewinn · umgesetzt 14.10.2026** | Einkauf inklusive bezahltem Aufschlag, Haltedauer und echten Verkaufserlös gegenüberstellen; offene Wertsteigerung getrennt von realisiertem Gewinn zeigen. | Liga-Transferhistorie und eigener Bestand, eindeutige Kauf-/Verkaufszuordnung, unvollständige Historie als unbekannt. Wiederholte Sammlung dupliziert nichts; keine zukünftigen Gewinne im verfügbaren Budget. |
| **C19 Trendwende statt Wochen-Minus · Tagesansicht umgesetzt 14.10.2026** | Aufeinanderfolgende bestätigte Tagesänderungen neben dem 3-/7-Tage-Kontext zeigen. Optional Hinweis, wenn ein steigender Trade nach dem Update tatsächlich dreht. | Keine Lücken als Tageswerte; Update-Zeiten/alten API-Anker prüfen; mindestens ein eigener bestätigter Update-Stand. Deduplizierte Hinweise nur bei aktiviertem Telegram. Eine Prognose bleibt am Phase-8-Backtest gated. |
| **C20 Nachrichten-Verlässlichkeit · Revisionen umgesetzt 14.10.2026** | Geänderte Artikel mit gleicher GUID nachziehen, später zuordenbare Meldungen erneut matchen und die verwendete Textbasis nachvollziehbar machen. | Inhaltsrevisionen begrenzt verarbeiten; keine erneute Benachrichtigung bereits behandelter Artikel nach Neu-Auswertung. Quellendatum, Textausschnitt und Zuordnungsgrund sichtbar. Eine zusätzliche Konfliktregel bleibt Folgeausbau; News ändern keine Einsatzprognose automatisch. |
| **C21 Deadline-Plan mit Erinnerung · umgesetzt 21.10.2026** | Zum tatsächlichen Anpfiff Budgetziel, freien Plätzen, geschützten Spielern und nötigen Verkäufen folgen; Verkaufs-Puffer berücksichtigen, auch in englischen Wochen. | Konservative Werte, offene Gebote einmal zählen, Rückwärtsplanung ab echter API-Deadline. Erinnerung nur bei aktiviertem Telegram und relevanter Änderung; bereits gelöstes Defizit löst keinen neuen Alarm aus. |
| **C22 Spieltagsabschluss und Korrekturen** | Vorläufige Punkte mit späterem bestätigtem Abschluss vergleichen; optional gesammelte Merkliste-Spieler auf Abruf ergänzen. | Endgültigkeit nicht aus Abpfiff ableiten; letzte offizielle Korrektur und Quelle dokumentieren. Abgeschlossene Bilanz nicht aus unvollständiger Live-Summe überschreiben. Weitere Spieler nur mit dokumentiertem Endpunkt und bestehendem Request-Budget. |

Last-Budget der neuen umgesetzten Ansichten: Trading/Originalartikel nutzen vorhandenen Cache
bzw. Datenbank. Nachrichten: drei Basisabrufe pro Tag plus höchstens sechs stündliche Abrufe
im Deadline-Fenster; keine zusätzliche KI-Auswertung unveränderter Artikel. Spieltagspunkte:
höchstens sechs Teamcenter-Abrufe je Stunde bei aktiver Ansicht, dazu der geteilte Spielplan-
Cache (30 min) und User-Cache (24 h). Keine Live-Abfragen, wenn die Ansicht geschlossen ist.
Deadline-Plan: keine neuen Endpunkte, keine eigenen Abrufe – Karte und Erinnerung lesen
Markt, Kader, Aufstellung, Budget und eigene Gebote aus denselben Caches wie die Teamseite.
Der stündliche Job (7–23 Uhr) bricht ab, bevor er lädt, wenn Telegram oder die Regel
`deadline_plan` in `config/notifications.yaml` aus sind; ohne offenes Defizit oder außerhalb
`trading.close_hours` wird nichts gesendet.


C14 umgesetzt: `/empfehlungen/simulation` kombiniert bis zu 25 Verkäufe/Käufe mit anpassbarem
Kaufpreis. Budgetrechnung berücksichtigt Restgebote und rechnet vorhandene Gebote ausgewählter
Käufe einmal an, ohne neue Mittel zu erzeugen. Bei widersprüchlichen Budget-/Gebotsständen
wird der höhere Reservebetrag verwendet und gewarnt. Die reine Elf-Auswahl ist mit Beste Elf
geteilt; Käufe zählen dafür nur bei zeitlich bestätigten Kickbase-Angeboten vor der echten
Deadline. Ausgangskader und Kickbase bleiben unverändert, kein Worker-Job/keine Migration.
lokal archivierte Browserprüfung (Screenshots nicht zur Veröffentlichung).


C17 umgesetzt: eigener Stammkader und Trading-Notiz unter `/kader/rollen`, lokal pro Liga/Spieler
gespeichert. Schutz gilt in allen automatischen Finanzierungsvarianten, ohne geschützte Spieler
bei der Beurteilung der verbleibenden Elf zu entfernen. Beste Elf bleibt nach Daten gewählt,
Verletzungs-/Trendrisiken sichtbar; explizite Simulator-Verkäufe erhalten eine Warnung. Speichern
verlangt normale Anmeldung/CSRF und einen aktuellen eigenen Kader, Fehler melden keinen Erfolg.
Additive Migration 0013; Rückwechsel ohne Präferenzlöschung siehe Deployment-Doku.
lokal archivierte Browserprüfung (Screenshots nicht zur Veröffentlichung).

Nach C14/C17 sind C18 tatsächlicher Trading-Gewinn, C19 bestätigte Tagestrends, C20
Nachrichtenrevisionen und C21 Deadline-Plan mit Erinnerung umgesetzt. Weitere Vorschläge:
C22 Spieltagskorrekturen; C15 sollte die nun belegte knappe VM-Plattenkapazität überwachen.
Diese übrigen Folgefeatures bleiben geplant und werden getrennt umgesetzt.


C18 umgesetzt: `/empfehlungen/gewinne` zeigt eindeutig zuordenbare Verkäufe der letzten
60 Tage mit echtem Einstand (inkl. Aufschlag), Erlös und Haltedauer. Offene Positionen nutzen
bestätigte Kaufpreise und heutige Marktwerte getrennt von realisierten Gewinnen; kein Budget-
Zufluss ohne Verkauf. Nullable Verkäufer-Flag aus dokumentiertem Fixture-Feld `slr`, Altbestand
nur bei genau passendem Feed/Cache-Ereignis ergänzt. Andere, unbekannte oder gleichzeitige
Transfers unterbrechen die Zuordnung. Teilhistorie, fehlende Einstände und Sammlerfehler bleiben
sichtbar; Manager-Verkäufe ohne Verkäufer sind nicht eindeutig auswertbar. Kein zusätzlicher
Feed-Abruf beim Seitenaufruf, keine neue Worker-Abfragefrequenz.


C19 Tagesansicht umgesetzt: `/empfehlungen/trends` zeigt sieben genaue Tagesdifferenzen des
Kaders, fortgesetzte Serien und bestätigte Richtungswechsel. Aktueller gespeicherter Marktwert
und Tagesdelta müssen zu frischen Kickbase-Kaderdaten passen; Datum, Update-Fenster und
Stale-Status werden geprüft. Lücken/Nullupdates unterbrechen die Serie. Keine Prognose oder
Verkaufsempfehlung; kein zusätzliches API-Polling. Telegram-Trendmeldungen bleiben optionaler
Folgeausbau und werden nicht neu aktiviert.


C20 Revisionen umgesetzt: Ändert kicker Titel, Teaser oder Tags eines bekannten Artikels,
bleibt dessen ursprüngliches Veröffentlichungsdatum erhalten. Die UI nennt zusätzlich den
Zeitpunkt, an dem die Änderung erkannt wurde, und zeigt nur Meldungen der aktuellen Revision.
Bis zur neuen Auswertung bleibt die überholte Meldung ausgeblendet; Originalartikel sind auch
ohne KI sichtbar. Je Artikel höchstens drei Neustarts, wartende Änderungen werden zusammengefasst;
die bestehenden KI-Budgets/Fehlversuchsgrenzen bleiben bestehen. Verspätete Ergebnisse oder
Fehler einer überholten Textbasis überschreiben den neueren Stand nicht.

Ungeklärte Spieler werden beim bestehenden Nachrichtenlauf erneut eindeutig nach Name/Verein
zugeordnet, ohne neue KI-Anfrage. Verwendeter Textausschnitt und Zuordnungsgrund sind mobil
aufklappbar. Bereits durch den Telegram-Schritt behandelte Artikel bleiben auch nach leerer
Zwischenauswertung als behandelt markiert; Revisionen erzeugen keine erneute Nachricht für
diese GUID. Das bestehende Verhalten bei Transportfehlern bleibt unverändert. Keine neue
Abruffrequenz, keine automatische Statuswirkung auf Empfehlungen. Additive Migration 0015;
lokal archivierte Browserprüfung (Screenshots nicht zur Veröffentlichung).

Team-Meldungen werden nach dem beobachteten Änderungszeitpunkt sortiert. Das Morgenbriefing
bewahrt das ursprüngliche Quellendatum und benennt zusätzlich erkannte Änderung und Revision.


C21 umgesetzt: Die Karte „Deadline-Plan" auf der Teamseite führt von jetzt bis zum echten
Aufstellungs-Anpfiff zurück: freie Plätze je Position, Finanzierung mit konservativ reserviertem
Budget (offene eigene Gebote genau einmal, bei widersprüchlichem Budget-/Gebotsstand der höhere
Reservebetrag wie in der Transfer-Simulation), geschützte Stammspieler (C17) und die daraus
nötigen Verkäufe aus dem Verkaufs-Guide ohne geschützte Spieler. Verkäufe müssen bis
`deadline − trading.safety_hours` verbucht sein, sonst zeigt die Karte den Hinweis „Verkauf vor
Deadline nicht mehr sicher" statt eines Zeitplans. Bleibt das Defizit nur durch geschützte
Stammspieler deckbar, wird das sichtbar gemacht statt still eines davon einzuplanen. Fehlende
Daten erscheinen als „unbekannt", veraltete Cache-Werte als stale-Hinweis; neue Kickbase-
Endpunkte gibt es nicht. Der Telegram-Job `deadline_plan_reminder` läuft stündlich von 7 bis
23 Uhr, aber nur bei aktiviertem Telegram und aktiver Regel `deadline_plan`
(`config/notifications.yaml`) sowie mit offenem Defizit innerhalb von `trading.close_hours` vor
der Deadline. Eine Nachricht geht nur bei geänderter Lage (Fingerabdruck aus Deadline,
Defizit-Stufe und empfohlener Verkaufsliste); ein gelöstes Defizit merkt sich als Baseline und
löst keinen neuen Alarm aus, ein erneut auftretendes schon. Zustand liegt im bestehenden
Key-Value-Speicher (`notify.deadline_plan.fingerprint`), also ohne Migration.
