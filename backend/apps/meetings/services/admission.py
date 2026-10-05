"""Who may enter a meeting, and what host vs co-host may do.

The person who creates the meeting (Start meeting or Schedule) is the host.
Their browser keeps a secret host key. That key is not part of the shareable
room URL, so opening the link does not make someone the host.

Co-hosts are invited by that host. The title applies only after they accept.
A co-host can admit or decline the waiting room, share their screen, share
slides, mute someone else, and remove a participant. A co-host cannot
appoint co-hosts or end the meeting for everyone. The host can.

Everyone else knocks. They get a LiveKit token only after accept. Someone
who was already admitted can rejoin without knocking again, until they are
removed or the meeting ends.
"""

import hashlib
import logging
import re
import secrets
from datetime import datetime, timezone

from django.conf import settings
from django.db import IntegrityError, connection, transaction

from ..models import AdmissionRequest, CohostInvite, Meeting, MeetingMember, SharedSlides
from .room_control import (
    RoomControlError,
    delete_meeting_room,
    mute_participant,
    publish_waiting_count,
    remove_participant,
    set_participant_attributes,
    set_participant_role,
)
from .tokens import create_participant_token

logger = logging.getLogger(__name__)

ROOM_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
IDENTITY_RE = re.compile(r"^p_[a-f0-9]{16}$")


class AdmissionError(Exception):
    def __init__(self, detail: str, status_code: int = 400, code: str = "error"):
        super().__init__(detail)
        self.detail = detail
        self.status_code = status_code
        self.code = code


