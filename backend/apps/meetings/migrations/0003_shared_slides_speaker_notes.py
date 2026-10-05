from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0002_shared_slides"),
    ]

    operations = [
        migrations.AddField(
            model_name="sharedslides",
            name="speaker_notes",
            field=models.JSONField(blank=True, default=list),
        ),
    ]
