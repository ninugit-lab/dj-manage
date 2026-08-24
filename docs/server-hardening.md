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
