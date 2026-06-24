# Production-Setup nginx + Cloudflare Tunnel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den dj-manage + WordPress Stack produktionsreif machen — gesamter Traffic über nginx (Reverse Proxy, Rate-Limits, Security-Header, scharfe CSP), nach außen nur via Cloudflare Tunnel, deploybar auf einem frischen Ubuntu-24.04-Server.

**Architecture:** `cloudflared → nginx:80 → {wordpress:80, web:8000}`. Keine offenen Inbound-Ports; einziger Außenkontakt ist der ausgehende Tunnel. nginx routet per `Host`-Header und ist die zentrale Hardening-Schicht. Admin-Pfade zusätzlich hinter Cloudflare Access.

**Tech Stack:** Docker Compose, nginx (alpine), cloudflared, Django/gunicorn, WordPress, MySQL 8, Ubuntu 24.04.

**Spec:** `docs/superpowers/specs/2026-06-24-production-cloudflare-nginx-design.md`

---

## Hinweis zur Arbeitsweise

Dieser Plan ist überwiegend Infrastruktur-Konfiguration. Statt Unit-TDD validiert jede Task ihr Artefakt mit einem konkreten Befehl (`nginx -t`, `docker compose config`, `python manage.py check --deploy`, HTTP-Smoke-Test). Lokal wird auf Port `8500` getestet (bestehendes Mapping bleibt bis Task 9), erst beim Server-Deploy fällt es weg.

Arbeitsverzeichnis: `/home/camp/Server/workspace/Rene/dj-manage`. Branch: `master`.

---

## File Structure

| Datei | Verantwortung | Task |
|---|---|---|
| `nginx/nginx.conf` | Haupt-Config: `worker`, `http`-Block, Rate-Limit-Zonen, real_ip, Map-Direktiven | 1, 2 |
| `nginx/conf.d/djredoo.conf` | Zwei `server`-Blöcke (WP + dj-manage), Routing, CSP, location-Regeln | 3, 4 |
| `cloudflared/config.yml` | Ingress-Regel → nginx (optional bei Token-Tunnel; dokumentiert) | 6 |
| `docker-compose.yml` | nginx + cloudflared neu, ngrok raus, Ports raus, Hardening | 5, 6, 9 |
| `app/dj_wishlist/settings.py` | Prod-Security-Settings (Proxy-SSL, HSTS, Hosts, CSRF) | 7 |
| `.env.example` | Secret-Vorlage ohne Werte | 8 |
| `DEPLOY.md` | Server-Umzug + Cloudflare-Dashboard-Schritte | 10 |
| `docs/server-hardening.md` | ufw/fail2ban/unattended-upgrades Schritte | 11 |

---

## Task 1: nginx-Grundgerüst + Rate-Limit-Zonen

**Files:**
- Create: `nginx/nginx.conf`

- [ ] **Step 1: nginx.conf anlegen**

```nginx
user  nginx;
worker_processes  auto;
error_log  /var/log/nginx/error.log warn;
pid        /var/run/nginx.pid;

events { worker_connections 1024; }

http {
    include       /etc/nginx/mime.types;
    default_type  application/octet-stream;

    server_tokens off;
    client_max_body_size 16m;
    sendfile on;
    keepalive_timeout 65;

    log_format main '$remote_addr - $remote_user [$time_local] "$request" '
                    '$status $body_bytes_sent "$http_referer" "$http_user_agent"';
    access_log /var/log/nginx/access.log main;

    # --- Rate-Limit-Zonen (pro echter Client-IP, s. real_ip in Task 2) ---
    limit_req_zone $binary_remote_addr zone=login:10m     rate=5r/m;
    limit_req_zone $binary_remote_addr zone=api_write:10m rate=15r/m;
    limit_req_zone $binary_remote_addr zone=general:10m   rate=60r/m;
    limit_req_status 429;

    include /etc/nginx/conf.d/*.conf;
}
```

- [ ] **Step 2: Syntax validieren (via temporärem Container)**

Run:
```bash
docker run --rm -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.27-alpine nginx -t
```
Expected: Fehler `host not found in upstream` oder `open() conf.d` ist OK an dieser Stelle (conf.d noch leer). Die Zeile `nginx: configuration file ... syntax is ok` muss erscheinen. Falls `conf.d`-Include scheitert (leeres Verzeichnis), in Step 1 ignorierbar — wird in Task 3 behoben.