def hash_key(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_identity() -> str:
    return "p_" + secrets.token_hex(8)


def clean_room_name(room_name: str) -> str:
    slug = (room_name or "").strip().lower()
    if not slug or not ROOM_NAME_RE.fullmatch(slug) or len(slug) > 64:
        raise AdmissionError(
            "Room name must be lowercase letters, numbers, and dashes.",
            400,
        )
    return slug


def clean_display_name(name: str) -> str:
    cleaned = " ".join((name or "").split())[:128]
    if not cleaned:
        raise AdmissionError("Enter your name.", 400)
    return cleaned


def create_meeting(room_name: str) -> tuple[Meeting, str]:
    """Create the meeting and its host. Returns the raw host key once."""
    slug = clean_room_name(room_name)
    if Meeting.objects.filter(room_name=slug).exists():
        raise AdmissionError("That room name is already in use.", 409, "conflict")
    host_key = secrets.token_urlsafe(32)
    try:
        with transaction.atomic():
            meeting = Meeting.objects.create(room_name=slug)
            MeetingMember.objects.create(
                meeting=meeting,
                key_hash=hash_key(host_key),
                identity=new_identity(),
                display_name="",
                role=MeetingMember.Role.HOST,
                admitted=True,
            )
    except IntegrityError as exc:
        raise AdmissionError("That room name is already in use.", 409, "conflict") from exc
    return meeting, host_key


def join_meeting(
    *,
    room_name: str,
    participant_name: str,
    host_key: str = "",
    participant_key: str = "",
) -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    name = clean_display_name(participant_name)
    member = _member_for_key(meeting, host_key) or _member_for_key(meeting, participant_key)

    if member and member.role == MeetingMember.Role.HOST:
        member.display_name = name
        member.admitted = True
        member.save(update_fields=["display_name", "admitted"])
        return _token_payload(member)

    if member and member.admitted:
        member.display_name = name
        member.save(update_fields=["display_name"])
        return _token_payload(member)

    issued_key = ""
    if member is None:
        issued_key = secrets.token_urlsafe(32)
        member = MeetingMember.objects.create(
            meeting=meeting,
            key_hash=hash_key(issued_key),
            identity=new_identity(),
            display_name=name,
            role=MeetingMember.Role.PARTICIPANT,
            admitted=False,
        )
    else:
        member.display_name = name
        member.save(update_fields=["display_name"])
        issued_key = participant_key

    request = _pending_request(meeting, member)
    _publish_waiting(meeting)
    return {
        "status": "pending",
        "request_id": str(request.id),
        "participant_key": issued_key,
    }


def admission_status(*, room_name: str, request_id, participant_key: str) -> dict:
    meeting = _meeting(room_name)
    if meeting.ended_at is not None:
        return {"status": "ended", "detail": "The host has ended the meeting."}
    try:
        request = AdmissionRequest.objects.select_related("member").get(id=request_id, meeting=meeting)
    except AdmissionRequest.DoesNotExist as exc:
        raise AdmissionError("That join request was not found.", 404, "missing") from exc
    if not participant_key or request.member.key_hash != hash_key(participant_key):
        raise AdmissionError("This join request belongs to someone else.", 403, "forbidden")
    if request.status == AdmissionRequest.Status.PENDING:
        return {"status": "pending"}
    if request.status == AdmissionRequest.Status.DECLINED or not request.member.admitted:
        return {"status": "declined"}
    return _token_payload(request.member)


def list_waiting(*, room_name: str, host_key: str = "", participant_key: str = "") -> list[dict]:
    meeting = _meeting(room_name)
    _require_manager(meeting, host_key, participant_key)
    pending = (
        AdmissionRequest.objects.filter(meeting=meeting, status=AdmissionRequest.Status.PENDING)
        .select_related("member")
        .order_by("created_at")
    )
    return [
        {
            "id": str(request.id),
            "name": request.member.display_name or "Someone",
            "created_at": request.created_at.isoformat(),
        }
        for request in pending
    ]


def accept_request(*, room_name: str, request_id, host_key: str = "", participant_key: str = "") -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    _require_manager(meeting, host_key, participant_key)
    with transaction.atomic():
        request = _locked_request(meeting, request_id)
        if request.status == AdmissionRequest.Status.DECLINED:
            raise AdmissionError("That request was already declined.", 409, "conflict")
        if request.status == AdmissionRequest.Status.PENDING:
            now = datetime.now(timezone.utc)
            request.status = AdmissionRequest.Status.ACCEPTED
            request.decided_at = now
            request.save(update_fields=["status", "decided_at"])
            member = request.member
            member.admitted = True
            member.save(update_fields=["admitted"])
    _publish_waiting(meeting)
    return {"status": "accepted"}


def decline_request(*, room_name: str, request_id, host_key: str = "", participant_key: str = "") -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    _require_manager(meeting, host_key, participant_key)
    with transaction.atomic():
        request = _locked_request(meeting, request_id)
        if request.status == AdmissionRequest.Status.ACCEPTED:
            raise AdmissionError("That request was already accepted.", 409, "conflict")
        if request.status == AdmissionRequest.Status.PENDING:
            request.status = AdmissionRequest.Status.DECLINED
            request.decided_at = datetime.now(timezone.utc)
            request.save(update_fields=["status", "decided_at"])
            member = request.member
            member.admitted = False
            member.save(update_fields=["admitted"])
    _publish_waiting(meeting)
    return {"status": "declined"}


def set_role(
    *,
    room_name: str,
    identity: str,
    role: str,
    host_key: str = "",
) -> dict:
    """Only the host can invite a co-host, or take that title away.

    Making someone a co-host does not change their role. They get a pending
    invitation and become a co-host only if they accept it.
    """
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    host = _member_for_key(meeting, host_key)
    if host is None or host.role != MeetingMember.Role.HOST:
        raise AdmissionError("Only the host can assign co-hosts.", 403, "forbidden")
    if role not in (MeetingMember.Role.COHOST, MeetingMember.Role.PARTICIPANT):
        raise AdmissionError("Role must be co-host or participant.", 400)
    target = _member_by_identity(meeting, identity)
    if target.role == MeetingMember.Role.HOST:
        raise AdmissionError("The host role stays with the person who started the meeting.", 403, "forbidden")
    if not target.admitted:
        raise AdmissionError("Admit them before assigning a co-host.", 409, "conflict")
    if role == MeetingMember.Role.PARTICIPANT:
        target.role = MeetingMember.Role.PARTICIPANT
        target.save(update_fields=["role"])
        _close_pending_cohost_invites(member=target)
        _sync_role(meeting.room_name, target.identity, target.role)
        return {"status": "ok", "role": MeetingMember.Role.PARTICIPANT}
    if target.role == MeetingMember.Role.COHOST:
        return {"status": "ok", "role": MeetingMember.Role.COHOST}
    invite = _pending_cohost_invite(meeting, target)
    _mark_cohost_invite(meeting.room_name, target.identity, str(invite.id), role=target.role)
    return {"status": "invited", "invite_id": str(invite.id), "role": target.role}


def list_cohost_invites(*, room_name: str, host_key: str = "") -> list[dict]:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    host = _member_for_key(meeting, host_key)
    if host is None or host.role != MeetingMember.Role.HOST:
        raise AdmissionError("Only the host can see co-host invitations.", 403, "forbidden")
    pending = (
        CohostInvite.objects.filter(meeting=meeting, status=CohostInvite.Status.PENDING)
        .select_related("member")
        .order_by("created_at")
    )
    return [
        {
            "id": str(invite.id),
            "identity": invite.member.identity,
            "name": invite.member.display_name or "Someone",
        }
        for invite in pending
    ]


def my_cohost_invite(*, room_name: str, participant_key: str = "") -> dict | None:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    member = _member_for_key(meeting, participant_key)
    if member is None or not member.admitted:
        raise AdmissionError("Only someone in the meeting can see that invitation.", 403, "forbidden")
    invite = (
        CohostInvite.objects.filter(member=member, status=CohostInvite.Status.PENDING)
        .order_by("created_at")
        .first()
    )
    if invite is None:
        return None
    host = MeetingMember.objects.filter(meeting=meeting, role=MeetingMember.Role.HOST).first()
    host_name = (host.display_name if host is not None else "") or "The host"
    return {"id": str(invite.id), "host_name": host_name}


def accept_cohost_invite(*, room_name: str, invite_id, participant_key: str = "") -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    member = _member_for_key(meeting, participant_key)
    if member is None or not member.admitted:
        raise AdmissionError("Only the invited person can answer.", 403, "forbidden")
    with transaction.atomic():
        invite = _locked_cohost_invite(meeting, invite_id)
        if invite.member_id != member.id:
            raise AdmissionError("Only the invited person can answer.", 403, "forbidden")
        if invite.status == CohostInvite.Status.DECLINED:
            raise AdmissionError("That invitation was already declined.", 409, "conflict")
        if invite.status == CohostInvite.Status.PENDING:
            now = datetime.now(timezone.utc)
            invite.status = CohostInvite.Status.ACCEPTED
            invite.decided_at = now
            invite.save(update_fields=["status", "decided_at"])
            member.role = MeetingMember.Role.COHOST
            member.save(update_fields=["role"])
    member.refresh_from_db(fields=["role"])
    _sync_role(meeting.room_name, member.identity, member.role)
    _mark_cohost_invite(meeting.room_name, member.identity, "", role=member.role)
    return {"status": "accepted", "role": member.role}


def decline_cohost_invite(*, room_name: str, invite_id, participant_key: str = "") -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    member = _member_for_key(meeting, participant_key)
    if member is None or not member.admitted:
        raise AdmissionError("Only the invited person can answer.", 403, "forbidden")
    with transaction.atomic():
        invite = _locked_cohost_invite(meeting, invite_id)
        if invite.member_id != member.id:
            raise AdmissionError("Only the invited person can answer.", 403, "forbidden")
        if invite.status == CohostInvite.Status.ACCEPTED:
            raise AdmissionError("That invitation was already accepted.", 409, "conflict")
        if invite.status == CohostInvite.Status.PENDING:
            invite.status = CohostInvite.Status.DECLINED
            invite.decided_at = datetime.now(timezone.utc)
            invite.save(update_fields=["status", "decided_at"])
    _mark_cohost_invite(meeting.room_name, member.identity, "", role=member.role)
    return {"status": "declined", "role": member.role}


def remove_member(
    *,
    room_name: str,
    identity: str,
    host_key: str = "",
    participant_key: str = "",
) -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    actor = _require_manager(meeting, host_key, participant_key)
    target = _member_by_identity(meeting, identity)
    if target.identity == actor.identity:
        raise AdmissionError("Leave the meeting with the Leave button.", 400)
    if target.role == MeetingMember.Role.HOST:
        raise AdmissionError("The host can't be removed.", 403, "forbidden")
    if actor.role == MeetingMember.Role.COHOST and target.role == MeetingMember.Role.COHOST:
        raise AdmissionError("Only the host can remove a co-host.", 403, "forbidden")
    target.admitted = False
    target.role = MeetingMember.Role.PARTICIPANT
    target.save(update_fields=["admitted", "role"])
    AdmissionRequest.objects.filter(
        member=target,
        status=AdmissionRequest.Status.PENDING,
    ).update(status=AdmissionRequest.Status.DECLINED, decided_at=datetime.now(timezone.utc))
    _close_pending_cohost_invites(member=target)
    try:
        remove_participant(meeting.room_name, target.identity)
    except RoomControlError as exc:
        if not exc.not_found:
            raise AdmissionError(
                "They have to knock again, but the room could not disconnect them.",
                502,
            ) from exc
    return {"status": "removed"}


def mute_member(
    *,
    room_name: str,
    identity: str,
    host_key: str = "",
    participant_key: str = "",
) -> dict:
    meeting = _meeting(room_name)
    _ensure_open(meeting)
    actor = _require_manager(meeting, host_key, participant_key)
    target = _member_by_identity(meeting, identity)
    if target.identity == actor.identity:
        raise AdmissionError("Use your own microphone button to mute yourself.", 400)
    try:
        mute_participant(meeting.room_name, target.identity)
    except RoomControlError as exc:
        if exc.not_found:
            raise AdmissionError("That person is not in the meeting right now.", 404, "missing") from exc
        raise AdmissionError("Could not mute that microphone.", 502) from exc
    return {"status": "muted"}


def end_meeting(*, room_name: str, host_key: str = "") -> dict:
    meeting = _meeting(room_name)
    host = _member_for_key(meeting, host_key)
    if host is None or host.role != MeetingMember.Role.HOST:
        raise AdmissionError("Only the host can end the meeting for everyone.", 403, "forbidden")
    if meeting.ended_at is None:
        now = datetime.now(timezone.utc)
        meeting.ended_at = now
        meeting.save(update_fields=["ended_at"])
        AdmissionRequest.objects.filter(
            meeting=meeting,
            status=AdmissionRequest.Status.PENDING,
        ).update(status=AdmissionRequest.Status.DECLINED, decided_at=now)
        _close_pending_cohost_invites(meeting=meeting)
    try:
        delete_meeting_room(meeting.room_name)
    except RoomControlError:
        # The meeting is closed in our database either way. A room that was
        # never opened, or a LiveKit blip, should not keep the host stuck.
        pass
    return {"status": "ended"}


def _meeting(room_name: str) -> Meeting:
    slug = clean_room_name(room_name)
    try:
        return Meeting.objects.get(room_name=slug)
    except Meeting.DoesNotExist as exc:
        raise AdmissionError(
            "This meeting hasn't been started by a host.",
            404,
            "missing",
        ) from exc


def _ensure_open(meeting: Meeting) -> None:
    if meeting.ended_at is not None:
        raise AdmissionError("The host has ended the meeting.", 409, "ended")


def _member_for_key(meeting: Meeting, raw_key: str) -> MeetingMember | None:
    if not raw_key:
        return None
    return MeetingMember.objects.filter(meeting=meeting, key_hash=hash_key(raw_key)).first()


def _require_manager(meeting: Meeting, host_key: str, participant_key: str) -> MeetingMember:
    actor = _member_for_key(meeting, host_key) or _member_for_key(meeting, participant_key)
    if actor is None or not actor.admitted or actor.role not in (
        MeetingMember.Role.HOST,
        MeetingMember.Role.COHOST,
    ):
        raise AdmissionError("Only the host or a co-host can do that.", 403, "forbidden")
    return actor


def manager_for(room_name: str, host_key: str, participant_key: str, *, allow_ended: bool = False) -> MeetingMember:
    """Host or co-host. Recording can be stopped after the meeting is closed."""
    meeting = _meeting(room_name)
    if not allow_ended:
        _ensure_open(meeting)
    return _require_manager(meeting, host_key, participant_key)


def admitted_member(room_name: str, host_key: str, participant_key: str) -> MeetingMember:
    """Someone the host let in. Past recordings stay available after the meeting ends."""
    meeting = _meeting(room_name)
    member = _member_for_key(meeting, host_key) or _member_for_key(meeting, participant_key)
    if member is None or not member.admitted:
        raise AdmissionError("Only people in this meeting can do that.", 403, "forbidden")
    return member


def _member_by_identity(meeting: Meeting, identity: str) -> MeetingMember:
    if not IDENTITY_RE.fullmatch(identity or ""):
        raise AdmissionError("That person is not in this meeting.", 404, "missing")
    try:
        return MeetingMember.objects.get(meeting=meeting, identity=identity)
    except MeetingMember.DoesNotExist as exc:
        raise AdmissionError("That person is not in this meeting.", 404, "missing") from exc


def _pending_request(meeting: Meeting, member: MeetingMember) -> AdmissionRequest:
    existing = AdmissionRequest.objects.filter(
        member=member,
        status=AdmissionRequest.Status.PENDING,
    ).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return AdmissionRequest.objects.create(
                meeting=meeting,
                member=member,
                status=AdmissionRequest.Status.PENDING,
            )
    except IntegrityError:
        return AdmissionRequest.objects.get(member=member, status=AdmissionRequest.Status.PENDING)


def _locked_request(meeting: Meeting, request_id) -> AdmissionRequest:
    queryset = AdmissionRequest.objects.select_related("member")
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(id=request_id, meeting=meeting)
    except AdmissionRequest.DoesNotExist as exc:
        raise AdmissionError("That join request was not found.", 404, "missing") from exc


def _token_payload(member: MeetingMember) -> dict:
    if not settings.LIVEKIT_URL:
        raise AdmissionError("LIVEKIT_URL is not defined", 500)
    if not settings.LIVEKIT_API_KEY or not settings.LIVEKIT_API_SECRET:
        raise AdmissionError("LiveKit API credentials are not defined", 500)
    token = create_participant_token(
        identity=member.identity,
        name=member.display_name or "Guest",
        room_name=member.meeting.room_name,
        role=member.role,
        allow_screen_share=_allow_screen_share(member.meeting.room_name, member.role),
    )
    return {
        "status": "admitted",
        "server_url": settings.LIVEKIT_URL,
        "participant_token": token,
        "role": member.role,
        "identity": member.identity,
    }


def _publish_waiting(meeting: Meeting) -> None:
    count = AdmissionRequest.objects.filter(
        meeting=meeting,
        status=AdmissionRequest.Status.PENDING,
    ).count()
    publish_waiting_count(meeting.room_name, count)


def _slides_are_active(room_name: str) -> bool:
    return SharedSlides.objects.filter(room_name=room_name, active=True).exists()


def _allow_screen_share(room_name: str, role: str) -> bool:
    return role in (MeetingMember.Role.HOST, MeetingMember.Role.COHOST) and not _slides_are_active(room_name)


def _mark_cohost_invite(room_name: str, identity: str, invite_id: str, *, role: str) -> None:
    """Tell that person's client about the invitation. Empty clears it.

    Role is sent again in the same update. A later attribute write that only
    cleared the invitation was dropping role, so the co-host never started
    watching the waiting room.
    """
    try:
        set_participant_attributes(
            room_name,
            identity,
            {"cohost_invite": invite_id, "role": role},
        )
    except Exception:
        logger.warning("Could not mark co-host invite for %s in %s", identity, room_name, exc_info=True)


def _sync_role(room_name: str, identity: str, role: str) -> None:
    try:
        set_participant_role(
            room_name,
            identity,
            role,
            allow_screen_share=_allow_screen_share(room_name, role),
        )
    except Exception:
        # They may not be connected yet, or LiveKit may be down. The next
        # token still carries the role, and the database is the authority.
        logger.warning("Could not sync %s role in %s", identity, room_name, exc_info=True)


def _pending_cohost_invite(meeting: Meeting, member: MeetingMember) -> CohostInvite:
    existing = CohostInvite.objects.filter(member=member, status=CohostInvite.Status.PENDING).first()
    if existing is not None:
        return existing
    try:
        with transaction.atomic():
            return CohostInvite.objects.create(
                meeting=meeting,
                member=member,
                status=CohostInvite.Status.PENDING,
            )
    except IntegrityError:
        return CohostInvite.objects.get(member=member, status=CohostInvite.Status.PENDING)


def _locked_cohost_invite(meeting: Meeting, invite_id) -> CohostInvite:
    queryset = CohostInvite.objects.select_related("member")
    if connection.features.has_select_for_update:
        queryset = queryset.select_for_update()
    try:
        return queryset.get(id=invite_id, meeting=meeting)
    except CohostInvite.DoesNotExist as exc:
        raise AdmissionError("That co-host invitation was not found.", 404, "missing") from exc


def _close_pending_cohost_invites(*, meeting: Meeting | None = None, member: MeetingMember | None = None) -> None:
    pending = CohostInvite.objects.filter(status=CohostInvite.Status.PENDING)
    if meeting is not None:
        pending = pending.filter(meeting=meeting)
    if member is not None:
        pending = pending.filter(member=member)
    rows = list(pending.select_related("member", "meeting"))
    if not rows:
        return
    CohostInvite.objects.filter(id__in=[row.id for row in rows]).update(
        status=CohostInvite.Status.DECLINED,
        decided_at=datetime.now(timezone.utc),
    )
    for row in rows:
        _mark_cohost_invite(row.meeting.room_name, row.member.identity, "", role=row.member.role)
