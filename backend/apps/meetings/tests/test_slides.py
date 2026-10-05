import shutil
import tempfile
import urllib.error
import zipfile
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from pptx import Presentation
from pptx.util import Inches, Pt
from pypdf import PdfWriter

from apps.meetings.models import SharedSlides
from apps.meetings.services.slide_fonts import shrink_large_fonts
from apps.meetings.services.slides import _soffice_executable, pptx_to_pdf


def _pdf_bytes() -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=100)
    writer.add_blank_page(width=200, height=100)
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


def _pptx_bytes() -> bytes:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.notes_slide.notes_text_frame.text = "Welcome the room"
    second = presentation.slides.add_slide(presentation.slide_layouts[0])
    second.notes_slide.notes_text_frame.text = "Ask for questions"
    blank = presentation.slides.add_slide(presentation.slide_layouts[6])
    box = blank.shapes.add_textbox(Inches(0.5), Inches(0.2), Inches(8), Inches(0.4))
    run = box.text_frame.paragraphs[0].add_run()
    run.text = "Exact face"
    run.font.size = Pt(48)
    run.font.name = "Palatino"
    buffer = BytesIO()
    presentation.save(buffer)
    return buffer.getvalue()


def _embed_font(data: bytes, font: bytes) -> bytes:
    source = zipfile.ZipFile(BytesIO(data))
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as dest:
        for info in source.infolist():
            dest.writestr(info, source.read(info.filename))
        dest.writestr("ppt/fonts/font1.fntdata", font)
    source.close()
    return buffer.getvalue()


