from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0017_offer_email_snapshot')]

    operations = [
        migrations.AddField(
            model_name='pricecalculationrevision', name='email_recipient',
            field=models.EmailField(blank=True, max_length=254),
        ),
    ]
