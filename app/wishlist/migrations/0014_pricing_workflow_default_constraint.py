from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0013_pricing_formula_modes')]

    operations = [
        migrations.AddConstraint(
            model_name='pricingworkflow',
            constraint=models.UniqueConstraint(
                fields=('is_default',), condition=models.Q(is_default=True, is_active=True),
                name='unique_active_default_pricing_workflow',
            ),
        ),
    ]
