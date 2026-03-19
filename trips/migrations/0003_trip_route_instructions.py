from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("trips", "0002_trip_route_options"),
    ]

    operations = [
        migrations.AddField(
            model_name="trip",
            name="route_instructions",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
