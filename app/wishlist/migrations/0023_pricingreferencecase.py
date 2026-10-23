from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0022_pricecalculationrevision_comparison_key')]

    operations = [migrations.CreateModel(
        name='PricingReferenceCase',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('name', models.CharField(max_length=200, unique=True)),
            ('result_json', models.JSONField(default=dict)),
            ('expected_v1_total', models.DecimalField(decimal_places=2, max_digits=12)),
            ('expected_v2_total', models.DecimalField(decimal_places=2, max_digits=12)),
            ('expected_difference', models.DecimalField(decimal_places=2, max_digits=12)),
            ('tolerance_threshold', models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True)),
            ('approved', models.BooleanField(default=True)),
            ('updated_at', models.DateTimeField(auto_now=True)),
        ],
    )]