- [ ] **Step 3: Commit**

```bash
git add nginx/nginx.conf
git commit -m "feat(nginx): http-Grundgeruest mit Rate-Limit-Zonen"
```

---

## Task 2: Cloudflare Real-IP-Wiederherstellung

**Files:**
- Modify: `nginx/nginx.conf` (http-Block, nach `limit_req_status`)

- [ ] **Step 1: real_ip-Block einfügen**

Füge im `http`-Block direkt vor `include /etc/nginx/conf.d/*.conf;` ein:

```nginx
    # --- Cloudflare Real-IP (Client-IP steht in CF-Connecting-IP) ---
    # IPv4 Ranges (Stand pflegen via https://www.cloudflare.com/ips/)
    set_real_ip_from 173.245.48.0/20;
    set_real_ip_from 103.21.244.0/22;
    set_real_ip_from 103.22.200.0/22;
    set_real_ip_from 103.31.4.0/22;
    set_real_ip_from 141.101.64.0/18;
    set_real_ip_from 108.162.192.0/18;
    set_real_ip_from 190.93.240.0/20;
    set_real_ip_from 188.114.96.0/20;
    set_real_ip_from 197.234.240.0/22;
    set_real_ip_from 198.41.128.0/17;
    set_real_ip_from 162.158.0.0/15;
    set_real_ip_from 104.16.0.0/13;
    set_real_ip_from 104.24.0.0/14;
    set_real_ip_from 172.64.0.0/13;
    set_real_ip_from 131.0.72.0/22;
    # IPv6 Ranges
    set_real_ip_from 2400:cb00::/32;
    set_real_ip_from 2606:4700::/32;
    set_real_ip_from 2803:f800::/32;
    set_real_ip_from 2405:b500::/32;
    set_real_ip_from 2405:8100::/32;
    set_real_ip_from 2a06:98c0::/29;
    set_real_ip_from 2c0f:f248::/32;
    real_ip_header CF-Connecting-IP;
    real_ip_recursive on;
```

- [ ] **Step 2: Syntax validieren**

Run:
```bash
docker run --rm -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" nginx:1.27-alpine nginx -t 2>&1 | grep -E "syntax is ok|emerg"
```
Expected: `syntax is ok` (Upstream-/conf.d-Hinweise weiter möglich, kein `emerg` zu real_ip-Direktiven).

- [ ] **Step 3: Commit**

```bash
git add nginx/nginx.conf
git commit -m "feat(nginx): Cloudflare Real-IP-Wiederherstellung"
```

---

## Task 3: Site-Config — Routing beider Hosts

**Files:**
- Create: `nginx/conf.d/djredoo.conf`

- [ ] **Step 1: Site-Config mit beiden server-Blöcken anlegen**

```nginx
# --- gemeinsame Proxy-Header ---
proxy_set_header Host              $host;
proxy_set_header X-Real-IP         $remote_addr;
proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
proxy_set_header X-Forwarded-Proto https;
proxy_http_version 1.1;

# ======================= WordPress: djredoo.de =======================
server {
    listen 80;
    server_name djredoo.de;

    limit_req zone=general burst=20 nodelay;

    # Hardening: gefaehrliche WP-Pfade sperren
    location = /xmlrpc.php { deny all; }
    location ~* /(wp-config\.php|readme\.html|license\.txt) { deny all; }

    # Login strenger limitieren (Access schuetzt zusaetzlich via CF-Dashboard)
    location = /wp-login.php {
        limit_req zone=login burst=3;
        proxy_pass http://wordpress:80;
    }

    location / {
        proxy_pass http://wordpress:80;
    }
}

# ======================= dj-manage: app.djredoo.de ===================
server {
    listen 80;
    server_name app.djredoo.de;

    limit_req zone=general burst=20 nodelay;

    location = /admin/login/ {
        limit_req zone=login burst=3;
        proxy_pass http://web:8000;
    }

    location = /api/wish/          { limit_req zone=api_write burst=5; proxy_pass http://web:8000; }
    location = /api/submit-event/  { limit_req zone=api_write burst=5; proxy_pass http://web:8000; }
    location = /api/check-date/    { limit_req zone=api_write burst=5; proxy_pass http://web:8000; }

    location / {
        proxy_pass http://web:8000;
    }
}
```

