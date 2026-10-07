from django.db import migrations, models


class Migration(migrations.Migration):
    """Store the distance from the office captured at check-in / check-out."""

    dependencies = [
        ("attendance", "0002_attendance_location"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendance",
            name="check_in_distance_m",
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendance",
            name="check_out_distance_m",
            field=models.FloatField(blank=True, null=True),
        ),
    ]
