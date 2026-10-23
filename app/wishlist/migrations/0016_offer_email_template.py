from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0015_pricing_engine_mode')]

    operations = [
        migrations.AddField(
            model_name='appconfig', name='offer_email_subject',
            field=models.CharField(default='Dein Angebot für {event_name}', max_length=200,
                                   verbose_name='Angebots-Mail Betreff'),
        ),
        migrations.AddField(
            model_name='appconfig', name='offer_email_body',
            field=models.TextField(blank=True,
                                   default='Hallo {client_name},\n\nanbei dein Angebot für {event_name} am {event_date}.\n\n{price_breakdown}\n\nBrutto-Endpreis: {gross_total} €\n\nBeste Grüße,\n{dj_name}',
                                   help_text='Platzhalter: {client_name}, {event_name}, {event_date}, {price_breakdown}, {gross_total}, {dj_name}',
                                   verbose_name='Angebots-Mail Text'),
        ),
        migrations.AddField(
            model_name='emaillog', name='revision',
            field=models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL,
                                    related_name='email_logs', to='wishlist.pricecalculationrevision'),
        ),
    ]