- [ ] **Step 2: Syntax mit Dummy-Upstreams validieren**

Da `nginx -t` die Upstream-Hostnamen (`web`, `wordpress`) nicht auflösen kann, gegen das echte Compose-Netz testen ist erst ab Task 9 möglich. Hier reine Syntaxprüfung mit aufgelösten Namen via `resolver`-Trick weglassen — stattdessen Klammer-/Direktiven-Syntax prüfen:

Run:
```bash
docker run --rm \
  -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$PWD/nginx/conf.d:/etc/nginx/conf.d:ro" \
  --add-host web:127.0.0.1 --add-host wordpress:127.0.0.1 \
  nginx:1.27-alpine nginx -t 2>&1 | grep -E "syntax is ok|test is successful|emerg"
```
Expected: `syntax is ok` und `test is successful` (durch `--add-host` sind die Upstream-Namen auflösbar).

- [ ] **Step 3: Commit**

```bash
git add nginx/conf.d/djredoo.conf
git commit -m "feat(nginx): Host-Routing fuer djredoo.de und app.djredoo.de"
```

---

## Task 4: Security-Header + scharfe CSP pro Host

**Files:**
- Modify: `nginx/conf.d/djredoo.conf`

- [ ] **Step 1: Gemeinsame Header als Snippet definieren**

Füge ganz oben in `nginx/conf.d/djredoo.conf` (vor den `proxy_set_header`-Zeilen) NICHT ein — `add_header` muss in den `server`-Blöcken stehen, damit `always` pro Antwort greift. Ergänze in **beiden** `server`-Blöcken direkt nach der `limit_req zone=general`-Zeile:

```nginx
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;
    add_header Permissions-Policy "geolocation=(), microphone=(), camera=()" always;
```

- [ ] **Step 2: CSP für WordPress (`djredoo.de`-Block) ergänzen**

Direkt nach den Headern aus Step 1 im **WordPress**-`server`-Block:

```nginx
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'; base-uri 'self'; object-src 'none'" always;
```

- [ ] **Step 3: CSP für dj-manage (`app.djredoo.de`-Block) ergänzen**

Direkt nach den Headern aus Step 1 im **dj-manage**-`server`-Block:

```nginx
    add_header Content-Security-Policy "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data: https://i.scdn.co https://*.spotifycdn.com; connect-src 'self' https://api.spotify.com; font-src 'self' data:; frame-ancestors 'self'; base-uri 'self'; form-action 'self'; object-src 'none'" always;
```

- [ ] **Step 4: Syntax validieren**

Run:
```bash
docker run --rm \
  -v "$PWD/nginx/nginx.conf:/etc/nginx/nginx.conf:ro" \
  -v "$PWD/nginx/conf.d:/etc/nginx/conf.d:ro" \
  --add-host web:127.0.0.1 --add-host wordpress:127.0.0.1 \
  nginx:1.27-alpine nginx -t 2>&1 | grep -E "test is successful|emerg"
```
Expected: `test is successful`.

- [ ] **Step 5: Commit**

```bash
git add nginx/conf.d/djredoo.conf
git commit -m "feat(nginx): Security-Header und scharfe CSP pro Host"
```

---

## Task 5: nginx-Service ins Compose, ngrok entfernen

**Files:**
- Modify: `docker-compose.yml`

- [ ] **Step 1: ngrok-Service entfernen**

Lösche den kompletten `ngrok:`-Service-Block (inkl. `image`, `command`, `environment`, `ports`, `depends_on`).

- [ ] **Step 2: web-Port-Mapping entfernen, internes Netz vorbereiten**

Im `web`-Service `ports:`-Block (`- "8500:8000"`) ENTFERNEN und durch `expose` ersetzen:

```yaml
    expose:
      - "8000"
```

- [ ] **Step 3: nginx-Service hinzufügen**

Füge als neuen Service hinzu:

