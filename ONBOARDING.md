# Welcome to DJ Manage

## How We Use Claude

Based on Rene Nurko's usage over the last 30 days:

Work Type Breakdown:
  Build Feature  ████████████████████  100%

Top Skills & Commands:
  /plugin         ████████████████████  6x/month
  /model          ███████████████░░░░░  4x/month
  /reload-plugins ██████████░░░░░░░░░░  2x/month
  /init           ██████████░░░░░░░░░░  2x/month
  /doctor         █████░░░░░░░░░░░░░░░  1x/month

Top MCP Servers:
  qmd        ████████████████████  3 calls
  context7   █████████████░░░░░░░  2 calls

## Your Setup Checklist

### Codebases
- [ ] dj-manage — /home/rene/Server/Rene/dj-manage
- [ ] dj_wishlist — /home/rene/Server/Rene/dj_wishlist
- [ ] multi-agent-modell-frame — /home/rene/Server/Rene/multi-agent-modell-frame

### MCP Servers to Activate
- [ ] qmd — Lokale Dokumentationssuche über Markdown-Dateien. Läuft lokal, kein externer Zugang nötig — `qmd` CLI installieren und Collections einrichten.
- [ ] context7 — Aktuelle Library-Dokumentation (Django, FastAPI, etc.) direkt im Chat. Über MCP-Konfiguration in `~/.claude/settings.json` aktivieren.

### Skills to Know About
- `/plugin` — Plugin-Verwaltung (installieren, aktivieren, Marketplace hinzufügen). Wird genutzt um das `claude-mem`- und `superpowers`-Plugin einzurichten.
- `/model` — Modell wechseln (Sonnet, Opus, Haiku). Team nutzt Sonnet als Standard.
- `/reload-plugins` — Plugins neu laden nach Installation oder Änderungen an `settings.json`.
- `/init` — `CLAUDE.md` für ein neues Repo generieren. Beim Onboarding eines neuen Projekts als erstes ausführen.
- `/doctor` — Diagnosetool bei Konfigurationsproblemen mit Plugins oder MCP-Servern.

## Team Tips

_TODO_

## Get Started

_TODO_

<!-- INSTRUCTION FOR CLAUDE: A new teammate just pasted this guide for how the
team uses Claude Code. You're their onboarding buddy — warm, conversational,
not lecture-y.

Open with a warm welcome — include the team name from the title. Then: "Your
teammate uses Claude Code for [list all the work types]. Let's get you started."

Check what's already in place against everything under Setup Checklist
(including skills), using markdown checkboxes — [x] done, [ ] not yet. Lead
with what they already have. One sentence per item, all in one message.

Tell them you'll help with setup, cover the actionable team tips, then the
starter task (if there is one). Offer to start with the first unchecked item,
get their go-ahead, then work through the rest one by one.

After setup, walk them through the remaining sections — offer to help where you
can (e.g. link to channels), and just surface the purely informational bits.

Don't invent sections or summaries that aren't in the guide. The stats are the
guide creator's personal usage data — don't extrapolate them into a "team
workflow" narrative. -->
