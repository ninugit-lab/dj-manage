# Pricing V2: lokales Rollout- und Rücknahme-Runbook

**Status:** Implementierung im Feature-Branch; keine Produktionsfreigabe. Änderungen an Produktion, Merge, Push und Aktivierung nur nach gesonderter Zustimmung. Öffentliche Beträge sind **unverbindliche Brutto-Schätzungen**; V2 addiert keine Umsatzsteuer. V1 bleibt im Modus `legacy` autoritativ.

## Voraussetzungen und Belege

1. Vor einer späteren, separat genehmigten Bereitstellung: Datenbank-Backup samt geprüftem Restore-Verfahren, Versionsstand, Migrationsfolge, Monitoring und Zeitfenster festhalten. Die Migrationen installieren V2-Tabellen; sie aktivieren den Modus nicht. Ein Code-Rollback nach Schema-Migration ist **nicht** gleichbedeutend mit Datenbank-Rollback.
2. Lokal im isolierten Worktree unter `app/` (Python 3.12):

   ```powershell
   $env:DJANGO_DISABLE_SCHEDULER='1'; $env:PYTHONUTF8='1'
   $env:DJANGO_SETTINGS_MODULE='dj_wishlist.settings'; $env:DJANGO_SECRET_KEY='test-only-not-secret'
   uv run --python 3.12 --with-requirements ..\requirements.txt --with pytest --with pytest-django python -m pytest -q
   uv run --python 3.12 --with-requirements ..\requirements.txt python manage.py check
   uv run --python 3.12 --with-requirements ..\requirements.txt python manage.py makemigrations --check --dry-run
   ```

   Zusätzlich Docker-Build und PostgreSQL-Konkurrenztests vor produktivem Cutover nachweisen. Der lokale SQLite-Lauf überspringt einen Row-Lock-Test; er ersetzt keine Prüfung gegen die Ziel-Datenbank.

## Konfiguration und Beobachtung

- `AppConfig.pricing_engine_mode`: `legacy` (V1 sichtbar/autorisiert), `shadow` (V1 sichtbar, gespeicherte Events erhalten eine V2-Vergleichsrevision), `v2` (V2 sichtbar). Shadow-Vergleiche für reine Vorschauen werden **nicht** persistiert.
- Genau ein aktives Profil und ein aktiver Standard-Workflow; referenzierte öffentliche Pakete/Posten, Regeln/Formeln und Overrides prüfen. Region `NW` ist der Default. Paketwerte erben globale Einstellungen oder verwenden `override|disabled` nach ihrer Konfiguration.
- Vergleich ist ohne `--apply` schreibfrei: `python manage.py pricing_v2_compare --output <lokaler-bericht.json>`; `--event-id` und `--status` schränken ein. JSON-Zeilen mit Differenzen und Diagnosen prüfen und Abweichungen fachlich freigeben. **Achtung:** `pricing_v2_compare --apply` schreibt Audit-Revisionen und hat keinen eigenständigen DB-Allowlist-Gate: nur mit gesonderter Freigabe gegen eine explizit ausgewählte isolierte Testdatenbank; niemals als unbedenklichen Produktionsbefehl behandeln.
- `python manage.py pricing_v2_reprice --output <lokaler-bericht.json>` ist Dry-Run. Inquiries unabhängig vom Datum und bestätigte Events ab `timezone.localdate()` sind Kandidaten; historische/abgesagte Events nicht. Bericht, alte und neue Bruttowerte, Fehler und betroffene IDs prüfen. `--apply` erzeugt lediglich neue Drafts, keine Änderung an `Event.total_price` oder freigegebenen Angeboten. Wiederholung bei gleichen eingefrorenen Eingaben ist idempotent.
- **Nur lokale/testweise Anwendung:** Debug-Modus und lokaler Host, `--confirm-local-apply` sowie `PRICING_V2_APPLY_DB_NAME` mit exakt dem aktiven temporären SQLite-Testdatenbankziel sind erforderlich; der Befehl prüft die tatsächlich geöffnete Verbindung. Beispiel *ohne* Schreibaufruf: `$env:PRICING_V2_APPLY_DB_NAME='<exakter temporärer Test-DB-Pfad>'`. Ein Ort im Temp-Verzeichnis beweist für sich allein keine Unbedenklichkeit; Ziel und Backup unabhängig kontrollieren. Dieser Pfad ist **kein** automatisches Verfahren zur Produktionsmigration. Produktives Apply bedarf separater Freigabe und einer ausdrücklich entwickelten/abgenommenen Vorgehensweise.

## Aktivierungs-Gate (nicht in dieser Arbeitsphase ausführen)

1. Vergleichsrevisionen und Differenzen prüfen; fehlgeschlagene/historisch fehlerhafte Snapshots beseitigen. Angebots- und Event-Historie erhalten.
2. Im Staff-Admin Referenzfälle mit erwarteten V1-, V2- und Differenzbeträgen sowie ggf. genehmigtem Schwellenwert speichern. Freigabe der Referenzfälle dokumentieren. Der Gate verwendet die gespeicherten Fälle; Prüfliste und Fingerprint müssen aktuell sein. Beträge nicht allein aus einem alten Browser-Formular übernehmen.
3. Checkliste auf Fehlerfreiheit prüfen; Aktivierung nur per explizitem Staff-POST mit CSRF. Aktivierung validiert nach AppConfig-Sperre erneut und wechselt nur den Modus. Keine Modusänderung beim bloßen Bearbeiten eines Profils.
4. Nach separat genehmigtem Cutover: öffentliche Vorschau, neue Anfrage, Admin-Vorschau, freigegebenes Angebot und Logs gegen die Referenzfälle gegenprüfen. Freigabe, Mailversand und Event-Bestätigung bleiben getrennte Handlungen.

## Angebote und Rücknahme

- Versendete Angebote verwenden eingefrorene Empfänger-, Betreff-, Body- und Preissnapshots; ein späterer Event-/Konfigurationsedit ändert diese nicht. Bei `send_failed` den Status und `EmailLog` pro Versuch prüfen, dann kontrolliert erneut senden.
- Ein hängender `sending`-Claim ist **nicht** der Beweis, dass Gmail nichts zugestellt hat. Vor manueller Recovery Gmail/Empfänger nach Möglichkeit abgleichen und die mögliche **Doppelzustellung ausdrücklich bestätigen**. Ein Claim-Token schützt nur gegen verspätetes Überschreiben des Status/Logs; Gmail garantiert hier keine Exactly-once-Zustellung. Unklaren Zustand zur manuellen Klärung offenlassen.
- Rücknahme erfolgt als gesonderter Staff-POST `rollback_to_legacy`: **nur** `pricing_engine_mode` zurück auf `legacy`, ohne V2-Revisions-/Angebotsdaten zu löschen oder `Event.total_price` umzuschreiben. Danach Vorschau/Submission unter V1 prüfen. Kein blindes Schema-Downgrade oder Zurücksetzen der Angebots-Historie.

## Separat: Google-Status „getrennt“ auf Produktion

Der früher read-only diagnostizierte Produktionsstand verwendete ältere Expiry-only-Statuslogik. Das ist kein Pricing-V2-Cutover-Befund und wird durch Aktivierung von V2 nicht automatisch behoben. Vor einer eigenen Korrektur laufenden Versionsstand und Google-Status getrennt nachweisen; keine Änderung an Produktion im Rahmen dieses Runbooks.