```yaml
  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    volumes:
      - ./nginx/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./nginx/conf.d:/etc/nginx/conf.d:ro
    depends_on:
      - web
      - wordpress
    security_opt:
      - no-new-privileges:true
    tmpfs:
      - /tmp
      - /var/run
      - /var/cache/nginx
```

- [ ] **Step 4: Compose-Syntax validieren**

Run:
```bash
docker compose config >/dev/null && echo "compose ok"
```
Expected: `compose ok` (keine YAML-/Schema-Fehler).

- [ ] **Step 5: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(compose): nginx-Service ergaenzt, ngrok entfernt"
```

---

## Task 6: cloudflared-Service + Token

**Files:**
- Modify: `docker-compose.yml`
- Create: `cloudflared/config.yml` (Referenz-Doku für Nicht-Token-Tunnel)

- [ ] **Step 1: cloudflared-Service hinzufügen (Token-Modus)**

```yaml
  cloudflared:
    image: cloudflare/cloudflared:latest
    restart: unless-stopped
    command: tunnel --no-autoupdate run --token ${TUNNEL_TOKEN}
    depends_on:
      - nginx
    security_opt:
      - no-new-privileges:true
```

Im Token-Modus wird das gesamte Ingress-Routing im Cloudflare-Dashboard
definiert (Public Hostname → Service `http://nginx:80`). Eine lokale
`config.yml` ist dann NICHT nötig.

- [ ] **Step 2: Referenz-config.yml für credentials-Modus anlegen**

Für den Fall eines credentials-basierten Tunnels (Alternative zum Token),
als Doku im Repo:

```yaml
# cloudflared/config.yml — nur fuer credentials-basierten Tunnel.
# Im Token-Modus (siehe docker-compose) wird Ingress im CF-Dashboard
# konfiguriert und diese Datei nicht verwendet.
tunnel: <TUNNEL-ID>
credentials-file: /etc/cloudflared/<TUNNEL-ID>.json

ingress:
  - hostname: djredoo.de
    service: http://nginx:80
  - hostname: app.djredoo.de
    service: http://nginx:80
  - service: http_status:404
```

- [ ] **Step 3: Compose-Syntax validieren**

Run:
```bash
TUNNEL_TOKEN=dummy docker compose config >/dev/null && echo "compose ok"
```
Expected: `compose ok`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml cloudflared/config.yml
git commit -m "feat(compose): cloudflared-Tunnel-Service"
```

---

## Task 7: Django Production-Security-Settings

**Files:**
- Modify: `app/dj_wishlist/settings.py:5-19`

- [ ] **Step 1: Proxy-SSL + HSTS + SSL-Redirect ergänzen**

Ersetze den Block um `_secure_cookies` (Zeilen ~11-19) durch:

```python
_secure_cookies = os.environ.get('SECURE_COOKIES', 'False') == 'True'
CSRF_COOKIE_SECURE = _secure_cookies
SESSION_COOKIE_SECURE = _secure_cookies

# Hinter Reverse-Proxy/Cloudflare: Original-Schema aus Header lesen
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'False') == 'True'
SECURE_HSTS_SECONDS = int(os.environ.get('SECURE_HSTS_SECONDS', '0'))
SECURE_HSTS_INCLUDE_SUBDOMAINS = SECURE_HSTS_SECONDS > 0
SECURE_HSTS_PRELOAD = SECURE_HSTS_SECONDS > 0

