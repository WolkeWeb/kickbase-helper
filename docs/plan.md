# Gesamtplan

Abgestimmt am 2026-10-06. Regeln, Stack und Arbeitsweise stehen in der `CLAUDE.md`, dieser Plan
beschreibt das *Was* und *Wie* der Phasen.

## Entscheidungen

- **Nur eine Liga:** konfigurierbar über den eigenen Namen (`KICKBASE_LEAGUE_NAME` in `.env`). Kein Liga-Umschalter;
  ligabezogene Tabellen haben trotzdem eine `league_id`-Spalte.
- **Secrets** sind lokale Docker-Secret-Dateien, standardmäßig unter `./secrets/`, Modus 0600.
  Pfade und Container-UID/GID sind konfigurierbar. Bestehende Installationen können ihre
  externen Secret-Pfade weiterverwenden.
- **Erster Commit** (README, .gitignore) einmalig direkt auf `main`, danach nur Branches + PRs.
- `GET /v4/leagues/{leagueId}/market` kommt zusätzlich in den Client (für „Kaufen“).
- Die Spec enthält kaum Antwortschemas → Pydantic-Modelle entstehen aus echten, anonymisierten
  Antworten (`scripts/probe.py` → `tests/fixtures/kickbase/`).

## Erkenntnisse aus der Probe (Phase 1)

- **Ligen** stehen nicht in `/v4/user/me`, sondern in `GET /v4/leagues/selection` (`it[].i/n/cpi`).
  Der Resolver nutzt deshalb diesen Endpunkt.
- **Token:** Login liefert `tkn` und `tknex` (ISO-Ablauf, ~7 Tage).
- **`/v4/competitions/1/players`** ist nur eine Top-25-Liste ohne Marktwerte – ungeeignet für den
  Gesamtabzug. **`/v4/competitions/1/teams/{teamId}/teamprofile`** liefert alle Spieler eines Vereins
  mit Marktwert, 7-Tage-Änderung (`sdmvt`) und Status, aber ohne Punkte.
- **Marktwerte** werden täglich gegen 22:00 (MESZ) aktualisiert (`mvud` im Markt).
- **Spielplan:** `/v4/competitions/1/matchdays` enthält die komplette Saison (34 × 9 Spiele) mit
  Anstoß, Ergebnis und Status.
- **Punkte:** Spielerdetails enthalten `ph` (Punkte der letzten Spieltage, neuester zuerst), `tp`,
  `ap`; `/performance` hat die Punkte je Spieltag über mehrere Saisons (~70 KB je Spieler).
- **Status `st`:** in den Daten 0/1/2/4 (fit/verletzt/angeschlagen/Aufbautraining); in der
  Top-Liste hat `st` eine andere Bedeutung.
- **Spieltagspunkte (Phase 3):** `/v4/competitions/1/teams/{teamId}/teamcenter?dayNumber=N`
  liefert die Punkte aller eingesetzten Spieler eines Vereins in diesem Spieltag – 2 Requests pro
  Spiel statt ~550 Einzelabrufen. Nicht aufgeführte Spieler wurden nicht eingesetzt.

## Worker (Phase 3)

