# Design: Preiskalkulation V2

**Datum:** 2026-10-22  
**Status:** Fachliches Design im Gespräch freigegeben; schriftliche Gesamtfreigabe ausstehend

## 1. Ziel

Die bisherige Preiskalkulation wird durch eine versionierte Engine V2 ersetzt. Sie liefert für dieselben Eingaben in öffentlichem Buchungsformular, DJ-Admin und Speicherung dasselbe Ergebnis.

Die öffentliche Ausgabe ist ausdrücklich eine **unverbindliche Brutto-Schätzung**. Der verbindliche Angebotspreis entsteht erst, nachdem ein Admin die Kalkulation geprüft, gegebenenfalls begründet angepasst und als unveränderliche Angebotsversion freigegeben hat. Preisfreigabe und Eventbestätigung bleiben getrennte Aktionen.

Der bisherige Funktionsumfang bleibt erhalten und wird erweitert:

- Pakete und enthaltene Leistungen
- optionale, standardmäßig ausgewählte und verpflichtende Extras
- individuelle interne Preispositionen
- Gäste-Staffelung
- automatische Regeln und Bedingungen
- abgesicherte freie Formeln
- frei angeordnete Preis-Workflows
- Rabatte
- Dauer und Mehrstunden
- Anfahrt und Freikilometer
- Wochenend- und Feiertagszuschläge
- Saisonzeiträume
- kurzfristige Buchungen
- Auf- und Abbauzeiten
- Mindestpreis
- Preisrundung
- versionierte Angebote und E-Mail-Zusammenfassungen

## 2. Ausgangslage und Probleme

Aktuell existieren mehrere konkurrierende Rechenwege:

1. `PriceEngine.calculate()` als Legacy-Berechnung,
2. `PriceEngine.calculate_workflow()` als frei angeordneter Workflow,
3. clientseitige Summierung im öffentlichen Formular,
4. clientseitige manuelle Kalkulatoren im Event-Admin,
5. Gäste-Staffelung über `EventOffer`,
6. frei editierbares `Event.total_price`.

Dadurch können Schätzung, Admin-Vorschau, gespeicherte Kalkulation und Event-Gesamtpreis voneinander abweichen. Weitere belegte Schwächen:

- `PricingRule.applies_to` wird gespeichert, aber von der Engine nicht angewandt.
- Ein Event-Speichern persistiert Auswahl und Rabatt, berechnet aber nicht immer verbindlich neu.
- Deaktivierte Pflicht-Checkboxen werden von Browserformularen nicht übertragen.
- Legacy- und Workflow-Engine haben unterschiedliche Ergebnisstrukturen und Semantik.
- unbekannte Workflow-Blöcke werden als stille Nullschritte behandelt.
- doppelte Blöcke können Beträge mehrfach anwenden.
- `EventOffer.guest_count` dupliziert `Event.guest_count`.
- APIs wandeln Geldwerte teilweise in `float` um.
- dynamische Preisnamen werden clientseitig über `innerHTML` ausgegeben.
- Snapshots enthalten keine vollständige Versionierung und Eingabeherkunft.
- ein Standard-Workflow ist nicht durch eine Datenbankregel eindeutig.

Die lokale Ausgangskonfiguration am 22.10.2026 besteht aus einem öffentlichen Paket `Basis-Paket` zu 600,00 €, einem aktiven Standard-Workflow mit einem Paketblock und keinen Posten, Regeln, Formeln oder Angeboten.

## 3. Verbindliche fachliche Entscheidungen

### 3.1 Preisstatus

Es gibt vier unterschiedliche Preisartefakte:

1. **Kundenschätzung** (`estimate`): unverbindlich und automatisch.
2. **Arbeitskalkulation** (`draft`): vom Admin bearbeitbare aktuelle Fassung.
3. **Freigegebenes Angebot** (`offer`): unveränderliche, nummerierte Version.
4. **Historische V1-Kalkulation** (`legacy`): nur lesbar und vergleichbar.

Alle Beträge sind **Brutto-Endpreise**. Die Engine schlägt keine zusätzliche Mehrwertsteuer auf.

### 3.2 Öffentliches Formular

Kunden geben nur notwendige Angaben ein:

- Datum
- Anfangs- und Endzeit
- Veranstaltungsort und Adresse
- Gästezahl
- Eventart
- Paket
- Extras

