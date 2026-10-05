"""Pull installable faces out of a PowerPoint file and restyle large text.

Text under 30pt keeps the typeface and size stored in the deck. From 30pt
through 40pt, a run uses the deck face when that face or a similar one is
installed, and Anton otherwise. Above 40pt it uses the same similar face, or
Bodoni when none is installed, and the size is reduced by 10pt. LibreOffice
uses a face embedded in the file when the bytes are a real font, and the fonts
installed in the converter otherwise.
"""

from __future__ import annotations

import copy
import posixpath
import re
import zipfile
import zlib
from io import BytesIO

from lxml import etree

# EOT Flags bit: glyph data is MicroType-compressed and not a raw font.
_EOT_TTCOMPRESSED = 0x4
_EOT_MAGIC = 0x504C
_MAX_FONT_BYTES = 20 * 1024 * 1024
_SFNT_MAGICS = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")

# DrawingML stores sz in hundredths of a point.
_BAND_LOW = 30 * 100
_BAND_HIGH = 40 * 100
_SHRINK_BY = 13 * 100
FONT_FROM_30_THROUGH_40 = "Anton"
FONT_ABOVE_40 = "Bodoni"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
_SZ_ATTR = re.compile(br"""\bsz\s*=\s*["'](\d+)["']""")
_XML = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)

# Families installed in the converter and backend images, keyed by lower-case name.
_INSTALLED = {
    name.lower(): name
    for name in (
        "Arial",
        "Arial Black",
        "Andale Mono",
        "Comic Sans MS",
        "Courier New",
        "Georgia",
        "Impact",
        "Times New Roman",
        "Trebuchet MS",
        "Verdana",
        "Webdings",
        "Carlito",
        "Caladea",
        "Liberation Sans",
        "Liberation Sans Narrow",
        "Liberation Serif",
        "Liberation Mono",
        "DejaVu Sans",
        "DejaVu Serif",
        "DejaVu Sans Mono",
        "Selawik",
        "Noto Sans",
        "Noto Serif",
        "Noto Sans CJK JP",
        "Noto Sans CJK SC",
        "Noto Sans CJK TC",
        "Noto Sans CJK KR",
        "Noto Sans CJK HK",
        "Roboto",
        "Roboto Condensed",
        "Roboto Mono",
        "Roboto Slab",
        "Open Sans",
        "Lato",
        "Montserrat",
        "Oswald",
        "Source Sans 3",
        "Poppins",
        "Nunito",
        "Nunito Sans",
        "Raleway",
        "Inter",
        "Outfit",
        "Manrope",
        "DM Sans",
        "Lexend",
        "Work Sans",
        "Barlow",
        "Karla",
        "Cabin",
        "Libre Franklin",
        "Ubuntu",
        "Fira Sans",
        "Quicksand",
        "Josefin Sans",
        "Mulish",
        "Rubik",
        "Hind",
        "Archivo",
        "Archivo Narrow",
        "Titillium Web",
        "Exo 2",
        "Signika",
        "PT Sans",
        "PT Serif",
        "Merriweather",
        "Playfair Display",
        "Libre Baskerville",
        "Lora",
        "EB Garamond",
        "Crimson Text",
        "Source Serif 4",
        "Bitter",
        "Domine",
        "Spectral",
        "Newsreader",
        "Fraunces",
        "Literata",
        "Figtree",
        "Plus Jakarta Sans",
        "Albert Sans",
        "Be Vietnam Pro",
        "Sora",
        "Gelasio",
        "Comic Neue",
        "Pacifico",
        "Dancing Script",
        "Caveat",
        "Permanent Marker",
        "Indie Flower",
        "Shadows Into Light",
        "Amatic SC",
        "Patrick Hand",
        "Kalam",
        "Satisfy",
        "Great Vibes",
        "Lobster",
        "Bebas Neue",
        "Anton",
        "Libre Bodoni",
        "Abril Fatface",
        "Yanone Kaffeesatz",
        "Comfortaa",
        "Inconsolata",
    )
}

