---
description: Auftrag erledigen – Claude plant und steuert, opencode programmiert (PR, Review, Merge, Deploy)
argument-hint: "[Aufgabe, z. B. „C2 Angebote auf eigene Spieler“] – leer = offene Checkboxen weitermachen"
---

Auftrag vom Nutzer: $ARGUMENTS

Du bist **Planer und Bauleiter**, nicht Programmierer. Den Code schreibt opencode mit dem
günstigen Modell (`opencode-go/minimax-m3`). Du planst, startest opencode pro Schritt, prüfst das
Ergebnis und erledigst PR, Merge und Deploy. Es gelten alle Regeln aus `AGENTS.md`.

## Sparregeln (wichtig – dein Kontingent ist teurer als opencode)

- Keinen Anwendungscode (`app/`, `tests/`) selbst lesen oder schreiben. Kleinste Ausnahme: ein
  Schritt scheitert zweimal – dann nur die Fehlerzeilen ansehen, um opencode gezielt anzuweisen.
- opencode-Logs nie komplett lesen: nur `tail -n 30`.
- Prüfen nur mit kurzen Ausgaben: `git log -1 --oneline`, `git status --short`,
  `git diff --stat`, `uv run pytest -q -n auto 2>&1 | tail -n 3`.
- `HANDOFF.md` gezielt lesen (Kopf + Abschnitt „Plan“), `docs/roadmap.md`/`docs/plan.md` nur per
  `grep` mit den passenden Stichworten.
- Zwischendurch im Chat nur kurze Statuszeilen (z. B. „Schritt 2/4 fertig, Tests grün“).

## 1. Vorbereiten

1. `git status --short` muss leer sein. Sonst: kurz zeigen, was offen ist, und den Nutzer fragen.
2. Ist der Auftrag leer: auf dem aktuellen Branch mit den offenen Checkboxen unter „Plan“
   weitermachen (weiter bei 2.). Steht dort nichts offen, das kurz sagen und aufhören.
3. Sonst planen:
   - `git checkout main && git pull --ff-only`, dann `git checkout -b feat/<kurzer-name>`.
   - Unter „Plan“ in `HANDOFF.md` einen Block mit 3–6 kleinen Checkboxen schreiben. Jede ist in
     einer opencode-Session machbar und nennt betroffene Dateien und wie getestet wird. Letzte
     Checkbox: „PR öffnen, einmal reviewen, Findings fixen, Auto-Merge, deployen“.
   - „Aktiver Agent“ auf „Claude Code (/auftrag) + opencode“ setzen, committen, `git push -u origin HEAD`.
   - Den Plan in 3–6 Zeilen im Chat zeigen und **sofort weitermachen** (nicht auf Freigabe warten).
   - Nur fragen, wenn der Auftrag unklar ist, den Regeln widerspricht oder eine
     Produktentscheidung außerhalb von `AGENTS.md`/Phasenplan braucht.

## 2. Schritte abarbeiten (alle Checkboxen außer der letzten)

Für jede offene Checkbox:

1. opencode starten – mit dem Bash-Tool **im Hintergrund** (`run_in_background`), dann auf die
   Fertig-Meldung warten:

   ```bash
   export PATH="$HOME/.opencode/bin:$PATH" && \
     mkdir -p .agent-logs && opencode run --agent build --model opencode-go/minimax-m3 --auto \
       --title "auftrag schritt N" "<SCHRITT-PROMPT>" > .agent-logs/$(date +%Y%m%d-%H%M%S)-schritt-N.log 2>&1
   ```

   SCHRITT-PROMPT: „Arbeite genau EINEN Schritt ab: die erste offene Checkbox unter „Plan“ in
   HANDOFF.md, nach dem Schritt-Ablauf in AGENTS.md. Arbeite zügig: sobald die Tests grün sind,
   sofort HANDOFF.md abhaken, committen und pushen. Danach aufhören. Braucht der Schritt eine
   Entscheidung des Nutzers oder ist er blockiert: keinen Code ändern, Grund unter „Offene
   Probleme“ in HANDOFF.md eintragen und aufhören.“ Hast du einen konkreten Hinweis (z. B. aus
   einem vorigen Fehlversuch), hänge ihn an.

2. Prüfen: neuer Commit da? Checkbox in `git show HEAD:HANDOFF.md` abgehakt? Arbeitsverzeichnis
   sauber? `uv run pytest -q -n auto 2>&1 | tail -n 3` grün? (Ruff prüft opencode selbst; bei Zweifel
   `uv run ruff check . -q && uv run ruff format --check . -q`.)

3. Wenn nicht:
   - Änderungen uncommittet → einmal opencode mit „Nichts neu bauen: git status/diff ansehen,
     Tests laufen lassen, nur Fehler beheben, dann Checkbox abhaken, Eintrag unter „Aktueller
     Stand“, committen, pushen.“
   - Tests rot oder Schritt unfertig → `tail -n 30` vom Log, dann **ein** weiterer opencode-Lauf
     mit konkreter Anweisung.
   - Hat opencode „Offene Probleme“ eingetragen (Entscheidung nötig) → den Nutzer im Chat fragen,
     Antwort als Hinweis an den nächsten Lauf geben.
   - Klappt es nach zwei Versuchen nicht: kurz berichten (was, woran es hängt, Logdatei) und
     aufhören.

## 3. Abschluss (letzte Checkbox)

1. PR öffnen: `gh pr create --base main --title "…" --body-file <datei>`. Beschreibung selbst
   schreiben (Was, warum, wie getestet) aus `git log main..HEAD --oneline` und
   `git diff main...HEAD --stat`. Bei UI-Änderungen opencode Screenshots erzeugen lassen.
2. Review **einmal**, günstig über opencode:
   „Nutze den Subagent review für den Diff `git diff main...HEAD`. Behebe danach alle
   berechtigten Findings, Tests grün, committen und pushen. Antworte am Ende mit einer
   Kurzliste: Finding → behoben/verworfen (Grund).“ Die Kurzliste (`tail -n 30`) in den PR als
   Kommentar (`gh pr comment`).
3. Checkbox abhaken, „Aktiver Agent“ auf „keiner“, kurzer Eintrag unter „Aktueller Stand“,
   committen, pushen.
4. Auf die CI warten und erst bei Grün mergen – im Hintergrund (`run_in_background`), dann auf
   die Fertig-Meldung warten:
   `sleep 20 && gh pr checks --watch --interval 30 --fail-fast && gh pr merge --squash --delete-branch; echo exit $?`
   Kein `--auto`: Das Repo ist privat im Gratis-Plan und hat keine Pflicht-Checks, `--auto` würde
   sofort mergen und die CI abbrechen. CI rot → Fehler über opencode beheben lassen, pushen,
   Befehl wiederholen.
5. Deployen nach `docs/maintainer/deployment.md` und Health-Check.
6. Kurze Schlussmeldung im Chat: was jetzt kann, PR-Link, offene Punkte. Empfehlen, für den
   nächsten Auftrag eine neue Session zu öffnen.

## Nie

- Gesperrte Secrets (`/opt/kickbase-helper/secrets/`) lesen, ausgeben oder ändern; Secret-Werte
  aus `docker compose config`/`docker inspect` wiedergeben.
- Direkt auf `main` pushen, Force-Push, deployte Migrationen ändern.
- Schreibende Kickbase-Requests; Request-Schleifen gegen die Live-API.
