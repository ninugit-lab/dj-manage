import json
import os

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.conf import settings
from django.utils import timezone

from wishlist.models import Event
from wishlist.pricing_v2.migration import reprice_event


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
            is_pytest_db = bool(os.environ.get("PYTEST_CURRENT_TEST"))
            if "prod" in str(name).lower() or "production" in str(name).lower():
                raise CommandError("--apply requires an explicitly named temporary test database")
            if not name or ("test" not in str(name).lower()
                            and not str(name).endswith(":memory:") and not is_pytest_db):
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
