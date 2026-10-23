from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wishlist", "0021_pricecalculationrevision_send_claim_token")]

    operations = [
        migrations.AddField(
            model_name="pricecalculationrevision",
            name="comparison_key",
            field=models.CharField(max_length=64, null=True, blank=True, db_index=True),
        ),
        migrations.AddConstraint(
            model_name="pricecalculationrevision",
            constraint=models.UniqueConstraint(
                fields=("event", "kind", "comparison_key"),
                condition=models.Q(comparison_key__isnull=False),
                name="unique_migration_comparison_key",
            ),
        ),
    ]
