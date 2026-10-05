"""Start, stop, list, and download room recordings.

Start and stop stay on /api/record/* so the in-call control keeps its path.
The catalog is /api/recordings because ListEgress does not keep finished files.
"""

import logging
from pathlib import Path

from django.http import FileResponse, HttpResponse, JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET

from .services.admission import AdmissionError, admitted_member, manager_for
from .services.egress import (
    RecordingStorageError,
    WebhookRejected,
    apply_egress_webhook,
    list_room_recordings,
    recording_download,
    recording_object_key_ok,
    start_room_recording,
    stop_room_recording,
)

logger = logging.getLogger(__name__)

_EGRESS_ASSETS = Path(__file__).resolve().parent / "static" / "egress"


@require_GET
def recording_template(request):
    """Fallback page with the same start contract as the Next /egress template."""
    html = (_EGRESS_ASSETS / "template.html").read_text(encoding="utf-8")
    return HttpResponse(html, content_type="text/html; charset=utf-8")


@require_GET
def recording_template_script(request):
    return FileResponse(
        (_EGRESS_ASSETS / "livekit-client.umd.min.js").open("rb"),
        content_type="text/javascript",
    )


def _keys(request) -> tuple[str, str]:
    return (
        request.headers.get("X-Host-Key", ""),
        request.headers.get("X-Participant-Key", ""),
    )


def _room_name(request) -> str | None:
    if "roomName" in request.GET:
        return request.GET.get("roomName")
    if "room_name" in request.GET:
        return request.GET.get("room_name")
    return None


def _recording_payload(row) -> dict:
    return {
        "egressId": row.egress_id,
        "status": row.status,
        "key": row.object_key,
        "sizeBytes": row.size_bytes,
        "error": row.error,
        "startedAt": row.started_at.isoformat() if row.started_at else None,
        "endedAt": row.ended_at.isoformat() if row.ended_at else None,
    }


def _admission_response(exc: AdmissionError) -> JsonResponse:
    return JsonResponse({"detail": exc.detail, "status": exc.code}, status=exc.status_code)


@csrf_exempt
def record_start(request):
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    room_name = _room_name(request)
    if room_name is None:
        return HttpResponse("Missing roomName parameter", status=403)
    host_key, participant_key = _keys(request)
    try:
        member = manager_for(room_name, host_key, participant_key)
        recording = start_room_recording(member.meeting.room_name)
    except AdmissionError as exc:
        return _admission_response(exc)
    except ValueError as exc:
        return JsonResponse({"detail": str(exc)}, status=409)
    except Exception as exc:
        logger.exception("Failed to start recording for %s", room_name)
        return JsonResponse({"detail": str(exc)}, status=500)
    return JsonResponse(_recording_payload(recording))


@csrf_exempt
def record_stop(request):
    if request.method not in ("GET", "POST"):
        return HttpResponse(status=405)
    room_name = _room_name(request)
    if room_name is None:
        return HttpResponse("Missing roomName parameter", status=403)
    host_key, participant_key = _keys(request)
    try:
        member = manager_for(room_name, host_key, participant_key, allow_ended=True)
        stopped = stop_room_recording(member.meeting.room_name)
    except AdmissionError as exc:
        return _admission_response(exc)
    except LookupError as exc:
        return JsonResponse({"detail": str(exc)}, status=404)
    except Exception as exc:
        logger.exception("Failed to stop recording for %s", room_name)
        return JsonResponse({"detail": str(exc)}, status=500)
    return JsonResponse({"recordings": [_recording_payload(row) for row in stopped]})


@require_GET
def recordings_collection(request):
    room_name = _room_name(request)
    if not room_name:
        return JsonResponse({"detail": "Missing roomName parameter"}, status=403)
    host_key, participant_key = _keys(request)
    try:
        member = admitted_member(room_name, host_key, participant_key)
        rows = list_room_recordings(member.meeting.room_name)
    except AdmissionError as exc:
        return _admission_response(exc)
    return JsonResponse({"recordings": [_recording_payload(row) for row in rows]})


@require_GET
def recordings_file(request):
    room_name = (request.GET.get("roomName") or request.GET.get("room_name") or "").strip().lower()
    key = request.GET.get("key") or ""
    # Reject another room's key before any credential lookup.
    if not recording_object_key_ok(room_name, key):
        return JsonResponse({"detail": "Recording not found"}, status=404)
    host_key, participant_key = _keys(request)
    try:
        member = admitted_member(room_name, host_key, participant_key)
        opened = recording_download(member.meeting.room_name, key)
    except AdmissionError as exc:
        return _admission_response(exc)
    except RecordingStorageError as exc:
        return JsonResponse({"detail": exc.detail}, status=404)

    if "url" in opened:
        return JsonResponse({"url": opened["url"]})

    if "path" in opened:
        opened_file = opened["path"].open("rb")
        filename = key.rsplit("/", 1)[-1]
        stream = FileResponse(opened_file, content_type=opened["content_type"], as_attachment=True, filename=filename)
        stream["Cache-Control"] = "private, no-store"
        return stream

    body = opened["body"]
    stream = StreamingHttpResponse(body.iter_chunks(), content_type=opened["content_type"])
    if opened["length"] is not None:
        stream["Content-Length"] = str(opened["length"])
    filename = key.rsplit("/", 1)[-1]
    stream["Content-Disposition"] = f'attachment; filename="{filename}"'
    stream["Cache-Control"] = "private, no-store"
    return stream


@csrf_exempt
def livekit_webhook(request):
    """LiveKit posts egress_ended here. The body must stay raw for the signature check."""
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        body = request.body.decode()
    except UnicodeDecodeError:
        return JsonResponse({"detail": "Invalid webhook body"}, status=400)
    try:
        apply_egress_webhook(body, request.headers.get("Authorization", ""))
    except WebhookRejected:
        logger.warning("Rejected LiveKit webhook")
        return JsonResponse({"detail": "Invalid webhook signature"}, status=401)
    except Exception:
        logger.exception("Failed to store LiveKit egress webhook")
        return JsonResponse({"detail": "Could not store the egress event"}, status=500)
    return HttpResponse(status=200)