class SlidesApiTests(TestCase):
    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.settings_override = override_settings(MEDIA_ROOT=self.media, MEETINGS_API_KEY="")
        self.settings_override.enable()
        lock_patch = patch("apps.meetings.services.slides.set_screen_share_locked")
        self.lock_screen = lock_patch.start()
        self.addCleanup(lock_patch.stop)

    def tearDown(self):
        self.settings_override.disable()
        shutil.rmtree(self.media, ignore_errors=True)

    def _upload(self, room="demo", identity="p_owner", name="Nico"):
        upload = SimpleUploadedFile("deck.pdf", _pdf_bytes(), content_type="application/pdf")
        return self.client.post(
            "/api/slides",
            {
                "room_name": room,
                "owner_identity": identity,
                "owner_name": name,
                "file": upload,
            },
        )

    def test_upload_page_and_stop(self):
        created = self._upload()
        self.assertEqual(created.status_code, 201)
        deck = created.json()["deck"]
        self.assertEqual(deck["pageCount"], 2)
        self.assertEqual(deck["page"], 1)
        self.assertEqual(deck["ownerName"], "Nico")
        self.assertFalse(deck["hasNotes"])
        self.assertEqual(created.json()["notes"], [])

        current = self.client.get("/api/slides", {"roomName": "demo"})
        self.assertEqual(current.json()["deck"]["deckId"], deck["deckId"])

        pdf = self.client.get(f"/api/slides/{deck['deckId']}/file")
        self.assertEqual(pdf.status_code, 200)
        body = b"".join(pdf.streaming_content)
        self.assertTrue(body.startswith(b"%PDF-"))

        denied = self.client.post(
            f"/api/slides/{deck['deckId']}/page",
            data={"page": 2, "owner_identity": "p_other"},
            content_type="application/json",
        )
        self.assertEqual(denied.status_code, 403)

        moved = self.client.post(
            f"/api/slides/{deck['deckId']}/page",
            data={"page": 2, "owner_identity": "p_owner"},
            content_type="application/json",
        )
        self.assertEqual(moved.status_code, 200)
        self.assertEqual(moved.json()["deck"]["page"], 2)

        stopped = self.client.post(
            f"/api/slides/{deck['deckId']}/stop",
            data={"owner_identity": "p_owner"},
            content_type="application/json",
        )
        self.assertEqual(stopped.status_code, 200)
        self.assertIsNone(self.client.get("/api/slides", {"roomName": "demo"}).json()["deck"])
        self.assertFalse(SharedSlides.objects.get(id=deck["deckId"]).active)
        self.lock_screen.assert_any_call("demo", True)
        self.lock_screen.assert_any_call("demo", False)

    def test_second_upload_replaces_the_room_deck(self):
        first = self._upload().json()["deck"]
        second = self._upload().json()["deck"]
        current = self.client.get("/api/slides", {"roomName": "demo"}).json()["deck"]
        self.assertEqual(current["deckId"], second["deckId"])
        self.assertEqual(self.client.get(f"/api/slides/{first['deckId']}/file").status_code, 404)

    def test_rejects_non_pdf(self):
        upload = SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")
        response = self.client.post(
            "/api/slides",
            {"room_name": "demo", "owner_identity": "p_owner", "file": upload},
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Choose a PDF or PowerPoint file")

    def test_pptx_keeps_speaker_notes_for_the_owner(self):
        upload = SimpleUploadedFile(
            "deck.pptx",
            _pptx_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        with patch("apps.meetings.services.slides.pptx_to_pdf", return_value=_pdf_bytes()) as convert:
            created = self.client.post(
                "/api/slides",
                {
                    "room_name": "demo",
                    "owner_identity": "p_owner",
                    "owner_name": "Nico",
                    "file": upload,
                },
            )
        self.assertEqual(created.status_code, 201)
        convert.assert_called_once()
        body = created.json()
        deck = body["deck"]
        self.assertEqual(deck["pageCount"], 2)
        self.assertTrue(deck["hasNotes"])
        self.assertEqual(body["notes"], ["Welcome the room", "Ask for questions"])
        self.assertNotIn("notes", deck)

        stored = b"".join(self.client.get(f"/api/slides/{deck['deckId']}/file").streaming_content)
        self.assertTrue(stored.startswith(b"%PDF-"))

        public = self.client.get("/api/slides", {"roomName": "demo"}).content.decode()
        self.assertNotIn("Welcome the room", public)
        self.assertTrue(self.client.get("/api/slides", {"roomName": "demo"}).json()["deck"]["hasNotes"])

        denied = self.client.get(
            f"/api/slides/{deck['deckId']}/notes",
            {"owner_identity": "p_other"},
        )
        self.assertEqual(denied.status_code, 403)
        allowed = self.client.get(
            f"/api/slides/{deck['deckId']}/notes",
            {"owner_identity": "p_owner"},
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["notes"], ["Welcome the room", "Ask for questions"])

    def test_pptx_is_accepted_before_conversion_finishes(self):
        started = []

        def capture(target, args):
            started.append((target, args))

        upload = SimpleUploadedFile(
            "deck.pptx",
            _pptx_bytes(),
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        with (
            patch("apps.meetings.services.slides.pptx_to_pdf", return_value=_pdf_bytes()),
            patch("apps.meetings.services.slides._schedule", side_effect=capture),
        ):
            created = self.client.post(
                "/api/slides",
                {
                    "room_name": "demo",
                    "owner_identity": "p_owner",
                    "owner_name": "Nico",
                    "file": upload,
                },
            )
            self.assertEqual(created.status_code, 202)
            body = created.json()
            self.assertEqual(body["status"], "converting")
            waiting = self.client.get("/api/slides", {"roomName": "demo"}).json()
            self.assertIsNone(waiting["deck"])
            self.assertEqual(waiting["converting"]["deckId"], body["deckId"])
            self.assertEqual(self.client.get(f"/api/slides/{body['deckId']}/file").status_code, 404)
            self.assertEqual(len(started), 1)
            started[0][0](*started[0][1])
        ready = self.client.get("/api/slides", {"roomName": "demo"}).json()
        self.assertEqual(ready["deck"]["pageCount"], 2)
        self.assertTrue(ready["deck"]["hasNotes"])
        self.assertIsNone(ready["converting"])
        self.assertIsNone(ready["error"])

    def test_rejects_invalid_pptx_without_converting(self):
        upload = SimpleUploadedFile(
            "deck.pptx",
            b"not a deck",
            content_type="application/vnd.openxmlformats-officedocument.presentationml.presentation",
        )
        with patch("apps.meetings.services.slides.pptx_to_pdf") as convert:
            response = self.client.post(
                "/api/slides",
                {"room_name": "demo", "owner_identity": "p_owner", "file": upload},
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["detail"], "Could not read that PowerPoint file")
        convert.assert_not_called()

    def test_finds_packaged_soffice_when_it_is_not_on_path(self):
        packaged = "/usr/lib/libreoffice/program/soffice"

        def isfile(path):
            return path == packaged

        with (
            patch("apps.meetings.services.slides.shutil.which", return_value=None),
            patch("apps.meetings.services.slides.os.path.isfile", side_effect=isfile),
            patch("apps.meetings.services.slides.os.access", return_value=True),
            patch.dict("os.environ", {"SOFFICE_PATH": ""}, clear=False),
        ):
            self.assertEqual(_soffice_executable(), packaged)

    def test_pptx_to_pdf_reports_unavailable_without_converter(self):
        with (
            patch("apps.meetings.services.slides.shutil.which", return_value=None),
            patch("apps.meetings.services.slides.os.path.isfile", return_value=False),
            patch.dict("os.environ", {"SOFFICE_PATH": "", "PPTX_CONVERTER_URL": ""}, clear=False),
        ):
            with self.assertRaisesMessage(ValueError, "PowerPoint conversion is unavailable"):
                pptx_to_pdf(b"PK")

    def test_pptx_uses_converter_service_when_soffice_is_missing(self):
        pdf = _pdf_bytes()

        class Response:
            def read(self):
                return pdf

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value=None),
            patch("urllib.request.urlopen", return_value=Response()) as opened,
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": "http://converter:3000"}),
        ):
            deck = _pptx_bytes()
            self.assertEqual(pptx_to_pdf(deck), pdf)
        request = opened.call_args.args[0]
        self.assertEqual(request.full_url, "http://converter:3000/forms/libreoffice/convert")
        self.assertIn(b'filename="deck.pptx"', request.data)
        self.assertIn(shrink_large_fonts(deck), request.data)
        self.assertIn(b'name="reduceImageResolution"', request.data)
        self.assertIn(b'name="maxImageResolution"\r\n\r\n150', request.data)
        self.assertIn(b'name="exportHiddenSlides"\r\n\r\ntrue', request.data)
        self.assertIn(b'name="exportNotes"\r\n\r\nfalse', request.data)

    def test_warm_converter_is_used_when_soffice_is_also_installed(self):
        pdf = _pdf_bytes()

        class Response:
            def read(self):
                return pdf

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("apps.meetings.services.slides.subprocess.run") as run,
            patch("urllib.request.urlopen", return_value=Response()) as opened,
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": "http://converter:3000"}),
        ):
            self.assertEqual(pptx_to_pdf(_pptx_bytes()), pdf)
        opened.assert_called_once()
        run.assert_not_called()

    def test_unreachable_converter_falls_back_to_local_soffice(self):
        pdf = _pdf_bytes()
        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")),
            patch("apps.meetings.services.slides._soffice_convert", return_value=pdf) as local,
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": "http://converter:3000"}),
        ):
            self.assertEqual(pptx_to_pdf(_pptx_bytes()), pdf)
        local.assert_called_once()

    def test_embedded_fonts_use_the_warm_converter_when_it_is_configured(self):
        font = b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + b"\x00" * 6 + b"glyf"
        deck = _embed_font(_pptx_bytes(), font)
        pdf = _pdf_bytes()

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return pdf

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("apps.meetings.services.slides.subprocess.run") as run,
            patch("urllib.request.urlopen", return_value=Response()) as opened,
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": "http://converter:3000"}),
        ):
            self.assertEqual(pptx_to_pdf(deck), pdf)
        opened.assert_called_once()
        run.assert_not_called()

    def test_soffice_keeps_the_deck_and_installs_embedded_fonts(self):
        font = b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + b"\x00" * 6 + b"glyf"
        deck = _embed_font(_pptx_bytes(), font)

        def fake_run(command, **kwargs):
            convert_at = command.index("--convert-to")
            self.assertIn("pdf:impress_pdf_Export:", command[convert_at + 1])
            self.assertIn('"EmbedStandardFonts":{"type":"boolean","value":"true"}', command[convert_at + 1])
            self.assertNotIn("meetings-soffice", command[convert_at - 1])
            conf = Path(kwargs["env"]["FONTCONFIG_FILE"]).read_text(encoding="utf-8")
            font_dir = conf.split("<dir>", 1)[1].split("</dir>", 1)[0]
            self.assertEqual(Path(font_dir, "face-0.ttf").read_bytes(), font)
            written = Path(command[-1]).read_bytes()
            with zipfile.ZipFile(BytesIO(written)) as archive:
                slides = b"".join(
                    archive.read(name)
                    for name in archive.namelist()
                    if name.startswith("ppt/slides/slide")
                )
            self.assertIn(b"Bodoni", slides)
            self.assertIn(b"Exact face", slides)
            self.assertNotIn(b'sz="4800"', slides)
            self.assertIn(b'sz="3800"', slides)
            out = Path(command[command.index("--outdir") + 1])
            (out / "deck.pdf").write_bytes(b"%PDF-1.4\n")
            return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("apps.meetings.services.slides.subprocess.run", side_effect=fake_run),
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": ""}),
        ):
            self.assertTrue(pptx_to_pdf(deck).startswith(b"%PDF-"))

    def test_warm_soffice_forwards_the_deck_to_the_running_instance(self):
        def fake_run(command, **kwargs):
            self.assertNotIn("--nolockcheck", command)
            convert_at = command.index("--convert-to")
            self.assertIn("meetings-soffice", command[convert_at - 1])
            out = Path(command[command.index("--outdir") + 1])
            (out / "deck.pdf").write_bytes(b"%PDF-1.4\n")
            return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("apps.meetings.services.slides._ensure_warm_soffice", return_value=True),
            patch("apps.meetings.services.slides.subprocess.run", side_effect=fake_run) as run,
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": ""}),
        ):
            self.assertTrue(pptx_to_pdf(_pptx_bytes()).startswith(b"%PDF-"))
        run.assert_called_once()

    def test_soffice_reuses_a_profile_when_the_deck_has_no_embedded_font(self):
        def fake_run(command, **kwargs):
            convert_at = command.index("--convert-to")
            self.assertIn("meetings-soffice", command[convert_at - 1])
            out = Path(command[command.index("--outdir") + 1])
            (out / "deck.pdf").write_bytes(b"%PDF-1.4\n")
            return SimpleNamespace(returncode=0, stderr=b"", stdout=b"")

        with (
            patch("apps.meetings.services.slides._soffice_executable", return_value="/usr/bin/soffice"),
            patch("apps.meetings.services.slides.subprocess.run", side_effect=fake_run),
            patch.dict("os.environ", {"PPTX_CONVERTER_URL": ""}),
        ):
            self.assertTrue(pptx_to_pdf(_pptx_bytes()).startswith(b"%PDF-"))
