from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('wishlist', '0012_pricing_v2_core')]

    operations = [
        migrations.AddField(
            model_name='pricingformula',
            name='mode',
            field=models.CharField(choices=[('add', 'Hinzufügen'), ('subtract', 'Abziehen'), ('replace', 'Ersetzen')],
                                   default='add', max_length=10, verbose_name='Modus'),
        ),
        migrations.AlterField(
            model_name='pricingrule',
            name='applies_to',
            field=models.CharField(choices=[('subtotal', 'Zwischensumme'), ('base', 'Grundpreis'),
                                            ('package', 'Paket'), ('tech', 'Technik'), ('extras', 'Extras'),
                                            ('travel', 'Anfahrt')], default='subtotal', max_length=20,
                                   verbose_name='Bezieht sich auf'),
        ),
    ]
