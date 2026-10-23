import json
import os
from pathlib import Path
import tempfile

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.conf import settings
from django.utils import timezone

from wishlist.models import Event
from wishlist.pricing_v2.migration import reprice_event


def _canonical_db_name(name):
    if name in (None, ":memory:") or str(name).startswith("file:"):
        return str(name) if name is not None else None
    return str(Path(str(name)).resolve())


class Command(BaseCommand):
    help = "Propose or append controlled V2 repricing drafts (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--output")
        parser.add_argument("--confirm-local-apply", action="store_true")

    def handle(self, *args, **options):
        if options["apply"]:
            name = connection.settings_dict.get("NAME")
            hosts = set(getattr(settings, "ALLOWED_HOSTS", ()))
            local_hosts = hosts.intersection({"localhost", "127.0.0.1", "testserver", "[::1]"})
            if not options["confirm_local_apply"] or not settings.DEBUG or not local_hosts:
                raise CommandError(
                    "--apply requires --confirm-local-apply, DEBUG=True, and an explicit local test host"
                )
            allowlisted = os.environ.get("PRICING_V2_APPLY_DB_NAME")
            if not allowlisted or _canonical_db_name(allowlisted) != _canonical_db_name(name):
                raise CommandError(
                    "PRICING_V2_APPLY_DB_NAME must exactly match the resolved database name"
                )
            try:
                db_path = Path(str(name)).resolve()
                temp_path = Path(tempfile.gettempdir()).resolve()
                is_ephemeral = db_path.is_relative_to(temp_path)
                if connection.vendor == "sqlite" and connection.connection is not None:
                    actual = connection.connection.execute("PRAGMA database_list").fetchone()[2]
                    if actual:
                        actual_path = Path(actual).resolve()
                        is_ephemeral = actual_path.is_relative_to(temp_path) and actual_path == db_path
                    else:
                        is_ephemeral = str(name) in {":memory:", "file:memorydb_default?mode=memory&cache=shared"}
            except (OSError, ValueError):
                is_ephemeral = False
            if not is_ephemeral:
                raise CommandError("--apply requires an explicitly named temporary test database")
        rows = []
        today = timezone.localdate()
        requested_at = timezone.now()
        for event in Event.objects.order_by("pk"):
            rows.append(reprice_event(event, today=today, requested_at=requested_at,
                                      apply=options["apply"]))
        report = {"summary": {"count": len(rows), "applied": bool(options["apply"])}, "rows": rows}
        output = json.dumps(report, sort_keys=True, indent=2)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as stream:
                stream.write(output + "\n")
        self.stdout.write(output)