| Job | Zeit (Europe/Berlin) | Requests | Inhalt |
|---|---|---|---|
| `sync_players` | 07:00, Nachholen 13:00 | 19 | Tabelle + 18 Teamprofile → `teams`, `players`, `market_values`, `player_stats_daily` |
| `sync_points` | 07:20 und 23:45 | 1 + 2 je neuem Spiel | Teamcenter je beendetem Spiel → `matchday_points`; Spiele der letzten 3 Tage erneut (Korrekturen) |
| `matchday_report` | 07:35 und 23:55 | 4–6 je Lauf | nach `sync_points`: Ranking + Teamcenter des neuen Spieltags + OpenLigaDB-Bestätigung → `matchday_results`; Telegram-Bilanz (Punkte, Δ Platz, Top/Flop, verpasste Punkte) wenn `notifications.matchday_report.enabled`; einmaliger Backfill fehlender früherer Spieltage (max. 2/Lauf) |
| `backfill_market_values` | einmalig nach dem ersten Abgleich | ~460 | 92 Tage Marktwertverlauf je Spieler |
| `sync_players` + `notify_squad` (Abend) | 22:45 | 19 + 1 | direkt nach dem Kickbase-Marktwert-Update (~22:00) |
| `notify_squad` | nach jedem Spieler-Abgleich | 1 | Telegram-Meldung zu eigenen Spielern (siehe unten) |
| `purge_cache` | sonntags 04:00 | 0 | abgelaufene Cache-Einträge älter als 7 Tage |
| `collect_news` (+ `daily_briefing` morgens) | 07:40, 12:40, 18:40 | kicker-RSS 1, KI 1–3 | kicker-Meldungen → KI-Auswertung → `news_reports`, Telegram zu eigenen Spielern; Briefing nach dem Morgenlauf |
| `collect_league_transfers` + `track_offers` | alle 2 h (:10) | 1 (erster Lauf ~8) + 1–2 | Liga-Transfers (`activitiesFeed`, `filter=15`) → `league_transfers` mit Marktwert am Kauftag; danach eigene Gebote vom Markt → `my_offers`, beendete auswerten (Kader, Siegerpreis) |
| `log_recommendations` | 09:15 (nur nach erfolgreichem `sync_players`, sonst beim Nachholen) | 0 (Cache) – wenige | Protokoll aller Angebote und Kaderspieler mit Score → `recommendation_log` (Bilanz-Seite) |
| `collect_lineups` | 07:30 und 23:50 | 1 + 1 je neu beendetem Spiel | Startelf/Bank je Spiel (`/v4/matches/{id}/details`) → `match_lineups`; jedes Spiel nur einmal |
| `collect_lineup_predictions` | 08:45 und 17:00 | LigaInsider 18 (2 s Abstand) | voraussichtliche Startelf + Positionskampf-Alternativen → `lineup_predictions`; Fehler, wenn kein Verein lesbar |
| `collect_injuries` | 09:00 | LigaInsider 1 | Verletzte/Gesperrte/Angeschlagene → `injuries` |
| `deadline_warning` | alle 30 min | 1 je Lauf, 4 im 2-h-Fenster | ~2 h vor der Deadline einmal: Ausfallrisiko, leere Plätze, Kaderlücken, auslaufende Gebote, Datenhinweise |
| Telegram-Fragen (Phase 7b) | dauerhaft (Long-Polling) | je Frage KI 1–4 | nur eigene Chat-ID, nur lesende Werkzeuge, Zahlenprüfung, eigenes Tageslimit |

Beim Start holt der Worker Verpasstes nach. Jeder Lauf steht mit Status und Request-Zahl in
`job_runs`. Der Worker-Client liefert nie alte Daten aus dem Cache und fragt höchstens einmal pro
Sekunde.

## Phasen

| Phase | Inhalt |
|---|---|
| 0 | Grundgerüst: uv-Projekt, Compose (`db`, `app`, `worker`, `proxy`), optionaler Caddy (Standard-HTTPS oder IONOS-DNS-01), `.env`, Login, `/health`, PWA, CI |
| 1 | Kickbase-Client (nur lesend), Token-/Response-Cache in Postgres, Rate-Limit, Probe-Skript, Fixtures, Modelle |
| 2 | „Mein Team“: Spielfeld mit Aufstellung, Kader mit Marktwert und 7-Tage-Trend, Kopfzeile Budget/Teamwert/Platz, Refresh |
| 3 | Worker sammelt täglich Marktwert/Punkte/Status aller Bundesliga-Spieler; Spieler-Detailseite mit Charts |
| 4 | OpenLigaDB-Spielplan, Score-Modell, Ansichten Kaufen / Verkaufen / Beste Elf |
| 5 | Telegram-Benachrichtigungen zu eigenen Spielern (starke Marktwertsprünge, Statusänderungen) |
| KI | Mistral-Anbindung mit harten Limits, kicker-Meldungen, Morgen-Briefing |
| 6a/6b | Kaufen-Seite (Filter, Deadline, Gebotsempfehlung), Bilanz und eigene Gebote |
| 7 | „Wer spielt?“: Startquote, LigaInsider-Prognose und Verletzte, Startchance, Score-Bestandteil Einsatz, Deadline-Warnung |
| 7b | „Frag dein Dashboard“ per Telegram |
| 7c (Block C4, erledigt 13.10.2026) | Aufstellungsindex (intern `xp`) = Startchance × Formrang × Programm; relative Kennzahl 0–100, keine Punkteprognose. Beste Elf und Kaufen sortieren nach Index bzw. Index/Mio., Spielerseite zeigt die Aufschlüsselung; Wettquoten (Phase 9 / Block C6) sind geparkt, bis eine Quelle ohne Account gefunden ist (Hook in `app/services/xp.py`) |