Automatisch abgeleitet werden Dauer, Entfernung, Wochentag, Feiertag, Saison und Kurzfristigkeit. Auf-/Abbauwerte stammen zunächst aus globalen beziehungsweise paketbezogenen Defaults und können im Admin korrigiert werden.

### 3.3 Angebot und Eventstatus

`Angebot freigeben`:

- friert die aktuelle Preisfassung ein,
- vergibt eine fortlaufende Versionsnummer,
- sendet eine konfigurierbare E-Mail-Zusammenfassung,
- verändert den Eventstatus nicht.

`Event bestätigen` bleibt eine getrennte Aktion für Eventstatus, Google-Kalender und Bestätigungsmail.

Es gibt keine öffentliche Angebotsseite und keine Online-Annahmefunktion.

### 3.4 Feiertagsregion

Nordrhein-Westfalen ist der globale Standard. Pro Event kann der Admin ein anderes Bundesland auswählen. Das öffentliche Formular fragt das Bundesland nicht ab.

## 4. Architektur

### 4.1 Kontrollierter V2-Umstieg

V2 wird zunächst neben V1 entwickelt. Während des Schattenbetriebs bleibt V1 öffentlich maßgeblich; V2 berechnet dieselben Inputs parallel und speichert Vergleichsergebnisse. Erst nach validierter Konfiguration und freigegebenen Referenzfällen schaltet ein zentraler Feature-Schalter alle maßgeblichen Pfade gemeinsam auf V2 um.

Die Legacy-Engine bleibt bis zum Ende des Beobachtungszeitraums verfügbar. Alte Snapshots bleiben dauerhaft lesbar.

### 4.2 Einheitliche Engine-Schnittstelle

```python
PricingEngineV2.calculate(pricing_request) -> PricingResult
```

`PricingRequest` enthält ausschließlich normalisierte, serverseitig validierte Eingaben und eingefrorene Konfigurationsversionen. `PricingResult` enthält nur deterministische, serialisierbare Werte. Geld wird intern ausschließlich als `Decimal` verarbeitet und an API-Grenzen als Dezimalstring übertragen.

Aufrufer:

- öffentliche Preisvorschau,
- serverseitige Neuberechnung beim Absenden,
- Event-Admin-Vorschau,
- Speichern einer Arbeitskalkulation,
- Angebotsfreigabe,
- V1/V2-Vergleich,
- Bestandsmigration.

### 4.3 Modulgrenzen

V2 wird nicht in `views.py` oder Templates gerechnet. Geplante Einheiten:

- `pricing_v2/input.py`: Normalisierung und Validierung von Eingaben
- `pricing_v2/config.py`: Auflösen globaler Werte und Paket-Overrides
- `pricing_v2/calendar.py`: Wochenenden, Feiertage, Saison und Kurzfristigkeit
- `pricing_v2/blocks.py`: klar abgegrenzte Rechenblöcke
- `pricing_v2/engine.py`: Workflow-Ausführung
- `pricing_v2/result.py`: Resultat, Positionen und Serialisierung
- `pricing_v2/migration.py`: V1/V2-Vergleich und kontrollierte Neuberechnung

Jede Einheit besitzt eine kleine, testbare Schnittstelle. HTTP-Views übersetzen nur zwischen Requests und diesen Services.

## 5. Datenmodell

### 5.1 `PricingProfile`

Ein versioniertes globales Preisprofil enthält:

- Name und Versionsnummer
- Status `draft`, `active`, `retired`
- gültig ab
- enthaltene Standardstunden
- Mehrstundenpreis
- Standard-Aufbau- und Abbauzeit
- Auf-/Abbaustundensatz
- Freikilometer
- Kilometerpreis
- Wochenendzuschläge
- Feiertagszuschlag
- Standard-Bundesland, initial NRW
- Saisonzeiträume und Zuschläge
- Kurzfristigkeitsstufen
- Gäste-Staffeln
- Mindestpreis
- Rundungsmodus und Rundungsschritt
- Zeitstempel und Bearbeiter

Aktive Profile werden nicht in-place verändert. Eine Änderung erzeugt eine neue Version.

### 5.2 Paket-Overrides

Pakete behalten Grundpreis, Beschreibung, Badge und enthaltene Preisposten. Optional überschreibbar sind:

- enthaltene Stunden und Mehrstundenpreis
- enthaltene Kilometer und Kilometerpreis
- Auf-/Abbauwerte
- Wochenend-, Feiertags-, Saison- und Kurzfristigkeitsfaktoren
- Gäste-Staffeln
- Mindestpreis
- Rundung

