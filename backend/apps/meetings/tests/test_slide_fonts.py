import zipfile
import zlib
from io import BytesIO

from django.test import SimpleTestCase

from apps.meetings.services.slide_fonts import extract_embedded_fonts, installable_font, shrink_large_fonts


def _ttf() -> bytes:
    return b"\x00\x01\x00\x00" + (1).to_bytes(2, "big") + b"\x00" * 6 + b"glyf"


def _otf() -> bytes:
    return b"OTTO" + (1).to_bytes(2, "big") + b"\x00" * 6 + b"CFF "


def _eot(font: bytes, *, compressed: bool = False) -> bytes:
    header = bytearray(80)
    flags = 0x4 if compressed else 0
    header[12:16] = flags.to_bytes(4, "little")
    header[34:36] = (0x504C).to_bytes(2, "little")
    return bytes(header) + font


def _package(*fonts: bytes) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/presentation.xml", b"<p/>")
        for index, font in enumerate(fonts, start=1):
            archive.writestr(f"ppt/fonts/font{index}.fntdata", font)
        archive.writestr("ppt/media/picture.jpg", b"\xff" * 1000)
    return buffer.getvalue()


class EmbeddedFontTests(SimpleTestCase):
    def test_raw_faces_are_kept_and_pictures_are_skipped(self):
        ttf = _ttf()
        otf = _otf()
        faces = extract_embedded_fonts(_package(ttf, otf))
        self.assertEqual(faces, [ttf, otf])

    def test_uncompressed_eot_is_unwrapped(self):
        ttf = _ttf()
        self.assertEqual(installable_font(_eot(ttf)), ttf)

    def test_microtype_eot_stays_in_the_package(self):
        self.assertIsNone(installable_font(_eot(_ttf(), compressed=True)))
        self.assertEqual(extract_embedded_fonts(_package(_eot(_ttf(), compressed=True))), [])

    def test_zlib_wrapped_font_is_unpacked(self):
        ttf = _ttf()
        self.assertEqual(installable_font(zlib.compress(ttf)), ttf)


def _text_deck(*parts: tuple[str, bytes], stored: bytes | None = None) -> bytes:
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in parts:
            archive.writestr(name, payload)
        if stored is not None:
            info = zipfile.ZipInfo("ppt/media/picture.jpg")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, stored)
    return buffer.getvalue()


class LargeFontShrinkTests(SimpleTestCase):
    def test_large_text_uses_a_fallback_face_and_shrinks_by_10pt(self):
        slide = (
            b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            b'<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            b' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b"<p:sp><p:txBody><a:p>"
            b'<a:r><a:rPr sz="4800"/><a:t>Title</a:t></a:r>'
            b'<a:r><a:rPr sz="4000"/><a:t>Limit</a:t></a:r>'
            b'<a:r><a:rPr sz="1800"/><a:t>Body</a:t></a:r>'
            b'<a:endParaRPr sz="4100"/>'
            b"</a:p></p:txBody></p:sp></p:sld>"
        )
        picture = b"\xff" * 2048
        shrunk = shrink_large_fonts(_text_deck(("ppt/slides/slide1.xml", slide), stored=picture))
        with zipfile.ZipFile(BytesIO(shrunk)) as archive:
            xml = archive.read("ppt/slides/slide1.xml")
            self.assertEqual(archive.read("ppt/media/picture.jpg"), picture)
        self.assertIn(b'sz="3800"', xml)
        self.assertIn(b'sz="3100"', xml)
        self.assertIn(b'sz="4000"', xml)
        self.assertIn(b'sz="1800"', xml)
        self.assertIn(b'typeface="Bodoni"', xml)
        self.assertIn(b'typeface="Anton"', xml)
        self.assertNotIn(b'sz="4800"', xml)
        self.assertNotIn(b'sz="4100"', xml)
        self.assertIn(b"Title", xml)

    def test_installed_and_similar_faces_are_kept(self):
        slide = (
            b'<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            b' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b"<p:sp><p:txBody><a:p>"
            b'<a:r><a:rPr sz="3200"><a:latin typeface="Arial"/></a:rPr><a:t>Known</a:t></a:r>'
            b'<a:r><a:rPr sz="3600"><a:latin typeface="Calibri"/></a:rPr><a:t>Similar</a:t></a:r>'
            b'<a:r><a:rPr sz="3400"><a:latin typeface="Palatino"/></a:rPr><a:t>Missing</a:t></a:r>'
            b'<a:r><a:rPr sz="5200"><a:latin typeface="Palatino"/></a:rPr><a:t>Large</a:t></a:r>'
            b'<a:r><a:rPr sz="1800"><a:latin typeface="Palatino"/></a:rPr><a:t>Small</a:t></a:r>'
            b"</a:p></p:txBody></p:sp></p:sld>"
        )
        shrunk = shrink_large_fonts(_text_deck(("ppt/slides/slide1.xml", slide)))
        with zipfile.ZipFile(BytesIO(shrunk)) as archive:
            xml = archive.read("ppt/slides/slide1.xml")
        self.assertIn(b'typeface="Arial"', xml)
        self.assertIn(b'typeface="Carlito"', xml)
        self.assertIn(b'typeface="Anton"', xml)
        self.assertIn(b'typeface="Bodoni"', xml)
        self.assertIn(b'sz="4200"', xml)
        self.assertIn(b'sz="1800"', xml)
        self.assertIn(b'typeface="Palatino"', xml)
        self.assertNotIn(b'sz="5200"', xml)

    def test_deck_without_large_text_is_not_rewritten(self):
        slide = b'<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:rPr sz="1800"/></p:sld>'
        original = _text_deck(("ppt/slides/slide1.xml", slide))
        self.assertIs(shrink_large_fonts(original), original)

    def test_styles_above_40pt_shrink_and_pictures_stay_compressed(self):
        master = (
            b'<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            b' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            b'<p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz="6000"/></a:lvl1pPr></p:titleStyle></p:txStyles>'
            b"</p:sldMaster>"
        )
        notes = b'<p:notes xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:rPr sz="7200"/></p:notes>'
        photo = os_urandom_photo()
        original = _text_deck(
            ("ppt/slideMasters/slideMaster1.xml", master),
            ("ppt/notesSlides/notesSlide1.xml", notes),
            stored=photo,
        )
        # Store the photo deflated as well as the stored copy from _text_deck.
        buffer = BytesIO()
        with zipfile.ZipFile(BytesIO(original)) as source, zipfile.ZipFile(buffer, "w") as dest:
            for info in source.infolist():
                dest.writestr(info, source.read(info.filename))
            deflated = zipfile.ZipInfo("ppt/media/chart.bin")
            deflated.compress_type = zipfile.ZIP_DEFLATED
            dest.writestr(deflated, photo)
        packed = buffer.getvalue()
        shrunk = shrink_large_fonts(packed)
        with zipfile.ZipFile(BytesIO(shrunk)) as archive:
            self.assertIn(b'sz="5000"', archive.read("ppt/slideMasters/slideMaster1.xml"))
            self.assertIn(b'sz="7200"', archive.read("ppt/notesSlides/notesSlide1.xml"))
            self.assertEqual(archive.read("ppt/media/picture.jpg"), photo)
            self.assertEqual(archive.read("ppt/media/chart.bin"), photo)


def os_urandom_photo() -> bytes:
    return bytes(range(256)) * 40
