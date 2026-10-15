# Entwicklung

Kurzfassung und Befehle stehen in der [README](../README.md#entwicklung-und-beiträge). Beiträge: [CONTRIBUTING.md](../CONTRIBUTING.md). Arbeitsweise und Regeln: [`AGENTS.md`](../AGENTS.md), Stand in [`HANDOFF.md`](../HANDOFF.md), Ausbauplan in [`roadmap.md`](roadmap.md), Maintainer-Ablauf in [`maintainer/`](maintainer/README.md).

## Tests, Screenshots, Regeln

- Tests laufen gegen anonymisierte Fixtures unter `tests/fixtures/` (Kickbase, LigaInsider);
  Postgres-Tests zusätzlich mit `TEST_POSTGRES_URL`.
- Mobile Screenshots und Prüfung auf horizontales Scrollen: `scripts/screenshot.py --base URL`;
  Ausgabe lokal unter `artifacts/screenshots/` (gitignored). Live-Screenshots mit privaten
  Liga- und Kontodaten gehören nicht ins Repository.
- README-Screenshots aus synthetischen Daten (ohne Kickbase-Zugang) neu erzeugen:
  ```bash
  uv run python -m scripts.demo_server &    # Port 8099, Passwort „demo“
  uv run --with playwright python scripts/readme_screenshots.py
  ```
  Das Ergebnis landet in `docs/images/demo/`; nur dieser Ordner und das Banner dürfen Bilder
  enthalten (`scripts/check_public_tree.py`).

## Projektstruktur

```
app/
  kickbase/     Client (nur lesend), Cache, Modelle      lineups/   LigaInsider, Startelf-Prognosen
  services/     Scores, Empfehlungen, Guide, Briefing    bot/       Telegram-Fragen (Lagebild, Werkzeuge)
  worker/       Sammeljobs und Zeitplan                  llm/       Mistral mit harten Limits
  web/          Routen, Templates, CSS, PWA              news/      kicker-Meldungen
config/  docs/  scripts/  tests/  alembic/  docker/
```