Pro Faktor existieren drei Modi:

- `inherit`: globalen Wert übernehmen,
- `override`: Paketwert verwenden,
- `disabled`: Faktor für dieses Paket nicht anwenden.

### 5.3 Event-Felder

Das Event erhält nur fachliche Eingaben, keine duplizierte Rechenlogik:

- Bundesland mit Default aus dem aktiven Profil
- interne Aufbauzeit
- interne Abbauzeit
- optional korrigierte Entfernung

`Event.guest_count` bleibt die einzige Gästezahl. Die Gästezahl in `EventOffer` wird bei der Migration aufgelöst und danach nicht als unabhängige Quelle weitergeführt.

### 5.4 Kalkulationsrevisionen

`PriceCalculationRevision` ist unveränderlich und enthält:

- Event
- Typ: `estimate`, `draft`, `offer`, `legacy`, `migration_compare`
- fortlaufende Revision beziehungsweise Angebotsnummer
- Engine-Version
- Profil-ID und Profilversion
- Paket-ID und Paketversion
- Workflow-ID und Workflowversion
- normalisierte Inputs als JSON
- vollständiges Resultat als JSON
- Brutto-Endpreis als `DecimalField`
- Status: `draft`, `released`, `sent`, `send_failed`, `superseded`
- Ersteller und Zeitstempel
- optional Vorgängerrevision
- optional V1-Betrag und Differenz

Eine freigegebene Revision darf nicht geändert werden. Ein neues Angebot erzeugt eine neue Revision.

### 5.5 Manuelle Korrekturen

Der Endpreis ist kein frei überschreibbares Feld mehr. Manuelle Zu- oder Abschläge sind eigene Positionen mit:

- Bezeichnung
- Betrag
- verpflichtender Begründung
- Bearbeiter
- Zeitstempel

`Event.total_price` bleibt während der Übergangsphase als denormalisierter aktuell freigegebener beziehungsweise aktueller Arbeitsbetrag erhalten und wird nur durch den Pricing-Service gesetzt.

### 5.6 Eindeutigkeit und Integrität

- höchstens ein aktives Preisprofil,
- höchstens ein aktiver Standard-Workflow,
- eindeutige Versionsnummer je Profil,
- eindeutige Angebotsnummer je Event,
- Check-Constraints für nichtnegative Preise, Kilometer und Stunden,
- Rabattwerte zwischen 0 und 100,
- Rundungsschritte größer null,
- freigegebene Revisionen werden an der Servicegrenze unveränderlich behandelt.

## 6. Auflösung globaler und paketbezogener Werte

Vor der Berechnung entsteht eine `ResolvedPricingConfig`. Für jeden Faktor enthält sie:

- finalen Wert,
- Quelle `global`, `package_override` oder `package_disabled`,
- Profil-/Paketversion,
- verständliche Beschreibung.

Damit kann jede Preisposition ihre Herkunft ausweisen. Änderungen an späteren Profilversionen verändern bestehende Revisionen nicht.

## 7. Workflow V2

### 7.1 Standardreihenfolge

1. Paket-Grundpreis
2. Extras
3. Dauer und Mehrstunden
4. Auf-/Abbau
5. Anfahrt
6. Gäste-Staffelung
7. Kalenderfaktoren
8. zusätzliche Regeln
9. Formel-Anpassungen
10. Rabatt
11. Mindestpreis
12. Rundung

### 7.2 Blocktypen

- `package`
- `items`
- `duration`
- `setup_teardown`
- `travel`
- `guest_tiers`
- `calendar_factors`
- `rules`
- `formula`
- `discount`
- `minimum_price`
- `rounding`

### 7.3 Validierung

Ein Workflow kann erst aktiviert werden, wenn:

- jeder Blocktyp bekannt ist,
- Paket, Extras und automatische Faktoren höchstens einmal vorkommen,
- Mindestpreis höchstens einmal vorkommt,
- Rundung der letzte aktive Block ist,
- alle Referenzen auf aktive oder bewusst versioniert eingefrorene Objekte zeigen,
- Rabatte zwischen 0 und 100 liegen,
- Preise und Zuschläge nicht negativ sind,
- Formeln syntaktisch und semantisch valide sind,
- mindestens ein preisbildender Block vorhanden ist.

Unbekannte oder ungültige Blöcke ergeben einen `configuration_error`; sie werden nicht als `0 €` ausgeführt.

