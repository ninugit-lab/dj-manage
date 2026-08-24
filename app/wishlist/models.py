import re
import uuid
from django.db import models
from django.utils import timezone
from django.urls import reverse


class EventStatus(models.TextChoices):
    INQUIRY = 'inquiry', 'Angefragt'
    CONFIRMED = 'confirmed', 'Bestätigt'
    PAST = 'past', 'Vergangen'
    CANCELLED = 'cancelled', 'Storniert'


def _extract_spotify_id(value):
    """Extract Spotify playlist ID from URL or return as-is."""
    if not value:
        return ''
    # Match open.spotify.com/playlist/<ID> or spotify:playlist:<ID>
    m = re.search(r'playlist[/:]([a-zA-Z0-9]{22})', value)
    if m:
        return m.group(1)
    # Already an ID?
    if re.match(r'^[a-zA-Z0-9]{22}$', value.strip()):
        return value.strip()
    return value.strip()


class Event(models.Model):
    name = models.CharField(max_length=200, verbose_name="Event Name")
    date = models.DateField(verbose_name="Datum")
    time_start = models.TimeField(verbose_name="Beginn", null=True, blank=True)
    time_end = models.TimeField(verbose_name="Ende", null=True, blank=True)
    location = models.CharField(max_length=200, verbose_name="Veranstaltungsort")
    address_street = models.CharField(max_length=200, blank=True, verbose_name="Straße + Nr.")
    address_zip = models.CharField(max_length=10, blank=True, verbose_name="PLZ")
    address_city = models.CharField(max_length=100, blank=True, verbose_name="Stadt")
    description = models.TextField(blank=True, verbose_name="Beschreibung")
    cover_image_url = models.URLField(blank=True, verbose_name="Cover Bild URL")
    cover_image = models.ImageField(upload_to='event_covers/', blank=True, verbose_name="Cover Bild Upload")
    event_type = models.CharField(max_length=100, blank=True, verbose_name="Art des Events",
        help_text="z.B. Hochzeit, Firmenfeier, Geburtstag, Club")
    status = models.CharField(max_length=20, choices=EventStatus.choices,
        default=EventStatus.INQUIRY, verbose_name="Status")
    is_active = models.BooleanField(default=False, verbose_name="Aktive Wishlist",
        help_text="Nur 1 Event gleichzeitig aktiv")
    client_name = models.CharField(max_length=200, blank=True, verbose_name="Auftraggeber")
    client_email = models.EmailField(blank=True, verbose_name="E-Mail")
    client_phone = models.CharField(max_length=50, blank=True, verbose_name="Telefon")
    client_company = models.CharField(max_length=200, blank=True, verbose_name="Firma")
    client_notes = models.TextField(blank=True, verbose_name="Kundennotizen")
    spotify_playlist_id = models.CharField(max_length=100, blank=True, verbose_name="Spotify Wishlist Playlist",
        help_text="ID oder URL — wird automatisch gekürzt")
    spotify_preselection_playlist_id = models.CharField(max_length=100, blank=True,
        verbose_name="Spotify Vorauswahl Playlist",
        help_text="Automatisch erstellt bei Event-Erstellung")
    max_wishes_per_session = models.PositiveIntegerField(default=3, verbose_name="Max. Wünsche pro Session")
    wishlist_show_cover = models.BooleanField(default=True, verbose_name="Cover anzeigen")
    wishlist_show_artist = models.BooleanField(default=True, verbose_name="Künstler anzeigen")
    wishlist_show_preview = models.BooleanField(default=False, verbose_name="Preview anzeigen")
    wishlist_show_duration = models.BooleanField(default=False, verbose_name="Dauer anzeigen")
    wishlist_require_name = models.BooleanField(default=True, verbose_name="Name Pflichtfeld")
    wishlist_show_played = models.BooleanField(default=True, verbose_name="Gespielte anzeigen")
    spotify_remove_on_played = models.BooleanField(default=True, verbose_name="Gespielt → aus Spotify entfernen")
    wishlist_custom_message = models.CharField(max_length=300, blank=True, verbose_name="Begrüßungstext")
    wishlist_dj_message = models.CharField(max_length=300, blank=True, verbose_name="DJ Nachricht",
        help_text="Wird beim Wunsch-Counter angezeigt")
    default_block_reason = models.CharField(max_length=300, blank=True,
        default="Unangemessene Eingaben", verbose_name="Standard Sperrgrund (intern)")
    default_block_message = models.CharField(max_length=300, blank=True,
        default="Du wurdest für diese Wishlist gesperrt.", verbose_name="Standard Sperrnachricht (öffentlich)")
    total_price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Gesamtpreis")
    price_notes = models.TextField(blank=True, verbose_name="Preis-Notizen")
    guest_count = models.PositiveIntegerField(null=True, blank=True, verbose_name="Gästeanzahl")
    special_requests = models.TextField(blank=True, verbose_name="Sonderwünsche")
    distance_km = models.DecimalField(max_digits=7, decimal_places=1, null=True, blank=True, verbose_name="Entfernung (km)")
    admin_token = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    google_calendar_event_id = models.CharField(max_length=200, blank=True, verbose_name="Google Calendar Event ID")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    review_requested_at = models.DateTimeField(null=True, blank=True,
        verbose_name="Bewertungsanfrage gesendet",
        help_text="Zeitpunkt der automatischen Nachfass-Mail. Leer = noch nicht gesendet.")

    class Meta:
        verbose_name = "Event"
        verbose_name_plural = "Events"
        ordering = ['-date']

    def __str__(self):
        return f"{self.name} — {self.date.strftime('%d.%m.%Y')}"

    def get_admin_url(self):
        return reverse('dj_admin:event_edit', kwargs={'pk': self.pk})

    @property
    def is_past(self):
        return self.date < timezone.now().date()

    @property
    def full_address(self):
        """Combine structured address fields into a single string."""
        parts = [self.address_street, f"{self.address_zip} {self.address_city}".strip()]
        return ', '.join(p for p in parts if p)

    @property
    def cover_url(self):
        """Return uploaded image URL or fallback to URL field."""
        if self.cover_image:
            return self.cover_image.url
        return self.cover_image_url or ''

    def save(self, *args, **kwargs):
        # Auto-parse Spotify URL to ID
        if self.spotify_playlist_id:
            self.spotify_playlist_id = _extract_spotify_id(self.spotify_playlist_id)
        if self.spotify_preselection_playlist_id:
            self.spotify_preselection_playlist_id = _extract_spotify_id(self.spotify_preselection_playlist_id)
        if self.date and self.is_past and self.status not in (EventStatus.PAST, EventStatus.CANCELLED):
            self.status = EventStatus.PAST
        if self.is_active:
            Event.objects.filter(is_active=True).exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)


