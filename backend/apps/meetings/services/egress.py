"""Room recordings through LiveKit StartEgress.

https://docs.livekit.io/transport/media/ingress-egress/egress/
https://docs.livekit.io/reference/other/egress/api/
https://docs.livekit.io/transport/media/ingress-egress/egress/outputs/

A meeting recording is one TemplateSource (the speaker layout served by Egress)
and one MP4 file. Storage is a request-level StorageConfig, which applies to
every output; the Egress server config is only a fallback. Workers share Redis
with LiveKit and pick up jobs themselves, so more recordings means more Egress
containers, not more work inside Django.

LiveKit's ListEgress omits finished jobs. egress_ended webhooks are what make
the file catalog durable.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from django.conf import settings
from django.db import IntegrityError, transaction
from livekit.api import (
    AudioCodec,
    EncodedFileType,
    EncodingOptions,
    EgressInfo,
    EgressStatus,
    FileOutput,
    ListEgressRequest,
    LiveKitAPI,
    Output,
    S3Upload,
    StartEgressRequest,
    StopEgressRequest,
    VideoCodec,
    StorageConfig,
    TemplateSource,
    TokenVerifier,
    TwirpError,
    WebhookReceiver,
)

from ..models import Meeting, Recording

logger = logging.getLogger(__name__)

# Same shape as admission.ROOM_NAME_RE. A slash in the room name would let an
# object key climb out of that room's prefix.
_ROOM_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_FILE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]*\.mp4$")

# Passed to the template as the layout query param (EgressHelper.getLayout).
# custom_base_url is the meeting stage. audio_only stays false: with a layout
# or custom base URL set, LiveKit runs the video pipeline, and a room with no
# camera still needs a picture.
RECORDING_LAYOUT = "speaker"
# {room_name} and {time} are filled in by Egress. One folder per room.
RECORDING_FILEPATH = "{room_name}/{time}.mp4"
# Compose mounts the host recordings folder here. The path is inside Egress,
# not on the Django machine.
RECORDING_EGRESS_DIR = "/out"

OPEN_STATUSES = (
    Recording.Status.STARTING,
    Recording.Status.ACTIVE,
    Recording.Status.ENDING,
)

# Chrome has to launch before Egress will accept Stop. Keep asking until the
# pipeline exists so a click on Stop still writes the MP4.
_PIPELINE_NOT_READY = "stop called before pipeline could start"
# The recorder page has to paint and print START_RECORDING before StopEgress
# writes a file. A cold next dev compile of /egress took 33s, and Chrome on
# this host sits near one full CPU, so keep waiting well past that.
_STOP_READY_TIMEOUT_SECONDS = 75
_STOP_READY_POLL_SECONDS = 1
# Egress rewrites the MP4 when it stops (the moov index). Serving a file
# before that rewrite makes players jump from the start to the end, or play
# only the first part of a long recording.
_LOCAL_FILE_TIMEOUT_SECONDS = 60
_LOCAL_FILE_POLL_SECONDS = 0.4
_LOCAL_FILE_STABLE_SECONDS = 1.5

_STATUS_BY_VALUE = {
    EgressStatus.EGRESS_STARTING: Recording.Status.STARTING,
    EgressStatus.EGRESS_ACTIVE: Recording.Status.ACTIVE,
    EgressStatus.EGRESS_ENDING: Recording.Status.ENDING,
    EgressStatus.EGRESS_COMPLETE: Recording.Status.COMPLETE,
    EgressStatus.EGRESS_FAILED: Recording.Status.FAILED,
    EgressStatus.EGRESS_ABORTED: Recording.Status.ABORTED,
    EgressStatus.EGRESS_LIMIT_REACHED: Recording.Status.LIMIT_REACHED,
}

EGRESS_WEBHOOK_EVENTS = frozenset({"egress_started", "egress_updated", "egress_ended"})


class RecordingStorageError(Exception):
    def __init__(self, detail: str):
        super().__init__(detail)
        self.detail = detail


class WebhookRejected(Exception):
    pass


def recording_object_key_ok(room_name: str, key: str) -> bool:
    """True only for this room's own MP4, with no parent-directory segments."""
    if not _ROOM_RE.fullmatch(room_name or ""):
        return False
    if not key or "\\" in key or key.startswith("/") or ".." in key:
        return False
    prefix = f"{room_name}/"
    if not key.startswith(prefix):
        return False
    name = key[len(prefix) :]
    if "/" in name:
        return False
    return _FILE_RE.fullmatch(name) is not None