### 7.4 Reihenfolgesemantik

Die Reihenfolge ist fachlich wirksam. Der Builder erklärt insbesondere:

- `Rabatt → Mindestpreis`: Rabatt kann den Mindestpreis nicht unterschreiten.
- `Mindestpreis → Rabatt`: Rabatt darf den Betrag unter den Mindestpreis senken.

Diese Semantik wird in Vorschau, Testlabor und Resultat dokumentiert.

## 8. Rechenblöcke

### 8.1 Paket

Addiert den Paket-Grundpreis und markiert enthaltene Items, damit diese nicht erneut berechnet werden.

### 8.2 Extras

Addiert ausgewählte öffentliche oder interne Preisposten. Pflichtposten werden serverseitig ergänzt, selbst wenn ein manipulierter Request sie auslässt. Inaktive oder nicht öffentliche IDs werden im öffentlichen Pfad verworfen.

### 8.3 Dauer und Mehrstunden

Die Eventdauer wird aus Start und Ende abgeleitet; Ende kleiner/gleich Start bedeutet Folgetag. Enthaltene Stunden werden abgezogen, verbleibende Zeit wird nach konfigurierbarer Einheit und Rundungsregel berechnet.

### 8.4 Auf-/Abbau

Verwendet globale oder paketbezogene Defaults. Der Admin kann die tatsächlichen Zeiten pro Event korrigieren. Kunden müssen keine weiteren Angaben machen.

### 8.5 Anfahrt

Die öffentliche Schätzung verwendet die bestehende Distanznäherung. Im Admin kann die Entfernung korrigiert werden. Nach Freikilometern wird die Reststrecke mit dem Kilometerpreis berechnet. Die Preisposition weist geschätzte beziehungsweise korrigierte Distanz aus.

### 8.6 Gäste-Staffelung

Verwendet ausschließlich `Event.guest_count`. Staffelgrenzen und Preise sind global konfigurierbar und paketweise überschreibbar. Die bestehende `EventOffer`-Logik wird in V2 überführt, nicht parallel weitergerechnet.

### 8.7 Kalenderfaktoren

- Wochenende aus dem Eventdatum
- gesetzliche Feiertage anhand Eventdatum und Bundesland
- Saison anhand nicht überlappender Zeiträume
- Kurzfristigkeit anhand Tage zwischen Anfragezeitpunkt und Eventdatum

Jeder Faktor erzeugt eine eigene benannte Position.

### 8.8 Regeln

Regeln bleiben UND-verknüpfte Bedingungen. `applies_to` wird umgesetzt und kann auf Paket, Technik, Extras, Anfahrt oder aktuelle Zwischensumme zeigen. Regeln erzeugen benannte Zu- oder Abschläge; `flat_set` wird in V2 durch explizite Mindestpreis- oder Formelmodi ersetzt und nicht mehr als stiller Sonderfall behandelt.

### 8.9 Formeln

Formeln bleiben AST-whitelist-basiert und erhalten einen expliziten Modus:

- Betrag addieren
- Betrag abziehen
- Zwischensumme ersetzen

„Zwischensumme ersetzen“ wird als fortgeschritten markiert. Formeln werden vor Aktivierung mit Referenzinputs validiert. Ein Fehler in einer aktiven Formel erzeugt einen `calculation_error` statt ein stilles Weiterrechnen.

### 8.10 Rabatt, Mindestpreis und Rundung

- Rabatte sind prozentual oder absolut und erzeugen eine negative Position.
- Mindestpreis ergänzt nur die Differenz bis zum konfigurierten Mindestbetrag.
- Rundung unterstützt mindestens mathematische Rundung, Aufrunden und Abrunden auf konfigurierbare Schritte, zum Beispiel 5 €, 10 € oder 50 €.

## 9. Resultatformat

Jede Position enthält mindestens:

```json
{
  "code": "travel",
  "label": "Anfahrt",
  "basis": "42 km - 20 Freikilometer",
  "quantity": "22.00",
  "unit": "km",
  "unit_price": "0.50",
  "amount": "11.00",
  "source": "package_override"
}
```

`PricingResult` enthält:

- `engine_version`
- Konfigurationsversionen
- normalisierte Inputs
- geordnete Positionen
- Zwischensummen je Block
- Gesamtrabatt
- Rundungsdifferenz
- Brutto-Endpreis
- Warnungen
- Fehlerstatus
- Modus `estimate` oder `offer`

