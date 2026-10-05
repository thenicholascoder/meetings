import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0001_scheduled_room"),
    ]

    operations = [
        migrations.CreateModel(
            name="SharedSlides",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("room_name", models.CharField(max_length=128)),
                ("owner_identity", models.CharField(max_length=256)),
                ("owner_name", models.CharField(blank=True, max_length=128)),
                ("original_name", models.CharField(max_length=255)),
                ("file", models.FileField(upload_to="slides")),
                ("page", models.PositiveIntegerField(default=1)),
                ("page_count", models.PositiveIntegerField(default=1)),
                ("active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
        ),
        migrations.AddIndex(
            model_name="sharedslides",
            index=models.Index(fields=["room_name", "active"], name="slides_room_active_idx"),
        ),
    ]