class SongWish(models.Model):
    event = models.ForeignKey(Event, on_delete=models.CASCADE, related_name='wishes', verbose_name="Event")
    spotify_track_id = models.CharField(max_length=100, verbose_name="Spotify Track ID")
    track_name = models.CharField(max_length=300, verbose_name="Titel")
    artist_name = models.CharField(max_length=300, verbose_name="Künstler")
    album_name = models.CharField(max_length=300, blank=True, verbose_name="Album")
    album_cover_url = models.URLField(blank=True, verbose_name="Album Cover URL")
    duration_ms = models.PositiveIntegerField(default=0, verbose_name="Länge (ms)")
    preview_url = models.URLField(blank=True, null=True, verbose_name="Preview URL")
    added_to_playlist = models.BooleanField(default=False, verbose_name="Zur Playlist hinzugefügt")
    played = models.BooleanField(default=False, verbose_name="Gespielt")
    guest_name = models.CharField(max_length=100, blank=True, verbose_name="Gast-Name")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP-Adresse")
    created_at = models.DateTimeField(auto_now_add=True)
    session_key = models.CharField(max_length=100, blank=True, verbose_name="Session")
    audio_bpm = models.FloatField(null=True, blank=True, verbose_name="BPM")
    audio_energy = models.FloatField(null=True, blank=True, verbose_name="Energie")
    audio_danceability = models.FloatField(null=True, blank=True, verbose_name="Tanzbarkeit")
    audio_valence = models.FloatField(null=True, blank=True, verbose_name="Stimmung")
    audio_acousticness = models.FloatField(null=True, blank=True, verbose_name="Akustik")
    audio_instrumentalness = models.FloatField(null=True, blank=True, verbose_name="Instrumental")

    class Meta:
        verbose_name = "Song-Wunsch"
        verbose_name_plural = "Song-Wünsche"
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['event', 'spotify_track_id'], name='unique_track_per_event')
        ]

    def __str__(self):
        return f"{self.track_name} – {self.artist_name}"

    @property
    def duration_formatted(self):
        total_seconds = self.duration_ms // 1000
        return f"{total_seconds // 60}:{total_seconds % 60:02d}"