def _recording_encoding() -> EncodingOptions:
    """540p at 15 fps, AAC at 48 kHz.

    720p30 and a 44.1 kHz resample use the whole machine. The pipeline stays
    paused, Pulse drops audio, and the MP4 stores each hole as one long
    sample. Players skip that hole, so a long meeting comes out a few seconds
    long and the speech is unclear. 48 kHz is the rate Pulse and Chrome
    already use, so this path does not resample.
    """
    return EncodingOptions(
        width=960,
        height=540,
        depth=24,
        framerate=15,
        audio_codec=AudioCodec.AAC,
        audio_bitrate=64,
        audio_frequency=48000,
        video_codec=VideoCodec.H264_BASELINE,
        video_bitrate=1000,
        key_frame_interval=2,
    )


def build_start_request(room_name: str) -> StartEgressRequest:
    """One speaker composite.

    StartEgress replaces StartRoomCompositeEgress. Audio-only is left off:
    setting layout while audio_only is true would still run the video pipeline.
    A local directory keeps the MP4 on disk. Otherwise the request uploads it.
    """
    request = StartEgressRequest(
        room_name=room_name,
        template=TemplateSource(
            layout=RECORDING_LAYOUT,
            audio_only=False,
            custom_base_url=settings.RECORDING_TEMPLATE_URL,
        ),
        advanced=_recording_encoding(),
        outputs=[
            Output(
                file=FileOutput(
                    file_type=EncodedFileType.MP4,
                    filepath=_recording_filepath(),
                )
            )
        ],
    )
    if not _local_recording_dir():
        if not settings.S3_BUCKET:
            raise RuntimeError("Recording is not configured (missing S3/MinIO settings).")
        request.storage.CopyFrom(StorageConfig(s3=_s3_upload()))
    return request


def _recording_filepath() -> str:
    if _local_recording_dir():
        return f"{RECORDING_EGRESS_DIR}/{RECORDING_FILEPATH}"
    return RECORDING_FILEPATH


def _local_recording_dir() -> Path | None:
    raw = (settings.RECORDING_LOCAL_DIR or "").strip()
    if not raw:
        return None
    return Path(raw)


def _s3_upload() -> S3Upload:
    # force_path_style is required for MinIO and other non-AWS endpoints.
    # AWS regional endpoints leave it off and use the region instead.
    return S3Upload(
        access_key=settings.S3_KEY_ID,
        secret=settings.S3_KEY_SECRET,
        region=settings.S3_REGION,
        endpoint=settings.S3_ENDPOINT,
        bucket=settings.S3_BUCKET,
        force_path_style=bool(settings.S3_ENDPOINT),
    )


def _http_api_url() -> str:
    """HTTP base URL for LiveKitAPI (Room/Egress). Not the browser WebSocket URL."""
    url = settings.LIVEKIT_API_URL or settings.LIVEKIT_URL
    if url.startswith("ws://"):
        return "http://" + url[len("ws://") :]
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    return url


def _client() -> LiveKitAPI:
    return LiveKitAPI(
        url=_http_api_url(),
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )


def start_room_recording(room_name: str) -> Recording:
    meeting = Meeting.objects.get(room_name=room_name)
    if Recording.objects.filter(meeting=meeting, status__in=OPEN_STATUSES).exists():
        raise ValueError("Meeting is already being recorded")

    active = _run(_list_active, room_name)
    if active:
        for info in active:
            record_egress_info(info)
        raise ValueError("Meeting is already being recorded")

    info = _run(_start_egress, build_start_request(room_name))
    try:
        return record_egress_info(info)
    except IntegrityError:
        _run(_stop_egress, info.egress_id)
        raise ValueError("Meeting is already being recorded") from None


def stop_room_recording(room_name: str) -> list[Recording]:
    active = _run(_list_active, room_name)
    if not active:
        closed = _close_open_recordings(room_name, "Recording already ended")
        if closed:
            return closed
        raise LookupError("No active recording found")

    saved: list[Recording] = []
    for item in active:
        try:
            info = _stop_when_ready(item.egress_id)
        except RuntimeError as exc:
            # LiveKit rejects stop once the job has already failed or finished.
            if not _already_finished(exc):
                raise
            info = item
            info.status = EgressStatus.EGRESS_FAILED
            if not info.error or "cannot be stopped" in info.error:
                info.error = "Recording failed before it could be stopped"
        row = _attach_local_file(room_name, record_egress_info(info))
        if row is not None:
            saved.append(row)
    return saved