Details zu Phase 6–9 in `docs/roadmap.md`; offene Punkte und nächste Schritte in `HANDOFF.md`.

## Ordnerstruktur

```
app/
  main.py, config.py, auth.py, secret_files.py, log.py
  db/          models.py, session.py, stores.py, upsert.py
  kickbase/    client.py, cache.py, models.py, league.py, errors.py, calendar.py
  openligadb/  client.py, models.py
  llm/         client.py, config.py, usage.py, factory.py, errors.py (Mistral, harte Limits)
  news/        kicker.py, extract.py, matching.py, service.py
  lineups/     ligainsider.py (Scraping, Taktgeber), service.py (Prognosen, Verletzte, Hinweise)
  bot/         polling.py, conversation.py, tools.py (Telegram-Fragen, nur lesend)
  notify/      events.py, telegram.py
  services/    team.py, players.py, form.py, programme.py, scoring.py, lineup.py,
               recommendations.py, transfers.py, offers.py, track_record.py, briefing.py,
               sell_guide.py, player_ai.py
  web/         routes/, templates/ (Jinja2 + HTMX), static/ (CSS, Fonts, htmx, Chart.js, Icons, SW)
  worker/      main.py (APScheduler), jobs.py, news_jobs.py, runner.py
alembic/       Migrationen
config/        scoring.yaml, team_mapping.yaml, ai.yaml, notifications.yaml, ligainsider.yaml
docker/        app.Dockerfile, app-entrypoint.sh, caddy/
docs/          kickbase-openapi.yaml, plan.md, roadmap.md, images/ (Banner); Live-Screenshots nur lokal unter artifacts/
scripts/       probe.py, hash_password.py, screenshot.py
tests/         fixtures/ (anonymisiert), Unit- und Integrationstests
```

## Datenbankschema

**Stammdaten Bundesliga:** `teams` (id = Kickbase-tid, name, image, updated_at), `players` (id,
name, team_id, position, status, market_value, image, updated_at).

**Zeitreihen:**
- `market_values` (player_id, day, value), PK (player_id, day): täglich, plus einmaliger Backfill
- `player_stats_daily` (player_id, day, status, team_id, market_value_change_7d)
- `matchday_points` (season, matchday, player_id, points, team_id, opponent_id, is_home, kickoff)
- Spielplan/Ergebnisse von OpenLigaDB liegen im `api_cache` (kein eigenes Modell).

**Ligabezogen (`league_id`):**
- `league_transfers` (activity_id PK, player_id, price, by_me, at, market_value): Liga-Transfers
  aus dem `activitiesFeed` (Typ 15), Marktwert am Kauftag aus `market_values` (Phase 6a)
- `my_offers` (offer_id PK = `player_id@listed_at`, player_id, amount, asking_price, market_value,
  first_seen, last_seen, expires_at, outcome, final_price, evaluated_at): eigene Gebote und ihr
  Ausgang (Phase 6b)
- `recommendation_log` (day, view, player_id, score, recommended, price, market_value,
  components), unique (league_id, day, view, player_id): täglicher Schnappschuss aller Angebote
  und Kaderspieler mit Score (Phase 6b)

