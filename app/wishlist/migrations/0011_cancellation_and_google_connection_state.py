from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('wishlist', '0010_appconfig_google_review_url_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='event', name='status_before_cancellation',
            field=models.CharField(blank=True, choices=[('inquiry', 'Angefragt'), ('confirmed', 'Bestätigt'), ('past', 'Vergangen'), ('cancelled', 'Storniert')], max_length=20, verbose_name='Status vor Stornierung'),
        ),
        migrations.AddField(model_name='event', name='cancellation_reason', field=models.TextField(blank=True, verbose_name='Stornogrund')),
        migrations.AddField(model_name='event', name='cancelled_at', field=models.DateTimeField(blank=True, null=True, verbose_name='Storniert am')),
        migrations.AddField(
            model_name='event', name='cancelled_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cancelled_events', to=settings.AUTH_USER_MODEL, verbose_name='Storniert von'),
        ),
        migrations.AddField(model_name='appconfig', name='cancellation_email_subject', field=models.CharField(default='Deine Buchung wurde storniert', max_length=200, verbose_name='Storno-Mail Betreff')),
        migrations.AddField(model_name='appconfig', name='cancellation_email_body', field=models.TextField(blank=True, default='Hallo {client_name},\n\nleider wurde dein Event "{event_name}" am {event_date} um {event_time} in {location} storniert.\n\n{cancellation_reason}\n\nBeste Grüße,\n{dj_name}', help_text='Platzhalter: {client_name}, {event_name}, {event_date}, {event_time}, {location}, {cancellation_reason}, {dj_name}', verbose_name='Storno-Mail Text')),
        migrations.AddField(model_name='googletoken', name='connection_state', field=models.CharField(default='connected', max_length=40)),
        migrations.AddField(model_name='googletoken', name='reauthorization_required', field=models.BooleanField(default=False)),
        migrations.AddField(model_name='googletoken', name='last_refresh_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='googletoken', name='last_success_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='googletoken', name='last_error_at', field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name='googletoken', name='last_error_code', field=models.CharField(blank=True, max_length=100)),
        migrations.AddField(model_name='googletoken', name='last_error_message', field=models.TextField(blank=True)),
    ]