CSRF_TRUSTED_ORIGINS = [
    "https://app.djredoo.de",
    "https://*.ngrok-free.dev",
    "http://localhost:8500",
]
```

- [ ] **Step 2: Settings-Import-Check (lokal, ohne Prod-Env)**

Run:
```bash
docker exec dj-manage-web-1 python manage.py check 2>&1 | tail -5
```
Expected: `System check identified no issues` (oder nur unkritische Warnungen) — kein Import-/Syntaxfehler.

- [ ] **Step 3: Prod-Deploy-Check simulieren**

Run:
```bash
docker exec -e SECURE_COOKIES=True -e SECURE_SSL_REDIRECT=True -e SECURE_HSTS_SECONDS=31536000 -e DEBUG=False -e ALLOWED_HOSTS=app.djredoo.de dj-manage-web-1 python manage.py check --deploy 2>&1 | tail -6
```
Expected: nur noch W009 möglich falls Key kurz — W004/W008/W012/W016 verschwunden.

- [ ] **Step 4: Commit**

```bash
git add app/dj_wishlist/settings.py
git commit -m "feat(security): Django Prod-Settings (Proxy-SSL, HSTS, SSL-Redirect)"
```

---

## Task 8: .env.example

**Files:**
- Create: `.env.example`

- [ ] **Step 1: Vorlage ohne Werte anlegen**

```bash
# === Django ===
# Key generieren: python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
DJANGO_SECRET_KEY=
DEBUG=False
ALLOWED_HOSTS=app.djredoo.de
SECURE_COOKIES=True
SECURE_SSL_REDIRECT=True
SECURE_HSTS_SECONDS=31536000

# === Spotify (Redirect-URI auch in Spotify-Dashboard eintragen) ===
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
SPOTIFY_REDIRECT_URI=https://app.djredoo.de/spotify/callback/

# === Google (Redirect-URI auch in Google-Cloud-Console eintragen) ===
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=https://app.djredoo.de/google/callback/

# === Cloudflare Tunnel (Token aus CF Zero-Trust Dashboard) ===
TUNNEL_TOKEN=

# === WordPress / MySQL (starke Passwoerter generieren: openssl rand -base64 24) ===
WP_DB_NAME=wordpress
WP_DB_USER=
WP_DB_PASSWORD=
WP_DB_ROOT_PASSWORD=

# === Gunicorn ===
GUNICORN_WORKERS=4
GUNICORN_THREADS=4
```

- [ ] **Step 2: Sicherstellen, dass echte .env weiter ignoriert wird**

Run:
```bash
grep -q "^.env$" .gitignore && echo "ignored" || echo "FEHLT"
```
Expected: `ignored`.

- [ ] **Step 3: Commit**

```bash
git add .env.example
git commit -m "docs(env): .env.example mit allen Prod-Variablen"
```

---

## Task 9: Lokaler Integrationstest des nginx-Stacks

**Files:**
- Keine (temporäre Override-Datei, wird nicht committet)

- [ ] **Step 1: Temporäres Port-Mapping für nginx, damit lokal testbar**

Erstelle `docker-compose.override.yml` (lokal, durch `.gitignore` ausgeschlossen — siehe Step 5):

```yaml
services:
  nginx:
    ports:
      - "8480:80"
