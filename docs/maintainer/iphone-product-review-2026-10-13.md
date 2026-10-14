# iPhone-, Produkt- und Datenprüfung (13.10.2026)

Auftrag: gesamte Handy-UI prüfen, mit offizieller Kickbase-UI vergleichen, Trading während der
Woche, Spieltagsplanung, Nachrichten-Nutzen und Live-Spieltagspunkte verbessern. Kickbase
bleibt nur lesend; die Umsetzung ist in eigene PR-Blöcke aufgeteilt.

## Beobachtung

Die vollständige Oberfläche wurde mit normaler Testanmeldung bei 375 px aufgenommen:
Login, Team, Kaufen, Verkaufen, Beste Elf, Bilanz, Spielersuche, Spielerdetails und Merkliste.
Die Screenshots zeigen anonymisierte Fixtures, keine Produktionssitzung; die normale
Produktionsanmeldung stand in dieser Session nicht zur Verfügung. Ein Versuch mit eigens
signierter Produktionssitzung wurde von der automatischen Freigabeprüfung abgelehnt und nicht
ausgeführt. Keine Secrets ausgegeben oder gesperrte Secret-Dateien geöffnet.

Als Referenz wurden die [offiziellen iPhone-Screenshots des Herstellers im App Store](https://apps.apple.com/de/app/kickbase-fussball-manager/id678241305)
angesehen. Die vorhandene Gestaltung ist lesbar, nutzt aber zu viel Platz für wiederholte
Erklärungen. Konkrete Probleme:

- Auf der Teamseite verdrängen viele „Heute“-Hinweise und eine leere/historische Bilanz die
  Aufstellung. Budget, aktuelle Handlungsprioritäten und danach die Elf sollten vorne stehen.
- Auf der Spielerseite erscheinen Startchance, Index und Erklärungen mehrfach. Marktwert,
  Tagesänderung, Einsatzrisiko und die Entscheidung gehören zuerst; Modelltabellen, Saison-
  Summen, KI und ältere Nachrichten können in den zweiten Blick.
- Ein 7-Tage-Minus wird als Verkaufssignal behandelt, obwohl der Spieler aktuell steigen kann.
  Ein Nicht-Stammspieler kann unter der Woche ein sinnvoller Trade sein.
- Nachrichten wirken wie eine umfassende aktuelle Lage, obwohl nur RSS-Auszüge zu
  Verletzung/Sperre/Rückkehr ausgewertet werden. Historische Meldungen sind kein aktueller Status.
- Spieltagspunkte fehlen. Die Spieltags-Elf muss von der für nächste Woche veränderbaren
  Aufstellung getrennt bleiben.

Die [offizielle Spielerprofil-Hilfe](https://help.kickbase.com/help/spielerprofil) priorisiert
Marktwertänderungen der letzten 24 Stunden und zeigt bei eigenen Spielern Gewinn/Verlust.
Marktwerte werden im Seasonal-Modus täglich aktualisiert, siehe
[Marktwertberechnung](https://help.kickbase.com/help/kickbase-marktwert).
Die Finanzierung richtet sich nach dem tatsächlichen offiziellen Spieltagsbeginn, auch bei
englischen Wochen, siehe [Kontostand und Aufstellung](https://help.kickbase.com/help/regel-4-kontostand-aufstellung).

## Nachrichten: tatsächlicher Betrieb

Lesende aggregierte Prüfung der Produktionsdaten am 13.10.2026:

- 126 RSS-Artikel gespeichert, alle verarbeitet; 30 Ausfall-/Rückkehr-Berichte,
  26 einem Spieler zugeordnet.
- Letzte sieben Tage: 108 Artikel, 22 Berichte, 18 zugeordnet; keine aufgegebenen Artikel.
- Aktueller Feed-/Auswertungsstatus OK, keine wartenden Artikel. Dennoch hat der eigene
  16-Spieler-Kader nur einen Bericht zu einem Spieler in den 7-/30-Tage-Fenstern.
- Abruflatenz letzte sieben Tage: Median 3,69 Stunden, Mittel 6,32 Stunden, 95. Perzentil
  22,53 Stunden (inklusive Unterbrechung/Nachholen). Das beweist begrenzte Aktualität und
  persönliche Abdeckung, nicht dass eine bestimmte Verletzung übersehen wurde.

Nutzen heute: fokussierte Ausfallhinweise und KI-Kontext. Kein vollständiger Nachrichtenfeed,
keine direkte Änderung der deterministischen Scores. Ziel des Folgeblocks: Originalmeldungen
auch ohne KI zeigen, Quellenzeit/Umfang klar benennen und vor der Deadline öfter aktualisieren.

## Umgesetzt: Trading und Deadline-Plan

- Kader, Kaufangebote und Spielerseite priorisieren die beobachtete 24-h-Änderung.
  Fehlende API-Felder oder fehlende exakte Tageswerte bleiben unbekannt statt scheinbar null.
- Ein aktueller Anstieg kann unter der Woche gegen vorschnellen Verkauf sprechen, auch wenn
  Startchance oder 7-Tage-Trend schlecht sind. Innerhalb von `trading.close_hours` vor der
  echten Deadline zählt die Spieltagsplanung. Aufstellung und Finanzierung bleiben getrennt.
- Eigener Trading-Bereich: vorgeschlagene Elf, zusätzliche Positionen, Gewinn seit Kauf,
  Konto und Budget nach allen offenen Geboten. Finanzierung nutzt heutige Marktwerte;
  mögliche künftige Gewinne werden nicht als verfügbares Geld gerechnet.
- Kaufchancen berücksichtigen Preis/Gebot, aktuellen Tagesanstieg, verbleibende Marktwert-
  Updates und Verkaufspuffer. Spieler ohne Stammplatz sind hier nicht allein deshalb ausgeschlossen.
  Manager-Angebote ohne verlässlichen Annahmezeitpunkt werden nicht als planbarer Trade gezeigt.
- Gewinnrechnung ist ein ausdrücklich bezeichnetes Szenario bei unveränderter Tagesbewegung,
  keine Marktwertprognose. Phase 8 bleibt gated. Kein Kauf/Verkauf erfolgt im Helper.

Parameter und Schwellen stehen in `config/scoring.yaml::trading`. Eigene Gebote werden nicht
zweimal vom Budget abgezogen. Falls zur Finanzierung Spieler der Elf gebraucht werden,
zeigt der Plan das ausdrücklich an; eine bereits vorhandene Kaderlücke bleibt sichtbar.

## Live-Punkte: belastbare Grenze

Der vorhandene eigene `user_teamcenter`-Endpoint liefert Spieltags-Elf/Bank und Punkte.
Die aktuelle Aufstellung (`my_eleven`) enthält keine belegten Spielerpunkte und ist dafür
ungeeignet. Live-Abruf braucht kurzen Cache, echte Spieltagszuordnung, sichtbares Datenalter und
fehlende Werte als „unbekannt“. Während eines laufenden Spiels konnte die Verfügbarkeit in
dieser Session noch nicht beobachtet werden. Die Anzeige darf daher keinen Sekunden-Liveticker
versprechen. Abpfiff heißt außerdem nicht endgültig:
[Kickbase bestätigt mögliche nachträgliche Korrekturen](https://help.kickbase.com/help/wann-stehen-meine-spieltagspunkte-fest).

## Nachrichten und mobile Hierarchie umgesetzt (F1)

Originalartikel werden ohne KI aus expliziten Spieler- und Vereins-Tags zugeordnet; fehlende
oder mehrdeutige Tags erzeugen keine Zuordnung. Es gibt keine vermeintliche Statusbestätigung
oder automatische Score-Änderung aus einem Artikel. KI-Ausfälle/Rückkehr zeigen das Meldungsdatum;
ältere Meldungen sind archiviert, Originalartikel und dieselbe Ausfallmeldung werden dedupliziert.
Der Abrufstand und die begrenzte RSS-Abdeckung sind sichtbar. Zusätzlich zu drei täglichen
Basisabrufen prüft der Worker im Sechs-Stunden-Fenster vor der echten Deadline stündlich den Feed;
auch Fehlversuche werden dabei höchstens stündlich wiederholt.

Auf dem iPhone stehen Budget, Deadline-Aufgaben und Elf vor der eingeklappten historischen Bilanz.
Auf der Spielerseite stehen Marktwert/Tagestrend vor der Entscheidung; doppelte Score-/Index-
Erklärungen wurden gekürzt, Einsatzhistorie, nächste Spiele und optionale KI bleiben aufklappbar.
Die zugrunde liegenden Gewichte und Gründe bleiben vollständig abrufbar.
Screenshots: lokal archivierte Browserprüfung (Screenshots nicht zur Veröffentlichung).

## Spieltagspunkte umgesetzt (F2)

Gezielte Prüfung des bestehenden eigenen Teamcenter-Endpunkts im App-Container ergab eine
Elf mit elf vorhandenen Punktefeldern und eigener Spieltagssumme. Keine Rohdaten fremder
Manager ausgegeben; der normale lesende Client wurde genutzt. Aktuell war die Partie bereits
abgepfiffen; das Verhalten während einer laufenden Partie ist zusätzlich per Fixture geprüft
und lässt sich erst am nächsten echten Spieltag live beobachten.

`/spieltag` zeigt die neueste bereits begonnene Runde anhand des Spielplans und die dafür
gefrorene Elf. Der API-Wert „nächster Spieltag“ und die heute editierbare Aufstellung werden
nicht dafür verwendet. Bankpunkte bleiben getrennt, verkaufte Spieler bleiben in der Spieltags-
Elf erhalten. Offizielle Summe wird über die eigene User-ID ausgewählt; andere Managerdaten
kommen nicht in die Ansicht. Fehlende Punkte/Summen bleiben unbekannt, null und negative
Punkte bleiben echte Werte. Abpfiff bedeutet keine endgültige Wertung.

Die Punkte haben mindestens zehn Minuten Cache; ein älterer Bericht mit 24-h-Cache wird beim
Live-Aufruf anhand seines Alters erneuert. Die Seite prüft während möglicher laufender Spiele
nur sichtbar/online automatisch. Manuelle Prüfung nutzt denselben Cache und zeigt den
Datenstand. Unbestätigte Statuscodes werden nicht als laufend/ausgewechselt interpretiert.
lokal archivierte Browserprüfung (Screenshots nicht zur Veröffentlichung).

Eigenprüfung: Das API-Feld `mt` wird nicht als persönliche Einsatzdauer ausgegeben; Fixture-
Werte beschreiben den Match-Kontext auch bei Ein-/Auswechslung. Temporäre Datenbank-/Quellen-
Fehler erlauben eine begrenzte Wiederholung nach zehn Minuten, Zugriffsverweigerung nicht.
