# Kickbase-Helper – Projektregeln (AGENTS.md)

> Steuert die KI-Agenten des Maintainers. Pfade wie `/opt/kickbase-helper/secrets/` und der User
> `claude` beziehen sich auf die Maintainer-VM; Überblick: `docs/maintainer/README.md`.

Privates, mobil-first Dashboard für einen Kickbase-Account: Aufstellung, Kader, Budget,
Marktwert-Trends, Spielerform und nachvollziehbare Kauf-/Verkaufsempfehlungen. Single-User,
läuft als Docker-Stack im Homelab. Kickbase-Zugriff **nur lesend**.

Diese Datei gilt für alle Agenten (Claude Code und opencode). Das *Was* und *Wie* der Phasen
steht in `docs/plan.md` und `docs/roadmap.md`, der aktuelle Arbeitsstand in `HANDOFF.md`.

## Liga

- Relevant ist **nur die konfigurierte Liga** (`KICKBASE_LEAGUE_NAME` in `.env`). Kein Liga-Umschalter.
- `leagueId` beim Start per Namen aus `/v4/leagues/selection` ermitteln und cachen
  (`/v4/user/me` enthält keine Ligen); nicht gefunden → klare Fehlermeldung mit den verfügbaren
  Ligen, nie einfach die erste nehmen.
- Liga-bezogene Tabellen haben trotzdem eine `league_id`-Spalte.

## Stack

- Python 3.12, FastAPI, Jinja2 + HTMX, Chart.js
- PostgreSQL 16, SQLAlchemy 2 (async) + Alembic
- `httpx` für alle HTTP-Clients
- `uv` (Deps), `ruff` (Lint/Format), `pytest` (Tests)
- Kern: `app` (Web), `worker` (APScheduler), `db`. Optionaler `proxy` (Caddy) per
  `compose.https.yaml` oder `compose.ionos.yaml`; lokal oder eigener Proxy ohne Caddy.

## Datenquellen

- **Kickbase API** (inoffiziell): Spec in `docs/kickbase-openapi.yaml`. Feldnamen sind kurz
  (`tkn`, `lins`, `mv` …) – immer in der Spec nachsehen, nie raten. Die Spec hat ~115k Tokens:
  **nie direkt lesen**, sondern `uv run python scripts/spec.py <pfad>` bzw.
  `uv run python scripts/spec.py --list <filter>`.
- **OpenLigaDB** (`https://api.openligadb.de`): Spielplan/Ergebnisse/Tabelle, kostenlos, ODbL.
- **Datenabruf für die eigene Installation:** LigaInsider, kicker,
  Transfermarkt, Quoten-Vergleichsseiten. Regeln:
  - nur lesend; keine Logins, Paywalls oder Captchas umgehen
  - schonend: höchstens 1 Request / 2 s pro Domain, Ergebnisse cachen, feste Zeitfenster im
    Worker, eigener User-Agent
  - Daten nur für dieses Dashboard, nicht weitergeben oder veröffentlichen
  - Parser defensiv: Seitenänderungen erkennen, sichtbar machen und loggen – nie still falsche
    oder alte Daten als aktuell ausgeben
  - Tests mit gespeicherten HTML-Fixtures unter `tests/fixtures/`, nie gegen die Live-Seite
- **Wettquoten:** bevorzugt eine Quelle ohne Account (Scraping); API-Token (`./secrets/odds_api_key`)
  ist nur optionaler Zusatz und wird automatisch genutzt, wenn die Datei existiert.
- **KI:** Mistral Gratis-Plan (Key `./secrets/llm_api_key`), anbieterneutral über die
  OpenAI-kompatible Schnittstelle mit `httpx`; Standard `ministral-14b-latest`, Ausweichmodell
  `ministral-8b-latest`. Harte Grenzen in `config/ai.yaml`. Weil die Gratis-Stufe mit Eingaben
  trainieren darf: nur Bundesliga-Daten und eigener Kader, nie Namen anderer Manager. Ohne Key
  oder bei Fehlern muss alles ohne KI weiter funktionieren.

## Harte Regeln

1. **Kickbase nur lesend.** Der Client erlaubt ausschließlich `GET` plus `POST /v4/user/login`;
   jede andere Methode wirft eine Exception. Keine Gebote, Verkäufe, Aufstellungsänderungen,
   Bonus-Abholungen.
2. **Schonender Zugriff:** max. 2 parallele Requests, Cache (Live-Daten 10 min, Spielerdetails
   30 min, Stammdaten 24 h), Token cachen statt neu einloggen. Bei 401 einmal neu einloggen, dann
   abbrechen.