```

- [ ] **Step 2: Stack starten (ohne cloudflared, da kein Token lokal)**

Run:
```bash
TUNNEL_TOKEN=dummy docker compose up -d --build web wordpress wordpress_db nginx
until curl -sf -H "Host: app.djredoo.de" http://localhost:8480/ -o /dev/null; do sleep 2; done; echo READY
```
Expected: `READY` — nginx leitet an `web` weiter.

- [ ] **Step 2b: Routing beider Hosts prüfen**

Run:
```bash
echo "=== app.djredoo.de ==="; curl -s -o /dev/null -w "%{http_code}\n" -H "Host: app.djredoo.de" http://localhost:8480/
echo "=== djredoo.de (WP) ==="; curl -s -o /dev/null -w "%{http_code}\n" -H "Host: djredoo.de" http://localhost:8480/
echo "=== xmlrpc gesperrt ==="; curl -s -o /dev/null -w "%{http_code}\n" -H "Host: djredoo.de" http://localhost:8480/xmlrpc.php
```
Expected: app → `200` oder `302`; WP → `200`/`302`; xmlrpc → `403`.

- [ ] **Step 3: Security-Header prüfen**

Run:
```bash
curl -sI -H "Host: app.djredoo.de" http://localhost:8480/ | grep -iE "content-security-policy|x-frame-options|strict-transport|x-content-type"
```
Expected: alle vier Header vorhanden; CSP enthält `cdn.jsdelivr.net` und `api.spotify.com`.

- [ ] **Step 4: Rate-Limit prüfen (login-Zone)**

Run:
```bash
for i in $(seq 1 10); do curl -s -o /dev/null -w "%{http_code} " -H "Host: app.djredoo.de" http://localhost:8480/admin/login/; done; echo
```
Expected: erste Anfragen `200`, danach `429` (Limit 5r/m + burst=3 greift).

- [ ] **Step 5: override aus Git ausschließen + Stack stoppen**

Run:
```bash
grep -q "docker-compose.override.yml" .gitignore || echo "docker-compose.override.yml" >> .gitignore
docker compose down
git add .gitignore
git commit -m "chore: docker-compose.override.yml aus Git ausschliessen"
```
Expected: Commit erstellt; `docker-compose.override.yml` bleibt lokal.

---

## Task 10: DEPLOY.md

**Files:**
- Create: `DEPLOY.md`

- [ ] **Step 1: Deployment-Anleitung schreiben**

````markdown
# Deployment — djredoo.de Stack

## Voraussetzungen
- Zielserver: Ubuntu 24.04, Docker + Compose-Plugin installiert
- Domain `djredoo.de` bei Cloudflare (Nameserver auf CF)
- SSH-Zugang zum Server

## 1. Repo + Secrets
```bash
sudo mkdir -p /opt/djredoo && sudo chown $USER /opt/djredoo
git clone <REPO-URL> /opt/djredoo && cd /opt/djredoo
cp .env.example .env
# DJANGO_SECRET_KEY generieren:
docker run --rm python:3.12-slim python -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'!@#$%^&*(-_=+)') for _ in range(60)))"
# MySQL-Passwoerter:
openssl rand -base64 24   # je fuer WP_DB_PASSWORD und WP_DB_ROOT_PASSWORD
# .env mit allen Werten befuellen (inkl. TUNNEL_TOKEN aus Schritt 2)
nano .env
```

## 2. Cloudflare Tunnel anlegen
1. Cloudflare Dashboard → Zero Trust → Networks → Tunnels → **Create a tunnel** (Typ: Cloudflared).
2. Tunnel benennen (`djredoo`), **Token** kopieren → in `.env` als `TUNNEL_TOKEN`.
3. **Public Hostnames** hinzufügen:
   - `djredoo.de` → Service `http://nginx:80`
   - `app.djredoo.de` → Service `http://nginx:80`
4. DNS-Records werden von CF automatisch als CNAME auf den Tunnel gesetzt.

## 3. Cloudflare Access (Admin-Schutz)
Zero Trust → Access → Applications → **Add an application** (Self-hosted), je eine pro Pfad:
- `app.djredoo.de/dj-admin` und `app.djredoo.de/admin`
- `djredoo.de/wp-admin` und `djredoo.de/wp-login.php`

Policy je App: **Allow**, Selector `Emails` → eigene E-Mail(s). Login-Methode: One-time PIN oder Google.

> Hinweis: `/spotify/callback/` und `/google/callback/` NICHT hinter Access legen — bricht OAuth.

## 4. OAuth Redirect-URIs umstellen
- Spotify Developer Dashboard → App → Redirect URIs: `https://app.djredoo.de/spotify/callback/`
- Google Cloud Console → OAuth-Client → Redirect URIs: `https://app.djredoo.de/google/callback/`

## 5. Starten
```bash
docker compose up -d --build
docker compose ps          # alle Services healthy/Up
docker compose logs -f cloudflared   # "Registered tunnel connection"
```

## 6. Verifikation
```bash
# Tests im web-Container
docker compose exec web python -m pytest -q
# Deploy-Check
docker compose exec web python manage.py check --deploy
```
- Browser: `https://app.djredoo.de` und `https://djredoo.de` öffnen.
- DevTools-Konsole auf CSP-Violations prüfen (beide Seiten).
- Admin-Pfade: CF-Access-Login muss erscheinen.

## Updates
```bash
cd /opt/djredoo && git pull && docker compose up -d --build
```
````

- [ ] **Step 2: Commit**

```bash
git add DEPLOY.md
git commit -m "docs: DEPLOY.md fuer Server-Umzug und Cloudflare-Setup"
```

---

## Task 11: Server-Hardening-Doku + Ausführung

**Files:**
- Create: `docs/server-hardening.md`

- [ ] **Step 1: Hardening-Doku schreiben**

````markdown
# Server-Hardening — Ubuntu 24.04

