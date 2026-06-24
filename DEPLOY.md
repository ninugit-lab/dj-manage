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
