# Veröffentlichung am 14.10.2026

## Ergebnis der Prüfung

Die bisherige private Entwicklung wurde vor der Veröffentlichung untersucht:

- Beide Remote-Branches und alle 75 historischen Pull-Request-Heads wurden abgerufen;
  insgesamt 306 erreichbare Commits und 1.219 historische Text-Blobs geprüft.
- Gitleaks 8.30.1 lief mit `--all`, redigierter Ausgabe und zunächst **ohne** Ausnahmen.
  Sechs Treffer: ein Test-Key, ein früherer Platzhalter, zwei öffentliche VAPID-Schlüssel
  aus gespeicherten Webseiten sowie zwei Token-Beispiele der fremden API-Spezifikation.
  Kein bestätigter echter Zugangsschlüssel der eigenen Installation gefunden.
- Die API-Spezifikation war bytegleich zur öffentlichen Quelle
  `kevinskyba/kickbase-api-doc`, Commit `9a371a708af11155e7d36c3a1a467c9d759e1fdc`.
  Ihr JWT-Beispiel ist abgelaufen. Die Token-Beispiele wurden dennoch entfernt und die
  fehlende MIT-Lizenz beigefügt.
- Alle historischen Kickbase-JSON-Fixtures wurden auf bekannte Identitätsfelder geprüft;
  keine nicht anonymisierten Werte in diesen Feldern gefunden. Unbekannte Felder können
  durch eine solche Prüfung nicht vollständig beurteilt werden.
- 176 historische Bildversionen wurden per OCR untersucht. 45 enthielten den echten
  Liganamen; einige zeigten auch Budget/Kader. OCR ist keine vollständige Bildprüfung.
- 75 PR-/Issue-Texte, 32 Kommentare und die Review-Kommentarliste wurden geprüft und
  mit Gitleaks gescannt. Es gab keine zusätzlichen Secret-Treffer. Historische Texte
  enthielten weiterhin installationsbezogene Angaben und Screenshot-Verweise.
- Zum Auditzeitpunkt: keine Releases, keine Wiki-/Pages-/Discussions-Inhalte, keine Forks.
  Alte Actions-Logs und Build-Artefakte wurden nicht vollständig inhaltlich geprüft.

## Bereinigter Veröffentlichungsstand

Persönliche Liga-/Host-Vorgaben wurden durch Beispiele ersetzt. Caddy übernimmt den
Host aus `PUBLIC_URL`; Installationen behalten ihre eigenen Werte in `.env`.
Screenshots mit Live-Daten sind nicht mehr versioniert. Neue Aufnahmen landen unter
`artifacts/screenshots/`, auch Datenbank-Backups und Schlüsseldateien werden ignoriert.
Die CI blockiert entsprechende Pfade selbst dann, wenn jemand sie mit `git add -f`
aufnimmt. Sie prüft außerdem Secrets in allen lokal vorhandenen Git-Refs.

Gespeicherte komplette LigaInsider-Seiten wurden durch strukturelle Regression-Fixtures
mit synthetischem Text ersetzt. Lizenztexte für Chart.js, htmx und die API-Spezifikation
sind unter `licenses/` abgelegt. Sicherheitsmeldungen sind in `SECURITY.md` beschrieben.
Für den eigenen Projektcode ist noch keine Open-Source-Lizenz festgelegt.

## Warum eine neue Historie erforderlich ist

Ein Lösch-Commit allein entfernt private Bilder und Angaben nicht aus alten Commits.
Auch ein Force-Push bereinigt nicht automatisch GitHub-PR-Referenzen, Kommentare und Logs.
Deshalb wurde die bisherige Entwicklung unter
`WolkeWeb/kickbase-helper-private-history` **privat und archiviert** erhalten; die öffentliche Adresse
`WolkeWeb/kickbase-helper` enthält ausschließlich den bereinigten Stand mit neuer Historie.
Das ist ein neues GitHub-Repository, kein Fork des privaten Archivs. Alte PR-Nummern in
Entwicklungsdokumenten beziehen sich auf das private Archiv, nicht auf neue öffentliche PRs.

Eine vollständige lokale Sicherung der bisherigen Git-Refs liegt außerhalb des Repos.
Der geparkte Quoten-Branch bleibt ebenfalls im privaten Archiv erhalten. Es wird kein
Backup, alter Git-Ref, PR-Text oder Actions-Artefakt in das neue Repository importiert.
Der frische Git-Verlauf wurde ohne Scanner-Ausnahmen geprüft (0 Treffer), anschließend
waren CI und Proxy-Image in GitHub erfolgreich. Die öffentliche Freigabe ist abgeschlossen.
Ein anonymer Git-Klon enthielt zur Freigabe genau einen neuen Commit. Das private Archiv
und alte PRs waren anonym nicht erreichbar (HTTP 404); eine vollständige alte Commit-ID
wurde im neuen Repository mit „No commit found for SHA“ zurückgewiesen (HTTP 422).

Vorhandene andere lokale Klone müssen neu aus dem öffentlichen Repo geklont werden.
Alte Branches nicht in die öffentliche Historie mergen oder pushen, weil sie entfernte
Inhalte erneut veröffentlichen könnten. `.env` und lokale Secrets separat behalten.
Die bestehende Anwendung und ihre Datenbank werden durch den Repo-Wechsel nicht migriert.

Der Audit ist eine zeitpunktbezogene Prüfung, keine Garantie gegen jede Art sensibler
Information. Der private Altbestand bleibt deshalb privat.

## GitHub-Schutz

`main` erfordert einen PR und die erfolgreichen Checks `Lint & Tests` sowie
`App-Image (Build + Smoke-Test)`, auch für den Administrator. Force-Push und Löschen
sind gesperrt. Merges erfolgen per Squash. Secret-Scanning, Secret-Push-Schutz,
Dependabot-Warnungen/-Sicherheitsupdates und private Sicherheitsmeldungen sind aktiviert.
Workflows fremder Fork-Beiträge benötigen eine Freigabe vor ihrer Ausführung.

Die vollständigen lokalen Prüfungen ergeben 1360 erfolgreiche Tests und einen Skip
(SQLite und isoliertes PostgreSQL). Gezielt nach Änderungen nachgetestet; Ruff,
actionlint, zizmor, Caddy-Hostprüfung und der Secret-Scan waren erfolgreich.