Dezimalzahlen werden als Strings serialisiert. Darstellung und Berechnung werden strikt getrennt.

## 10. Öffentliche Benutzeroberfläche

Die bestehenden fünf Buchungsschritte bleiben erhalten. Die V2-API aktualisiert die Preisvorschau nach relevanten Änderungen.

Anzeige:

- Paket
- Extras
- Mehrstunden
- Auf-/Abbau, sofern in der Schätzung wirksam
- Anfahrt
- automatische Zuschläge und Rabatte
- wirksamer Mindestpreis
- Rundung
- `Unverbindliche Schätzung: ca. 1.245,00 € brutto`

Fehlende Inputs erscheinen als Hinweise statt Nullpositionen. Beispiel: `Anfahrt wird nach Auswahl einer vollständigen Adresse berechnet.`

Der Browserbetrag ist niemals maßgeblich. Beim Absenden berechnet der Server erneut und speichert eine unveränderliche `estimate`-Revision.

Die Oberfläche erzeugt dynamische Zeilen über DOM-APIs beziehungsweise escaped Templatewerte, nicht durch ungeprüftes `innerHTML` mit administrativ gepflegten Texten.

## 11. Admin-Oberfläche

### 11.1 Preisverwaltung

Die Preisverwaltung wird in vier Bereiche gegliedert:

1. **Globale Defaults**
2. **Pakete und Overrides**
3. **Extras, Regeln, Formeln und Saison-/Kurzfristigkeitsstufen**
4. **Workflows und Testlabor**

Geerbte Werte werden als `Global: …` dargestellt. Overrides und Deaktivierungen sind eindeutig erkennbar.

### 11.2 Workflow-Testlabor

Testinputs:

- Datum und Bundesland
- Start-/Endzeit
- Entfernung
- Gästezahl
- Eventart
- Paket und Extras
- Auf-/Abbau

Das Testlabor zeigt Positionen, Herkunft, Zwischensummen, Warnungen und V1/V2-Differenz. Ein Workflow kann erst nach erfolgreicher Validierung aktiviert werden.

### 11.3 Eventbereich „Preis & Angebot“

Der Event-Admin zeigt:

- ursprüngliche Kundenschätzung
- aktuelle V2-Neuberechnung
- Differenz und Begründung
- editierbare interne Positionen
- manuelle Korrekturen mit Pflichtbegründung
- aktuellen finalen Brutto-Angebotspreis
- Angebotsmail-Vorschau
- Revisionshistorie
- Aktion `Angebot freigeben und senden`

## 12. Angebotsversionierung und Versand

Bei Freigabe wird eine unveränderliche `offer`-Revision erzeugt. Angebotnummern sind pro Event fortlaufend. Frühere Angebote bleiben sichtbar und werden durch spätere Versionen nicht verändert.

Konfigurierbare E-Mail-Platzhalter:

- `{client_name}`
- `{event_name}`
- `{event_date}`
- `{price_breakdown}`
- `{gross_total}`
- `{dj_name}`

Der Versand wird in `EmailLog` und an der Revision dokumentiert:

- Erfolg → `sent`
- Gmail-/Google-Fehler → `send_failed`
- erneutes Senden verwendet dieselbe Revision und verändert keinen Preis

## 13. Fehlerklassen

- `configuration_error`: ungültiges Profil oder Workflow
- `missing_input`: noch nicht berechenbare Eingabe
- `invalid_input`: fachlich ungültiger Wert
- `reference_error`: fehlende Paket-, Regel- oder Formelreferenz
- `calculation_error`: Rechen- oder Formelfehler
- `migration_difference`: unerwartete V1/V2-Abweichung

Öffentlich gilt bei Fehlern:

- Anfrage darf abgesendet werden,
- statt eines falschen Preises erscheint `Preis wird individuell geprüft`,
- technische Details bleiben intern.

Im Admin blockieren Konfigurationsfehler Aktivierung und Angebotsfreigabe. Ein externer Mailfehler verändert die freigegebene Revision nicht.

## 14. Migration und Rollout

### Phase 1: Schema und V2-Engine

- neue Modelle und Constraints
- V2-Rechenblöcke
- V1 bleibt unverändert maßgeblich

### Phase 2: Schattenbetrieb

- V1 und V2 parallel berechnen
- Differenzen in `migration_compare`-Revisionen speichern
- Referenzfälle prüfen

