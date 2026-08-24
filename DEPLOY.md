# Deployment — dj-redoo.de Stack

## Voraussetzungen
- Zielserver: Ubuntu 24.04, Docker + Compose-Plugin installiert
- Domain `dj-redoo.de` bei Cloudflare (Nameserver auf CF)
- SSH-Zugang zum Server

## 1. Repo + Secrets
```bash
sudo mkdir -p /opt/dj-redoo && sudo chown $USER /opt/dj-redoo
git clone <REPO-URL> /opt/dj-redoo && cd /opt/dj-redoo
cp .env.example .env
# DJANGO_SECRET_KEY generieren:
docker run --rm python:3.12-slim python -c "import secrets,string; print(''.join(secrets.choice(string.ascii_letters+string.digits+'!@#$%^&*(-_=+)') for _ in range(60)))"
# .env mit allen Werten befüllen (inkl. TUNNEL_TOKEN aus Schritt 2)
nano .env
```

## 2. Cloudflare Tunnel anlegen
1. Cloudflare Dashboard → Zero Trust → Networks → Tunnels → **Create a tunnel** (Typ: Cloudflared).
2. Tunnel benennen (`djredoo`), **Token** kopieren → in `.env` als `TUNNEL_TOKEN`.
3. **Public Hostnames** hinzufügen:
   - `dj-redoo.de` → Service `http://nginx:80`
   - `app.dj-redoo.de` → Service `http://nginx:80`
4. DNS-Records werden von CF automatisch als CNAME auf den Tunnel gesetzt.

## 3. Cloudflare Access (Admin-Schutz)
Zero Trust → Access → Applications → **Add an application** (Self-hosted), je eine pro Pfad:
- `app.dj-redoo.de/dj-admin` und `app.dj-redoo.de/admin`

Policy je App: **Allow**, Selector `Emails` → eigene E-Mail(s). Login-Methode: One-time PIN oder Google.

> Hinweis: `/spotify/callback/` und `/google/callback/` NICHT hinter Access legen — bricht OAuth.

## 4. OAuth Redirect-URIs umstellen
- Spotify Developer Dashboard → App → Redirect URIs: `https://app.dj-redoo.de/spotify/callback/`
- Google Cloud Console → OAuth-Client → Redirect URIs: `https://app.dj-redoo.de/google/callback/`

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
- Browser: `https://app.dj-redoo.de` und `https://dj-redoo.de` öffnen.
- DevTools-Konsole auf CSP-Violations prüfen (beide Seiten).
- Admin-Pfade: CF-Access-Login muss erscheinen.

## SSH-Zugang

Auf dem Entwicklungsrechner ist ein dedizierter Deploy-Key hinterlegt
(`~/.ssh/djredoo_ed25519`), Host-Alias in `~/.ssh/config`:

```
Host djredoo djredoo-prod
    HostName 100.69.222.62
    Port 54322
    User redooad
    IdentityFile ~/.ssh/djredoo_ed25519
    IdentitiesOnly yes
    StrictHostKeyChecking yes
    ServerAliveInterval 30
```

Damit genügt `ssh djredoo`. Passwort-Login wird nicht mehr gebraucht.
Neuen Rechner anbinden:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/djredoo_ed25519 -N "" -C "deploy@dj-manage"
chmod 600 ~/.ssh/djredoo_ed25519
ssh-copy-id -i ~/.ssh/djredoo_ed25519.pub -p 54322 redooad@100.69.222.62
```

## Deploy (Update auf bestehendem Server)

Projektpfad auf dem Server: `/opt/dj-redoo`, Compose-Projekt `dj-redoo`.
Der Server folgt `master`; Feature-Branches werden vorher per PR gemergt.

**1. Lokal pushen**

```bash
git push origin master
```

**2. Umfang prüfen** — entscheidet, welche Schritte nötig sind:

```bash
ssh djredoo 'cd /opt/dj-redoo && git fetch origin && \
  git diff --name-only HEAD origin/master | cut -d/ -f1 | sort -u'
