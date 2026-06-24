# Production-Setup: nginx + Cloudflare Tunnel — Design

**Datum:** 2026-06-24
**Status:** Genehmigt (Design)

## Ziel

Den bestehenden dj-manage + WordPress Stack produktionsreif machen:
gesamter Traffic über nginx (Reverse Proxy + Hardening), nach außen
ausschließlich über einen Cloudflare Tunnel. Keine offenen Inbound-Ports.
WordPress unter `djredoo.de`, dj-manage unter `app.djredoo.de`.
Anschließend Installation auf einem frischen Ubuntu-24.04-Server.

## Architektur

```
                    Internet
                       │
              ┌────────▼─────────┐
              │   Cloudflare      │  TLS-Terminierung, DNS, Access (Admin)
              └────────┬─────────┘
                       │ ausgehender Tunnel (keine offenen Ports)
              ┌────────▼─────────┐
              │  cloudflared      │  Container, TUNNEL_TOKEN aus .env
              └────────┬─────────┘
                       │ ingress: alles → nginx:80
              ┌────────▼─────────┐
              │      nginx        │  Rate-Limits, Security-Header, real_ip,
              │  (Reverse Proxy)  │  Host-Routing, xmlrpc-Block
              └───┬───────────┬───┘
        djredoo.de│           │app.djredoo.de
          ┌───────▼──┐   ┌────▼───────┐
          │wordpress │   │   web      │  gunicorn (dj-manage)
          │  :80     │   │  :8000     │
          └────┬─────┘   └────────────┘
               │
        ┌──────▼──────┐
        │ wordpress_db│  MySQL, nur intern
        └─────────────┘
```

**Prinzipien:**
- Einziger Außenkontakt = ausgehender cloudflared-Tunnel. Keine
  `ports:`-Mappings am Host (kein 80/443/8500/8501/8502).
- nginx ist die zentrale Routing- und Hardening-Schicht.
- TLS endet bei Cloudflare; intern HTTP im Docker-Netz.
- Admin-Bereiche zusätzlich hinter Cloudflare Access.
- `ngrok`-Service wird vollständig entfernt.

## Komponenten

### nginx

Datei-Layout: `nginx/nginx.conf` + `nginx/conf.d/djredoo.conf`, read-only
in den Container gemountet.

**Real-IP:** Client-IP steht im Header `CF-Connecting-IP`.
`real_ip_header CF-Connecting-IP` + `set_real_ip_from <Cloudflare-IP-Ranges>`.
Ohne dies zählt Rate-Limiting alle Anfragen als eine IP (cloudflared).

**Rate-Limit-Zonen:**

| Zone        | Rate        | Burst         | Pfade                                                        |
|-------------|-------------|---------------|--------------------------------------------------------------|
| `login`     | 5 req/min   | burst=3       | `/admin/login/`, `/wp-login.php`                             |
| `api_write` | 15 req/min  | burst=5       | `/api/wish/`, `/api/submit-event/`, `/api/check-date/`       |
| `general`   | 60 req/min  | burst=20 nodelay | alles andere                                              |

**Host-Routing:**
- `server_name djredoo.de` → `proxy_pass http://wordpress:80`
- `server_name app.djredoo.de` → `proxy_pass http://web:8000`

**Weitere Härtung:**
- `client_max_body_size 16m`
- `server_tokens off`
- WordPress: `location = /xmlrpc.php { deny all; }`,
  `location ~* /(wp-config\.php|readme\.html) { deny all; }`
- Proxy-Header: `Host`, `X-Real-IP`, `X-Forwarded-For`,
  `X-Forwarded-Proto https`

### Security-Header (nginx, `add_header ... always`)

| Header                        | Wert                                                    |
|-------------------------------|---------------------------------------------------------|
| `Strict-Transport-Security`   | `max-age=31536000; includeSubDomains; preload`          |
| `X-Content-Type-Options`      | `nosniff`                                               |
| `X-Frame-Options`             | `SAMEORIGIN`                                            |
| `Referrer-Policy`             | `strict-origin-when-cross-origin`                       |
| `Permissions-Policy`          | `geolocation=(), microphone=(), camera=()`              |

### CSP (scharf, getrennt pro Host)

**dj-manage (`app.djredoo.de`):**
```
default-src 'self';
script-src 'self' https://cdn.jsdelivr.net;
style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline';
img-src 'self' data: https://i.scdn.co https://*.spotifycdn.com;
connect-src 'self' https://api.spotify.com;
font-src 'self' data:;
frame-ancestors 'self'; base-uri 'self'; form-action 'self';
object-src 'none'
```
`style-src 'unsafe-inline'` nötig wegen inline-`style`-Attributen in
Templates (z.B. Workflow-Builder-Blöcke). Inline-Scripts werden nicht
benötigt → `script-src` ohne `unsafe-inline`.

