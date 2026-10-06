import json

from django.db import transaction
from django.http import FileResponse, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .models import ScheduledRoom, SharedSlides
from .serializers import ScheduledRoomSerializer
from .services.admission import AdmissionError, create_meeting
from .services.slides import (
    MAX_PDF_BYTES,
    current_deck,
    deck_payload,
    file_content_type,
    notes_payload,
    save_deck,
    set_page,
    speaker_notes_for,
    stop_deck,
)


@require_GET
def health(_request):
    return JsonResponse({"status": "ok"})


@csrf_exempt
@api_view(["POST"])
@permission_classes([AllowAny])
def get_token(_request):
    """Do not mint a join token here.

    A direct token would skip the waiting room. The host admits people from
    POST /api/meetings/<room>/join.
    """
    return Response(
        {
            "detail": "Join from the meeting page. The host has to admit you before you can enter.",
            "status": "forbidden",
        },
        status=status.HTTP_403_FORBIDDEN,
    )


@require_GET
def connection_details(_request):
    """Legacy Meet-style GET. Tokens now go through the waiting room."""
    return JsonResponse(
        {
            "detail": "Join from the meeting page. The host has to admit you before you can enter.",
            "status": "forbidden",
        },
        status=403,
    )


@csrf_exempt
@api_view(["GET", "POST"])
@permission_classes([AllowAny])
def schedules(request):
    """Persist a shareable LiveKit room name and start time.

    LiveKit CreateRoom is optional; the SFU room is created when the first
    participant joins. https://docs.livekit.io/reference/other/roomservice-api.md
    """
    if request.method == "GET":
        queryset = ScheduledRoom.objects.all()
        return Response(ScheduledRoomSerializer(queryset, many=True).data)

    serializer = ScheduledRoomSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    room_name = str(serializer.validated_data["room_name"]).lower()
    serializer.validated_data["room_name"] = room_name
    try:
        with transaction.atomic():
            _meeting, host_key = create_meeting(room_name)
            serializer.save()
    except AdmissionError as exc:
        return Response({"detail": exc.detail, "status": exc.code}, status=exc.status_code)
    data = serializer.data
    data["host_key"] = host_key
    return Response(data, status=status.HTTP_201_CREATED)


def _json_error(detail: str, status_code: int):
    return JsonResponse({"detail": detail}, status=status_code)


@csrf_exempt
def slides_collection(request):
    """GET the room's active deck, or POST a PDF or PowerPoint to start sharing it."""
    if request.method == "GET":
        room_name = request.GET.get("roomName") or request.GET.get("room_name") or ""
        try:
            deck = current_deck(room_name)
        except ValueError as exc:
            return _json_error(str(exc), 400)
        return JsonResponse({"deck": deck_payload(deck) if deck else None})
    if request.method == "POST":
        return _slides_upload(request)
    return HttpResponse(status=405)


def _slides_upload(request):
    upload = request.FILES.get("file")
    if upload is None:
        return _json_error("Choose a PDF or PowerPoint file", 400)
    if upload.size and upload.size > MAX_PDF_BYTES:
        return _json_error("Slides must be 25 MB or smaller", 413)
    try:
        deck = save_deck(
            room_name=request.POST.get("room_name") or "",
            owner_identity=request.POST.get("owner_identity") or "",
            owner_name=request.POST.get("owner_name") or "",
            original_name=upload.name or "slides.pdf",
            data=upload.read(),
        )
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return JsonResponse({"deck": deck_payload(deck), "notes": notes_payload(deck)}, status=201)


@csrf_exempt
def slides_page(request, deck_id):
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return _json_error("Invalid JSON", 400)
    try:
        page = int(body.get("page"))
    except (TypeError, ValueError):
        return _json_error("Missing page", 400)
    try:
        deck = set_page(
            deck_id=deck_id,
            owner_identity=str(body.get("owner_identity") or ""),
            page=page,
        )
    except SharedSlides.DoesNotExist:
        return _json_error("Slides not found", 404)
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return JsonResponse({"deck": deck_payload(deck)})


@csrf_exempt
def slides_stop(request, deck_id):
    if request.method != "POST":
        return HttpResponse(status=405)
    try:
        body = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return _json_error("Invalid JSON", 400)
    try:
        stop_deck(deck_id=deck_id, owner_identity=str(body.get("owner_identity") or ""))
    except SharedSlides.DoesNotExist:
        return _json_error("Slides not found", 404)
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return JsonResponse({"ok": True})


@csrf_exempt
def slides_notes(request, deck_id):
    if request.method != "GET":
        return HttpResponse(status=405)
    try:
        notes = speaker_notes_for(
            deck_id=deck_id,
            owner_identity=request.GET.get("owner_identity") or "",
        )
    except SharedSlides.DoesNotExist:
        return _json_error("Slides not found", 404)
    except PermissionError as exc:
        return _json_error(str(exc), 403)
    except ValueError as exc:
        return _json_error(str(exc), 400)
    return JsonResponse({"notes": notes})


@csrf_exempt
def slides_file(request, deck_id):
    if request.method != "GET":
        return HttpResponse(status=405)
    try:
        deck = SharedSlides.objects.get(id=deck_id, active=True, page_count__gt=0)
    except SharedSlides.DoesNotExist:
        return _json_error("Slides not found", 404)
    if not deck.file:
        return _json_error("Slides not found", 404)
    response = FileResponse(deck.file.open("rb"), content_type=file_content_type(deck))
    response["Cache-Control"] = "private, max-age=3600"
    return response