3. **Secrets, zwei Stufen:**
   - **Gesperrt:** `/opt/kickbase-helper/secrets/` (`kickbase_email`, `kickbase_password`,
     `ionos_token`). Diese Dateien nie lesen, ausgeben, kopieren oder verändern; sie werden als
     Docker Secrets eingebunden. Fehlt eine Datei → nachfragen, nicht selbst anlegen.
   - **Frei:** `.env` im Repo (gitignored) und `./secrets/` im Repo (Telegram-Bot, optional
     `llm_api_key`; gitignored, `.dockerignore`, Modus 0600). `.env.example` mit Platzhaltern
     pflegen.
   - Neue Installationen nutzen standardmäßig `./secrets/`; die bestehenden gesperrten
     Dateien bleiben über konfigurierbare Pfade eingebunden. IONOS ist nur ein optionaler
     Modus. `scripts/setup.py` niemals über eine bestehende Installation laufen lassen.
   - Werte aus allen Stufen nie loggen, nie committen, nie in PR-Texte.
4. **Fixtures statt Live-API in Tests:** echte Antworten einmalig über `scripts/probe.py` holen,
   anonymisieren (Namen/IDs anderer Manager ersetzen) und unter `tests/fixtures/` ablegen.
5. **Mobil zuerst:** jede Seite bei 375 px ohne horizontales Scrollen; große Touch-Ziele,
   Dark-Mode, PWA.
6. **Empfehlungen sind erklärbar:** jeder Score zeigt seine Bestandteile (Form, Trend, Programm,
   Einsatz, Klasse, Status) mit Gewichten; Gewichte in `config/scoring.yaml`.

## Arbeitsweise

Beide Agenten arbeiten selbstständig auf dieser VM als User `claude` (passwortloses `sudo`);
fehlende Tools werden selbst installiert.

- Jeder Block (zusammengehörige Checkboxen in `HANDOFF.md`) auf eigenem Branch (`feat/...`);
  nie direkt auf `main` pushen, kein Force-Push. **Ein PR pro Block**, nicht pro Schritt.
- Pro Block: alle Schritte auf dem Branch abarbeiten → PR öffnen → **einmal** reviewen
  (opencode: Subagent `review`; Claude Code: `/pr-review-toolkit:review-pr code`; beide nur
  lesend) → Findings beheben → auf die CI warten und erst bei Grün mergen:
  `sleep 20 && gh pr checks --watch --interval 30 --fail-fast && gh pr merge --squash --delete-branch`
  (alle ausgelösten Checks abwarten; das `&&` verhindert einen Merge bei roter CI) → deployen
  (`docs/maintainer/deployment.md`) → kurz prüfen.
- PR-Beschreibung: Was, warum, wie getestet, Screenshots bei UI.
- Kleine, fokussierte Commits. Für breite Code-Suchen Subagents nutzen.
- Bei Unklarheiten zur API: `scripts/spec.py`, dann ggf. ein einzelner Probe-Request – keine
  Request-Schleifen gegen die Live-API.

Nur in diesen Fällen stoppen und den Nutzer fragen: ein gesperrtes Secret fehlt oder der
Kickbase-Login schlägt fehl; etwas Unumkehrbares (DB-Volume löschen, nicht rückrollbare
Daten-Migration); eine Produktentscheidung außerhalb dieser Regeln oder des Phasenplans; nach
Abschluss einer kompletten Phase (kurze Zusammenfassung, dann weiter zur nächsten Phase).

## Schritt-Ablauf (autonom)

Gearbeitet wird in kleinen, in sich abgeschlossenen Schritten. Der nächste Schritt steht immer in
`HANDOFF.md` unter „Plan“ (erste offene Checkbox).

Jeder Schritt wird in dieser Reihenfolge abgeschlossen:

1. **Umsetzen** – nur die eine offene Checkbox, klein halten.
2. **Testen** – `uv run ruff check . && uv run ruff format --check . && uv run pytest -q -n auto`
   lokal grün (bei Fehlern nur die fehlschlagenden Tests erneut laufen lassen).
3. **HANDOFF.md aktualisieren** – Checkbox abhaken, unter „Aktueller Stand“ **ein kurzer
   Eintrag** (2–4 Zeilen, Dateien + Entscheidungen).
4. **Committen + pushen** – kleiner, fokussierter Commit auf den Block-Branch.

Ist die **letzte Checkbox eines Blocks** erledigt: PR öffnen, einmal reviewen, Findings fixen,
Auto-Merge setzen, deployen (siehe „Arbeitsweise“).

Nach jedem Schritt muss der Stand in `HANDOFF.md` so vollständig sein, dass ein anderer Agent
ohne Chatverlauf sofort weitermachen kann. Wird der Kontext zu voll: keinen neuen großen Schritt
mehr anfangen, sondern `/handoff` ausführen und aufhören.

Der Review-Subagent `review` (`.opencode/agents/review.md`) nutzt `opencode-go/kimi-k2.7-code`
und ist strikt nur lesend (kein `edit`, `bash` nur für `scripts/spec.py`).

