from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0018_offer_email_recipient')]

    operations = [
        migrations.AddField(
            model_name='pricecalculationrevision',
            name='send_claimed_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
