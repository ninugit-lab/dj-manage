from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0016_offer_email_template')]

    operations = [
        migrations.AddField(
            model_name='pricecalculationrevision', name='email_context',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name='pricecalculationrevision', name='email_subject',
            field=models.TextField(blank=True),
        ),
        migrations.AddField(
            model_name='pricecalculationrevision', name='email_body',
            field=models.TextField(blank=True),
        ),
    ]