def _stop_when_ready(egress_id: str) -> EgressInfo:
    """Stop only after Chrome has started the pipeline.

    StopEgress while the job is still STARTING aborts it with
    "Stop called before pipeline could start" and writes no file.
    """
    deadline = time.monotonic() + _STOP_READY_TIMEOUT_SECONDS
    while True:
        current = _run(_get_egress, egress_id)
        if current is None:
            raise LookupError("No active recording found")
        if current.status in (EgressStatus.EGRESS_FAILED, EgressStatus.EGRESS_ABORTED):
            # The job already ended. StopEgress would be rejected, and raising
            # here turns that into a 500 on the Stop button.
            current.error = _egress_failure_message(current.error or "Recording failed")
            return current
        if current.status in (EgressStatus.EGRESS_ACTIVE, EgressStatus.EGRESS_ENDING):
            try:
                return _run(_stop_egress, egress_id)
            except RuntimeError as exc:
                if not _pipeline_not_ready(exc) or time.monotonic() >= deadline:
                    raise
        elif time.monotonic() >= deadline:
            logger.warning("Recording %s was stopped before it could start", egress_id)
            try:
                return _run(_stop_egress, egress_id)
            except RuntimeError:
                current.status = EgressStatus.EGRESS_FAILED
                current.error = "Recording did not start in time"
                return current
        else:
            logger.info("Waiting for recording %s to start before stopping it", egress_id)
        time.sleep(_STOP_READY_POLL_SECONDS)


def _pipeline_not_ready(exc: BaseException) -> bool:
    return _PIPELINE_NOT_READY in str(exc).lower()


