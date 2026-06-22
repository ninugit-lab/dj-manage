## Architektur

### Zwei URL-Namespaces

- **Öffentlich** (`wishlist/urls.py`, kein Prefix): Gäste-Wishlist (`/`), Buchungsformular (`/buchen/`), Spotify/Google-OAuth-Callbacks, JSON-APIs (`/api/search/`, `/api/wish/`, `/api/wishes-stream/`)
- **DJ-Admin** (`wishlist/admin_urls.py`, Prefix `/dj-admin/`, app_name `dj_admin`): Custom-Dashboard, Event-CRUD, Live-Wishlist, Kalender, Konfiguration. Alle Views sind `@staff_member_required`.

Django's eingebauter Admin unter `/admin/` existiert parallel, wird aber kaum genutzt.

### Models (wishlist/models.py)

| Model | Zweck |
|---|---|
| `Event` | Zentrales Model — enthält Event-Daten, Kundendaten, Spotify-Playlist-IDs, Wishlist-Einstellungen. `is_active=True` → nur 1 gleichzeitig (enforced in `save()`). Status-Workflow: inquiry → confirmed → past/cancelled. |
| `SongWish` | Song-Wunsch eines Gastes, FK zu Event. UniqueConstraint auf (event, spotify_track_id). |
| `AppConfig` | Singleton (pk=1, `AppConfig.load()`). Globale Einstellungen: API-Keys, Defaults, E-Mail-Templates, DJ-Info. |
| `SpotifyToken` / `GoogleToken` | OAuth-Token-Speicher mit `is_expired` Property und Auto-Refresh in den Services. |
| `PriceItem` / `EventPriceCalculation` | Modularer Preiskalkulator — PriceItems sind wiederverwendbare Posten, EventPriceCalculation verknüpft sie pro Event. |
| `BlockedClient` | Session/IP-basierte Sperrung, event-spezifisch oder global, mit optionalem Ablaufdatum. |
| `EmailLog` | Protokoll versendeter E-Mails. |

### Service-Layer

- **`spotify.py`** → `SpotifyService`: Thread-safe Token-Refresh mit Lock, Retry-Logik (Timeout, 5xx, 401), Playlist-Management. Spotify-API nutzt `/playlists/{id}/items` (nicht `/tracks`).
- **`google_services.py`** → `GoogleService`: Google Calendar + Gmail-Integration, gleiche Retry/Refresh-Architektur.

### Frontend-Stack

- **Django-Templates** mit **Hotwire Turbo** (via CDN, v7.3.0) für Partial-Updates (Turbo Frames/Streams)
- **CSS:** Inline in `base.html`, Dark-Mode-Design mit CSS Custom Properties (`--bg`, `--accent`, `--accent2`)
- **Fonts:** Bebas Neue (Headlines), DM Sans (Body), Space Mono (Monospace/Labels)
- **Kein Build-System** — kein npm, kein Webpack. Alles direkt in Templates.
- **WhiteNoise** für Static-File-Serving in Produktion

### Infrastruktur

- **Docker Compose**: `web` (Django + Gunicorn) + `ngrok` (Tunnel für Webhooks/OAuth)
- **SQLite** mit WAL-Modus (Pragmas via `connection_created`-Signal in settings.py)
- **Gunicorn**: gthread-Worker, konfigurierbar über ENV (`GUNICORN_WORKERS`, `GUNICORN_THREADS`)
- **Sessions:** Signed-Cookie-Backend (kein DB-Write pro Request)
- **Cache:** LocMem (In-Process, 5 Min TTL)

### Verzeichnisstruktur

- `app/` — aktives Django-Projekt (wird als Volume gemountet)
- Templates in `app/templates/wishlist/` (öffentlich) und `app/templates/dj_admin/` (Admin)

## Konventionen

- `AppConfig` ist Singleton — immer über `AppConfig.load()` zugreifen, nie direkt instanziieren
- Spotify-Playlist-IDs werden beim Speichern automatisch aus URLs extrahiert (`_extract_spotify_id`)
- Nur ein Event kann gleichzeitig `is_active=True` sein — wird im Model enforced
- Event-Status-Workflow: `inquiry` → `confirmed` → `past` (automatisch wenn Datum vergangen)
- Öffentliche APIs unter `/api/` geben JSON zurück, Admin-Views rendern HTML (teils als Turbo Frames)
- Umgebungsvariablen in `.env` (nicht committen) — `.env.example` als Vorlage
