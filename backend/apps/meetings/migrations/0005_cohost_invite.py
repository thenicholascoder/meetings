import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("meetings", "0004_meeting_admission"),
    ]

    operations = [
        migrations.CreateModel(
            name="CohostInvite",
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
                        related_name="cohost_invites",
                        to="meetings.meeting",
                    ),
                ),
                (
                    "member",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="cohost_invites",
                        to="meetings.meetingmember",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at"],
            },
        ),
        migrations.AddConstraint(
            model_name="cohostinvite",
            constraint=models.UniqueConstraint(
                condition=models.Q(status="pending"),
                fields=("member",),
                name="one_pending_cohost_invite_per_member",
            ),
        ),
    ]
