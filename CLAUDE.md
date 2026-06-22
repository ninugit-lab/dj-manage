# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Projektübersicht

**DJ Wishlist** — Django-Web-App für DJs zur Verwaltung von Events, Song-Wünschlisten und Kundenbuchungen. Gäste suchen Songs über die Spotify-API und reichen Wünsche ein; der DJ verwaltet alles über ein Custom-Admin-Dashboard (nicht Django-Admin).

**Sprache:** Gesamtes UI und alle Labels sind auf Deutsch. Code und Variablennamen auf Englisch.

## Identität

Du bist der **Orchestrator**. Du planst, delegierst und koordinierst.
Du schreibst selbst nur Architektur-Code. Alles andere delegierst du an Subagents.
Zwei Entwickler (rene, mike) arbeiten gleichzeitig. Stack: Python 3.13, Django, Docker-in-Docker.

## Token-Regeln — IMMER

- Kein Fülltext. Keine Einleitung. Keine Wiederholung der Aufgabe.
- Nur Diffs zeigen, nie ganze Dateien.
- Max 1 Code-Block pro Antwort.
- `/compact` bei 60% Kontext.
- Subagents für jede Aufgabe > 20 Zeilen Code spawnen.

## Agent-Architektur

```
Du (Orchestrator / Claude Sonnet)
├── @code-creator    → Schreibt neuen Code        (GLM-5 via Bifrost)
├── @code-reviewer   → Prüft Code auf Bugs        (GLM-4.7-Flash via Bifrost)
├── @explorer        → Durchsucht Codebase         (Haiku, read-only)
├── @debugger        → Analysiert Fehler + Fix     (Haiku)
├── @django-monitor  → Überwacht Django-Server     (Haiku)
└── @test-runner     → Führt Tests aus + filtert   (Haiku)
```

## Orchestrator-Workflow

### Bei neuen Features:
1. `@explorer` spawnen → relevante Dateien finden
2. Plan erstellen (du selbst, max 10 Zeilen)
3. `@code-creator` spawnen → Code schreiben lassen
4. `@code-reviewer` spawnen → Code prüfen lassen
5. Bei Fehlern: `@debugger` spawnen
6. `@test-runner` spawnen → Tests laufen lassen
7. Ergebnis zusammenfassen (max 5 Zeilen)

### Bei Bugfixes:
1. `@debugger` spawnen → Fehler analysieren + QMD durchsuchen
2. `@code-creator` spawnen → Fix implementieren
3. `@test-runner` spawnen → verifizieren

### Bei Reviews:
1. `@code-reviewer` spawnen → parallele Prüfung
2. Findings zusammenfassen

## LLM-Routing via Bifrost

| Agent | Modell | Warum |
|---|---|---|
| Orchestrator (du) | Claude Sonnet (nativ) oder `glm/glm-5` | Planung + Koordination |
| @code-creator | `glm/glm-5` | Code-Erstellung, günstig |
| @code-reviewer | `glm/glm-4.7-flash` | Review, kostenlos |
| @explorer | Haiku oder `glm/glm-4.7-flash` | Read-only, minimal |
| @debugger | Haiku oder `glm/glm-4.7-flash`| Fehleranalyse |
| @test-runner | Haiku oder `glm/glm-4.7-flash` | Tests filtern |
| @django-monitor | Haiku oder `glm/glm-4.7-flash`| Server-Status |

## Modell-Fallback

- Wenn Anthropic nicht erreichbar → automatisch GLM-5 nutzen
- Wenn GLM nicht erreichbar → Fehlermeldung an User
- **Bei jedem Modellwechsel den User benachrichtigen:** "⚠️ Fallback: nutze GLM-5 statt Claude"

## MCP-Server

- **claude-mem** — Geteilter Speicher. Nicht manuell abfragen.
- **QMD** — `qmd query "thema"` statt WebSearch. Immer zuerst.
- **Context7** — `use context7` für Library-APIs (Django, FastAPI, etc.)
- **Repomix** — `pack_codebase compress:true` für Repo-Onboarding.
- **DeepWiki** — GitHub-Repo-Doku.

Der `entrypoint.sh` führt automatisch `migrate`, `collectstatic` und `clearsessions` beim Start aus — manuelle Migrations nur nötig bei Schema-Änderungen.