class SpotifyToken(models.Model):
    access_token = models.TextField()
    refresh_token = models.TextField()
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        verbose_name = "Spotify Token"
        verbose_name_plural = "Spotify Tokens"
    def __str__(self):
        return f"Spotify Token ({self.updated_at.strftime('%d.%m.%Y %H:%M')})"
    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class GoogleToken(models.Model):
    access_token = models.TextField()
    refresh_token = models.TextField()
    token_type = models.CharField(max_length=50, default='Bearer')
    expires_at = models.DateTimeField()
    scopes = models.TextField(blank=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        verbose_name = "Google Token"
        verbose_name_plural = "Google Tokens"
    def __str__(self):
        return f"Google Token ({self.updated_at.strftime('%d.%m.%Y %H:%M')})"
    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at


class AppConfig(models.Model):
    """Singleton config – pk always 1."""
    spotify_client_id = models.CharField(max_length=200, blank=True, verbose_name="Spotify Client ID")
    spotify_client_secret = models.CharField(max_length=200, blank=True, verbose_name="Spotify Client Secret")
    spotify_redirect_uri = models.URLField(blank=True, verbose_name="Spotify Redirect URI")
    google_client_id = models.CharField(max_length=300, blank=True, verbose_name="Google Client ID")
    google_client_secret = models.CharField(max_length=300, blank=True, verbose_name="Google Client Secret")
    google_redirect_uri = models.URLField(blank=True, verbose_name="Google Redirect URI")
    google_sheets_id = models.CharField(max_length=200, blank=True, verbose_name="Google Sheets ID",
        help_text="ID der Google-Tabelle für Event-Backup")
    # Wishlist defaults
    default_max_wishes = models.PositiveIntegerField(default=3, verbose_name="Standard Max. Wünsche")
    default_show_cover = models.BooleanField(default=True)
    default_show_artist = models.BooleanField(default=True)
    default_show_preview = models.BooleanField(default=False)
    default_show_duration = models.BooleanField(default=False)
    default_require_name = models.BooleanField(default=True)
    default_playlist_id = models.CharField(max_length=100, blank=True, verbose_name="Standard Spotify Playlist ID",
        help_text="ID oder URL — wird für neue Events als Default genutzt")
    # Email templates
    confirmation_email_subject = models.CharField(max_length=200, default="Deine Buchung wurde bestätigt! 🎶",
        verbose_name="Bestätigungs-Mail Betreff")
    confirmation_email_body = models.TextField(blank=True, verbose_name="Bestätigungs-Mail Text",
        help_text="Platzhalter: {client_name}, {event_name}, {event_date}, {event_time}, {location}, {dj_name}",
        default="Hallo {client_name},\n\ndein Event \"{event_name}\" am {event_date} um {event_time} in {location} wurde bestätigt!\n\nBeste Grüße,\n{dj_name}")
    inquiry_email_subject = models.CharField(max_length=200, default="Deine Anfrage ist eingegangen! 🎧",
        verbose_name="Anfrage-Mail Betreff")
    inquiry_email_body = models.TextField(blank=True, verbose_name="Anfrage-Mail Text",
        help_text="Platzhalter: {client_name}, {event_name}, {event_date}, {location}, {dj_name}",
        default="Hallo {client_name},\n\nvielen Dank für deine Anfrage für \"{event_name}\" am {event_date} in {location}.\n\nWir melden uns in Kürze bei dir!\n\nBeste Grüße,\n{dj_name}")
    review_request_email_subject = models.CharField(max_length=200,
        default="Wie war euer Abend? 🎶",
        verbose_name="Bewertungs-Mail Betreff")
    review_request_email_body = models.TextField(blank=True, verbose_name="Bewertungs-Mail Text",
        help_text="Platzhalter: {client_name}, {event_name}, {event_date}, {location}, {dj_name}, {review_url}",
        default="Hallo {client_name},\n\nvielen Dank, dass ich bei \"{event_name}\" am {event_date} in {location} für die Musik sorgen durfte!\n\nWenn dir der Abend gefallen hat, freue ich mich sehr über eine kurze Bewertung bei Google — das dauert keine Minute und hilft mir enorm weiter:\n{review_url}\n\nBeste Grüße,\n{dj_name}")
    google_review_url = models.URLField(blank=True, verbose_name="Google-Bewertungslink",
        help_text="Aus dem Google-Business-Profil: Rezensionen → Mehr Rezensionen erhalten")
    review_request_enabled = models.BooleanField(default=False,
        verbose_name="Automatische Bewertungsanfragen",
        help_text="Sendet nach jedem Event eine Nachfass-Mail. Erfordert einen Bewertungslink.")
    review_request_delay_days = models.PositiveIntegerField(default=2,
        verbose_name="Wartezeit nach Event (Tage)")
    # DJ info / Person
    dj_name = models.CharField(max_length=100, default="DJ", verbose_name="DJ Name / Künstlername")
    dj_real_name = models.CharField(max_length=200, blank=True, verbose_name="Echter Name")
    dj_email = models.EmailField(blank=True, verbose_name="E-Mail")
    dj_phone = models.CharField(max_length=50, blank=True, verbose_name="Telefon")
    dj_address = models.TextField(blank=True, verbose_name="Adresse")
    dj_home_lat = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        verbose_name="Standort Breitengrad",
        help_text="Für die Anfahrts-Berechnung im Buchungsformular")
    dj_home_lon = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True,
        verbose_name="Standort Längengrad")
    dj_website = models.URLField(blank=True, verbose_name="Website")
    # Wishlist public messages
    no_event_message = models.CharField(max_length=300, blank=True,
        default="Aktuell ist kein Event aktiv. Buche jetzt deinen DJ!",
        verbose_name="Kein-Event-Nachricht (öffentlich)")
    # Event form
    event_form_enabled = models.BooleanField(default=True, verbose_name="Event-Formular aktiv")
    event_form_intro = models.TextField(blank=True,
        default="Buche deinen DJ! Prüfe die Verfügbarkeit und erhalte sofort ein Angebot.",
        verbose_name="Formular Einleitungstext")
    event_form_date_prompt = models.CharField(max_length=300, blank=True,
        default="Wähle zuerst dein Wunschdatum, um die Verfügbarkeit zu prüfen.",
        verbose_name="Datum-Aufforderung")
    event_form_unavailable_msg = models.CharField(max_length=300, blank=True,
        default="Leider sind wir an diesem Datum nicht verfügbar. Bitte wähle ein anderes Datum.",
        verbose_name="Datum nicht verfügbar Text")
    # Wishlist messages
    wishlist_limit_message = models.CharField(max_length=300, blank=True,
        default="Danke für deine Wünsche! 🎶",
        verbose_name="Wunschlimit-Nachricht")
    default_block_reason = models.CharField(max_length=300, blank=True,
        default="Unangemessene Eingaben",
        verbose_name="Standard Sperrgrund (intern)")
    default_block_message = models.CharField(max_length=300, blank=True,
        default="Du wurdest für diese Wishlist gesperrt.",
        verbose_name="Standard Sperrnachricht (öffentlich)")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "App-Konfiguration"
        verbose_name_plural = "App-Konfiguration"
    def __str__(self):
        return "App-Konfiguration"
    def save(self, *args, **kwargs):
        self.pk = 1
        if self.default_playlist_id:
            self.default_playlist_id = _extract_spotify_id(self.default_playlist_id)
        super().save(*args, **kwargs)
    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class PriceItem(models.Model):
    CATEGORY_CHOICES = [
        ('base', 'Grundpreis'), ('tech', 'Technik'), ('equipment', 'Ausrüstung'),
        ('travel', 'Anfahrt'), ('extra', 'Extras'),
    ]
    name = models.CharField(max_length=200, verbose_name="Bezeichnung")
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, verbose_name="Kategorie")
    description = models.CharField(max_length=300, blank=True, verbose_name="Beschreibung")
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Preis (€)")
    is_default = models.BooleanField(default=False, verbose_name="Standard ausgewählt")
    is_required = models.BooleanField(default=False, verbose_name="Pflicht")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Sortierung")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    is_public = models.BooleanField(default=True, verbose_name="Öffentlich sichtbar")
    class Meta:
        verbose_name = "Preis-Posten"
        verbose_name_plural = "Preis-Posten"
        ordering = ['category', 'sort_order']
    def __str__(self):
        return f"{self.name} — {self.price} €"


