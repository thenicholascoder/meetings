import logging

from django.views.decorators.csrf import csrf_exempt
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .services.admission import (
    AdmissionError,
    accept_cohost_invite,
    accept_request,
    admission_status,
    create_meeting,
    decline_cohost_invite,
    decline_request,
    end_meeting,
    join_meeting,
    list_cohost_invites,
    list_waiting,
    mute_member,
    my_cohost_invite,
    remove_member,
    set_role,
)

logger = logging.getLogger(__name__)


def credentials(request) -> tuple[str, str]:
    data = request.data if isinstance(request.data, dict) else {}
    host_key = request.headers.get("X-Host-Key", "") or str(data.get("host_key") or "")
    participant_key = request.headers.get("X-Participant-Key", "") or str(data.get("participant_key") or "")
    return host_key, participant_key


def _finish(payload):
    status_code = 201 if isinstance(payload, dict) and "host_key" in payload else 200
    return Response(payload, status=status_code)


def _missing_table(exc: BaseException) -> bool:
    message = str(exc).lower()
    return "cohostinvite" in message or "cohost_invite" in message


def _run(fn):
    try:
        return _finish(fn())
    except AdmissionError as exc:
        return Response(
            {"detail": exc.detail, "status": exc.code},
            status=exc.status_code,
        )
    except Exception as exc:
        # The co-host table is new. A server that reloaded the code without
        # migrating returns an HTML error, which the host sees as
        # "That action was refused", and nobody receives the invitation.
        if _missing_table(exc):
            logger.warning("Applying migrations after a missing table")
            try:
                from django.core.management import call_command

                call_command("migrate", interactive=False, verbosity=1)
                return _finish(fn())
            except AdmissionError as retry_exc:
                return Response(
                    {"detail": retry_exc.detail, "status": retry_exc.code},
                    status=retry_exc.status_code,
                )
            except Exception:
                logger.exception("Meeting action failed after migrate")
        else:
            logger.exception("Meeting action failed")
        detail = str(exc).strip() or "That action failed."
        return Response({"detail": detail, "status": "error"}, status=500)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meetings_collection(request):
    """Start a meeting. The response host_key is the creator's host credential."""
    room_name = ""
    if isinstance(request.data, dict):
        room_name = str(request.data.get("room_name") or request.data.get("roomName") or "")

    def create():
        meeting, host_key = create_meeting(room_name)
        return {"room_name": meeting.room_name, "host_key": host_key}

    return _run(create)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_join(request, room_name):
    host_key, participant_key = credentials(request)
    data = request.data if isinstance(request.data, dict) else {}
    name = str(data.get("participant_name") or data.get("participantName") or "")

    def join():
        return join_meeting(
            room_name=room_name,
            participant_name=name,
            host_key=host_key,
            participant_key=participant_key,
        )

    return _run(join)


@csrf_exempt
@api_view(["GET"])
@permission_classes([AllowAny])
def meeting_admission(request, room_name, request_id):
    _host_key, participant_key = credentials(request)

    def status():
        return admission_status(
            room_name=room_name,
            request_id=request_id,
            participant_key=participant_key,
        )

    return _run(status)


@csrf_exempt
@api_view(["GET"])
@permission_classes([AllowAny])
def meeting_waiting(request, room_name):
    host_key, participant_key = credentials(request)

    def waiting():
        return {"requests": list_waiting(room_name=room_name, host_key=host_key, participant_key=participant_key)}

    return _run(waiting)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_admission_accept(request, room_name, request_id):
    host_key, participant_key = credentials(request)

    def accept():
        return accept_request(
            room_name=room_name,
            request_id=request_id,
            host_key=host_key,
            participant_key=participant_key,
        )

    return _run(accept)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_admission_decline(request, room_name, request_id):
    host_key, participant_key = credentials(request)

    def decline():
        return decline_request(
            room_name=room_name,
            request_id=request_id,
            host_key=host_key,
            participant_key=participant_key,
        )

    return _run(decline)


@csrf_exempt
@api_view(["GET"])
@permission_classes([AllowAny])
def meeting_cohost_invites(request, room_name):
    host_key, _participant_key = credentials(request)

    def invites():
        return {"invites": list_cohost_invites(room_name=room_name, host_key=host_key)}

    return _run(invites)


@csrf_exempt
@api_view(["GET"])
@permission_classes([AllowAny])
def meeting_cohost_invite(request, room_name):
    _host_key, participant_key = credentials(request)

    def invite():
        return {"invite": my_cohost_invite(room_name=room_name, participant_key=participant_key)}

    return _run(invite)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_cohost_invite_accept(request, room_name, invite_id):
    _host_key, participant_key = credentials(request)

    def accept():
        return accept_cohost_invite(
            room_name=room_name,
            invite_id=invite_id,
            participant_key=participant_key,
        )

    return _run(accept)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_cohost_invite_decline(request, room_name, invite_id):
    _host_key, participant_key = credentials(request)

    def decline():
        return decline_cohost_invite(
            room_name=room_name,
            invite_id=invite_id,
            participant_key=participant_key,
        )

    return _run(decline)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_participant_role(request, room_name, identity):
    host_key, _participant_key = credentials(request)
    data = request.data if isinstance(request.data, dict) else {}
    role = str(data.get("role") or "")

    def update():
        return set_role(room_name=room_name, identity=identity, role=role, host_key=host_key)

    return _run(update)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_participant_remove(request, room_name, identity):
    host_key, participant_key = credentials(request)

    def remove():
        return remove_member(
            room_name=room_name,
            identity=identity,
            host_key=host_key,
            participant_key=participant_key,
        )

    return _run(remove)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_participant_mute(request, room_name, identity):
    host_key, participant_key = credentials(request)

    def mute():
        return mute_member(
            room_name=room_name,
            identity=identity,
            host_key=host_key,
            participant_key=participant_key,
        )

    return _run(mute)


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def meeting_end(request, room_name):
    host_key, _participant_key = credentials(request)

    def end():
        return end_meeting(room_name=room_name, host_key=host_key)

    return _run(end)
