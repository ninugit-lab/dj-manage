import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0020_alter_pricecalculationrevision_status')]

    operations = [
        migrations.AddField(
            model_name='pricecalculationrevision',
            name='send_claim_token',
            field=models.UUIDField(blank=True, editable=False, null=True),
        ),
    ]