class PricingRule(models.Model):
    EFFECT_CHOICES = [
        ('percent_add', 'Prozent-Aufschlag'),
        ('flat_add', 'Pauschale hinzu'),
        ('flat_set', 'Pauschale setzen'),
    ]
    APPLIES_TO_CHOICES = [
        ('subtotal', 'Zwischensumme'),
        ('base', 'Grundpreis'),
        ('tech', 'Technik'),
    ]
    name = models.CharField(max_length=200, verbose_name="Bezeichnung")
    description = models.CharField(max_length=300, blank=True, verbose_name="Beschreibung")
    condition_json = models.JSONField(default=list, verbose_name="Bedingungen")
    effect_type = models.CharField(max_length=20, choices=EFFECT_CHOICES, verbose_name="Effekt-Typ")
    effect_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Effekt-Wert")
    applies_to = models.CharField(max_length=20, choices=APPLIES_TO_CHOICES, default='subtotal', verbose_name="Bezieht sich auf")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Sortierung")
    class Meta:
        verbose_name = "Preis-Regel"
        verbose_name_plural = "Preis-Regeln"
        ordering = ['sort_order']
    def __str__(self):
        return self.name


class PricingPackage(models.Model):
    name = models.CharField(max_length=200, verbose_name="Bezeichnung")
    description = models.TextField(blank=True, verbose_name="Beschreibung")
    base_price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Grundpreis (€)")
    included_items = models.ManyToManyField('PriceItem', blank=True, verbose_name="Enthaltene Posten")
    badge_label = models.CharField(max_length=50, blank=True, verbose_name="Badge")
    badge_color = models.CharField(max_length=20, blank=True, verbose_name="Badge-Farbe")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    is_public = models.BooleanField(default=True, verbose_name="Öffentlich sichtbar")
    sort_order = models.PositiveIntegerField(default=0, verbose_name="Sortierung")
    class Meta:
        verbose_name = "Preis-Paket"
        verbose_name_plural = "Preis-Pakete"
        ordering = ['sort_order']
    def __str__(self):
        return f"{self.name} — {self.base_price} €"


