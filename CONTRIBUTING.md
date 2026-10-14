# Mitwirken

*English summary below.*

Danke für dein Interesse! Kickbase Helper ist ein privates Single-User-Dashboard für
das Self-Hosting. Fehlerberichte, Verbesserungen und Ideen sind willkommen – bitte vorher
die folgenden Punkte lesen.

## Grundsätze

- **Kickbase nur lesend.** Beiträge, die Gebote, Verkäufe, Aufstellungsänderungen oder
  andere schreibende Kickbase-Aktionen hinzufügen, werden nicht angenommen.
- **Schonender Datenabruf.** Bestehende Limits und Caches nicht umgehen; keine Logins,
  Paywalls oder Captchas fremder Seiten aushebeln.
- **Keine privaten Daten.** Keine Zugangsdaten, `.env`-Inhalte, Datenbank-Dumps,
  Live-Screenshots oder Namen anderer Liga-Manager in Issues, PRs, Commits oder Fixtures.
  Details: [SECURITY.md](SECURITY.md#beiträge-und-testdaten).
- **Sicherheitslücken** nie in einem öffentlichen Issue, sondern über die
  [private Meldung](https://github.com/WolkeWeb/kickbase-helper/security/advisories/new).

## Issues

- Vor einem neuen Issue kurz nach bestehenden suchen.
- Für Fehler und Wünsche die Vorlagen verwenden; Logs vorher auf private Daten prüfen.
- Größere Änderungen bitte zuerst als Issue besprechen, bevor viel Arbeit in einen PR fließt.

## Entwicklung

Voraussetzungen: Python 3.12, [uv](https://docs.astral.sh/uv/), optional Docker und
PostgreSQL 16 für die Postgres-Tests.

```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest -q -n auto                  # Fixtures statt Live-APIs
uv run python scripts/check_public_tree.py
```

- Tests laufen ausschließlich gegen gespeicherte, anonymisierte Fixtures unter
  `tests/fixtures/` – nie gegen Kickbase oder andere Live-Seiten.
- Feldnamen der Kickbase-API stehen in `docs/kickbase-openapi.yaml`; zum Nachschlagen
  `uv run python scripts/spec.py <pfad>` verwenden.
- Datenbankänderungen immer als neue Alembic-Revision; bestehende Revisionen nicht ändern.
- UI-Änderungen müssen bei 375 px Breite ohne horizontales Scrollen funktionieren
  (hell und dunkel). Screenshots nur mit synthetischen Daten.
- Empfehlungen bleiben erklärbar: neue Score-Bestandteile mit Gewicht in
  `config/scoring.yaml` und sichtbar in der Oberfläche.
- Weitere Projektregeln und Architektur: [AGENTS.md](AGENTS.md) und [README](README.md).

## Pull Requests

1. Fork bzw. eigenen Branch anlegen (`feat/...`, `fix/...`), nicht auf `main` arbeiten.
2. Kleine, fokussierte Commits; ein PR pro zusammenhängender Änderung.
3. Die [PR-Vorlage](.github/pull_request_template.md) ausfüllen: Was, warum, wie getestet,
   Screenshots bei UI-Änderungen.
4. Die CI muss grün sein. Merges erfolgen per Squash.

## Lizenz

Mit einem Beitrag stimmst du zu, dass er unter der [MIT-Lizenz](LICENSE) des Projekts
veröffentlicht wird. Es gilt der [Verhaltenskodex](CODE_OF_CONDUCT.md).

---

## English summary

Contributions are welcome. Key rules: Kickbase access stays **read-only** (no bids, sales
or lineup changes); never post credentials, `.env` contents, database dumps, live
screenshots or other managers' names; report security issues privately via
[GitHub security advisories](https://github.com/WolkeWeb/kickbase-helper/security/advisories/new).
Run `uv sync`, `uv run ruff check .`, `uv run ruff format --check .` and
`uv run pytest -q -n auto` before opening a PR, and fill in the PR template. Tests use
stored fixtures only, never live APIs. Issues and PRs may be written in German or English.
By contributing you agree to license your work under the [MIT License](LICENSE).