def _already_finished(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "cannot be stopped" in text or "failed_precondition" in text


def _attach_local_file(room_name: str, row: Recording | None) -> Recording | None:
    """Store the MP4 path once Egress has flushed it under RECORDING_LOCAL_DIR."""
    if row is None or row.object_key or row.status in (
        Recording.Status.FAILED,
        Recording.Status.ABORTED,
    ):
        return row
    cutoff = None
    if row.created_at is not None:
        cutoff = row.created_at - timedelta(seconds=5)
    path = _wait_for_local_mp4(room_name, cutoff, row.size_bytes or None)
    if path is None:
        return row
    key = f"{room_name}/{path.name}"
    if not recording_object_key_ok(room_name, key):
        return row
    row.object_key = key
    row.size_bytes = path.stat().st_size
    if row.status in OPEN_STATUSES:
        row.status = Recording.Status.COMPLETE
        if row.ended_at is None:
            row.ended_at = datetime.now(timezone.utc)
    row.save(update_fields=["object_key", "size_bytes", "status", "ended_at"])
    return row


def _mp4_has_moov(path: Path) -> bool:
    """True once the MP4 index is on disk. Without it, players skip to the end."""
    try:
        size = path.stat().st_size
    except OSError:
        return False
    if size < 32:
        return False
    try:
        with path.open("rb") as handle:
            if b"moov" in handle.read(256 * 1024):
                return True
            if size > 256 * 1024:
                handle.seek(max(0, size - 2 * 1024 * 1024))
                return b"moov" in handle.read()
    except OSError:
        return False
    return False


def _wait_for_local_mp4(
    room_name: str,
    not_before: datetime | None,
    expected_size: int | None = None,
) -> Path | None:
    root = _local_recording_dir()
    if root is None:
        return None
    folder = root / room_name
    deadline = time.monotonic() + _LOCAL_FILE_TIMEOUT_SECONDS
    previous_size = -1
    stable_since: float | None = None
    while time.monotonic() < deadline:
        newest = _newest_mp4(folder, not_before)
        if newest is not None:
            size = newest.stat().st_size
            ready = size > 0 and size == previous_size and _mp4_has_moov(newest)
            if expected_size and size < expected_size:
                ready = False
            if ready:
                if stable_since is None:
                    stable_since = time.monotonic()
                elif time.monotonic() - stable_since >= _LOCAL_FILE_STABLE_SECONDS:
                    return newest
            else:
                stable_since = None
                previous_size = size
        time.sleep(_LOCAL_FILE_POLL_SECONDS)
    newest = _newest_mp4(folder, not_before)
    if newest is None or newest.stat().st_size <= 0 or not _mp4_has_moov(newest):
        return None
    # Egress already reported a larger file. The copy on disk is still the
    # first part, and handing it out is the short download.
    if expected_size and newest.stat().st_size < expected_size:
        return None
    return newest


def _newest_mp4(folder: Path, not_before: datetime | None) -> Path | None:
    if not folder.is_dir():
        return None
    files = [path for path in folder.glob("*.mp4") if path.is_file() and _file_is_new(path, not_before)]
    if not files:
        return None
    return max(files, key=lambda path: path.stat().st_mtime)


def _file_is_new(path: Path, not_before: datetime | None) -> bool:
    if not_before is None:
        return True
    modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return modified >= not_before


def _close_open_recordings(room_name: str, error: str) -> list[Recording]:
    now = datetime.now(timezone.utc)
    rows = list(
        Recording.objects.filter(meeting__room_name=room_name, status__in=OPEN_STATUSES)
    )
    for row in rows:
        row.status = Recording.Status.ABORTED
        row.error = error
        row.ended_at = now
        row.save(update_fields=["status", "error", "ended_at"])
    return rows


def list_room_recordings(room_name: str) -> list[Recording]:
    return list(
        Recording.objects.filter(meeting__room_name=room_name).order_by("-created_at", "-id")
    )


def record_egress_info(info: EgressInfo) -> Recording | None:
    """Insert or update the row for an EgressInfo from start, stop, or a webhook."""
    room_name = info.room_name
    if not info.egress_id or not room_name:
        return None
    try:
        meeting = Meeting.objects.get(room_name=room_name)
    except Meeting.DoesNotExist:
        logger.info("Ignoring egress %s for unknown room %s", info.egress_id, room_name)
        return None

    status = _STATUS_BY_VALUE.get(info.status, Recording.Status.FAILED)
    key = _object_key(info, room_name)
    size = _size_bytes(info, room_name)
    fields = {
        "status": status,
        "error": _egress_failure_message(info.error) if info.error else "",
    }
    started = _unix_time(info.started_at)
    ended = _unix_time(info.ended_at)
    if started is not None:
        fields["started_at"] = started
    if ended is not None:
        fields["ended_at"] = ended
    if key:
        fields["object_key"] = key
    if size is not None:
        fields["size_bytes"] = size

    existing = Recording.objects.filter(egress_id=info.egress_id).first()
    if existing is None:
        try:
            with transaction.atomic():
                return Recording.objects.create(meeting=meeting, egress_id=info.egress_id, **fields)
        except IntegrityError:
            existing = Recording.objects.filter(egress_id=info.egress_id).first()
            if existing is None:
                raise
    for name, value in fields.items():
        setattr(existing, name, value)
    existing.save()
    return existing


def apply_egress_webhook(body: str, auth_header: str) -> None:
    """Validate a LiveKit webhook and store egress_started / updated / ended."""
    token = auth_header or ""
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    receiver = WebhookReceiver(
        TokenVerifier(
            api_key=settings.LIVEKIT_API_KEY,
            api_secret=settings.LIVEKIT_API_SECRET,
        )
    )
    try:
        event = receiver.receive(body, token)
    except Exception as exc:
        raise WebhookRejected("Invalid webhook signature") from exc
    if event.event not in EGRESS_WEBHOOK_EVENTS:
        return
    if not event.egress_info.egress_id:
        return
    try:
        record_egress_info(event.egress_info)
    except IntegrityError:
        logger.info("Egress %s did not replace the open recording", event.egress_info.egress_id)


def recording_download(room_name: str, key: str) -> dict:
    """Local file when recordings stay on disk, otherwise a presigned URL or stream."""
    if not recording_object_key_ok(room_name, key):
        raise RecordingStorageError("Recording not found")
    if not Recording.objects.filter(meeting__room_name=room_name, object_key=key).exists():
        raise RecordingStorageError("Recording not found")
    local = _local_recording_file(key)
    if local is not None:
        return {"path": local, "content_type": "video/mp4", "length": local.stat().st_size}
    public = (settings.S3_PUBLIC_ENDPOINT or "").strip()
    if public:
        return {"url": _presigned_url(public, key)}
    body, content_type, length = _open_object(key)
    return {"body": body, "content_type": content_type, "length": length}


def _presigned_url(endpoint: str, key: str) -> str:
    client = _s3_client(endpoint)
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.S3_BUCKET,
            "Key": key,
            "ResponseContentDisposition": f'attachment; filename="{key.rsplit("/", 1)[-1]}"',
        },
        ExpiresIn=settings.RECORDING_URL_TTL_SECONDS,
    )


