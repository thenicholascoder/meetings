"""Store one deck per room and the page the presenter is on.

PDF files are stored as uploaded. PowerPoint files are converted to PDF
with text under 30pt left as stored. From 30pt through 40pt a run keeps a
similar installed face, or Anton. Above 40pt it keeps a similar installed
face, or Bodoni, and is drawn 10pt smaller. LibreOffice reads
faces embedded in the file; any of those that are real font files are also
installed for that conversion. Speaker notes are kept beside the deck, one
entry per page, and are not sent to other people.
"""

from __future__ import annotations

import logging
import os
import posixpath
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from django.core.files.base import ContentFile
from django.db import connection, transaction
from django.utils import timezone
from lxml import etree
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.meetings.models import Meeting, MeetingMember, SharedSlides
from apps.meetings.services.room_control import set_screen_share_locked
from apps.meetings.services.slide_fonts import extract_embedded_fonts, font_suffix, shrink_large_fonts

logger = logging.getLogger(__name__)

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_NOTE_CHARS = 4000
CONVERT_TIMEOUT_SECONDS = 90
# A conversion that never finishes must not keep Share screen locked.
_CONVERSION_DEADLINE = timedelta(seconds=120)
_ERROR_VISIBLE = timedelta(seconds=90)

# Raster pictures are downscaled so the PDF is smaller to store and to open.
# Text stays vectors. Runs above 40pt are drawn 10pt smaller than stored.
_PDF_IMAGE_QUALITY = "85"
_PDF_IMAGE_DPI = "150"

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

# One LibreOffice profile per process. Building a profile is most of a cold
# conversion, and a profile cannot be shared by two conversions at once.
_SOFFICE_LOCK = threading.Lock()
# A second soffice with the same profile hands the file to the process that
# is already running. That is the difference between a few seconds and a
# fresh startup of more than ten.
_WARM_LOCK = threading.Lock()
_warm_proc: subprocess.Popen | None = None
_warm_disabled = False
_warmup_pptx_bytes: bytes | None = None


def _require_presenter(room_name: str, identity: str) -> None:
    """Meetings without a membership row (slide tests, a bare room) stay open.

    Inside a meeting, only the host and a co-host may start a deck.
    """
    meeting = Meeting.objects.filter(room_name=room_name).first()
    if meeting is None:
        return
    allowed = MeetingMember.objects.filter(
        meeting=meeting,
        identity=identity,
        admitted=True,
        role__in=[MeetingMember.Role.HOST, MeetingMember.Role.COHOST],
    ).exists()
    if not allowed:
        raise PermissionError("Only the host or a co-host can share slides.")


def save_deck(
    *,
    room_name: str,
    owner_identity: str,
    owner_name: str,
    original_name: str,
    data: bytes,
) -> SharedSlides:
    room_name = _room_name(room_name)
    owner_identity = _identity(owner_identity)
    owner_name = owner_name.strip()[:128]
    original_name = _file_name(original_name)
    _require_presenter(room_name, owner_identity)
    if _is_pptx_upload(original_name, data):
        return _begin_pptx(
            room_name=room_name,
            owner_identity=owner_identity,
            owner_name=owner_name,
            original_name=original_name,
            data=data,
        )
    # Lock before the file is stored. A joiner who enters during the save
    # must not be able to publish a screen share.
    set_screen_share_locked(room_name, True)
    try:
        pdf_data, notes = slides_from_upload(original_name, data)
        return _store_ready_deck(
            room_name=room_name,
            owner_identity=owner_identity,
            owner_name=owner_name,
            original_name=original_name,
            pdf_data=pdf_data,
            notes=notes,
        )
    except Exception:
        if not _ready_decks(room_name).exists():
            set_screen_share_locked(room_name, False)
        raise


def _is_pptx_upload(original_name: str, data: bytes) -> bool:
    if data.startswith(b"%PDF-"):
        return False
    if original_name.lower().endswith(".pptx") or looks_like_pptx(data):
        if not looks_like_pptx(data):
            raise ValueError("Could not read that PowerPoint file")
        return True
    return False


