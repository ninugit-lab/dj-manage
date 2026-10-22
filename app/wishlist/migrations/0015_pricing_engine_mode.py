from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0014_pricing_workflow_default_constraint')]

    operations = [
        migrations.AddField(
            model_name='appconfig', name='pricing_engine_mode',
            field=models.CharField(choices=[('legacy', 'Legacy'), ('shadow', 'Shadow'), ('v2', 'V2')],
                                   default='legacy', max_length=6),
        ),
    ]