```

| Geänderte Pfade | Nötiger Schritt |
|---|---|
| nur `docs/` | nichts weiter |
| `site/` (Datei-Inhalte geändert) | nichts weiter — Bind-Mount ist sofort aktiv |
| `site/` (Dateien neu/gelöscht, Branch-Wechsel) | `docker compose up -d --force-recreate nginx` (s. Fallstricke) |
| `nginx/conf.d/` | `docker compose exec nginx nginx -s reload` |
| `nginx/nginx.conf` | `docker compose up -d --force-recreate nginx` (s. Fallstricke) |
| `app/`, `Dockerfile`, `requirements.txt` | `docker compose up -d --build web` |
| Model-Änderungen | Migration läuft per `entrypoint.sh` automatisch beim Start |

Der Hintergrund-Scheduler (Bewertungsanfragen, täglich 10:00) startet mit dem
`web`-Container. Über `fcntl.flock` auf `/tmp/dj-redoo-scheduler.lock` läuft er
in genau einem Gunicorn-Worker. Abschalten per `DJANGO_DISABLE_SCHEDULER=1`,
manuell auslösen mit
`docker compose exec web python manage.py send_review_requests`.

**3. Pull (nur Fast-Forward, damit lokale Änderungen auffallen)**

```bash
ssh djredoo 'cd /opt/dj-redoo && git status --short && \
  git rev-parse --short HEAD > /tmp/djredoo-rollback-ref && \
  git merge --ff-only origin/master'
```

`git status --short` muss leer sein. Der Rollback-Ref in `/tmp` erlaubt
später ein gezieltes Zurück.

**4. Anwenden** — nginx-Config immer erst testen:

```bash
ssh djredoo 'cd /opt/dj-redoo && docker compose exec -T nginx nginx -t'
ssh djredoo 'cd /opt/dj-redoo && docker compose up -d --force-recreate nginx'
```

**5. Verifizieren**

```bash
ssh djredoo 'cd /opt/dj-redoo && docker compose ps'
ssh djredoo 'docker logs dj-redoo-nginx-1 2>&1 | grep -i "error\|emerg" | tail'

for p in / /hochzeit.html /site.webmanifest /images/logo.svg; do
  printf "%-24s " "$p"
  curl -s -o /dev/null -w "%{http_code} %{content_type}\n" "https://dj-redoo.de$p"
done
curl -s -o /dev/null -w "app %{http_code}\n" https://app.dj-redoo.de/
```

**Rollback**

```bash
ssh djredoo 'cd /opt/dj-redoo && git reset --hard $(cat /tmp/djredoo-rollback-ref) && \
  docker compose up -d --force-recreate nginx'
```

## Cloudflare-API

Zugangsdaten liegen lokal in `secrets/cloudflare.env` (nicht im Repo,
`chmod 600`). Laden und benutzen:

```bash
set -a; . secrets/cloudflare.env; set +a
curl -s -H "Authorization: Bearer $CF_API_TOKEN" \
  "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
  | python3 -m json.tool