def _begin_pptx(
    *,
    room_name: str,
    owner_identity: str,
    owner_name: str,
    original_name: str,
    data: bytes,
) -> SharedSlides:
    """Store the upload and convert it off the request.

    PowerPoint conversion can take longer than a proxy will wait. The request
    returns as soon as the file is accepted. The person sharing publishes a
    LiveKit data message when the PDF is ready.
    """
    notes = extract_speaker_notes(data)
    deck_id = uuid.uuid4()
    deck = SharedSlides(
        id=deck_id,
        room_name=room_name,
        owner_identity=owner_identity,
        owner_name=owner_name,
        original_name=original_name,
        page=1,
        page_count=0,
        speaker_notes=[],
        active=True,
    )
    deck.file.save(f"{deck_id}.part", ContentFile(b""), save=True)
    _schedule(
        _finish_pptx,
        (str(deck_id), data, notes, room_name),
    )
    deck.refresh_from_db()
    return deck


def _finish_pptx(deck_id: str, data: bytes, notes: list[str], room_name: str) -> None:
    _wait_for_row(deck_id)
    # The room lock talks to LiveKit and does not need to finish before
    # conversion starts. People already in the room are held by the preparing
    # message; this closes screen share for anyone who joins mid-conversion.
    if connection.in_atomic_block:
        set_screen_share_locked(room_name, True)
    else:
        threading.Thread(
            target=set_screen_share_locked,
            args=(room_name, True),
            daemon=True,
        ).start()
    try:
        pdf_data = pptx_to_pdf(data)
        page_count = page_count_of(pdf_data)
        if notes:
            notes = align_notes(notes, page_count)
        with transaction.atomic():
            deck = SharedSlides.objects.select_for_update().get(id=deck_id)
            if not deck.active:
                _unlock_if_idle(room_name)
                return
            if deck.file:
                deck.file.delete(save=False)
            deck.page_count = page_count
            deck.speaker_notes = notes
            deck.file.save(f"{deck_id}.pdf", ContentFile(pdf_data), save=False)
            deck.save()
            for old in SharedSlides.objects.filter(room_name=room_name, active=True).exclude(id=deck.id):
                _retire(old)
    except Exception as exc:
        message = str(exc) if isinstance(exc, ValueError) else "Could not convert that PowerPoint file"
        logger.warning("PowerPoint conversion failed for %s: %s", room_name, message)
        _mark_conversion_failed(deck_id, room_name, message)
        if connection.in_atomic_block:
            raise


def _store_ready_deck(
    *,
    room_name: str,
    owner_identity: str,
    owner_name: str,
    original_name: str,
    pdf_data: bytes,
    notes: list[str],
) -> SharedSlides:
    page_count = page_count_of(pdf_data)
    if notes:
        notes = align_notes(notes, page_count)
    deck_id = uuid.uuid4()
    with transaction.atomic():
        deck = SharedSlides(
            id=deck_id,
            room_name=room_name,
            owner_identity=owner_identity,
            owner_name=owner_name,
            original_name=original_name,
            page=1,
            page_count=page_count,
            speaker_notes=notes,
            active=True,
        )
        deck.file.save(f"{deck_id}.pdf", ContentFile(pdf_data), save=True)
        for old in SharedSlides.objects.filter(room_name=room_name, active=True).exclude(id=deck.id):
            _retire(old)
    return deck


def _schedule(target, args: tuple) -> None:
    """Run now inside a test transaction; otherwise after this request returns."""
    if connection.in_atomic_block:
        target(*args)
        return

    def runner() -> None:
        from django.db import close_old_connections

        close_old_connections()
        try:
            target(*args)
        finally:
            close_old_connections()

    threading.Thread(target=runner, daemon=True).start()


def _wait_for_row(deck_id: str) -> None:
    if connection.in_atomic_block:
        return
    for _ in range(20):
        if SharedSlides.objects.filter(id=deck_id).exists():
            return
        time.sleep(0.05)


def _mark_conversion_failed(deck_id: str, room_name: str, message: str) -> None:
    updated = SharedSlides.objects.filter(id=deck_id, active=True, page_count=0).update(
        active=False,
        speaker_notes=[message[:500]],
        updated_at=timezone.now(),
    )
    if not updated:
        _unlock_if_idle(room_name)
        return
    failed = SharedSlides.objects.filter(id=deck_id).first()
    if failed and failed.file:
        failed.file.delete(save=False)
    _unlock_if_idle(room_name)


