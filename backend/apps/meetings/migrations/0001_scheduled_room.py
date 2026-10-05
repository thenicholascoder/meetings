from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="ScheduledRoom",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("room_name", models.SlugField(max_length=64, unique=True)),
                ("starts_at", models.DateTimeField()),
                ("duration_minutes", models.PositiveIntegerField(default=30)),
                ("max_participants", models.PositiveIntegerField(default=0)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["starts_at"],
            },
        ),
    ]