# Requested names that are not installed, with the closest face that is.
_SIMILAR = {
    "calibri": "Carlito",
    "calibri light": "Carlito",
    "cambria": "Caladea",
    "cambria math": "Caladea",
    "segoe ui": "Selawik",
    "segoe ui light": "Selawik",
    "segoe ui semibold": "Selawik",
    "arial narrow": "Liberation Sans Narrow",
    "source sans pro": "Source Sans 3",
    "garamond": "EB Garamond",
    "droid sans": "Roboto",
    "droid serif": "Noto Serif",
    "google sans": "Inter",
    "google sans text": "Inter",
    "google sans display": "Inter",
    "product sans": "Inter",
    "bodoni": "Libre Bodoni",
    "bodoni mt": "Libre Bodoni",
    "times": "Times New Roman",
    "courier": "Courier New",
    "helvetica": "Arial",
    "helvetica neue": "Arial",
    "comic sans": "Comic Sans MS",
}


def extract_embedded_fonts(data: bytes) -> list[bytes]:
    """Return TTF/OTF bytes embedded under ``ppt/fonts``."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.startswith("ppt/fonts/") and name.endswith(".fntdata")
            ]
            payloads = [archive.read(name) for name in names]
    except zipfile.BadZipFile:
        return []
    fonts: list[bytes] = []
    for payload in payloads:
        face = installable_font(payload)
        if face:
            fonts.append(face)
    return fonts


def installable_font(payload: bytes) -> bytes | None:
    """Return a font file fontconfig can load, or None when the part is not one.

    PowerPoint stores each face as ``.fntdata``. That is either the font itself
    or an EOT wrapper. Uncompressed EOT is unwrapped. MicroType-compressed EOT
    stays inside the package, where LibreOffice reads it while converting.
    """
    if not payload or len(payload) > _MAX_FONT_BYTES:
        return None
    if _is_sfnt(payload) or payload.startswith(b"ttcf"):
        return payload
    if payload[:2] in (b"\x78\x9c", b"\x78\xda", b"\x78\x01"):
        try:
            unpacked = zlib.decompress(payload)
        except zlib.error:
            unpacked = b""
        if unpacked and len(unpacked) <= _MAX_FONT_BYTES:
            found = installable_font(unpacked)
            if found:
                return found
    if _is_compressed_eot(payload):
        return None
    found = _sfnt_slice(payload)
    if found:
        return found
    return None


def font_suffix(data: bytes) -> str:
    if data.startswith(b"OTTO") or data.startswith(b"typ1"):
        return ".otf"
    if data.startswith(b"ttcf"):
        return ".ttc"
    return ".ttf"


def _is_compressed_eot(payload: bytes) -> bool:
    if len(payload) < 36 or int.from_bytes(payload[34:36], "little") != _EOT_MAGIC:
        return False
    flags = int.from_bytes(payload[12:16], "little")
    return bool(flags & _EOT_TTCOMPRESSED)


def _sfnt_slice(payload: bytes) -> bytes | None:
    start = 16 if len(payload) > 36 else 0
    for magic in _SFNT_MAGICS + (b"ttcf",):
        index = payload.find(magic, start)
        if index < 0:
            continue
        face = payload[index:]
        if _is_sfnt(face) or face.startswith(b"ttcf"):
            return face
    return None


def _is_sfnt(data: bytes) -> bool:
    if len(data) < 12 or data[:4] not in _SFNT_MAGICS:
        return False
    tables = int.from_bytes(data[4:6], "big")
    return 1 <= tables <= 64


def available_face(requested: str | None) -> str | None:
    """Return the installed face for a PowerPoint font name, when one exists."""
    key = (requested or "").strip().lower()
    if not key:
        return None
    if key in _INSTALLED:
        return _INSTALLED[key]
    return _SIMILAR.get(key)


def face_for_size(hundredths: int, requested: str | None) -> str | None:
    """Face to draw from 30pt up. Smaller text keeps the face stored in the deck."""
    if hundredths < _BAND_LOW:
        return None
    available = available_face(requested)
    if hundredths <= _BAND_HIGH:
        return available or FONT_FROM_30_THROUGH_40
    return available or FONT_ABOVE_40


def adjusted_hundredths(hundredths: int) -> int | None:
    """Point size above 40pt is reduced by 10pt. Other sizes stay as stored."""
    if hundredths > _BAND_HIGH:
        return max(hundredths - _SHRINK_BY, 100)
    return None


def shrink_large_fonts(data: bytes) -> bytes:
    """Return PPTX bytes with large text restyled for the PDF.

    The original bytes are returned when nothing is 30pt or larger, so a deck
    of body text is not rewritten. Pictures and other parts are copied compressed.
    """
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile:
        return data
    with archive:
        files: dict[str, bytes] = {}
        needs_edit = False
        for info in archive.infolist():
            if not _is_needed_part(info.filename):
                continue
            payload = archive.read(info.filename)
            files[info.filename] = payload
            if _is_text_part(info.filename) and _has_band_font(payload):
                needs_edit = True
        if not needs_edit:
            return data

        trees: dict[str, etree._Element] = {}
        for name, payload in files.items():
            if not _is_text_part(name):
                continue
            try:
                trees[name] = etree.fromstring(payload, _XML)
            except etree.XMLSyntaxError:
                continue

        dirty: set[str] = set()
        for name in list(trees):
            _restyle_part(trees[name], trees, files, name, dirty)
        if not dirty:
            return data
        replacements = {
            name: etree.tostring(trees[name], xml_declaration=True, encoding="UTF-8", standalone=True)
            for name in dirty
        }
        return _replace_zip_members(archive, replacements)


def _is_text_part(name: str) -> bool:
    if not name.endswith(".xml") or "/_rels/" in name:
        return False
    return (
        name == "ppt/presentation.xml"
        or name.startswith("ppt/slides/slide")
        or name.startswith("ppt/slideLayouts/")
        or name.startswith("ppt/slideMasters/")
        or name.startswith("ppt/charts/")
        or name.startswith("ppt/diagrams/")
    )


def _is_needed_part(name: str) -> bool:
    if "/media/" in name or name.startswith(("ppt/embeddings/", "ppt/fonts/")):
        return False
    return _is_text_part(name) or name.endswith(".rels") or name.startswith("ppt/theme/")


def _has_band_font(payload: bytes) -> bool:
    for match in _SZ_ATTR.finditer(payload):
        if int(match.group(1)) >= _BAND_LOW:
            return True
    return False


def _restyle_part(
    root: etree._Element,
    trees: dict[str, etree._Element],
    files: dict[str, bytes],
    part_name: str,
    dirty: set[str],
) -> None:
    for run in list(root.iter(f"{{{_A_NS}}}r")) + list(root.iter(f"{{{_A_NS}}}fld")):
        hundredths = _resolve_sz(run, root, trees, files, part_name)
        face = _resolve_face(run, root, trees, files, part_name)
        chosen = face_for_size(hundredths, face)
        new_size = adjusted_hundredths(hundredths)
        if chosen is None and new_size is None:
            continue
        rpr = run.find(f"{{{_A_NS}}}rPr")
        if rpr is None:
            rpr = etree.Element(f"{{{_A_NS}}}rPr")
            run.insert(0, rpr)
        if _apply_text_edit(rpr, chosen, new_size):
            dirty.add(part_name)
    for tag in ("defRPr", "endParaRPr"):
        for element in root.iter(f"{{{_A_NS}}}{tag}"):
            hundredths = _sz_attr(element)
            if hundredths is None:
                continue
            requested = _concrete_face(_latin_typeface(element), files, trees, part_name)
            chosen = face_for_size(hundredths, requested) if requested else None
            if _apply_text_edit(element, chosen, adjusted_hundredths(hundredths)):
                dirty.add(part_name)


def _apply_text_edit(element: etree._Element, font: str | None, new_size: int | None) -> bool:
    changed = False
    if font and _latin_typeface(element) != font:
        _set_typeface(element, font)
        changed = True
    if new_size is not None and _sz_attr(element) != new_size:
        element.set("sz", str(new_size))
        changed = True
    return changed


def _set_typeface(element: etree._Element, font: str) -> None:
    for tag in ("latin", "ea", "cs"):
        face = element.find(f"{{{_A_NS}}}{tag}")
        if face is None:
            face = etree.SubElement(element, f"{{{_A_NS}}}{tag}")
        face.set("typeface", font)


def _resolve_sz(
    run: etree._Element,
    root: etree._Element,
    trees: dict[str, etree._Element],
    files: dict[str, bytes],
    part_name: str,
) -> int:
    rpr = run.find(f"{{{_A_NS}}}rPr")
    found = _sz_attr(rpr)
    if found is not None:
        return found

    paragraph = run.getparent()
    level = _paragraph_level(paragraph)
    if paragraph is not None:
        ppr = paragraph.find(f"{{{_A_NS}}}pPr")
        if ppr is not None:
            found = _sz_attr(ppr.find(f"{{{_A_NS}}}defRPr"))
            if found is not None:
                return found

    tx_body = _ancestor(run, f"{{{_P_NS}}}txBody")
    if tx_body is None:
        tx_body = _ancestor(run, f"{{{_A_NS}}}txBody")
    if tx_body is not None:
        found = _sz_from_style(tx_body.find(f"{{{_A_NS}}}lstStyle"), level)
        if found is not None:
            return found

    shape = _ancestor(run, f"{{{_P_NS}}}sp")
    placeholder = _placeholder(shape) if shape is not None else None
    if placeholder is None:
        return 18 * 100

    if part_name.startswith("ppt/slides/slide"):
        layout_name = _related(files, part_name, "/slideLayout")
        layout = _tree(trees, files, layout_name)
        found = _sz_on_placeholder(layout, placeholder, level)
        if found is not None:
            return found
        master = _tree(trees, files, _related(files, layout_name, "/slideMaster") if layout_name else None)
        found = _sz_on_placeholder(master, placeholder, level)
        if found is not None:
            return found
        found = _sz_from_txstyles(master, placeholder[0], level)
        if found is not None:
            return found
    elif "slideLayouts/" in part_name:
        master = _tree(trees, files, _related(files, part_name, "/slideMaster"))
        found = _sz_on_placeholder(master, placeholder, level)
        if found is not None:
            return found
        found = _sz_from_txstyles(master, placeholder[0], level)
        if found is not None:
            return found
    elif "slideMasters/" in part_name:
        found = _sz_from_txstyles(root, placeholder[0], level)
        if found is not None:
            return found
    return 18 * 100


def _resolve_face(
    run: etree._Element,
    root: etree._Element,
    trees: dict[str, etree._Element],
    files: dict[str, bytes],
    part_name: str,
) -> str | None:
    rpr = run.find(f"{{{_A_NS}}}rPr")
    found = _concrete_face(_latin_typeface(rpr), files, trees, part_name)
    if found:
        return found

    paragraph = run.getparent()
    level = _paragraph_level(paragraph)
    if paragraph is not None:
        ppr = paragraph.find(f"{{{_A_NS}}}pPr")
        if ppr is not None:
            found = _concrete_face(_latin_typeface(ppr.find(f"{{{_A_NS}}}defRPr")), files, trees, part_name)
            if found:
                return found

    tx_body = _ancestor(run, f"{{{_P_NS}}}txBody")
    if tx_body is None:
        tx_body = _ancestor(run, f"{{{_A_NS}}}txBody")
    if tx_body is not None:
        found = _face_from_style(tx_body.find(f"{{{_A_NS}}}lstStyle"), level, files, trees, part_name)
        if found:
            return found

    shape = _ancestor(run, f"{{{_P_NS}}}sp")
    placeholder = _placeholder(shape) if shape is not None else None
    if placeholder is None:
        return _scheme_latin(files, trees, part_name, major=False)

    if part_name.startswith("ppt/slides/slide"):
        layout_name = _related(files, part_name, "/slideLayout")
        layout = _tree(trees, files, layout_name)
        found = _face_on_placeholder(layout, placeholder, level, files, trees, layout_name)
        if found:
            return found
        master_name = _related(files, layout_name, "/slideMaster") if layout_name else None
        master = _tree(trees, files, master_name)
        found = _face_on_placeholder(master, placeholder, level, files, trees, master_name)
        if found:
            return found
        found = _face_from_txstyles(master, placeholder[0], level, files, trees, master_name)
        if found:
            return found
    elif "slideLayouts/" in part_name:
        master_name = _related(files, part_name, "/slideMaster")
        master = _tree(trees, files, master_name)
        found = _face_on_placeholder(master, placeholder, level, files, trees, master_name)
        if found:
            return found
        found = _face_from_txstyles(master, placeholder[0], level, files, trees, master_name)
        if found:
            return found
    elif "slideMasters/" in part_name:
        found = _face_from_txstyles(root, placeholder[0], level, files, trees, part_name)
        if found:
            return found
    return _scheme_latin(files, trees, part_name, major=placeholder[0] in ("title", "ctrTitle"))


def _face_on_placeholder(
    root: etree._Element | None,
    placeholder: tuple[str | None, str | None],
    level: int,
    files: dict[str, bytes],
    trees: dict[str, etree._Element],
    part_name: str | None,
) -> str | None:
    if root is None or not part_name:
        return None
    shape = _find_placeholder(root, placeholder[0], placeholder[1])
    if shape is None:
        return None
    tx_body = shape.find(f"{{{_P_NS}}}txBody")
    if tx_body is None:
        return None
    found = _face_from_style(tx_body.find(f"{{{_A_NS}}}lstStyle"), level, files, trees, part_name)
    if found:
        return found
    for paragraph in tx_body.findall(f"{{{_A_NS}}}p"):
        ppr = paragraph.find(f"{{{_A_NS}}}pPr")
        if ppr is None:
            continue
        found = _concrete_face(_latin_typeface(ppr.find(f"{{{_A_NS}}}defRPr")), files, trees, part_name)
        if found:
            return found
    return None


def _face_from_txstyles(
    root: etree._Element | None,
    ph_type: str | None,
    level: int,
    files: dict[str, bytes],
    trees: dict[str, etree._Element],
    part_name: str | None,
) -> str | None:
    if root is None or not part_name:
        return None
    styles = root.find(f".//{{{_P_NS}}}txStyles")
    if styles is None:
        return None
    if ph_type in ("title", "ctrTitle"):
        style_name = "titleStyle"
    elif ph_type in ("body", "obj"):
        style_name = "bodyStyle"
    else:
        style_name = "otherStyle"
    return _face_from_style(styles.find(f"{{{_P_NS}}}{style_name}"), level, files, trees, part_name)


def _face_from_style(
    style: etree._Element | None,
    level: int,
    files: dict[str, bytes],
    trees: dict[str, etree._Element],
    part_name: str | None,
) -> str | None:
    if style is None or not part_name:
        return None
    level = min(max(level, 1), 9)
    level_pr = style.find(f"{{{_A_NS}}}lvl{level}pPr")
    if level_pr is not None:
        found = _concrete_face(_latin_typeface(level_pr.find(f"{{{_A_NS}}}defRPr")), files, trees, part_name)
        if found:
            return found
    default = style.find(f"{{{_A_NS}}}defPPr")
    if default is not None:
        return _concrete_face(_latin_typeface(default.find(f"{{{_A_NS}}}defRPr")), files, trees, part_name)
    return None


def _latin_typeface(element: etree._Element | None) -> str | None:
    if element is None:
        return None
    latin = element.find(f"{{{_A_NS}}}latin")
    if latin is None:
        return None
    face = (latin.get("typeface") or "").strip()
    return face or None


def _concrete_face(
    face: str | None,
    files: dict[str, bytes],
    trees: dict[str, etree._Element],
    part_name: str | None,
) -> str | None:
    if not face or not part_name:
        return None
    token = face.lower()
    if token.startswith("+mj-"):
        return _scheme_latin(files, trees, part_name, major=True)
    if token.startswith("+mn-"):
        return _scheme_latin(files, trees, part_name, major=False)
    return face


def _scheme_latin(
    files: dict[str, bytes],
    trees: dict[str, etree._Element],
    part_name: str | None,
    *,
    major: bool,
) -> str | None:
    theme_name = _theme_part(files, part_name)
    theme = _tree(trees, files, theme_name)
    if theme is None:
        return None
    group = "majorFont" if major else "minorFont"
    latin = theme.find(f".//{{{_A_NS}}}{group}/{{{_A_NS}}}latin")
    if latin is None:
        return None
    face = (latin.get("typeface") or "").strip()
    return face or None


def _theme_part(files: dict[str, bytes], part_name: str | None) -> str | None:
    if not part_name:
        return None
    direct = _related(files, part_name, "/theme")
    if direct:
        return direct
    master_name = part_name
    if part_name.startswith("ppt/slides/slide"):
        layout_name = _related(files, part_name, "/slideLayout")
        master_name = _related(files, layout_name, "/slideMaster") if layout_name else None
    elif "slideLayouts/" in part_name:
        master_name = _related(files, part_name, "/slideMaster")
    return _related(files, master_name, "/theme")


def _sz_on_placeholder(root: etree._Element | None, placeholder: tuple[str | None, str | None], level: int) -> int | None:
    if root is None:
        return None
    shape = _find_placeholder(root, placeholder[0], placeholder[1])
    if shape is None:
        return None
    tx_body = shape.find(f"{{{_P_NS}}}txBody")
    if tx_body is None:
        return None
    found = _sz_from_style(tx_body.find(f"{{{_A_NS}}}lstStyle"), level)
    if found is not None:
        return found
    for paragraph in tx_body.findall(f"{{{_A_NS}}}p"):
        ppr = paragraph.find(f"{{{_A_NS}}}pPr")
        if ppr is None:
            continue
        found = _sz_attr(ppr.find(f"{{{_A_NS}}}defRPr"))
        if found is not None:
            return found
    return None


def _sz_from_txstyles(root: etree._Element | None, ph_type: str | None, level: int) -> int | None:
    if root is None:
        return None
    styles = root.find(f".//{{{_P_NS}}}txStyles")
    if styles is None:
        return None
    if ph_type in ("title", "ctrTitle"):
        style_name = "titleStyle"
    elif ph_type in ("body", "obj"):
        style_name = "bodyStyle"
    else:
        style_name = "otherStyle"
    return _sz_from_style(styles.find(f"{{{_P_NS}}}{style_name}"), level)


def _sz_from_style(style: etree._Element | None, level: int) -> int | None:
    if style is None:
        return None
    level = min(max(level, 1), 9)
    level_pr = style.find(f"{{{_A_NS}}}lvl{level}pPr")
    if level_pr is not None:
        found = _sz_attr(level_pr.find(f"{{{_A_NS}}}defRPr"))
        if found is not None:
            return found
    default = style.find(f"{{{_A_NS}}}defPPr")
    if default is not None:
        return _sz_attr(default.find(f"{{{_A_NS}}}defRPr"))
    return None


def _find_placeholder(root: etree._Element, ph_type: str | None, ph_idx: str | None) -> etree._Element | None:
    shapes: list[tuple[etree._Element, str | None, str | None]] = []
    for shape in root.iter(f"{{{_P_NS}}}sp"):
        info = _placeholder(shape)
        if info is not None:
            shapes.append((shape, info[0], info[1]))
    for shape, typ, idx in shapes:
        if typ == ph_type and idx == ph_idx:
            return shape
    if ph_type in ("title", "ctrTitle"):
        for shape, typ, idx in shapes:
            if typ in ("title", "ctrTitle") and (ph_idx is None or idx is None or idx == ph_idx):
                return shape
    for shape, typ, idx in shapes:
        if typ == ph_type and (ph_idx is None or idx is None or idx == ph_idx):
            return shape
    return None


def _placeholder(shape: etree._Element | None) -> tuple[str | None, str | None] | None:
    if shape is None:
        return None
    ph = shape.find(f".//{{{_P_NS}}}ph")
    if ph is None:
        return None
    return ph.get("type"), ph.get("idx")


def _paragraph_level(paragraph: etree._Element | None) -> int:
    if paragraph is None:
        return 1
    ppr = paragraph.find(f"{{{_A_NS}}}pPr")
    if ppr is None or not ppr.get("lvl"):
        return 1
    try:
        return int(ppr.get("lvl")) + 1
    except ValueError:
        return 1


def _sz_attr(element: etree._Element | None) -> int | None:
    if element is None or not element.get("sz"):
        return None
    try:
        return int(element.get("sz"))
    except ValueError:
        return None


def _ancestor(element: etree._Element, tag: str) -> etree._Element | None:
    current = element.getparent()
    while current is not None:
        if current.tag == tag:
            return current
        current = current.getparent()
    return None


def _related(files: dict[str, bytes], part: str | None, type_suffix: str) -> str | None:
    if not part:
        return None
    folder, leaf = part.rsplit("/", 1)
    rels_name = f"{folder}/_rels/{leaf}.rels"
    payload = files.get(rels_name)
    if payload is None:
        return None
    try:
        root = etree.fromstring(payload, _XML)
    except etree.XMLSyntaxError:
        return None
    for rel in root:
        if rel.tag != f"{{{_PKG_NS}}}Relationship":
            continue
        if not rel.get("Type", "").endswith(type_suffix):
            continue
        target = rel.get("Target")
        if not target or "://" in target:
            continue
        if target.startswith("/"):
            return target.lstrip("/")
        return posixpath.normpath(posixpath.join(folder, target))
    return None


def _tree(trees: dict[str, etree._Element], files: dict[str, bytes], name: str | None) -> etree._Element | None:
    if not name:
        return None
    if name in trees:
        return trees[name]
    payload = files.get(name)
    if payload is None:
        return None
    try:
        trees[name] = etree.fromstring(payload, _XML)
    except etree.XMLSyntaxError:
        return None
    return trees[name]


def _replace_zip_members(src: zipfile.ZipFile, replacements: dict[str, bytes]) -> bytes:
    """Swap edited XML parts. Every other member is copied still compressed."""
    output = BytesIO()
    with zipfile.ZipFile(output, "w") as dest:
        for info in src.infolist():
            replacement = replacements.get(info.filename)
            if replacement is None:
                _copy_compressed_member(src, dest, info)
                continue
            edited = zipfile.ZipInfo(filename=info.filename, date_time=info.date_time)
            edited.compress_type = zipfile.ZIP_DEFLATED
            edited.external_attr = info.external_attr
            dest.writestr(edited, replacement)
    return output.getvalue()


def _copy_compressed_member(src: zipfile.ZipFile, dest: zipfile.ZipFile, info: zipfile.ZipInfo) -> None:
    payload = _compressed_payload(src, info)
    clone = copy.copy(info)
    # Sizes are already known, so the copy does not need a data descriptor.
    clone.flag_bits = info.flag_bits & ~0x8
    if dest.fp is None:
        raise ValueError("Could not read that PowerPoint file")
    dest.fp.seek(dest.start_dir)
    clone.header_offset = dest.fp.tell()
    dest._didModify = True
    dest.fp.write(clone.FileHeader(False))
    dest.fp.write(payload)
    dest.start_dir = dest.fp.tell()
    dest.filelist.append(clone)
    dest.NameToInfo[clone.filename] = clone


def _compressed_payload(src: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    handle = src.fp
    if handle is None:
        raise ValueError("Could not read that PowerPoint file")
    handle.seek(info.header_offset)
    header = handle.read(30)
    if len(header) != 30 or header[:4] != b"PK\x03\x04":
        raise ValueError("Could not read that PowerPoint file")
    name_len = int.from_bytes(header[26:28], "little")
    extra_len = int.from_bytes(header[28:30], "little")
    handle.seek(name_len + extra_len, 1)
    payload = handle.read(info.compress_size)
    if len(payload) != info.compress_size:
        raise ValueError("Could not read that PowerPoint file")
    return payload
