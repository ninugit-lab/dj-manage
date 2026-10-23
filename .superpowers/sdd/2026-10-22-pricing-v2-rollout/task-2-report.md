# Rollout Task 2 fix round 1 — controlled V2 repricing

Implemented the reviewer fixes for controlled open-event repricing.

## Safety and behavior

- `--apply` now requires `--confirm-local-apply`, `DEBUG=True`, and an explicit
  local/test host (`localhost`, `127.0.0.1`, `testserver`, or `[::1]`).
- Production/unknown database names are refused before event iteration or any
  write. This is a local/test-only safety gate; a future controlled production
  run requires a separately approved operational procedure and explicit user
  approval.
- Eligibility, date handling, old amount serialization, calculation, and draft
  persistence are inside the per-event error boundary. Malformed events become
  structured diagnostic rows and later events continue.
- One `timezone.now()` value is captured per command run and injected through
  the canonical calculation boundary. `requested_at` remains excluded from
  economic identity, while actual input/config/catalog changes create a new
  draft.
- Reused drafts report `already_applied`; new drafts report `applied`.
- Released, sent, and send-failed revisions, event totals, and legacy snapshots
  remain unchanged. Drafts retain old/new/difference audit values.

## Verification

- Focused repricing: `12 passed`
- Focused repricing + comparison regression: `27 passed`
- Full suite: `706 passed, 1 skipped`
  - Skip: SQLite cannot exercise concurrent row locks.
- Migration drift: `No changes detected`
- `git diff --check`: clean

All verification used local Python 3.12 via `uv` with
`DJANGO_DISABLE_SCHEDULER=1`, `PYTHONUTF8=1`, and a test-only Django secret.
No production `--apply`, push, deploy, or `LOCAL-DEV.md` changes were used.
