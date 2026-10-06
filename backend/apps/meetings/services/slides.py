"""Store one deck per room and the page the presenter is on.

PDF files are stored as uploaded and drawn with the PDF viewer. PowerPoint
files are stored as uploaded and drawn with the PowerPoint viewer, so sharing
does not wait on a conversion. Speaker notes are kept beside the deck, one
entry per slide, and are not sent to other people.
"""

from __future__ import annotations

import posixpath
import uuid
import zipfile
from io import BytesIO
from pathlib import Path

from django.core.files.base import ContentFile
from django.db import transaction
from lxml import etree
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from apps.meetings.models import Meeting, MeetingMember, SharedSlides
from apps.meetings.services.room_control import set_screen_share_locked

MAX_PDF_BYTES = 25 * 1024 * 1024
MAX_PDF_PAGES = 300
MAX_NOTE_CHARS = 4000
PDF_MEDIA_TYPE = "application/pdf"
PPTX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.presentationml.presentation"

A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)


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
    file_data, notes, extension = slides_from_upload(original_name, data)
    # Lock before the file is stored. A joiner who enters during the save
    # must not be able to publish a screen share.
    set_screen_share_locked(room_name, True)
    try:
        return _store_ready_deck(
            room_name=room_name,
            owner_identity=owner_identity,
            owner_name=owner_name,
            original_name=original_name,
            file_data=file_data,
            notes=notes,
            extension=extension,
        )
    except Exception:
        if not _ready_decks(room_name).exists():
            set_screen_share_locked(room_name, False)
        raise


def _store_ready_deck(
    *,
    room_name: str,
    owner_identity: str,
    owner_name: str,
    original_name: str,
    file_data: bytes,
    notes: list[str],
    extension: str,
) -> SharedSlides:
    page_count = page_count_of(file_data) if extension == "pdf" else len(notes)
    if extension == "pptx" and page_count < 1:
        raise ValueError("This PowerPoint file has no slides")
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
        deck.file.save(f"{deck_id}.{extension}", ContentFile(file_data), save=True)
        for old in SharedSlides.objects.filter(room_name=room_name, active=True).exclude(id=deck.id):
            _retire(old)
    return deck


def _ready_decks(room_name: str):
    return SharedSlides.objects.filter(room_name=room_name, active=True, page_count__gt=0)


def current_deck(room_name: str) -> SharedSlides | None:
    room_name = _room_name(room_name)
    return _ready_decks(room_name).order_by("-created_at").first()


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


def slides_from_upload(original_name: str, data: bytes) -> tuple[bytes, list[str], str]:
    """Return the stored bytes, speaker notes, and file extension."""
    if len(data) > MAX_PDF_BYTES:
        raise ValueError("Slides must be 25 MB or smaller")
    if data.startswith(b"%PDF-"):
        page_count_of(data)
        return data, [], "pdf"
    if original_name.lower().endswith(".pptx") or looks_like_pptx(data):
        if not looks_like_pptx(data):
            raise ValueError("Could not read that PowerPoint file")
        return data, extract_speaker_notes(data), "pptx"
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


def deck_format(deck: SharedSlides) -> str:
    name = deck.file.name if deck.file else deck.original_name
    return "pptx" if name.lower().endswith(".pptx") else "pdf"


def file_content_type(deck: SharedSlides) -> str:
    if deck_format(deck) == "pptx":
        return PPTX_MEDIA_TYPE
    return PDF_MEDIA_TYPE


def deck_payload(deck: SharedSlides) -> dict:
    return {
        "deckId": str(deck.id),
        "page": deck.page,
        "pageCount": deck.page_count,
        "name": deck.original_name,
        "format": deck_format(deck),
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