def _unlock_if_idle(room_name: str) -> None:
    if not _ready_decks(room_name).exists() and not _converting(room_name).exists():
        set_screen_share_locked(room_name, False)


def _ready_decks(room_name: str):
    return SharedSlides.objects.filter(room_name=room_name, active=True, page_count__gt=0)


def _converting(room_name: str):
    return SharedSlides.objects.filter(room_name=room_name, active=True, page_count=0)


def current_deck(room_name: str) -> SharedSlides | None:
    room_name = _room_name(room_name)
    return _ready_decks(room_name).order_by("-created_at").first()


def room_slide_state(room_name: str) -> dict:
    """Ready deck, in-progress PowerPoint conversion, or a recent failure."""
    room_name = _room_name(room_name)
    _expire_stale_conversions(room_name)
    ready = current_deck(room_name)
    converting = _converting(room_name).order_by("-created_at").first()
    error = None
    if ready is None and converting is None:
        failed = (
            SharedSlides.objects.filter(room_name=room_name, active=False, page_count=0)
            .order_by("-updated_at")
            .first()
        )
        detail = _conversion_error(failed)
        if failed and detail and failed.updated_at >= timezone.now() - _ERROR_VISIBLE:
            error = {
                "detail": detail,
                "deckId": str(failed.id),
                "ownerIdentity": failed.owner_identity,
            }
    return {"deck": ready, "converting": converting, "error": error}


def _expire_stale_conversions(room_name: str) -> None:
    cutoff = timezone.now() - _CONVERSION_DEADLINE
    stale = list(_converting(room_name).filter(created_at__lt=cutoff))
    if not stale:
        return
    for deck in stale:
        _mark_conversion_failed(str(deck.id), room_name, "Converting that PowerPoint file took too long")


def _conversion_error(deck: SharedSlides | None) -> str:
    if deck is None or not isinstance(deck.speaker_notes, list) or not deck.speaker_notes:
        return ""
    detail = deck.speaker_notes[0]
    return detail.strip() if isinstance(detail, str) else ""


def set_page(*, deck_id, owner_identity: str, page: int) -> SharedSlides:
    deck = SharedSlides.objects.get(id=deck_id, active=True)
    if deck.owner_identity != _identity(owner_identity):
        raise PermissionError("Only the person sharing can change slides")
    if page < 1 or page > deck.page_count:
        raise ValueError("That slide does not exist")
    deck.page = page
    deck.save(update_fields=["page", "updated_at"])
    return deck


def stop_deck(*, deck_id, owner_identity: str) -> None:
    deck = SharedSlides.objects.get(id=deck_id, active=True)
    if deck.owner_identity != _identity(owner_identity):
        raise PermissionError("Only the person sharing can stop slides")
    _retire(deck)
    set_screen_share_locked(deck.room_name, False)


def speaker_notes_for(*, deck_id, owner_identity: str) -> list[str]:
    deck = SharedSlides.objects.get(id=deck_id, active=True)
    if deck.owner_identity != _identity(owner_identity):
        raise PermissionError("Only the person sharing can view speaker notes")
    return notes_payload(deck)


def page_count_of(data: bytes) -> int:
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Slides must be 25 MB or smaller")
    if not data.startswith(b"%PDF-"):
        raise ValueError("Choose a PDF or PowerPoint file")
    try:
        reader = PdfReader(BytesIO(data))
    except PdfReadError as exc:
        raise ValueError("Could not read that PDF") from exc
    if reader.is_encrypted and reader.decrypt("") == 0:
        raise ValueError("This PDF is password-protected")
    try:
        count = len(reader.pages)
    except PdfReadError as exc:
        raise ValueError("Could not read that PDF") from exc
    if count < 1:
        raise ValueError("This PDF has no pages")
    if count > MAX_PDF_PAGES:
        raise ValueError(f"PDF must be {MAX_PDF_PAGES} pages or fewer")
    return count


def slides_from_upload(original_name: str, data: bytes) -> tuple[bytes, list[str]]:
    """Return PDF bytes and one speaker-note string per PowerPoint slide."""
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Slides must be 25 MB or smaller")
    if data.startswith(b"%PDF-"):
        return data, []
    is_pptx = looks_like_pptx(data)
    if original_name.lower().endswith(".pptx") or is_pptx:
        if not is_pptx:
            raise ValueError("Could not read that PowerPoint file")
        notes = extract_speaker_notes(data)
        return pptx_to_pdf(data), notes
    raise ValueError("Choose a PDF or PowerPoint file")