def _open_object(key: str):
    if not settings.S3_ENDPOINT and not settings.S3_BUCKET:
        raise RecordingStorageError("Recording is not configured (missing S3/MinIO settings).")
    client = _s3_client(settings.S3_ENDPOINT)
    try:
        obj = client.get_object(Bucket=settings.S3_BUCKET, Key=key)
    except Exception as exc:
        logger.warning("Recording object %s could not be opened: %s", key, exc)
        raise RecordingStorageError("Recording not found") from exc
    return obj["Body"], obj.get("ContentType") or "video/mp4", obj.get("ContentLength")


def _s3_client(endpoint: str):
    import boto3
    from botocore.config import Config

    addressing = "path" if endpoint else "virtual"
    return boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=settings.S3_KEY_ID,
        aws_secret_access_key=settings.S3_KEY_SECRET,
        region_name=settings.S3_REGION or "us-east-1",
        config=Config(signature_version="s3v4", s3={"addressing_style": addressing}),
    )


def _object_key(info: EgressInfo, room_name: str) -> str:
    for result in info.file_results:
        key = _recording_key(result.filename or "", room_name)
        if key:
            return key
    return ""


def _recording_key(filename: str, room_name: str) -> str:
    """Room-relative key, whether Egress reports a bucket key or /out/<room>/<file>."""
    parts = [part for part in filename.replace("\\", "/").split("/") if part and part != ".."]
    if len(parts) >= 2:
        key = f"{parts[-2]}/{parts[-1]}"
    else:
        key = filename.lstrip("/")
    if recording_object_key_ok(room_name, key):
        return key
    return ""


def _local_recording_file(key: str) -> Path | None:
    root = _local_recording_dir()
    if root is None:
        return None
    path = (root / key).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    if path.is_file():
        return path
    return None


def _size_bytes(info: EgressInfo, room_name: str) -> int | None:
    for result in info.file_results:
        if result.size and _recording_key(result.filename or "", room_name):
            return int(result.size)
    return None


def _status_in_progress(status: int) -> bool:
    return status in (
        EgressStatus.EGRESS_STARTING,
        EgressStatus.EGRESS_ACTIVE,
        EgressStatus.EGRESS_ENDING,
    )


def _unix_time(value: int) -> datetime | None:
    """Egress timestamps are unix nanoseconds. Accept milli and second values too."""
    if not value:
        return None
    if value > 10**17:
        seconds = value / 1_000_000_000
    elif value > 10**14:
        seconds = value / 1_000_000
    elif value > 10**11:
        seconds = value / 1_000
    else:
        seconds = float(value)
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _egress_failure_message(message: str) -> str:
    """Turn LiveKit's empty-worker timeout into the condition that caused it.

    StartEgress waits about 21 seconds, then returns "no response from
    servers" when no Egress worker accepts the job. That worker shares Redis
    with LiveKit and declines a template recording it cannot afford.
    https://docs.livekit.io/transport/self-hosting/egress/
    """
    text = message or "LiveKit egress request failed"
    lowered = text.lower()
    if "no response from servers" in lowered:
        return (
            "The recording service did not accept the job. "
            "Egress has to be running on the same Redis as LiveKit."
        )
    if "websocket url timeout" in lowered or "failed to launch chrome" in lowered:
        return (
            "Chrome in the recording service did not open the recorder page. "
            "Recreate the egress container and keep the app running on port 3000."
        )
    if "start signal not received" in lowered or "stop called before pipeline could start" in lowered:
        return (
            "The recorder page did not finish opening. "
            "Start the recording again once the meeting is on screen."
        )
    return text


def _run(func, *args):
    from asgiref.sync import async_to_sync

    try:
        return async_to_sync(func)(*args)
    except TwirpError as exc:
        raise RuntimeError(_egress_failure_message(exc.message)) from exc


async def _get_egress(egress_id: str) -> EgressInfo | None:
    lk = _client()
    try:
        existing = await lk.egress.list_egress(ListEgressRequest(egress_id=egress_id))
        return existing.items[0] if existing.items else None
    finally:
        await lk.aclose()


async def _list_active(room_name: str) -> list[EgressInfo]:
    lk = _client()
    try:
        existing = await lk.egress.list_egress(ListEgressRequest(room_name=room_name, active=True))
        return [item for item in existing.items if _status_in_progress(item.status)]
    finally:
        await lk.aclose()


async def _start_egress(request: StartEgressRequest) -> EgressInfo:
    lk = _client()
    try:
        return await lk.egress.start_egress(request)
    finally:
        await lk.aclose()


async def _stop_egress(egress_id: str) -> EgressInfo:
    lk = _client()
    try:
        return await lk.egress.stop_egress(StopEgressRequest(egress_id=egress_id))
    finally:
        await lk.aclose()