**WordPress (`djredoo.de`):**
```
default-src 'self';
script-src 'self' 'unsafe-inline';
style-src 'self' 'unsafe-inline';
img-src 'self' data:;
frame-ancestors 'self'; base-uri 'self'; object-src 'none'
```
WordPress/Theme/Plugins benötigen `'unsafe-inline'` für Scripts.

**Risiko:** Scharfe CSP kann unvorhergesehene Quellen brechen.
Mitigation: nach Deploy beide Seiten im Browser testen, Konsole auf
CSP-Violations prüfen, Quellen-Liste bei Bedarf justieren.

### Cloudflare Tunnel & Access

- `cloudflared`-Container, `TUNNEL_TOKEN` aus `.env`. Eine Ingress-Regel:
  alles → `nginx:80`. DNS-Records (`djredoo.de`, `app.djredoo.de`) zeigen
  im CF-Dashboard auf den Tunnel.
- **Cloudflare Access** (im CF-Dashboard konfiguriert, nicht im Compose)
  schützt vor Erreichen des Servers:
  - `app.djredoo.de/dj-admin/*`, `app.djredoo.de/admin/*`
  - `djredoo.de/wp-admin/*`, `djredoo.de/wp-login.php`
  - Policy: E-Mail-OTP oder Google-SSO, Allow-Liste auf eigene E-Mail(s).
- **Nicht** hinter Access: `/spotify/callback/`, `/google/callback/`
  (OAuth-Flow würde sonst brechen; durch state/PKCE geschützt).
- WordPress-Hauptseite (`djredoo.de`) bleibt öffentlich.

### Django-Settings (Production)

- `SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')`
- `SECURE_SSL_REDIRECT = True`
- `SECURE_HSTS_SECONDS = 31536000` (+ includeSubDomains/preload)
- `ALLOWED_HOSTS = ['app.djredoo.de']` (ngrok-Domain entfernt)
- `CSRF_TRUSTED_ORIGINS += ['https://app.djredoo.de']`
- Spotify/Google Redirect-URIs auf `https://app.djredoo.de/...`
- Behebt verbleibende `check --deploy`-Warnungen W004 + W008.

### docker-compose Änderungen

- `ngrok`-Service entfernt; `nginx` + `cloudflared` neu.
- `depends_on`: cloudflared → nginx; nginx → web, wordpress.
- Alle `ports:`-Mappings entfernt; internes Netz `internal_net`.
- Container-Hardening: `security_opt: [no-new-privileges:true]`,
  `cap_drop: [ALL]` + gezielte `cap_add`, `tmpfs` für `/tmp`,
  `restart: unless-stopped`.

## Secrets

- `.env.example` ins Repo (ohne Werte); echte `.env` nur auf dem Server.
- Beim Umzug neu generiert: `DJANGO_SECRET_KEY` (50+ Zeichen),
  `WP_DB_PASSWORD`, `WP_DB_ROOT_PASSWORD`.
- `TUNNEL_TOKEN` aus Cloudflare.

## Server-Hardening (Ubuntu 24.04)

- `ufw`: default deny incoming, allow nur SSH (22); kein 80/443 nötig.
- `fail2ban` für SSH.
- `unattended-upgrades` (automatische Security-Updates).
- Docker-Daemon mit `no-new-privileges` Default.

## Deployment (interaktiv mit User auf Server `djredoo`)

1. Repo klonen (`/opt/djredoo`).
2. `.env` aus `.env.example` befüllen, Secrets generieren.
3. `docker compose up -d --build`.
4. CF-Dashboard: DNS-Records + Access-Policies (User, nach DEPLOY.md).
5. Spotify/Google Redirect-URIs in Developer-Consoles umstellen (User).
6. Smoke-Test beider Seiten + CSP-Violation-Check im Browser.

## Testing

- 96 pytest-Tests im `web`-Container nach Deploy.
- Playwright-Smoketest gegen `https://app.djredoo.de` (golden path).
- Manuelle CSP-Konsolenprüfung beider Hosts.

## Offene Punkte / Annahmen

- WordPress bleibt bewusst Teil des gemeinsamen Stacks.
- SSH-Zugang zum Zielserver (`djredoo`) muss in der Ausführungsumgebung
  noch eingerichtet werden (Alias aktuell nicht auflösbar).
- Cloudflare Access wird manuell im Dashboard konfiguriert (liegt
  außerhalb des Compose-Stacks).
