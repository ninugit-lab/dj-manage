# Offers/admin final-fix wave

## Scope

- Serialized offer delivery claims with a committed `sending` state before Gmail.
- Added explicit stale-claim recovery; failed delivery remains retryable and every attempt keeps its own `EmailLog`.
- Locked the stable `Event` row while validating aggregate manual adjustments against the full Decimal range.
- Treated package rows with foreign draft overrides as shared and prevented implicit cross-profile mutation.
- Cross-profile package edits now require an explicit clone/assignment signal even when core package fields change.
- Forced newly created packages to private until profile/workflow publication is completed.

## RED

Added behavioral tests for:

- one delivery under concurrent send requests, including retry handling;
- stale sending-claim recovery;
- aggregate adjustment overflow and concurrent adjustment locking;
- foreign draft package isolation and explicit cloning;
- new active-profile packages requested as public being created private.

Initial focused test run was blocked by the global Python installation (`ModuleNotFoundError: django`). The project `.venv` then exposed the expected scheduler portability issue (`ModuleNotFoundError: fcntl`), resolved for local verification with `DJANGO_DISABLE_SCHEDULER=1`. The focused RED tests then failed on the missing claim/recovery, locking, and package-publication behavior.

## GREEN

Focused run, Python 3.12 project venv, UTF-8 source:

```text
DJANGO_SECRET_KEY=test-secret DJANGO_DISABLE_SCHEDULER=1
python -m pytest wishlist/tests/test_pricing_v2_offer_email.py
                         wishlist/tests/test_pricing_v2_offers.py
                         wishlist/tests/test_pricing_v2_package_admin.py -q
80 passed, 1 skipped
```

The single skip is the SQLite-only concurrent adjustment test: SQLite cannot exercise row locks. The concurrent send test still verifies one Gmail call on SQLite and accepts SQLite's backend lock conflict for the losing request; PostgreSQL row-lock behavior is the intended production path.

Migration drift/syntax verification:

```text
python manage.py makemigrations --check --dry-run
No changes detected
python manage.py migrate --plan
wishlist.0019_offer_send_claim
wishlist.0020_alter_pricecalculationrevision_status
python -m compileall -q wishlist
exit 0
```

Full local suite:

```text
676 passed, 1 skipped, 2 failed
```

The two failures are pre-existing Windows CP1252 stdin encoding failures in unrelated Node-based UI tests (`test_pricing_v2_admin_ui.py` and `test_pricing_v2_public_ui.py`); their HTML contains UTF-8 characters that the subprocess pipe cannot encode. No files in those tests or their UI scope were changed.