class PricingFormula(models.Model):
    name = models.CharField(max_length=200, verbose_name="Bezeichnung")
    expression = models.CharField(max_length=500, verbose_name="Formel")
    description = models.CharField(max_length=300, blank=True, verbose_name="Beschreibung")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    class Meta:
        verbose_name = "Preis-Formel"
        verbose_name_plural = "Preis-Formeln"
    def __str__(self):
        return self.name


class PricingWorkflow(models.Model):
    name = models.CharField(max_length=200, verbose_name="Bezeichnung")
    workflow_json = models.JSONField(default=list, verbose_name="Workflow-Blöcke")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    is_default = models.BooleanField(default=False, verbose_name="Standard")
    class Meta:
        verbose_name = "Preis-Workflow"
        verbose_name_plural = "Preis-Workflows"
    def __str__(self):
        return self.name


class EventPriceCalculation(models.Model):
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='price_calculation')
    selected_items = models.ManyToManyField(PriceItem, blank=True, verbose_name="Posten")
    custom_items_json = models.JSONField(default=list, blank=True, verbose_name="Individuelle Posten")
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0, verbose_name="Rabatt (%)")
    package = models.ForeignKey(PricingPackage, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Paket")
    applied_rules = models.ManyToManyField(PricingRule, blank=True, verbose_name="Angewandte Regeln")
    formula = models.ForeignKey(PricingFormula, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Formel")
    workflow = models.ForeignKey(PricingWorkflow, null=True, blank=True, on_delete=models.SET_NULL, verbose_name="Workflow")
    calculation_snapshot = models.JSONField(default=dict, blank=True, verbose_name="Berechnungs-Snapshot")
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        verbose_name = "Event-Kalkulation"
        verbose_name_plural = "Event-Kalkulationen"
    def __str__(self):
        return f"Kalkulation: {self.event.name}"
    @property
    def subtotal(self):
        from decimal import Decimal, InvalidOperation
        items_total = sum((i.price for i in self.selected_items.all()), Decimal('0'))
        custom_total = Decimal('0')
        for i in (self.custom_items_json or []):
            try:
                custom_total += Decimal(str(i.get('price', 0)))
            except (InvalidOperation, TypeError):
                pass
        return items_total + custom_total
    @property
    def total(self):
        from decimal import Decimal
        return self.subtotal * (Decimal('1') - self.discount_percent / Decimal('100'))


class EventOffer(models.Model):
    event = models.OneToOneField(Event, on_delete=models.CASCADE, related_name='offer')
    guest_count = models.PositiveIntegerField(default=0, verbose_name="Gästeanzahl")
    threshold = models.PositiveIntegerField(default=50, verbose_name="Schwellenwert Gäste")
    rate_1 = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Preis/Person bis Schwelle (€)")
    rate_2 = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Preis/Person über Schwelle (€)")
    base_rent = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Grundgebühr (€)")
    tech_package = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Technik-Pauschale (€)")
    tech_required = models.BooleanField(default=False, verbose_name="Technik benötigt")
    cleaning_fee = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Endreinigung (€)")

    class Meta:
        verbose_name = "Event-Angebot"
        verbose_name_plural = "Event-Angebote"

    def __str__(self):
        return f"Angebot: {self.event.name}"

    def clean(self):
        from django.core.exceptions import ValidationError
        for field in ('rate_1', 'rate_2', 'base_rent', 'tech_package', 'cleaning_fee'):
            val = getattr(self, field)
            if val is not None and val < 0:
                raise ValidationError({field: 'Darf nicht negativ sein.'})

    def get_detailed_breakdown(self):
        from decimal import Decimal
        n = self.guest_count or 0
        threshold = self.threshold or 0
        rate_1 = self.rate_1 or Decimal('0')
        rate_2 = self.rate_2 or Decimal('0')

        if n <= threshold:
            subtotal_var = Decimal(n) * rate_1
        else:
            subtotal_var = (Decimal(threshold) * rate_1) + (Decimal(n - threshold) * rate_2)

        subtotal_fix = (self.base_rent or Decimal('0')) + (self.cleaning_fee or Decimal('0'))
        if self.tech_required:
            subtotal_fix += self.tech_package or Decimal('0')

        return {
            'guest_count': n,
            'threshold': threshold,
            'subtotal_variable': subtotal_var,
            'subtotal_fix': subtotal_fix,
            'tech_included': self.tech_required,
            'grand_total': subtotal_var + subtotal_fix,
        }


class EmailLog(models.Model):
    event = models.ForeignKey(Event, on_delete=models.SET_NULL, null=True, blank=True, related_name='email_logs')
    recipient = models.EmailField(verbose_name="Empfänger")
    subject = models.CharField(max_length=300)
    body = models.TextField()
    sent_at = models.DateTimeField(auto_now_add=True)
    success = models.BooleanField(default=True)
    error_message = models.TextField(blank=True)
    class Meta:
        verbose_name = "E-Mail Log"
        verbose_name_plural = "E-Mail Logs"
        ordering = ['-sent_at']
    def __str__(self):
        return f"{self.subject} → {self.recipient}"


class BlockedClient(models.Model):
    session_key = models.CharField(max_length=100, verbose_name="Session Key")
    ip_address = models.GenericIPAddressField(null=True, blank=True, verbose_name="IP-Adresse")
    fingerprint = models.CharField(max_length=64, blank=True, verbose_name="Browser Fingerprint")
    reason = models.CharField(max_length=300, blank=True, verbose_name="Grund (intern)")
    public_message = models.CharField(max_length=300, blank=True, verbose_name="Sperrnachricht (öffentlich)",
        help_text="Wird dem gesperrten User angezeigt")
    blocked_by = models.CharField(max_length=100, default="admin", verbose_name="Blockiert von")
    event = models.ForeignKey(Event, on_delete=models.CASCADE, null=True, blank=True,
        related_name='blocked_clients', verbose_name="Event",
        help_text="Leer = global blockiert")
    is_active = models.BooleanField(default=True, verbose_name="Aktiv")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name="Ablaufdatum",
        help_text="Leer = permanent")
    class Meta:
        verbose_name = "Blockierter Client"
        verbose_name_plural = "Blockierte Clients"
        ordering = ['-created_at']
    def __str__(self):
        return f"Blockiert: {self.ip_address or self.session_key[:12]} — {self.reason}"
    @property
    def is_expired(self):
        if not self.expires_at:
            return False
        return timezone.now() >= self.expires_at
    @classmethod
    def is_blocked(cls, session_key=None, ip_address=None, event=None):
        """Returns the active BlockedClient object if blocked, else None."""
        from django.db.models import Q
        client_q = Q()
        if session_key:
            client_q |= Q(session_key=session_key)
        if ip_address:
            client_q |= Q(ip_address=ip_address)
        if not client_q:
            return None
        scope_q = Q(event=event) | Q(event__isnull=True)
        # Expiry direkt im Query filtern — kein UPDATE pro Aufruf
        not_expired_q = Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now())
        return cls.objects.filter(scope_q & client_q & not_expired_q, is_active=True).first()

    @classmethod
    def cleanup_expired(cls):
        """Deaktiviert abgelaufene Sperren (z.B. per Cronjob/Management-Command)."""
        return cls.objects.filter(is_active=True, expires_at__lte=timezone.now()).update(is_active=False)