**Wer spielt? (Phase 7):** `match_lineups` (match_id, player_id, season, matchday, team_id,
started, kickoff) – nur Spieler im Spieltagskader; `lineup_predictions` (team_id, player_id oder
null, player_name, status `startelf`/`alternative`, fetched_at); `injuries` (team_id, player_id
oder null, kind, reason, since, fetched_at). LigaInsider-Daten älter als 36 h werden ignoriert.

**KI/News:** `ai_usage` (at, task, model, status, prompt_tokens, completion_tokens, cost_usd),
`news_items` (guid PK, source, title, teaser, link, categories, published_at, processed_at),
`news_reports` (news_guid, player_id, kind, status, absence, expected_return, notified_at).

**Infrastruktur:** `api_cache` (key, payload jsonb, fetched_at, expires_at), `auth_tokens`
(provider, token, expires_at), `kv` (z. B. aufgelöste league_id), `job_runs` (job, started_at,
finished_at, status, requests, detail).

## Kickbase-Client

- `httpx.AsyncClient` gegen `https://api.kickbase.com`.
- **Nur lesend:** `_request()` erlaubt `GET` und genau `POST /v4/user/login`, sonst
  `ReadOnlyViolation`. Zusätzlich ein httpx-Request-Hook als zweites Sicherheitsnetz.
- **Auth:** Zugangsdaten werden nur beim Login aus `/run/secrets/` gelesen. Token mit Ablauf in
  `auth_tokens` (geteilt von app und worker). Bei 401 einmal neu einloggen, beim zweiten 401
  `KickbaseAuthError`.
- **Schonend:** je Prozess 1 paralleler Request (app + worker zusammen höchstens 2), mindestens
  250 ms Abstand, Retries mit Backoff bei 429/5xx nur für GET.
- **Cache (`api_cache`):** Live-Daten 10 min, Spielerdetails 30 min, Stammdaten 24 h. Refresh
  umgeht den Cache höchstens einmal pro 60 s.
- **LeagueResolver:** sucht den konfigurierten Namen in `GET /v4/leagues/selection`, cacht die ID in `kv`;
  nicht gefunden oder mehrdeutig → Fehler mit Liste der vorhandenen Ligen.
- **Abrufvolumen Worker:** siehe Abschnitt „Worker“ – täglich ~110 Kickbase-Requests (davon ~48
  Deadline-Prüfung, ~38 Spieler-Abgleich, 12 Transfers), nach Spielen 3 je Spiel, einmalig ~460
  für den Marktwert-Backfill (1 req/s).

## Score-Modell (umgesetzt in Phase 4, Gewichte in `config/scoring.yaml`)

```
F  Form      gewichteter Schnitt der Punkte der letzten 5 Spieltage (5-4-3-2-1, nicht gespielt = 0),
             Perzentil innerhalb der Position → 0…1
T  Trend     Marktwert-Änderung 7 Tage in %, gekappt auf ±15 %, linear auf 0…1 (0,5 = unverändert)
P  Programm  1 − Ø Gegnerstärke der nächsten 3 Spiele (Punkte/Spiel, ±10 % Heim/Auswärts;
             bis Spieltag 4 mit Vorjahrestabelle gemischt)
E  Einsatz   Startchance 0…1 (LigaInsider-Prognose, Verletzungen, Startquote; Phase 7)
K  Klasse    Marktwert-Rang in der Position, gemischt mit dem Saison-Ø (dessen Anteil wächst mit
             den Einsätzen, höchstens 50 % nach 8 Spielen) → 0…1 (seit 09.10.2026)
M  Status    Malus: fit 0 · angeschlagen 0,15 · Aufbautraining 0,4 · verletzt/gesperrt 1,0

Score = 100 · clamp(w_F·F + w_T·T + w_P·P + w_E·E + w_K·K − w_S·M, 0, 1)
```