def looks_like_pptx(data: bytes) -> bool:
    if not data.startswith(b"PK\x03\x04"):
        return False
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            return "ppt/presentation.xml" in archive.namelist()
    except zipfile.BadZipFile:
        return False


def extract_speaker_notes(data: bytes) -> list[str]:
    """One note string per slide, in slide order, without loading pictures."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = set(archive.namelist())
            presentation = _zip_xml(archive, names, "ppt/presentation.xml")
            pres_rels = _zip_xml(archive, names, "ppt/_rels/presentation.xml.rels")
            if presentation is None or pres_rels is None:
                raise ValueError("Could not read that PowerPoint file")
            slide_ids = presentation.findall(f".//{{{P_NS}}}sldId")
            if len(slide_ids) < 1:
                raise ValueError("This PowerPoint file has no slides")
            if len(slide_ids) > MAX_PDF_PAGES:
                raise ValueError(f"PowerPoint must be {MAX_PDF_PAGES} slides or fewer")
            rels = _relationship_map(pres_rels)
            notes: list[str] = []
            for slide_id in slide_ids:
                target = rels.get(slide_id.get(f"{{{R_NS}}}id") or "")
                slide_part = _package_target("ppt", target)
                notes.append(_slide_notes(archive, names, slide_part))
    except (zipfile.BadZipFile, etree.XMLSyntaxError, KeyError, OSError) as exc:
        raise ValueError("Could not read that PowerPoint file") from exc
    return notes


# Debian slim installs LibreOffice without the /usr/bin/soffice symlink when
# recommended packages are skipped. The program directory still has the binary.
_SOFFICE_CANDIDATES = (
    "/usr/bin/soffice",
    "/usr/bin/libreoffice",
    "/usr/lib/libreoffice/program/soffice",
)


def _soffice_executable() -> str | None:
    configured = os.environ.get("SOFFICE_PATH", "").strip()
    if configured and _is_executable(configured):
        return configured
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found and _is_executable(found):
            return found
    for candidate in _SOFFICE_CANDIDATES:
        if _is_executable(candidate):
            return candidate
    return None


def _is_executable(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def _converter_url() -> str | None:
    if "PPTX_CONVERTER_URL" in os.environ:
        value = os.environ.get("PPTX_CONVERTER_URL", "").strip().rstrip("/")
        return value or None
    # Local Django has no LibreOffice. The converter container is part of the
    # Docker services started beside a host backend.
    if os.environ.get("DJANGO_DEBUG") == "1":
        return "http://127.0.0.1:3100"
    return None


def pptx_to_pdf(data: bytes) -> bytes:
    """Convert PowerPoint to PDF. Text above 40pt is drawn 10pt smaller."""
    url = _converter_url()
    soffice = _soffice_executable()
    if not soffice and not url:
        raise ValueError("PowerPoint conversion is unavailable")
    # Font parts are unchanged by the size edit, so read them from the upload
    # before rewriting the text XML.
    is_pptx = looks_like_pptx(data)
    embedded = extract_embedded_fonts(data) if is_pptx else []
    if is_pptx:
        data = shrink_large_fonts(data)
    # The converter keeps LibreOffice running. Starting soffice per upload
    # rebuilds a profile and is the slow path, including for embedded fonts.
    # Those faces fall back to a local conversion only when the converter is down.
    if url:
        try:
            return _convert_with_service(url, data)
        except _ConverterUnavailable:
            if not soffice:
                raise ValueError("PowerPoint conversion is unavailable") from None
            logger.warning("PowerPoint converter is unavailable; using local LibreOffice")
        except ValueError:
            if not (embedded and soffice):
                raise
            logger.warning("PowerPoint converter rejected the file; using local LibreOffice")
    if not soffice:
        raise ValueError("PowerPoint conversion is unavailable")
    return _convert_with_soffice(soffice, data, embedded)


class _ConverterUnavailable(Exception):
    pass


def _convert_with_soffice(soffice: str, data: bytes, fonts: list[bytes] | None = None) -> bytes:
    with _SOFFICE_LOCK:
        return _soffice_convert(soffice, data, fonts)


def warm_pptx_converter() -> None:
    """Start LibreOffice before the first upload.

    A cold start is most of a conversion. The converter service keeps one
    process alive, and a local install keeps one listener on this profile.
    """
    url = _converter_url()
    if url:
        try:
            _convert_with_service(url, _warmup_pptx())
            return
        except Exception:
            logger.info("PowerPoint converter warmup did not finish")
    soffice = _soffice_executable()
    if soffice:
        _ensure_warm_soffice(soffice)


def _warmup_pptx() -> bytes:
    global _warmup_pptx_bytes
    if _warmup_pptx_bytes is None:
        from pptx import Presentation

        presentation = Presentation()
        presentation.slides.add_slide(presentation.slide_layouts[6])
        buffer = BytesIO()
        presentation.save(buffer)
        _warmup_pptx_bytes = buffer.getvalue()
    return _warmup_pptx_bytes


def _listener_port() -> int:
    return 21000 + (os.getpid() % 20000)


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _wait_for_port(port: int, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.15)
    return False


def _stop_warm_soffice() -> None:
    global _warm_proc, _warm_disabled
    with _WARM_LOCK:
        proc = _warm_proc
        _warm_proc = None
        _warm_disabled = True
    if proc is None or proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        proc.kill()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _ensure_warm_soffice(soffice: str) -> bool:
    """Keep one LibreOffice process for this profile. True once its socket is open."""
    global _warm_proc
    if _warm_disabled or not _is_executable(soffice):
        return False
    port = _listener_port()
    with _WARM_LOCK:
        alive = _warm_proc is not None and _warm_proc.poll() is None
        if not alive:
            profile = _soffice_profile()
            env = os.environ.copy()
            env["HOME"] = str(profile.parent)
            env["SAL_USE_VCLPLUGIN"] = "svp"
            env["SAL_DISABLE_OPENCL"] = "1"
            try:
                _warm_proc = subprocess.Popen(
                    [
                        soffice,
                        "--headless",
                        "--norestore",
                        "--nologo",
                        "--nofirststartwizard",
                        f"-env:UserInstallation={profile.resolve().as_uri()}",
                        f"--accept=socket,host=127.0.0.1,port={port};urp;StarOffice.ServiceManager",
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                    env=env,
                )
            except OSError:
                _warm_proc = None
                return False
    if _wait_for_port(port, 25):
        return True
    _stop_warm_soffice()
    return False


def _soffice_convert(soffice: str, data: bytes, fonts: list[bytes] | None = None) -> bytes:
    if fonts is None:
        fonts = extract_embedded_fonts(data) if looks_like_pptx(data) else []
    # Embedded faces need their own profile. Everything else reuses the listener.
    warm = _ensure_warm_soffice(soffice) if not fonts else False
    try:
        return _run_soffice(soffice, data, fonts, warm)
    except ValueError:
        if not warm:
            raise
        logger.warning("Warm LibreOffice did not convert the file; starting a new one")
        _stop_warm_soffice()
        return _run_soffice(soffice, data, fonts, False)


def _run_soffice(soffice: str, data: bytes, fonts: list[bytes], warm: bool) -> bytes:
    with tempfile.TemporaryDirectory(prefix="slides-") as tmp:
        root = Path(tmp)
        source = root / "deck.pptx"
        output = root / "out"
        output.mkdir()
        source.write_bytes(data)
        # Embedded faces need a fresh profile so LibreOffice does not keep a
        # font list from an earlier deck. Other conversions reuse one profile.
        if fonts:
            profile = root / "profile"
            profile.mkdir()
        else:
            profile = _soffice_profile()
        command = [
            soffice,
            "--headless",
            "--norestore",
            "--nologo",
            "--nofirststartwizard",
        ]
        # Without this flag, a second soffice forwards the file to the listener
        # instead of starting another office. A one-shot convert still ignores
        # a stale lock.
        if not warm:
            command.append("--nolockcheck")
        command.extend(
            [
                f"-env:UserInstallation={profile.resolve().as_uri()}",
                "--convert-to",
                _pdf_filter(),
                "--outdir",
                str(output),
                str(source),
            ]
        )
        env = os.environ.copy()
        env["HOME"] = str(root)
        env["SAL_USE_VCLPLUGIN"] = "svp"
        env["SAL_DISABLE_OPENCL"] = "1"
        if fonts:
            font_dir = root / "fonts"
            font_dir.mkdir()
            for index, face in enumerate(fonts):
                (font_dir / f"face-{index}{font_suffix(face)}").write_bytes(face)
            env["FONTCONFIG_FILE"] = str(_fontconfig(root, font_dir))
        try:
            result = subprocess.run(
                command,
                check=False,
                capture_output=True,
                timeout=CONVERT_TIMEOUT_SECONDS,
                env=env,
            )
        except subprocess.TimeoutExpired as exc:
            raise ValueError("Converting that PowerPoint file took too long") from exc
        pdfs = list(output.glob("*.pdf"))
        if result.returncode != 0 or len(pdfs) != 1:
            detail = (result.stderr or result.stdout or b"")[-500:]
            logger.warning("PowerPoint conversion failed: %s", detail.decode("utf-8", "replace"))
            raise ValueError("Could not convert that PowerPoint file")
        pdf = pdfs[0].read_bytes()
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("Could not convert that PowerPoint file")
    return pdf


def _soffice_profile() -> Path:
    profile = Path(tempfile.gettempdir()) / "meetings-soffice" / str(os.getpid())
    profile.mkdir(parents=True, exist_ok=True)
    return profile


def _pdf_filter() -> str:
    options = (
        '{"UseLosslessCompression":{"type":"boolean","value":"false"},'
        f'"Quality":{{"type":"long","value":"{_PDF_IMAGE_QUALITY}"}},'
        '"ReduceImageResolution":{"type":"boolean","value":"true"},'
        f'"MaxImageResolution":{{"type":"long","value":"{_PDF_IMAGE_DPI}"}},'
        '"EmbedStandardFonts":{"type":"boolean","value":"true"},'
        '"ExportNotes":{"type":"boolean","value":"false"},'
        '"ExportNotesPages":{"type":"boolean","value":"false"},'
        '"ExportHiddenSlides":{"type":"boolean","value":"true"}}'
    )
    return f"pdf:impress_pdf_Export:{options}"


def _fontconfig(root: Path, font_dir: Path) -> Path:
    path = root / "fonts.conf"
    path.write_text(
        "<?xml version=\"1.0\"?>\n"
        "<!DOCTYPE fontconfig SYSTEM \"fonts.dtd\">\n"
        "<fontconfig>\n"
        f"  <dir>{escape(str(font_dir))}</dir>\n"
        "  <include ignore_missing=\"yes\">/etc/fonts/fonts.conf</include>\n"
        "</fontconfig>\n",
        encoding="utf-8",
    )
    return path


def _convert_with_service(url: str, data: bytes) -> bytes:
    boundary = uuid.uuid4().hex
    fields = {
        "losslessImageCompression": "false",
        "quality": _PDF_IMAGE_QUALITY,
        "reduceImageResolution": "true",
        "maxImageResolution": _PDF_IMAGE_DPI,
        "exportNotes": "false",
        "exportNotesPages": "false",
        "exportHiddenSlides": "true",
    }
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                f"{value}\r\n"
            ).encode()
        )
    parts.append(
        (
            f"--{boundary}\r\n"
            'Content-Disposition: form-data; name="files"; filename="deck.pptx"\r\n'
            "Content-Type: application/vnd.openxmlformats-officedocument.presentationml.presentation\r\n\r\n"
        ).encode()
    )
    parts.append(data)
    parts.append(f"\r\n--{boundary}--\r\n".encode())
    request = urllib.request.Request(
        f"{url}/forms/libreoffice/convert",
        data=b"".join(parts),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=CONVERT_TIMEOUT_SECONDS) as response:
            pdf = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read()[-500:]
        logger.warning("PowerPoint conversion failed: %s", detail.decode("utf-8", "replace"))
        raise ValueError("Could not convert that PowerPoint file") from exc
    except urllib.error.URLError as exc:
        logger.warning("PowerPoint converter is unreachable: %s", exc.reason)
        raise _ConverterUnavailable(str(exc.reason)) from exc
    if not pdf.startswith(b"%PDF-"):
        raise ValueError("Could not convert that PowerPoint file")
    return pdf


def align_notes(notes: list[str], page_count: int) -> list[str]:
    aligned = [_clean_note(note) for note in notes[:page_count]]
    if len(aligned) < page_count:
        aligned.extend([""] * (page_count - len(aligned)))
    return aligned


def notes_payload(deck: SharedSlides) -> list[str]:
    raw = deck.speaker_notes if isinstance(deck.speaker_notes, list) else []
    if not raw:
        return []
    notes = [item if isinstance(item, str) else "" for item in raw]
    if len(notes) < deck.page_count:
        notes.extend([""] * (deck.page_count - len(notes)))
    return notes[: deck.page_count]


def deck_payload(deck: SharedSlides) -> dict:
    return {
        "deckId": str(deck.id),
        "page": deck.page,
        "pageCount": deck.page_count,
        "name": deck.original_name,
        "ownerIdentity": deck.owner_identity,
        "ownerName": deck.owner_name,
        "hasNotes": _has_notes(deck),
    }


def _has_notes(deck: SharedSlides) -> bool:
    return any(note.strip() for note in notes_payload(deck))


def _zip_xml(archive: zipfile.ZipFile, names: set[str], name: str) -> etree._Element | None:
    if name not in names:
        return None
    return etree.fromstring(archive.read(name), _XML)


def _relationship_map(root: etree._Element) -> dict[str, str]:
    found: dict[str, str] = {}
    for rel in root:
        if rel.tag != f"{{{PKG_NS}}}Relationship":
            continue
        rel_id = rel.get("Id")
        target = rel.get("Target")
        if rel_id and target and "://" not in target:
            found[rel_id] = target
    return found


def _package_target(base_dir: str, target: str | None) -> str | None:
    if not target:
        return None
    if target.startswith("/"):
        return target.lstrip("/")
    return posixpath.normpath(posixpath.join(base_dir, target))


def _slide_notes(archive: zipfile.ZipFile, names: set[str], slide_part: str | None) -> str:
    if not slide_part or "/" not in slide_part:
        return ""
    folder, leaf = slide_part.rsplit("/", 1)
    rels_name = f"{folder}/_rels/{leaf}.rels"
    rels_root = _zip_xml(archive, names, rels_name)
    if rels_root is None:
        return ""
    notes_part = None
    for rel in rels_root:
        if rel.tag != f"{{{PKG_NS}}}Relationship":
            continue
        if not rel.get("Type", "").endswith("/notesSlide"):
            continue
        notes_part = _package_target(folder, rel.get("Target"))
        break
    if not notes_part:
        return ""
    notes_root = _zip_xml(archive, names, notes_part)
    if notes_root is None:
        return ""
    return _clean_note(_notes_body(notes_root))


def _notes_body(root: etree._Element) -> str:
    for shape in root.iter(f"{{{P_NS}}}sp"):
        placeholder = shape.find(f".//{{{P_NS}}}ph")
        if placeholder is None or placeholder.get("type") != "body":
            continue
        body = shape.find(f"{{{P_NS}}}txBody")
        if body is None:
            return ""
        paragraphs: list[str] = []
        for paragraph in body.findall(f"{{{A_NS}}}p"):
            pieces: list[str] = []
            for node in paragraph.iter():
                if node.tag == f"{{{A_NS}}}t" and node.text:
                    pieces.append(node.text)
                elif node.tag == f"{{{A_NS}}}br":
                    pieces.append("\n")
            paragraphs.append("".join(pieces))
        return "\n".join(paragraphs)
    return ""


def _clean_note(value: str) -> str:
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if len(text) > MAX_NOTE_CHARS:
        text = text[:MAX_NOTE_CHARS].rstrip()
    return text


def _retire(deck: SharedSlides) -> None:
    deck.active = False
    deck.save(update_fields=["active", "updated_at"])
    if deck.file:
        deck.file.delete(save=False)


def _room_name(value: str) -> str:
    name = value.strip()
    if not name or len(name) > 128 or any(ord(ch) < 32 for ch in name):
        raise ValueError("Invalid room name")
    return name


def _identity(value: str) -> str:
    identity = value.strip()
    if not identity or len(identity) > 256:
        raise ValueError("Invalid participant")
    return identity


def _file_name(value: str) -> str:
    name = Path(value or "slides.pdf").name.replace("\x00", "").strip()
    return (name or "slides.pdf")[:255]