### Phase 3: Konfiguration freigeben

- globales Profil vervollständigen
- Paket-Overrides prüfen
- Standard-Workflow validieren
- bekannte Abweichungen dokumentiert akzeptieren

### Phase 4: V2 aktivieren

Ein zentraler Feature-Schalter stellt öffentliche Vorschau, Admin-Vorschau und Speicherung gemeinsam um. Ein Rückschalten auf V1 löscht keine V2-Revisionen.

### Phase 5: Bestand neu berechnen

Automatisch einbezogen:

- alle `inquiry`-Events,
- `confirmed`-Events mit Datum heute oder zukünftig.

Ausgeschlossen:

- `past`,
- `cancelled`.

Der Lauf ist idempotent und protokolliert V1-Betrag, V2-Betrag, Differenz, Profilversion und Zeitpunkt. Bestehende freigegebene Angebote werden nicht überschrieben; eine Neuberechnung erzeugt einen neuen Entwurf.

### Phase 6: Legacy entfernen

Erst nach Beobachtungszeitraum, freigegebenen Vergleichen und grüner Gesamtsuite. Alte V1-Snapshots bleiben lesbar.

## 15. Teststrategie

### Engine-Tests

- jeder Block isoliert
- erlaubte Reihenfolgen und verbotene Duplikate
- Vererbung, Override und Deaktivierung
- Event über Mitternacht
- NRW-Feiertage und andere Bundesländer
- Saisonüberschneidungen
- Kurzfristigkeitsgrenzen
- Freikilometer und enthaltene Stunden
- Gäste-Staffelgrenzen
- Mindestpreis vor und nach Rabatt
- Rundung auf 5, 10 und 50 €
- alle Formelmodi
- keine Float-Geldarithmetik
- deterministische Resultate

### Schnittstellen-Tests

- öffentlich, Admin und Speicherung liefern denselben Betrag
- Server berechnet beim Absenden neu
- manipulierte IDs werden verworfen
- Pflichtposten werden serverseitig ergänzt
- Preisnamen werden escaped
- Resultat enthält Inputs und Versionen
- V1/V2-Schalter wechselt alle Pfade gemeinsam

### Angebots-Tests

- Entwurf, Freigabe, Versand
- unveränderliche Revision
- manuelle Korrektur nur mit Begründung
- Wiederholungsversand ohne Neuberechnung
- Preisfreigabe bestätigt Event nicht
- Gmail-Fehler erzeugt `send_failed`

### Migrationstests

- nur vereinbarte Status-/Datumsgruppen
- historische und stornierte Events unverändert
- alter und neuer Betrag protokolliert
- idempotenter Wiederholungslauf
- Rückschalten ohne Datenverlust

## 16. Abnahmekriterien

Die V2-Überarbeitung gilt als fertig, wenn:

1. genau ein kanonischer Rechenweg für V2 existiert;
2. öffentliche Vorschau, Admin und gespeicherte Revision denselben Betrag liefern;
3. serverseitige Berechnung manipulierbare Clientwerte überstimmt;
4. jede Position fachlich erklärbar und auf eine Konfigurationsversion zurückführbar ist;
5. alle Geldoperationen `Decimal` verwenden;
6. Workflow- und Profilaktivierung vollständig validiert werden;
7. freigegebene Angebote reproduzierbar und unveränderlich sind;
8. V1/V2-Vergleiche für definierte Referenzfälle freigegeben sind;
9. die vereinbarte Bestandsmigration idempotent erfolgreich läuft;
10. vollständige Testsuite, Migrationen und Django-Systemcheck grün sind.

## 17. Bewusste Auslassungen

- keine öffentliche Angebotsseite
- keine Online-Annahme oder elektronische Signatur
- keine PDF-Erzeugung
- keine Netto-/MwSt-Aufteilung
- keine automatische Bundeslandserkennung im Kundenformular
- keine Rechnungsstellung oder Zahlungsabwicklung
- keine Entfernung alter V1-Snapshots

## 18. Beziehung zu älteren Preis-Spezifikationen

Die Specs vom 10.06.2026 bleiben historische Dokumente. Weiterhin gültig sind die unverbindliche öffentliche Schätzung, Distanznäherung und das Workflow-Testkonzept. Durch V2 ersetzt werden Aussagen, dass Engine-/Backend-Umbau, Angebotsverwaltung oder Testinfrastruktur außerhalb des Scopes liegen.