| Ansicht | w_F | w_T | w_P | w_E | w_K | w_S | Logik |
|---|---|---|---|---|---|---|---|
| Kaufen | 0,25 | 0,25 | 0,15 | 0,15 | 0,20 | 1,0 | Transfermarkt, Preis ≤ Budget, Score ≥ 40, sortiert nach Score je Mio € |
| Verkaufen | 0,25 | 0,25 | 0,10 | 0,10 | 0,30 | 1,0 | eigene Spieler mit Trend < −3 % oder Startchance < 50 %, schlechtester zuerst |
| Beste Elf | 0,45 | 0 | 0,20 | 0,15 | 0,20 | 1,0 | ohne Verletzte/Gesperrte, beste erlaubte Formation nach Score-Summe |

Im Minus empfiehlt „Ins Plus kommen“ den Verkauf mit dem geringsten Punkteverlust
(`sell_guide` in `config/scoring.yaml`).

## Deployment

`docker compose up -d --build` mit der in `.env` gewählten Zugriffsvariante. Standardmäßig
lokal auf Loopback; optional Caddy mit Standard-HTTPS, eigener TLS-Proxy oder IONOS DNS-01.
VPN und interne DNS-Auflösung sind frei wählbare Infrastruktur, siehe [Self-Hosting](self-hosting.md).

## Empfehlungen (Phase 4)

- **Programm:** Spielplan und Tabelle von OpenLigaDB (`getcurrentgroup`, `getmatchdata`,
  `getbltable` für aktuelle und vorige Saison; ~8 Requests pro Stunde, gecacht). Vereine werden
  über `config/team_mapping.yaml` zugeordnet, für Aufsteiger per Namensabgleich mit Warnung.
- **Form:** aus `matchday_points` – nur Spieltage, die der Worker schon abgeglichen hat.
- **Trend:** Kader aus Kickbase (`sdmvt`), Transfermarkt aus der Marktwert-Zeitreihe.
- **Kaufen:** Transfermarkt ohne eigene Angebote, Preis ≤ Budget nach offenen Geboten,
  Score ≥ `min_score`, sortiert nach Score pro Million.
- **Verkaufen:** eigene Spieler mit Trend unter `trend_threshold_percent` oder nicht fit,
  schwächster Score zuerst, mit Gewinn/Verlust seit Kauf.
- **Beste Elf:** Ausfälle raus, je erlaubter Formation die Besten pro Position; gewählt wird die
  Formation mit den wenigsten leeren Plätzen, dann den wenigsten Notbesetzungen und der
  höchsten Aufstellungsindex-Summe. Spielbarer Ersatz mit Formindex null darf einen freien Platz füllen.
- Jede Empfehlung zeigt die Beiträge (Form, Trend, Programm, Status) und aufklappbar Wert,
  Gewicht und Begründung. Fällt OpenLigaDB aus, zählt das Programm neutral (Hinweis auf der Seite).

## Benachrichtigungen (Phase 5)

- **Kanal:** Telegram-Bot (@Kickbase_AI_Helper_Bot). Token als Docker Secret
  `./secrets/telegram_bot_token` (gitignored, 0600), Chat-ID `TELEGRAM_CHAT_ID` in `.env`.
- **Wann:** nach jedem Spieler-Abgleich (07:00, 22:45, Nachholen 13:00 / beim Start).
- **Was** (Schwellen in `config/notifications.yaml`):
  - Marktwert eines eigenen Spielers: Tagesänderung (`tfhmvt`) mindestens ±10 % oder ±1,5 Mio. €.
    Übliche Tagesbewegungen liegen meist unter 5 %, 10 % sind selten und handlungsrelevant; der
    absolute Wert fängt teure Spieler ab.
  - Status eines eigenen Spielers: jede Änderung (z. B. fit → angeschlagen, verletzt → fit).
- Eine gesammelte Nachricht pro Lauf mit Links zur Spielerseite, jedes Ereignis nur einmal
  (`kv`: `notify.sent`, 14 Tage), beim ersten Lauf wird nur der Status-Stand gemerkt.
- Testnachricht: `docker compose exec worker python -m app.notify.check`.