Auf dem Zielserver als root/sudo ausführen.

## Firewall (ufw) — nur SSH eingehend
Da Cloudflared ausgehend verbindet, sind KEINE eingehenden 80/443 nötig.
```bash
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw enable
ufw status verbose
```

## fail2ban (SSH-Bruteforce-Schutz)
```bash
apt-get update && apt-get install -y fail2ban
systemctl enable --now fail2ban
fail2ban-client status sshd
```

## Automatische Security-Updates
```bash
apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades   # "Yes"
```

## Docker-Daemon: no-new-privileges Default
```bash
cat >/etc/docker/daemon.json <<'EOF'
{
  "no-new-privileges": true,
  "log-driver": "json-file",
  "log-opts": { "max-size": "10m", "max-file": "3" }
}
EOF
systemctl restart docker
```

## Verifikation
```bash
ufw status            # nur 22/OpenSSH ALLOW
fail2ban-client status sshd
docker info | grep -i "no new privileges"
```
````

- [ ] **Step 2: Commit**

```bash
git add docs/server-hardening.md
git commit -m "docs: Server-Hardening Anleitung (ufw, fail2ban, auto-updates)"
```

- [ ] **Step 3: Auf Zielserver ausführen (interaktiv mit User)**

> **Blocker:** SSH-Alias `djredoo` ist aus der Ausführungsumgebung aktuell nicht auflösbar. Vor diesem Schritt mit User SSH-Zugang einrichten (Alias in `~/.ssh/config` oder Host-IP + `-i ~/.ssh/id_ed25519`).

Nach Verbindung: Schritte aus `docs/server-hardening.md` ausführen, dann `DEPLOY.md` durchlaufen.

---

## Task 12: End-to-End-Verifikation (nach Server-Deploy)

**Files:**
- Keine (Verifikation gegen Live-System)

- [ ] **Step 1: pytest im Live-Container**

Run (auf Server):
```bash
docker compose exec web python -m pytest -q
```
Expected: `96 passed` (oder aktuelle Gesamtzahl).

- [ ] **Step 2: Deploy-Check**

Run:
```bash
docker compose exec web python manage.py check --deploy
```
Expected: keine W004/W008/W009/W012/W016-Warnungen mehr.

- [ ] **Step 3: Playwright-Smoketest gegen Live-Domain**

Run (lokal, mit Tunnel erreichbar):
```bash
TARGET_URL=https://app.djredoo.de E2E_USER=<admin> E2E_PASS=<pw> \
  node tests/e2e/full_app_e2e.js
```
Expected: alle Schritte ✅ (Admin-Schritte erfordern ggf. CF-Access-Bypass-Token oder Test über Service-Token — dokumentieren falls Access blockt).

- [ ] **Step 4: CSP-Violation-Check beider Hosts**

Browser-DevTools-Konsole auf `https://djredoo.de` und `https://app.djredoo.de` öffnen, durch Hauptfunktionen klicken, auf `Refused to ... Content Security Policy` achten. Bei Violations: betroffene Quelle in der jeweiligen CSP in `nginx/conf.d/djredoo.conf` ergänzen, `docker compose restart nginx`.

- [ ] **Step 5: Abschluss-Commit (falls CSP justiert)**

```bash
git add nginx/conf.d/djredoo.conf
git commit -m "fix(nginx): CSP-Quellen nach Live-Verifikation justiert"
```

---

## Self-Review-Ergebnis

- **Spec-Abdeckung:** nginx (T1-4), Real-IP (T2), Rate-Limits (T1,T3), Security-Header+CSP (T4), Compose/ngrok-Entfernung (T5), cloudflared (T6), Django-Settings (T7), Secrets/.env (T8), lokaler Test (T9), Deployment+CF-Access (T10), Server-Hardening (T11), E2E-Verifikation (T12). Alle Spec-Abschnitte abgedeckt.
- **Cloudflare Access:** im CF-Dashboard (T10 Schritt 3), korrekt als Nicht-Compose-Schritt markiert.
- **OAuth-Callbacks:** explizit von Access ausgenommen (T10).
- **Bekannter Blocker:** SSH-Zugang zum Zielserver (T11 Schritt 3) — vor Ausführung einzurichten.
