import json

from django.core.management.base import BaseCommand, CommandError

from wishlist.models import Event, EventStatus, PricingProfile, PricingWorkflow
from wishlist.pricing_v2.migration import compare_event


class Command(BaseCommand):
    help = "Compare saved event prices under V1 and V2 (dry-run by default)."

    def add_arguments(self, parser):
        parser.add_argument("--event-id", type=int)
        parser.add_argument("--status", choices=[value for value, _ in EventStatus.choices])
        parser.add_argument("--output")
        parser.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        profile = PricingProfile.objects.filter(status=PricingProfile.Status.ACTIVE).first()
        workflow = PricingWorkflow.objects.filter(is_active=True, is_default=True).first()
        if not profile or not workflow:
            raise CommandError("an active pricing profile and default workflow are required")
        events = Event.objects.order_by("pk")
        if options["event_id"] is not None:
            events = events.filter(pk=options["event_id"])
        if options["status"]:
            events = events.filter(status=options["status"])
        rows = []
        for event in events:
            try:
                row = compare_event(event, profile, workflow, persist=options["apply"])
            except Exception as exc:
                row = {"event_id": event.pk, "status": "error", "diagnostics": {"code": "internal_error", "message": str(exc)}}
            rows.append(row.to_dict() if hasattr(row, "to_dict") else row)
        report = {"summary": {"count": len(rows), "applied": bool(options["apply"])}, "rows": rows}
        output = json.dumps(report, sort_keys=True, indent=2)
        if options["output"]:
            with open(options["output"], "w", encoding="utf-8") as stream:
                stream.write(output + "\n")
        self.stdout.write(output)
