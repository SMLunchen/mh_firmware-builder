"""Splash-Erzeugung für beide Display-Mechanismen.

TFT  -> PNG (240x320 RGB) nach data/static/boot/logo.png im Firmware-Repo.
        device-ui lädt das über FileLoader::loadBootImage aus LittleFS.
OLED -> XBM (1-bit, LSB-first) als USERPREFS_OEM_IMAGE_DATA.
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import fonts

GREEN = (82, 234, 130)
BLACK = (0, 0, 0)

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
]


def _font(size: int) -> ImageFont.FreeTypeFont:
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _fit_font(text: str, max_width: int, start: int = 26, minimum: int = 10):
    """Größte Schriftgröße, bei der der Text noch in max_width passt."""
    for size in range(start, minimum - 1, -1):
        font = _font(size)
        if font.getbbox(text)[2] <= max_width:
            return font
    return _font(minimum)


def render_tft_png(source_logo: Path, text: str, out: Path,
                   width: int = 240, height: int = 320) -> Path:
    """Logo oben, Schriftzug in Grün darunter. Schwarzer Hintergrund."""
    canvas = Image.new("RGB", (width, height), BLACK)

    logo = Image.open(source_logo).convert("RGBA")
    margin = 8
    target_w = width - 2 * margin
    scale = target_w / logo.width
    logo_h = max(1, int(logo.height * scale))
    # Platz für den Schriftzug freihalten
    max_logo_h = height - 60
    if logo_h > max_logo_h:
        scale = max_logo_h / logo.height
        target_w = max(1, int(logo.width * scale))
        logo_h = max_logo_h
    logo = logo.resize((target_w, logo_h), Image.LANCZOS)

    logo_x = (width - target_w) // 2
    logo_y = max(0, (height - 44 - logo_h) // 2)
    canvas.paste(logo, (logo_x, logo_y), logo)

    if text:
        draw = ImageDraw.Draw(canvas)
        font = _fit_font(text, width - 16)
        bbox = font.getbbox(text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        tx = (width - tw) // 2 - bbox[0]
        ty = min(logo_y + logo_h + 12, height - th - 10) - bbox[1]
        draw.text((tx, ty), text, font=font, fill=GREEN)

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, "PNG")
    return out


def screen_resolution(width: int, height: int) -> str:
    """Spiegelt determineScreenResolution() aus SharedUIDisplay.cpp.

    Entscheidend fuer uns: drawOEMIconScreen zeichnet USERPREFS_OEM_TEXT nur
    bei "high". Auf einem normalen 128x64-OLED (= "low") erscheint der Text
    nie - dort muss der Schriftzug ins Bild gebacken werden, sonst waere die
    Personalisierung unsichtbar.
    """
    if width <= 64 or height <= 48:
        return "ultralow"
    if width > 128 and height <= 64:
        return "low"
    if width > 128:
        return "high"
    return "low"


def draws_oem_text(width: int, height: int) -> bool:
    return screen_resolution(width, height) == "high"


def xbm_size(width: int, height: int, font_kind: str = "default") -> tuple[int, int]:
    """Groesse, mit der das XBM deklariert werden darf.

    Bei High-Res positioniert drawOEMIconScreen so:

        y = (SCREEN_HEIGHT - FONT_HEIGHT_MEDIUM - IMAGE_HEIGHT) / 2 + 2

    Damit das Bild unter der Statuszeile beginnt (y >= FONT_HEIGHT_SMALL), gilt

        IMAGE_HEIGHT <= H - FONT_HEIGHT_MEDIUM - 2*FONT_HEIGHT_SMALL + 4

    Bei Low zeichnet die Firmware keinen Titel; dort bleibt die volle Hoehe,
    weil wir den Schriftzug selbst ins Bild setzen.
    """
    if not draws_oem_text(width, height):
        return width, height
    small, medium = fonts.FONT_HEIGHTS.get(font_kind, fonts.FONT_HEIGHTS["default"])
    usable = height - medium - 2 * small + 4
    return width, max(16, min(height, usable))


def render_oled_xbm(source_icon: Path, width: int, height: int,
                    text: str = "", font_kind: str = "default") -> str:
    """1-bit XBM als C-Array-Body (LSB-first) fuer USERPREFS_OEM_IMAGE_DATA.

    drawOEMIconScreen beansprucht Raender fuer sich: oben eine Statuszeile
    (Region links, Version rechts) und bei High-Res unten den Titel. Das Bild
    wird mittig zwischen diese Raender gesetzt:

        y = (SCREEN_HEIGHT - FONT_HEIGHT_MEDIUM - IMAGE_HEIGHT) / 2 + 2

    Ein bildschirmhohes Bild ergibt daraus ein negatives y - es ragt oben aus
    dem Schirm und verdeckt unten den Titel. Genau das passierte auf dem
    RAK14000 (122 px hoch): y = (122-28-122)/2+2 = -12.

    `text` wird nur eingebrannt, wenn die Firmware ihn nicht selbst zeichnet.
    """
    icon = Image.open(source_icon).convert("L")

    # Quellen liegen mal als helle Grafik auf dunklem, mal als dunkle Linien
    # auf hellem Grund vor. Das Motiv ist immer die Minderheit der Pixel -
    # daran erkennen wir die Polaritaet, statt sie zu raten.
    histogram = icon.histogram()
    if sum(histogram[128:]) > sum(histogram[:128]):
        icon = ImageOps.invert(icon)

    font_small, _ = fonts.FONT_HEIGHTS.get(font_kind, fonts.FONT_HEIGHTS["default"])
    draws_title = draws_oem_text(width, height)
    bake_text = bool(text) and not draws_title

    if draws_title:
        # Die Firmware positioniert selbst und laesst oben wie unten Platz -
        # das Bild ist deshalb genau die Icon-Flaeche, ohne eigene Raender.
        width, height = xbm_size(width, height, font_kind)
        top_margin = 0
        text_height = 0
        font = None
    else:
        # Wir setzen den Schriftzug selbst und halten oben die Statuszeile frei.
        top_margin = font_small if height >= 3 * font_small else 2
        text_height = 0
        font = None
        if bake_text:
            font = _fit_font(text, width - 4,
                             start=min(14, max(8, height // 5)), minimum=8)
            bbox = font.getbbox(text)
            text_height = bbox[3] - bbox[1] + 3

    box_h = max(1, height - top_margin - text_height)
    scale = min(width / icon.width, box_h / icon.height)
    new_size = (max(1, int(icon.width * scale)), max(1, int(icon.height * scale)))
    icon = icon.resize(new_size, Image.LANCZOS)

    canvas = Image.new("L", (width, height), 0)
    canvas.paste(icon, ((width - new_size[0]) // 2,
                        top_margin + (box_h - new_size[1]) // 2))

    mono = canvas.point(lambda p: 255 if p > 96 else 0, mode="1")

    if bake_text and font is not None:
        # Text erst nach dem Schwellwert zeichnen, sonst frisst ihn die
        # Quantisierung bei kleinen Schriftgraden auf.
        draw = ImageDraw.Draw(mono)
        bbox = font.getbbox(text)
        tx = (width - (bbox[2] - bbox[0])) // 2 - bbox[0]
        draw.text((tx, height - text_height - bbox[1] + 1), text, font=font, fill=1)

    row_bytes = (width + 7) // 8
    out_bytes: list[int] = []
    px = mono.load()
    for y in range(height):
        for byte_i in range(row_bytes):
            value = 0
            for bit in range(8):
                x = byte_i * 8 + bit
                if x < width and px[x, y]:
                    value |= 1 << bit  # XBM ist LSB-first
            out_bytes.append(value)

    # Geschweifte Klammern sind Pflicht: platformio-custom.py reicht nur Werte
    # roh als Define durch, die mit "{" beginnen. Alles andere laeuft durch
    # StringifyMacro und wuerde als String-Literal statt als C-Array landen -
    # `static const uint8_t xbm[] = "0x00, ..."` ergibt dann Bildmuell.
    return "{" + ", ".join(f"0x{b:02x}" for b in out_bytes) + "}"
