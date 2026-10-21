# Funktionsumfang

Ausführliche Liste aller Seiten und Funktionen von Kickbase Helper. Die Kurzfassung steht in
der [README](../README.md); wie die Scores entstehen, steht dort unter
[Wie die Empfehlungen entstehen](../README.md#wie-die-empfehlungen-entstehen).

Alles ist **nur lesend**: Die App gibt keine Gebote ab, verkauft nichts und ändert keine
Aufstellung. Simulationen und Pläne sind lokale Rechnungen.

| | |
|---|---|
| **Heute** | Countdown bis zur Aufstellungs-Deadline und alles, was davor zu tun ist: leere Plätze, Startspieler mit Ausfallrisiko, Minus, Kaderlücken, offene Gebote, beste Kaufchance – jeweils mit Link. |
| **Mein Team** | Budget, Teamwert, Ligaplatz, Aufstellung auf dem Spielfeld, Kader mit aktueller 24-h-Marktwertbewegung, Originalartikeln auch ohne KI und datierten Ausfall-/Rückkehrmeldungen, **Spieltags-Bilanz** (Punkte, Platz mit Δ, Top/Flop, verpasste Punkte durch die Bank) mit Rangverlauf-Diagramm. |
| **Eigener Stammkader** | Spieler lokal schützen oder als Trading notieren. Finanzierungspläne verkaufen geschützte Stammspieler nicht; ein dadurch ungedecktes Defizit bleibt sichtbar. Eigene hypothetische Verkäufe sind im Simulator mit Warnung möglich. |
| **Transfer-Simulation** | Verkäufe und Käufe lokal kombinieren: Wunschpreis, Budget nach verbleibenden Geboten, beste Formation/Index und freie Plätze. Unbestätigte oder zu späte Käufe zählen nicht zur nächsten Elf. Einstieg über Trading oder ein Kaufangebot. |
| **Spieltagspunkte** | Eigene gespeicherte Spieltags-Elf mit vorläufigen Spielerpunkten und offizieller Spieltagssumme. Datenstand sichtbar, Bank getrennt, während möglicher laufender Partien bei sichtbarer Online-Seite im 10-Minuten-Takt. |
| **Kaufen** | Marktangebote als kompakte Zeilen mit Score, Preis, Gebotsempfehlung (70-%-Quantil der Liga-Käufe), **Aufstellungsindex und Index pro Mio. €** und Deadline-Hinweis; Filter nach Position, Preis, Kaderlücke. |
| **Verkaufen** | Spieler mit aktuellem Marktwertrückgang oder vor der Deadline schlechter Startchance; steigende Trading-Spieler werden unter der Woche gehalten; im Minus der Verkaufs-Guide mit der Kombination, die **am wenigsten Punkte kostet**. |
| **Trading** | Tagesanstieg und Gewinn seit Kauf; eigener Plan bis zur echten Deadline mit Budget nach offenen Geboten, nötigen Verkäufen und Schutz der vorgeschlagenen Elf. Kaufchancen berücksichtigen Aufschlag und verbleibende Updates; Gewinne werden nur als Szenario gezeigt. |
| **Deadline-Plan** | Auf der Teamseite: Rückwärtsplan vom Jetzt bis zum echten Anpfiff mit freien Plätzen, Finanzierung, geschützten Stammspielern und datierten Schritten. Verkäufe sind bis `Deadline − Sicherheitsfenster` einzuplanen, sonst gibt es statt Zeitplan den Hinweis „Verkauf vor Deadline nicht mehr sicher“. Fehlende Daten stehen als „unbekannt", alte Cache-Werte sind als solche markiert. Telegram meldet sich nur bei offenem Budgetdefizit im Deadline-Fenster und nur, wenn sich die Lage geändert hat. |
| **Beste Elf** | Die stärkste Aufstellung aus dem Kader nach **Aufstellungsindex** (0–100 je Spieler), mit Notbesetzung statt leerer Plätze. |
| **Spieler** | „Kaufen?“ bzw. „Halten oder verkaufen?“ mit Score und Gründen, **Aufstellungsindex** für die nächsten Spiele mit Aufschlüsselung Startchance × Form × Gegner, Startchance (LigaInsider-Prognose, Verletzungen, Startquote), Markt-Karte, Marktwert- und Punkte-Verlauf, KI-Einschätzung. |
| **Bilanz** | Wie sich empfohlene Spieler nach 1, 3 und 7 Tagen entwickelt haben und wie die eigenen Gebote ausgingen. |
| **Telegram** | Meldungen zu eigenen Spielern, Warnung ~2 h vor der Deadline, Morgen-Briefing – und Fragen an das Dashboard („Wen soll ich verkaufen?“), beantwortet nur aus den eigenen Daten. **Deadline-Plan**: Alarm, wenn vor der Aufstellung ein ungedecktes Budgetdefizit offen ist; einmal je Lage statt bei jedem Lauf. **Spieltags-Bilanz** nach Abpfiff (Punkte, Platz-Änderung, Top/Flop, verpasste Punkte); dieselbe Bilanz steht im Lagebild des Bots und auf der Team-Seite. **Merkliste** (`/merkliste`): Alarme, wenn ein beobachteter Spieler auf den Markt kommt, sein Preis eine Schwelle (`price_below`) unterschreitet, sich sein Status ändert oder er aus der LigaInsider-Prognose-Elf fällt; dieselben Spieler stehen im Lagebild des Bots. **Gebote auf eigene Spieler**: Alarm, sobald ein anderer Liga-Manager einen meiner gelisteten Spieler über Marktwert überbietet; dieselben Listings stehen ebenfalls im Lagebild des Bots (ohne Bietername). |

Gebaut für das Handy (375 px, Dark Mode, PWA fürs Homescreen-Icon), ab 900 px zweispaltig.