```

Der Token hat *Zone → DNS → Edit* auf `dj-redoo.de` (Stand 2026-08-25).
Für die üblichen Aufgaben gibt es einen Wrapper:

```bash
scripts/cf-dns.sh list                    # alle Records
scripts/cf-dns.sh txt @ "google-site-verification=XXXX"
scripts/cf-dns.sh unproxy _dmarc          # orange Wolke aus
scripts/cf-dns.sh delete _cf-selftest
```

`txt` aktualisiert einen vorhandenen Record nur dann, wenn dessen Inhalt
mit demselben Präfix vor dem `=` beginnt — sonst wird angelegt. Das
verhindert, dass eine Verifizierung den SPF-Record überschreibt.

> Tokens im neuen `cfat_`-Format beantworten `/user/tokens/verify` nicht:
> ein „Invalid API Token" dort heißt nicht, dass der Token kaputt ist.
> Gegen `/zones` testen.

### Behoben: DMARC war wirkungslos

`_dmarc.dj-redoo.de` und `autodiscover.dj-redoo.de` standen auf *proxied*
(orange Wolke). Cloudflare lieferte dort Proxy-IPs statt des CNAME-Ziels,
`dig +short TXT _dmarc.dj-redoo.de` kam leer zurück — Empfänger fanden keine
DMARC-Policy, obwohl unter `dmarc.ionos.de` korrekt `v=DMARC1; p=none;` steht.
Das betraf die Zustellbarkeit aller Mails aus der App.

Am 2026-08-25 beide auf *DNS only* gestellt, verifiziert gegen 1.1.1.1 und
8.8.8.8: `_dmarc` liefert wieder `v=DMARC1; p=none;`.

**Faustregel: CNAMEs für Mail-Dienste dürfen nie proxied sein.** Nach jeder
DNS-Änderung prüfen:

```bash
dig +short TXT _dmarc.dj-redoo.de @1.1.1.1   # "v=DMARC1; p=none;"
dig +short TXT dj-redoo.de @1.1.1.1          # SPF unverändert
dig +short MX  dj-redoo.de @1.1.1.1          # mx00/mx01.ionos.de
```

> Noch offen: ein DKIM-Record war unter den üblichen Selektoren
> (`s1`, `s2`, `ionos1`) nicht auffindbar. Der IONOS-Selektor lässt sich nicht
> raten — im IONOS-Kundenmenü unter E-Mail → Einstellungen nachsehen, ob DKIM
> aktiv ist. Mit SPF, DKIM und DMARC zusammen ist die Zustellbarkeit deutlich
> besser als mit SPF allein.

### Fallstricke

- **Bind-Mounts überleben keinen Inode-Wechsel.** Docker bindet die Inode,
  nicht den Pfad. Ersetzt git die Datei oder das Verzeichnis, zeigt der
  Mount weiter auf das alte, bereits gelöschte Objekt — der Container
  sieht die Änderung nie. Betrifft beide Mounts:
  - `nginx.conf` (Single-File-Mount): `git merge` ersetzt die Datei. Ein
    `nginx -s reload` liest dann die *alte* Config und meldet trotzdem
    Erfolg.
  - `site/` (Verzeichnis-Mount): ein `git checkout` eines anderen Branches
    ersetzt das Verzeichnis. Der Mount ist danach **leer**, die Site
    liefert 404 und `rewrite or internal redirection cycle` (500).
    Ein bloßer `git merge` innerhalb desselben Branches ist unkritisch,
    solange nur Dateiinhalte geändert werden.

  In beiden Fällen hilft nur `docker compose up -d --force-recreate nginx`.
  Nach jedem Branch-Wechsel und nach jeder Änderung an `nginx.conf` also
  immer neu erstellen — und danach verifizieren:
  `docker exec dj-redoo-nginx-1 ls /usr/share/nginx/site/ | head`
  (leere Ausgabe = Mount ist tot).
  Für Änderungen nur in `nginx/conf.d/` reicht ein Reload.
- **Dateirechte.** nginx läuft als eigener User und liefert `403`, wenn
  Dateien kein Read-Bit für "others" haben. Bei `git`-Deploys greift die
  Server-umask und alles passt; bei manuell kopierten Dateien prüfen:
  `ssh djredoo 'find /opt/dj-redoo/site -type f ! -perm -o=r'` — Ausgabe
  muss leer sein.
- **Neue Dateiendungen brauchen einen MIME-Type.** Fehlt er, liefert nginx
  `application/octet-stream` und Browser ignorieren die Datei (so geschehen
  bei `.webmanifest`). Ergänzungen im `types`-Block in `nginx/nginx.conf`.
- **Cloudflare-Cache.** Änderungen an statischen Assets können verzögert
  ankommen. Bei Bedarf im CF-Dashboard unter Caching → Purge Everything.
