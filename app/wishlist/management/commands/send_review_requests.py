from django.core.management.base import BaseCommand

from wishlist.review_requests import send_review_requests


class Command(BaseCommand):
    help = "Verschickt fällige Bewertungsanfragen (sonst per Scheduler täglich um 10:00)."

    def handle(self, *args, **options):
        sent = send_review_requests()
        self.stdout.write(self.style.SUCCESS(f"{sent} Bewertungsanfrage(n) versendet."))
