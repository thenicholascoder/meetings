import uuid

from django.db import models


class Meeting(models.Model):
    """A meeting the host started or scheduled.

    LiveKit still opens the media room on connect. This row is who is allowed
    to be the host, who was admitted, and whether the meeting is over.
    """

    room_name = models.SlugField(max_length=64, unique=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.room_name


class MeetingMember(models.Model):
    """One browser's credential for a meeting.

    The host key and each participant key are stored only as a hash. The raw
    key stays in that browser. Role is the server's copy of host / co-host /
    participant; LiveKit attributes are a display copy of the same fact.
    """

    class Role(models.TextChoices):
        HOST = "host", "Host"
        COHOST = "cohost", "Co-host"
        PARTICIPANT = "participant", "Participant"

    meeting = models.ForeignKey(Meeting, related_name="members", on_delete=models.CASCADE)
    key_hash = models.CharField(max_length=64, unique=True)
    identity = models.CharField(max_length=32, unique=True)
    display_name = models.CharField(max_length=128, blank=True)
    role = models.CharField(max_length=16, choices=Role.choices, default=Role.PARTICIPANT)
    admitted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["meeting", "identity"],
                name="member_meeting_identity_uniq",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.display_name or self.identity} ({self.role})"


class AdmissionRequest(models.Model):
    """A knock on the waiting room. The joiner stays out until this is accepted."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, related_name="admissions", on_delete=models.CASCADE)
    member = models.ForeignKey(MeetingMember, related_name="admissions", on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(status="pending"),
                name="one_pending_admission_per_member",
            ),
        ]
        indexes = [
            models.Index(fields=["meeting", "status"], name="admission_meeting_status_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.member.display_name} {self.status}"


class CohostInvite(models.Model):
    """The host asked someone to be a co-host. The title applies only after they accept."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        DECLINED = "declined", "Declined"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    meeting = models.ForeignKey(Meeting, related_name="cohost_invites", on_delete=models.CASCADE)
    member = models.ForeignKey(MeetingMember, related_name="cohost_invites", on_delete=models.CASCADE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    created_at = models.DateTimeField(auto_now_add=True)
    decided_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["member"],
                condition=models.Q(status="pending"),
                name="one_pending_cohost_invite_per_member",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.member.display_name} {self.status}"


class Recording(models.Model):
    """One room-composite egress. LiveKit only lists active jobs, so the file lives here.

    The object key is `{room_name}/{time}.mp4` inside the recordings bucket.
    """

    class Status(models.TextChoices):
        STARTING = "starting", "Starting"
        ACTIVE = "active", "Active"
        ENDING = "ending", "Ending"
        COMPLETE = "complete", "Complete"
        FAILED = "failed", "Failed"
        ABORTED = "aborted", "Aborted"
        LIMIT_REACHED = "limit_reached", "Limit reached"

    meeting = models.ForeignKey(Meeting, related_name="recordings", on_delete=models.CASCADE)
    egress_id = models.CharField(max_length=64, unique=True)
    object_key = models.CharField(max_length=512, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.STARTING)
    error = models.TextField(blank=True)
    size_bytes = models.BigIntegerField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["meeting", "status"], name="recording_meeting_status_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["meeting"],
                condition=models.Q(status__in=["starting", "active", "ending"]),
                name="one_open_recording_per_meeting",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.egress_id} ({self.status})"


class ScheduledRoom(models.Model):
    """App-side schedule. LiveKit still creates the SFU room on first join.

    https://docs.livekit.io/reference/other/roomservice-api.md#createroom
    CreateRoom is optional; storing the shareable room name here is the scheduler.
    """

    title = models.CharField(max_length=200)
    room_name = models.SlugField(max_length=64, unique=True)
    starts_at = models.DateTimeField()
    duration_minutes = models.PositiveIntegerField(default=30)
    max_participants = models.PositiveIntegerField(default=0)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["starts_at"]

    def __str__(self) -> str:
        return f"{self.title} ({self.room_name})"


class SharedSlides(models.Model):
    """One active deck per room.

    PDF and PowerPoint uploads are stored as uploaded. Viewers render the file
    locally; only the page index is synced. Speaker notes stay on the deck and
    are returned only to the person sharing.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room_name = models.CharField(max_length=128)
    owner_identity = models.CharField(max_length=256)
    owner_name = models.CharField(max_length=128, blank=True)
    original_name = models.CharField(max_length=255)
    file = models.FileField(upload_to="slides")
    page = models.PositiveIntegerField(default=1)
    page_count = models.PositiveIntegerField(default=1)
    speaker_notes = models.JSONField(default=list, blank=True)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=["room_name", "active"], name="slides_room_active_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.original_name} ({self.room_name})"
