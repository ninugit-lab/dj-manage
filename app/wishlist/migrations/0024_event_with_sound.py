from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("wishlist", "0023_pricingreferencecase")]
    operations = [migrations.AddField(model_name="event", name="with_sound",
                                      field=models.BooleanField(default=True, verbose_name="Tontechnik dabei"))]
