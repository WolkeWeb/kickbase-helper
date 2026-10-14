# Maintainer-Dateien

Dieser Ordner und einige Dateien im Repository gehören zum Entwicklungs- und Betriebsablauf
des Maintainers. Für eine eigene Installation werden sie **nicht** benötigt – dafür reicht
[Self-Hosting](../self-hosting.md).

| Datei / Ordner | Zweck |
|---|---|
| `docs/maintainer/deployment.md` | Updates auf der Maintainer-VM, Claude Remote Control, Claude-Bridge |
| `docs/maintainer/audit-*.md`, `iphone-product-review-*.md` | Datierte Prüfberichte |
| `deploy/maintainer/claude-remote.service` | Beispiel-Unit für Claude Code Remote Control |
| `deploy/claude-bridge/`, `compose.claude.yaml` | Optionale, experimentelle Claude-Bridge für den Telegram-Bot |
| `AGENTS.md`, `CLAUDE.md`, `HANDOFF.md` | Regeln und Arbeitsstand für KI-Coding-Agenten |
| `.claude/`, `.opencode/`, `opencode.json`, `scripts/agent-loop.sh` | Konfiguration der Agenten (Claude Code, opencode) |

Pfade wie `/opt/kickbase-helper/secrets/` oder der User `claude` in diesen Dateien beschreiben
die Installation des Maintainers und sind für andere nur Beispiele.
