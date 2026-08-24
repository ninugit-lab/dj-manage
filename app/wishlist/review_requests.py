"""Automatische Bewertungsanfragen nach durchgeführten Events."""
import logging
from datetime import timedelta

from django.utils import timezone

logger = logging.getLogger(__name__)


def send_review_requests():
    """Verschickt einmalig eine Nachfass-Mail pro durchgeführtem Event.

    Kriterien: Status ``past``, Eventdatum mindestens ``review_request_delay_days``
    her, Kunden-E-Mail vorhanden, noch keine Anfrage gesendet.
    Gibt die Anzahl erfolgreich versendeter Mails zurück.
    """
    from .models import AppConfig, EmailLog, Event, EventStatus
    from .google_services import GoogleService

    config = AppConfig.load()
    if not config.review_request_enabled:
        return 0
    if not config.google_review_url:
        logger.warning("Bewertungsanfragen aktiv, aber kein google_review_url gesetzt — übersprungen.")
        return 0

    cutoff = timezone.localdate() - timedelta(days=config.review_request_delay_days)
    # Der Status wird erst beim nächsten save() auf "past" gesetzt; deshalb
    # zählt hier das Datum, und storniert wird ausgeschlossen.
    events = Event.objects.filter(
        date__lte=cutoff,
        review_requested_at__isnull=True,
        status__in=[EventStatus.PAST, EventStatus.CONFIRMED],
    ).exclude(client_email='')

    sent = 0
    for event in events:
        # Reservieren, bevor die Mail rausgeht: verhindert Doppelversand,
        # falls ein zweiter Worker denselben Event greift.
        claimed = Event.objects.filter(
            pk=event.pk, review_requested_at__isnull=True
        ).update(review_requested_at=timezone.now())
        if not claimed:
            continue

        subject = config.review_request_email_subject
        try:
            body = config.review_request_email_body.format(
                client_name=event.client_name or "Kunde",
                event_name=event.name,
                event_date=event.date.strftime('%d.%m.%Y'),
                location=event.location,
                dj_name=config.dj_name,
                review_url=config.google_review_url,
            )
        except (KeyError, IndexError, ValueError) as exc:
            # Unbekannter Platzhalter in der Vorlage — Event wieder freigeben.
            Event.objects.filter(pk=event.pk).update(review_requested_at=None)
            logger.error("Bewertungs-Vorlage fehlerhaft (%s) — Versand abgebrochen.", exc)
            return sent
        success, msg = GoogleService.send_email(
            event.client_email, subject, body, sender_name=config.dj_name
        )
        EmailLog.objects.create(
            event=event, recipient=event.client_email, subject=subject, body=body,
            success=success, error_message="" if success else msg,
        )
        if success:
            sent += 1
        else:
            # Fehlgeschlagene Mails erneut versuchen lassen.
            Event.objects.filter(pk=event.pk).update(review_requested_at=None)
            logger.error("Bewertungsanfrage für Event %s fehlgeschlagen: %s", event.pk, msg)

    if sent:
        logger.info("%s Bewertungsanfrage(n) versendet.", sent)
    return sent
