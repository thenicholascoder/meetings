import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0005_cohost_invite"),
    ]

    operations = [
        migrations.CreateModel(
            name="Recording",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("egress_id", models.CharField(max_length=64, unique=True)),
                ("object_key", models.CharField(blank=True, max_length=512)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("starting", "Starting"),
                            ("active", "Active"),
                            ("ending", "Ending"),
                            ("complete", "Complete"),
                            ("failed", "Failed"),
                            ("aborted", "Aborted"),
                            ("limit_reached", "Limit reached"),
                        ],
                        default="starting",
                        max_length=16,
                    ),
                ),
                ("error", models.TextField(blank=True)),
                ("size_bytes", models.BigIntegerField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "meeting",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recordings",
                        to="meetings.meeting",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="recording",
            index=models.Index(fields=["meeting", "status"], name="recording_meeting_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="recording",
            constraint=models.UniqueConstraint(
                condition=models.Q(status__in=["starting", "active", "ending"]),
                fields=("meeting",),
                name="one_open_recording_per_meeting",
            ),
        ),
    ]