**Aufträge (Standardweg):** `/auftrag <Aufgabe>` in Claude Code – auch vom Handy über Remote
Control (Einrichtung in `docs/maintainer/deployment.md`). Claude (Sonnet) plant die Checkboxen, startet pro
Schritt opencode (MiniMax M3), prüft Commit und Tests und erledigt PR, Review (über opencode),
Merge und Deploy. Ohne Argument macht es mit den offenen Checkboxen weiter. Ablauf und
Sparregeln: `.claude/commands/auftrag.md`.
Ohne Claude: `scripts/agent-loop.sh [MAX_SCHRITTE]` arbeitet die offenen Checkboxen nur mit
opencode ab und stoppt, sobald ein Schritt blockiert ist.

## Tokens sparen

- **Eine Aufgabe pro Session.** Nach einem Block neue Session (Claude Code: `/clear`, nicht
  `/compact`). Lange Sessions mit großem Kontext sind der größte Kostentreiber.
- Nie ganze große Dateien lesen: Spec nur über `scripts/spec.py`; `uv.lock`, `docs/screenshots/`,
  `docs/images/` nicht öffnen (außer die Aufgabe verlangt es ausdrücklich).
- Gezielt suchen (grep/glob mit Pfad), dann nur die relevanten Zeilen lesen.
- Testausgaben kurz halten (`pytest -q`, bei Fehlern nur die betroffenen Tests).
- Screenshots nur bei UI-Schritten erzeugen und ansehen. Live-Screenshots bleiben lokal unter
  `artifacts/screenshots/`; veröffentlichen nur mit synthetischen Daten.
- `HANDOFF.md` kurz halten: erledigte Blöcke einzeilig unter „Erledigt (Kurzfassung)“,
  unter „Aktueller Stand“ höchstens die letzten 5 Einträge – Details stehen in Git und PRs.

## Befehle

```bash
docker compose up -d --build        # alles starten
docker compose logs -f app worker   # Logs
uv run pytest -n auto               # Tests, parallel (pro Worker eigene Postgres-DB)
uv run ruff check . && uv run ruff format .
uv run alembic upgrade head         # Migrationen
```

## CI und Repo

- Öffentliches Repository mit neuer Historie ab 14.10.2026. Alte Klone/Branches nie in dieses
  Repository pushen oder mergen. Die private Entwicklung liegt im getrennten Archiv
  `WolkeWeb/kickbase-helper-private-history`; siehe `docs/publication.md`.

- `CI` (jeder PR + `main`), zwei Jobs: „Lint & Tests“ (ruff, gitleaks über den ganzen Verlauf,
  actionlint/zizmor, shellcheck, JS-Syntax, pytest mit SQLite + PostgreSQL inkl.
  Migrations-Check) und „App-Image“ (`docker compose config`, Image bauen und starten:
  Migrationen + `/health`). Rote CI nie wegdrücken – Ursache beheben.
- Actions-Minuten sind knapp (Free-Plan, jeder Job wird auf volle Minuten aufgerundet): keine
  neuen Jobs für Kleinigkeiten, lieber Schritte in bestehende Jobs; teure Checks nur mit
  `paths`-Filter oder auf `main`.
- `Security` (wöchentlich + PRs an Abhängigkeiten/`docker/`): pip-audit, Trivy fürs App-Image.
  `Proxy-Image` baut Caddy nur bei Änderungen unter `docker/caddy/`.
- gitleaks-Fehlalarm: nur nach Prüfung einzeln per Fingerprint in `.gitleaksignore` – nie ganze
  Pfade ausnehmen. Ein echtes Secret im Verlauf: stoppen und den Nutzer fragen (Rotation).
- Actions sind auf Commit-SHAs gepinnt (Kommentar mit Version); Updates kommen von Dependabot.
  Dependabot-PRs nur auf Auftrag des Nutzers mergen.
- Merge nur per Squash; der Branch wird danach automatisch gelöscht.

## Deployment

Nach jedem Merge (einmal pro Block) auf der VM – Befehle und Details in `docs/maintainer/deployment.md`.
Immer gültig:

- Portainer sieht den Stack nur zur Übersicht – dort nichts ändern.
- `docker compose config` und `docker inspect` nur wenn nötig; falls dort ein Secret-Wert
  erscheint, die Ausgabe nie wiedergeben.
- Deployte Migrationen nie nachträglich ändern – immer eine neue Revision anlegen.

## Übergabe zwischen Agenten

- Es arbeitet immer nur ein Agent. Wer aktiv ist, steht in `HANDOFF.md` unter „Aktiver Agent“.
- Bei Arbeitsbeginn: `HANDOFF.md` lesen, „Aktiver Agent“ auf dich setzen.
- Nach jedem abgeschlossenen Schritt: Tests laufen lassen, `HANDOFF.md` aktualisieren, committen.
- Vor dem Wechsel: `/handoff` ausführen, dann pushen – **nie direkt auf `main`**, sondern auf dem
  offenen Feature-Branch bzw. über `chore/handoff-*` + PR.
