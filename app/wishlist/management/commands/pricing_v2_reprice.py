import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from wishlist.models import Event
from wishlist.pricing_v2.migration import reprice_event


class Command(BaseCommand):
    help = "Propose or append controlled V2 repricing drafts (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--apply", action="store_true")
        parser.add_argument("--output")

    def handle(self, *args, **options):
        if options["apply"] and connection.vendor != "sqlite":
            raise CommandError("--apply is restricted to the ephemeral SQLite test database")
        rows = []
        today = __import__("django.utils.timezone", fromlist=["localdate"]).localdate()
        for event in Event.objects.order_by("pk"):
            rows.append(reprice_event(event, today=today, apply=options["apply"]))
        report = {"summary": {"count": len(rows), "applied": bool(options["apply"])}, "rows": rows}
        output = json.dumps(report, sort_keys=True, indent=2)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as stream:
                stream.write(output + "\n")
        self.stdout.write(output)
