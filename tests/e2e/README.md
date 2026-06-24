# E2E-Smoketest (Playwright)

`full_app_e2e.js` fährt die gesamte App durch: Login, Dashboard,
Preiskalkulator (Posten/Paket/Regel/Formel + Workflow inkl. Regelauswahl),
Event-Anlage, öffentliche Wishlist, Buchungsformular, Config, Kalender.

## Voraussetzungen

- Laufende App (Standard: `http://localhost:8500`)
- Ein Staff-/Superuser zum Einloggen
- Node mit installiertem Playwright + Chromium

## Test-Superuser anlegen

```bash
docker exec dj-manage-web-1 python manage.py shell -c "
from django.contrib.auth import get_user_model
U=get_user_model(); U.objects.filter(username='pwtest').delete()
U.objects.create_superuser('pwtest','pwtest@example.com','PwTest!2026')"
```

## Ausführen

Über die playwright-skill-Runner (löst Modulpfade auf):

```bash
cd <playwright-skill-dir> && node run.js /pfad/zu/tests/e2e/full_app_e2e.js
```

Oder direkt, wenn `playwright` lokal installiert ist:

```bash
node tests/e2e/full_app_e2e.js
```

## Konfiguration (ENV)

| Variable        | Default                  | Zweck                          |
|-----------------|--------------------------|--------------------------------|
| `TARGET_URL`    | `http://localhost:8500`  | Basis-URL der App              |
| `E2E_USER`      | `pwtest`                 | Login-Benutzername             |
| `E2E_PASS`      | `PwTest!2026`            | Login-Passwort                 |
| `E2E_HEADLESS`  | `true`                   | `false` für sichtbaren Browser |
| `E2E_SHOT_DIR`  | `/tmp/dj-e2e`            | Prefix der Screenshot-Dateien  |

## Hinweise

- Der Test legt Datensätze mit Prefix `E2E ` an (Posten, Paket, Regel,
  Formel, Workflow, Event). Aufräumen z.B.:

  ```bash
  docker exec dj-manage-web-1 python manage.py shell -c "
  from wishlist.models import PricingRule, PriceItem, PricingPackage, PricingFormula, PricingWorkflow, Event
  for M in (PricingRule, PriceItem, PricingPackage, PricingFormula, PricingWorkflow, Event):
      M.objects.filter(name__startswith='E2E ').delete()"
  ```

- Code-Änderungen am Backend erfordern ggf. `docker compose restart web`,
  da der Autoreload im Container nicht zuverlässig greift.
