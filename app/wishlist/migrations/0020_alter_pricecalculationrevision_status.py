from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0019_offer_send_claim')]

    operations = [
        migrations.AlterField(
            model_name='pricecalculationrevision',
            name='status',
            field=models.CharField(
                choices=[
                    ('draft', 'Draft'),
                    ('released', 'Released'),
                    ('sending', 'Sending'),
                    ('sent', 'Sent'),
                    ('send_failed', 'Send failed'),
                    ('superseded', 'Superseded'),
                ],
                max_length=20,
            ),
        ),
    ]
