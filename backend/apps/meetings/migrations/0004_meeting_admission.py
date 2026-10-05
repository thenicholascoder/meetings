import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0003_shared_slides_speaker_notes"),
    ]

    operations = [
        migrations.CreateModel(
            name="Meeting",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("room_name", models.SlugField(max_length=64, unique=True)),
                ("ended_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="MeetingMember",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_hash", models.CharField(max_length=64, unique=True)),
                ("identity", models.CharField(max_length=32, unique=True)),
                ("display_name", models.CharField(blank=True, max_length=128)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("host", "Host"),
                            ("cohost", "Co-host"),
                            ("participant", "Participant"),
                        ],
                        default="participant",
                        max_length=16,
                    ),
                ),
                ("admitted", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "meeting",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="members",
                        to="meetings.meeting",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="meetingmember",
            constraint=models.UniqueConstraint(
                fields=("meeting", "identity"),
                name="member_meeting_identity_uniq",
            ),
        ),
        migrations.CreateModel(
            name="AdmissionRequest",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("accepted", "Accepted"),
                            ("declined", "Declined"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("decided_at", models.DateTimeField(blank=True, null=True)),
                (
                    "meeting",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="admissions",
                        to="meetings.meeting",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="admissions",
                        to="meetings.meetingmember",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddIndex(
            model_name="admissionrequest",
            index=models.Index(fields=["meeting", "status"], name="admission_meeting_status_idx"),
        ),
        migrations.AddConstraint(
            model_name="admissionrequest",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="pending"),
                fields=("member",),
                name="one_pending_admission_per_member",
            ),
        ),
    ]
