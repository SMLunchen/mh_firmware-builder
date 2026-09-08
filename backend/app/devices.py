"""Geräte-Katalog - wird aus dem Firmware-Repo abgeleitet, nicht gepflegt.

Jede Board-Variante trägt in ihrer platformio.ini `custom_meshtastic_*`-Felder
(dieselben, aus denen Meshtastic seinen eigenen Flasher speist). Wir lesen die
aus und leiten den Splash-Mechanismus aus den build_flags ab. Dadurch stimmt der
Katalog automatisch mit dem gebauten FIRMWARE_REF überein.

Splash-Mechanismen:
  "png" - device-ui (LVGL) lädt /boot/logo.png aus dem LittleFS, farbig
  "xbm" - klassisches Screen-Modul, 1-Bit-Splash über USERPREFS_OEM_IMAGE_DATA
  None  - kein Display
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass, field

from . import fonts
from pathlib import Path

FIRMWARE_DIR = Path(os.environ.get("FIRMWARE_DIR", "/firmware"))

_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")
_REF = re.compile(r"\$\{([^.}]+)\.([^}]+)\}")
_INCLUDE_DIR = re.compile(r"-I\s*(variants/[A-Za-z0-9_./-]+)")
_H_DEFINE = re.compile(r"^\s*#define\s+([A-Z_][A-Z_0-9]*)\s+(\d+)\s*(?://.*)?$")
# Auch wertlose Defines zaehlen: "#define USE_EINK" entscheidet ueber die
# Schriftwahl, hat aber keine Zahl.
_H_DEFINE_BARE = re.compile(r"^\s*#define\s+([A-Z_][A-Z_0-9]*)\s*(?://.*)?$")
_H_IFDEF = re.compile(r"^\s*#\s*(ifdef|ifndef)\s+([A-Za-z_][A-Za-z_0-9]*)")
_H_IF_DEFINED = re.compile(r"^\s*#\s*if\s+(!?)defined\s*\(\s*([A-Za-z_][A-Za-z_0-9]*)\s*\)\s*$")
_H_IF_OTHER = re.compile(r"^\s*#\s*if\b")
_H_ELSE = re.compile(r"^\s*#\s*else\b")
_H_ENDIF = re.compile(r"^\s*#\s*endif\b")

_DEFINE = re.compile(r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=(\S+))?")

# Aus den Build-Flags laesst sich nur ablesen, WELCHER RENDERER laeuft, nicht
# welches Panel verbaut ist: HAS_TFT=1 bedeutet device-ui (LVGL, farbig), sonst
# rendert das klassische Screen-Modul in 1 Bit - auch auf Farb-Panels wie dem
# T-Deck. Das Label darf deshalb nicht "OLED" behaupten.
_EINK_FLAGS = ("USE_EINK", "HAS_EINK", "EINK_DISPLAY_MODEL")

# ScreenFonts.h schaltet bei diesen Treibern auf die groesseren Schriften um
# (FONT_SMALL wird dann ArialMT_Plain_16 statt _10). Das gilt nicht nur fuer
# E-Ink - der Heltec T114 etwa faellt ueber USE_ST7789 darunter.
_LARGE_FONT_FLAGS = (
    "USE_EINK", "ILI9341_DRIVER", "ILI9342_DRIVER", "ST7701_CS", "ST7735_CS",
    "ST7789_CS", "USE_ST7789", "HX8357_CS", "ILI9488_CS", "ST7796_CS",
    "USE_ST7796", "HACKADAY_COMMUNICATOR",
)


def _font_kind(flags: str, variant: dict[str, int], defines: dict[str, str]) -> str:
    if "DISPLAY_FORCE_SMALL_FONTS" in defines or "DISPLAY_FORCE_SMALL_FONTS" in variant:
        return "default"
    known = set(defines) | set(variant)
    return "eink" if known.intersection(_LARGE_FONT_FLAGS) else "default"

# Envs ohne custom_meshtastic_*-Felder, die trotzdem echte Hardware sind und
# angeboten werden sollen. Ohne diese Liste blieben sie unsichtbar, denn es gibt
# nichts, woraus sich ein Anzeigename ableiten liesse - und alle namenlosen Envs
# aufzunehmen wuerde native-/debug-Ziele mit einsammeln.
EXTRA_ENVS: dict[str, str] = {
    "rak4631_eink": "RAK WisBlock 4631 E-Ink (RAK14000)",
    "rak4631_eink_onrxtx": "RAK WisBlock 4631 E-Ink (RAK14000, on RX/TX)",
}


@dataclass
class Device:
    id: str
    name: str
    env: str
    arch: str
    splash: str | None
    display: str
    width: int = 0
    height: int = 0
    # Welche Schrift die Firmware fuer den Splash-Titel nimmt, und wieviel
    # Breite dafuer zur Verfuegung steht. Siehe ScreenFonts.h.
    splash_font: str = "default"
    splash_max_px: int = 0
    supported: bool = True
    support_level: int = 3
    board_level: str = "release"
    tags: list[str] = field(default_factory=list)
    # Erster Eintrag aus custom_meshtastic_images - Dateiname unter
    # /img/devices/ im Frontend, dieselben Grafiken wie im offiziellen Flasher.
    image: str = ""
    image_count: int = 0
    # custom_meshtastic_hw_model - Modellnummer aus dem Protobuf. Der offizielle
    # Flasher sortiert danach; das ergibt grob die Reihenfolge der Aufnahme
    # statt eines Alphabets, in dem Gaengiges untergeht.
    hw_model: int = 9999
    variant_label: str = ""
    # Klartext-Hinweis, was diese Variante ausmacht - abgeleitet aus den
    # Build-Flags, damit auch ohne Vorwissen erkennbar ist, wofuer z. B.
    # "rak4631_eth_gw" oder "_dbg" steht.
    note: str = ""
    notes: str = ""

    def as_dict(self) -> dict:
        d = asdict(self)
        d["has_display"] = self.splash is not None
        return d


# ------------------------------------------------------------------ Parser

def _parse_ini(path: Path) -> dict[str, dict[str, str]]:
    """Minimaler INI-Parser, der Fortsetzungszeilen zusammenzieht."""
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    key: str | None = None
    try:
        lines = path.read_text(errors="replace").splitlines()
    except OSError:
        return sections

    for raw in lines:
        line = raw.split(";")[0].rstrip()
        if not line.strip():
            continue
        match = _SECTION.match(line.strip())
        if match:
            current = {}
            sections[match.group(1)] = current
            key = None
            continue
        if current is None:
            continue
        if raw[:1] in (" ", "\t") and key:          # Fortsetzung
            current[key] += "\n" + line.strip()
        elif "=" in line:
            key, value = line.split("=", 1)
            key = key.strip()
            current[key] = value.strip()
    return sections


def _export_ref(firmware_ref: str, target: Path) -> bool:
    """platformio.ini + variants/ + arch/ eines Refs in ein Temp-Verzeichnis
    entpacken. Rein lesend - laeuft parallel zu einem Build, der im Arbeits-
    baum gerade eine andere Version ausgecheckt hat."""
    try:
        # git archive bricht ab, sobald ein Pfad im Ref fehlt - und "arch/"
        # gibt es erst ab bzw. nur in bestimmten Versionen. Deshalb vorher
        # nachsehen, was das Ref ueberhaupt enthaelt.
        listing = subprocess.run(
            ["git", "ls-tree", "--name-only", firmware_ref],
            cwd=FIRMWARE_DIR, capture_output=True, text=True, timeout=60,
        )
        if listing.returncode != 0:
            return False
        present = set(listing.stdout.split())
        wanted = [p for p in ("platformio.ini", "variants", "arch") if p in present]
        if not wanted:
            return False

        archive = subprocess.run(
            ["git", "archive", firmware_ref, *wanted],
            cwd=FIRMWARE_DIR, capture_output=True, timeout=120,
        )
        if archive.returncode != 0 or not archive.stdout:
            return False
        subprocess.run(["tar", "-x", "-C", str(target)],
                       input=archive.stdout, capture_output=True,
                       timeout=120, check=True)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def _collect_sections(root: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    """Sektionen plus Herkunftspfad je Sektion.

    Der Pfad traegt Information, die in keinem Flag steht: Boards unter
    variants/*/diy/ sind Selbstbau-Aufbauten. Ohne das hiessen der Xiao-Kit
    und sein DIY-Gegenstueck gleich.
    """
    sections: dict[str, dict[str, str]] = {}
    origin: dict[str, str] = {}
    top = root / "platformio.ini"
    if top.exists():
        for name, body in _parse_ini(top).items():
            sections.setdefault(name, {}).update(body)
            origin.setdefault(name, "platformio.ini")
    for pattern in ("variants/**/*.ini", "arch/**/*.ini"):
        for ini in sorted(root.glob(pattern)):
            rel = ini.relative_to(root).as_posix()
            for name, body in _parse_ini(ini).items():
                sections.setdefault(name, {}).update(body)
                origin.setdefault(name, rel)
    return sections, origin


def _resolve(sections: dict[str, dict[str, str]], section: str, key: str,
             depth: int = 0) -> str:
    """Wert inklusive `extends`-Kette und ${sektion.key}-Referenzen auflösen."""
    if depth > 6 or section not in sections:
        return ""
    body = sections[section]
    value = body.get(key, "")

    if not value and "extends" in body:
        for parent in body["extends"].replace(",", " ").split():
            inherited = _resolve(sections, parent, key, depth + 1)
            if inherited:
                return inherited
        return ""

    def expand(match: re.Match) -> str:
        return _resolve(sections, match.group(1), match.group(2), depth + 1)

    return _REF.sub(expand, value)


def _flags_to_defines(flags: str) -> dict[str, str]:
    """`-D NAME=WERT`-Paare als Dict. Verhindert Teilstring-Fehltreffer wie
    MESHTASTIC_USE_EINK_UI=0 auf USE_EINK."""
    return {m.group(1): (m.group(2) or "1") for m in _DEFINE.finditer(flags)}


def _variant_defines(root: Path, flags: str) -> dict[str, int]:
    """Zahlen-Defines aus der variant.h des Boards.

    Viele Boards legen ihre echte Displaygroesse nicht in die build_flags,
    sondern in variants/<arch>/<board>/variant.h - der Heltec T114 etwa hat
    dort TFT_WIDTH 240 / TFT_HEIGHT 135. Ohne das raten wir 128x64, und aus
    einer falschen Groesse folgt die falsche Aufloesungsklasse: die Firmware
    zeichnet USERPREFS_OEM_TEXT dann selbst, waehrend wir ihn zusaetzlich ins
    Bild backen - der Schriftzug stuende doppelt auf dem Schirm.
    """
    active_defines = set(_flags_to_defines(flags))
    out: dict[str, int] = {}

    for rel in _INCLUDE_DIR.findall(flags):
        header = root / rel / "variant.h"
        if not header.is_file():
            continue
        try:
            lines = header.read_text(errors="replace").splitlines()
        except OSError:
            continue

        # Minimaler Praeprozessor. Ohne ihn liest man Defines aus Bloecken, die
        # gar nicht uebersetzt werden: der T-Beam definiert TFT_WIDTH 480 in
        # "#ifdef USE_ST7796" fuer ein optionales Display - der normale Build
        # hat aber nur ein 128x64-OLED.
        stack: list[bool] = []
        for line in lines:
            if _H_ENDIF.match(line):
                if stack:
                    stack.pop()
                continue
            if _H_ELSE.match(line):
                if stack:
                    stack[-1] = not stack[-1]
                continue

            match = _H_IFDEF.match(line)
            if match:
                known = match.group(2) in active_defines or match.group(2) in out
                stack.append(known if match.group(1) == "ifdef" else not known)
                continue
            match = _H_IF_DEFINED.match(line)
            if match:
                known = match.group(2) in active_defines or match.group(2) in out
                stack.append(not known if match.group(1) == "!" else known)
                continue
            if _H_IF_OTHER.match(line):
                # Alles Komplexere werten wir nicht aus und ueberspringen den
                # Block lieber, als eine womoeglich falsche Groesse zu nehmen.
                stack.append(False)
                continue

            if all(stack):
                match = _H_DEFINE.match(line)
                if match:
                    out.setdefault(match.group(1), int(match.group(2)))
                    continue
                bare = _H_DEFINE_BARE.match(line)
                if bare:
                    out.setdefault(bare.group(1), 1)
    return out


def _extends_names(sections: dict[str, dict[str, str]], name: str,
                   depth: int = 0) -> set[str]:
    """Alle Sektionen der extends-Kette. InkHUD-Varianten erkennt man daran
    (extends = nrf52840_base, inkhud), nicht an einem Define."""
    if depth > 6 or name not in sections:
        return set()
    out: set[str] = set()
    for parent in sections[name].get("extends", "").replace(",", " ").split():
        out.add(parent)
        out |= _extends_names(sections, parent, depth + 1)
    return out


# Kurzbezeichnung der Variante - taucht im Namen auf, wenn mehrere Boards
# denselben Grundnamen tragen. Aus Flags abgeleitet, wo das traegt.
_FLAG_LABELS: list[tuple[str, str]] = [
    ("EBYTE_E22_900M33S", "E22-900M33S (33 dBm)"),
    ("EBYTE_E22_900M30S", "E22-900M30S (30 dBm)"),
    ("SEEED_XIAO_NRF_WIO_BTB", "Wio-BTB-Anschluss"),
    ("EINK_NOT_HIBERNATE", "Hardware-Rev. 1.1"),
    ("USE_SEMIHOSTING", "Debug"),
]

# ... und aus dem Env-Suffix, wo die Flags nichts hergeben.
_SUFFIX_LABELS: list[tuple[str, str]] = [
    ("_eth_gw_dbg", "Ethernet-Gateway, Debug"),
    ("_eth_gw", "Ethernet-Gateway"),
    ("_eink_onrxtx", "E-Paper an RX/TX"),
    ("_eink", "RAK14000 E-Paper"),
    ("-inkhud", "InkHUD"),
    ("_dbg", "Debug"),
    ("_i2c", "I2C"),
    ("-displayshield", "mit Display-Shield"),
]


def _variant_label(env: str, defines: dict[str, str], bases: set[str],
                   origin: str = "") -> str:
    """Knappe Kennzeichnung, was diese Variante von den Geschwistern trennt.

    Zusammengesetzt aus bis zu zwei Merkmalen, vom Spezifischen zum
    Allgemeinen: Funkmodul vor Bauform vor "DIY". Ein pauschales "DIY-Aufbau"
    zuerst wuerde die Xiao-Varianten wieder ununterscheidbar machen - sie
    liegen alle unter variants/*/diy/.
    """
    parts: list[str] = []

    if defines.get("HAS_TFT") == "1":
        parts.append("TFT")
    elif "inkhud" in bases:
        parts.append("InkHUD")

    # Suffix vor Flag: "_eth_gw_dbg" ist spezifischer als USE_SEMIHOSTING,
    # das sonst beide Debug-Varianten gleich benennen wuerde.
    for suffix, text in _SUFFIX_LABELS:
        if env.endswith(suffix):
            parts.append(text)
            break

    if not parts:
        for flag, text in _FLAG_LABELS:
            if defines.get(flag) == "1":
                parts.append(text)
                break

    if len(parts) < 2:
        # Kit-Aufbau gegen blankes Modul - trennt seeed_xiao_* von xiao_ble*
        if defines.get("SEEED_XIAO_NRF52840_KIT") == "1":
            parts.append("Kit-Aufbau")
        elif "/diy/" in origin:
            parts.append("DIY-Aufbau")

    return ", ".join(parts[:2])


# Varianten-Suffixe, die sich nicht zuverlaessig aus Flags ableiten lassen.
# Der Ethernet-Aufbau etwa steckt im Variantencode, nicht in einem Define.
_SUFFIX_NOTES: list[tuple[str, str]] = [
    ("_eth_gw_dbg", "Ethernet-Gateway, Debug-Build"),
    ("_eth_gw", "Ethernet-Gateway (RAK13800)"),
    ("_eink_onrxtx", "E-Paper an RX/TX verdrahtet"),
    ("_eink", "mit RAK14000 E-Paper"),
    ("-inkhud", "InkHUD-Oberfläche"),
    ("_dbg", "Debug-Build"),
]


def _describe(env: str, defines: dict[str, str], bases: set[str],
              splash: str | None) -> str:
    """Kurzer Klartext-Hinweis, was diese Variante ausmacht.

    Bewusst knapp und nur Belegbares: Angaben wie "N Zusatzmodule entfernt"
    stehen auf fast jedem nRF52-Board und unterscheiden nichts.
    """
    parts: list[str] = []

    if defines.get("HAS_TFT") == "1":
        parts.append("farbige Oberfläche mit Menüs")
    elif "inkhud" in bases:
        parts.append("InkHUD-Oberfläche")

    for flag, text in _FLAG_LABELS:
        if defines.get(flag) == "1" and text not in parts and flag != "USE_SEMIHOSTING":
            parts.append(text)
            break

    for suffix, text in _SUFFIX_NOTES:
        if env.endswith(suffix) and text not in parts:
            parts.append(text)
            break

    if splash is None and "ohne Display" not in " ".join(parts):
        parts.append("ohne Display")
    if defines.get("USE_SEMIHOSTING") == "1" and not any("Debug" in x for x in parts):
        parts.append("Debug-Build")
    if any("Debug" in x for x in parts):
        parts.append("nicht für den normalen Betrieb")

    return " · ".join(parts)


def _classify(flags: str, variant: dict[str, int] | None = None) -> tuple[str | None, str, int, int]:
    """(splash, Anzeigename des Displays, Breite, Höhe) aus den build_flags."""
    defines = _flags_to_defines(flags)
    variant = variant or {}

    def size(default_w: int, default_h: int) -> tuple[int, int]:
        raw = defines.get("DISPLAY_SIZE", "")
        match = re.fullmatch(r"(\d+)x(\d+)", raw)
        if match:
            return int(match.group(1)), int(match.group(2))
        for w_key, h_key in (("TFT_WIDTH", "TFT_HEIGHT"),
                             ("SCREEN_WIDTH", "SCREEN_HEIGHT")):
            try:
                return int(defines[w_key]), int(defines[h_key])
            except (KeyError, ValueError):
                pass
            if w_key in variant and h_key in variant:
                return variant[w_key], variant[h_key]
        return default_w, default_h

    # HAS_TFT zuerst: TFT-Boards setzen teils zusätzlich HAS_SCREEN=0, weil das
    # Rendern über device-ui statt über das klassische Screen-Modul läuft.
    if defines.get("HAS_TFT") == "1":
        width, height = size(240, 320)
        return "png", "Farbdisplay", width, height

    # HAS_SCREEN=0 und MESHTASTIC_EXCLUDE_SCREEN schliessen nur das klassische
    # Screen-Modul aus. Zusammen mit HAS_TFT ist das normal (device-ui rendert),
    # ohne bedeutet es wirklich: kein Display.
    if (defines.get("HAS_SCREEN") == "0"
            or defines.get("MESHTASTIC_EXCLUDE_SCREEN") == "1"):
        return None, "kein Display", 0, 0

    if any(defines.get(flag, "0") != "0" for flag in _EINK_FLAGS):
        # Manche E-Ink-Varianten nennen ihre echte Panelgroesse in
        # EINK_WIDTH/EINK_HEIGHT (z. B. rak4631_eink mit 250x122). Wo das fehlt,
        # ist 128x64 der sichere Rueckfall - drawOEMIconScreen zentriert, ein
        # kleineres Bild passt also immer.
        try:
            width = int(defines["EINK_WIDTH"])
            height = int(defines["EINK_HEIGHT"])
        except (KeyError, ValueError):
            if "EINK_WIDTH" in variant and "EINK_HEIGHT" in variant:
                width, height = variant["EINK_WIDTH"], variant["EINK_HEIGHT"]
            else:
                width, height = size(128, 64)
        return "xbm", "E-Ink", width, height

    # Auch hier die echte Groesse nehmen, wenn sie bekannt ist: der Heltec T114
    # faellt in diesen Zweig, hat aber ein 240x135-Panel. Aus 128x64 folgte
    # sonst faelschlich "low" - und damit doppelter Text, weil die Firmware bei
    # "high" den Titel selbst zeichnet.
    width, height = size(128, 64)
    return "xbm", "Standard-UI", width, height


def _scan(firmware_ref: str | None) -> list[Device]:
    """Katalog fuer ein Ref. Ohne Ref (oder wenn der Export scheitert) wird der
    Arbeitsbaum gelesen."""
    if firmware_ref:
        with tempfile.TemporaryDirectory(prefix="mh-catalog-") as tmp:
            root = Path(tmp)
            if _export_ref(firmware_ref, root):
                return _scan_root(root)
    return _scan_root(FIRMWARE_DIR)


_ARCH_BASE = re.compile(r"^(nrf5\d+|esp32[a-z0-9]*|rp\d+)_base$")


def _infer_arch(sections: dict[str, dict[str, str]], name: str, depth: int = 0) -> str:
    """Architektur aus der extends-Kette ableiten.

    Envs wie rak4631_eink tragen kein custom_meshtastic_architecture. Ihre Basis
    heisst aber nach der Architektur ([nrf52840_base] in
    variants/nrf52840/nrf52840.ini). Ohne das bliebe arch "unbekannt" - und
    collect() entscheidet daran, ob ein ESP32-Partitionsmanifest gebaut wird.
    """
    if depth > 6 or name not in sections:
        return ""
    for parent in sections[name].get("extends", "").replace(",", " ").split():
        match = _ARCH_BASE.match(parent)
        if match:
            raw = match.group(1)
            if raw.startswith("esp32") and len(raw) > 5:
                return f"esp32-{raw[5:]}"      # esp32s3 -> esp32-s3
            return raw
        inherited = _infer_arch(sections, parent, depth + 1)
        if inherited:
            return inherited
    return ""


def _variant_suffix(sections: dict[str, dict[str, str]], name: str, env: str) -> str:
    """Unterscheidungszusatz fuer geerbte Anzeigenamen.

    t-deck und t-deck-tft haetten sonst denselben Namen. Der Zusatz kommt aus
    dem, was der Env-Name gegenueber dem geerbten Env mehr hat.
    """
    parent = sections.get(name, {}).get("extends", "").strip()
    if parent.startswith("env:"):
        base = parent[4:]
        if env.startswith(base) and len(env) > len(base):
            extra = env[len(base):].strip("-_")
            if extra:
                return f" ({extra.upper()})"
    # Ohne ableitbaren Zusatz die Env-Kennung nehmen. Ein pauschales "(TFT)"
    # waere schlicht falsch - die meisten dieser Varianten sind keine.
    return f" ({env})"


def _scan_root(root: Path) -> list[Device]:  # noqa: C901
    sections, origin = _collect_sections(root)
    found: list[Device] = []

    for name, body in sections.items():
        if not name.startswith("env:"):
            continue

        # Varianten wie t-deck-tft tragen keine eigenen custom_meshtastic_*-
        # Felder, sondern erben sie ueber "extends = env:t-deck". Wer nur das
        # eigene Feld liest, verliert genau die device-ui-Builds.
        env = name[4:]
        display_name = body.get("custom_meshtastic_display_name")
        inherited = False
        if not display_name:
            display_name = _resolve(sections, name, "custom_meshtastic_display_name")
            inherited = bool(display_name)
        if not display_name:
            display_name = EXTRA_ENVS.get(env, "")
            inherited = False
        if not display_name:
            continue


        flags = _resolve(sections, name, "build_flags")
        variant = _variant_defines(root, flags)
        splash, display, width, height = _classify(flags, variant)
        board_defines = _flags_to_defines(flags)
        font_kind = _font_kind(flags, variant, board_defines)
        bases = _extends_names(sections, name)
        note = _describe(env, board_defines, bases, splash)
        variant_label = _variant_label(env, board_defines, bases,
                                       origin.get(name, ""))

        images = [x.strip() for x in (
            body.get("custom_meshtastic_images")
            or _resolve(sections, name, "custom_meshtastic_images") or ""
        ).split(",") if x.strip()]

        try:
            hw_model = int(body.get("custom_meshtastic_hw_model")
                           or _resolve(sections, name, "custom_meshtastic_hw_model")
                           or 9999)
        except ValueError:
            hw_model = 9999

        try:
            level = int(body.get("custom_meshtastic_support_level")
                        or _resolve(sections, name, "custom_meshtastic_support_level") or 3)
        except ValueError:
            level = 3

        found.append(Device(
            id=env,
            name=display_name,
            env=env,
            # Auch die Architektur kann geerbt sein. Sie steuert in collect(),
            # ob ein ESP32-Partitionsmanifest gebaut wird - "unbekannt" fuehrte
            # zu einem leeren Manifest bei erfolgreichem Build.
            arch=(body.get("custom_meshtastic_architecture")
                  or _resolve(sections, name, "custom_meshtastic_architecture")
                  or _infer_arch(sections, name)
                  or "unbekannt"),
            splash=splash,
            display=display,
            width=width,
            height=height,
            splash_font=font_kind,
            # 4 px Rand je Seite, damit zentrierter Text nicht am Rand klebt
            splash_max_px=max(0, width - 8),
            supported=_resolve(sections, name, "custom_meshtastic_actively_supported").strip() == "true",
            support_level=level,
            board_level=(body.get("board_level")
                         or _resolve(sections, name, "board_level") or "release").strip(),
            image=images[0] if images else "",
            image_count=len(images),
            hw_model=hw_model,
            note=note,
            variant_label=variant_label,
            tags=[x.strip() for x in (
                body.get("custom_meshtastic_tags")
                or _resolve(sections, name, "custom_meshtastic_tags") or ""
            ).split(",") if x.strip()],
        ))

    # Wo es eine "-tft"-Schwester gibt, ist die Wahl erklaerungsbeduerftig:
    # dasselbe Geraet, aber klassische Schwarzweiss-Oberflaeche gegen device-ui.
    ids = {d.id for d in found}
    for device in found:
        if (f"{device.id}_v1_1" in ids or f"{device.id}-v1_1" in ids) \
                and not device.variant_label:
            device.variant_label = "ursprüngliche Revision"
        if f"{device.id}-tft" in ids:
            if not device.note:
                device.note = ("klassische schwarzweiße Oberfläche — "
                               "die TFT-Variante bietet die farbige Bedienung")
            if not device.variant_label:
                device.variant_label = "klassisch"

    # Namen eindeutig machen. Sieben Eintraege "Seeed Xiao NRF52840 Kit" sind
    # nicht auswaehlbar - und die Env-Kennung dahinterzusetzen hilft nur, wer
    # sie ohnehin kennt. Deshalb zuerst die sprechende Kurzbezeichnung.
    counts: dict[str, int] = {}
    for device in found:
        counts[device.name] = counts.get(device.name, 0) + 1
    for device in found:
        if counts[device.name] < 2:
            continue
        # Ohne eigenes Merkmal ist es die Grundvariante. "Standard" sagt das
        # jedem; die Env-Kennung nur denen, die sie ohnehin kennen.
        label = device.variant_label or "Standard"
        device.name = f"{device.name} — {label}"

    # Falls die Kurzbezeichnung nicht reichte, doch die Env-Kennung anhaengen.
    counts = {}
    for device in found:
        counts[device.name] = counts.get(device.name, 0) + 1
    for device in found:
        if counts[device.name] > 1:
            device.name = f"{device.name} ({device.env})"

    # Reihenfolge wie im offiziellen Flasher (deviceStore.ts:sortedDevices):
    # nach support_level 1, 2, 3 gruppiert, innerhalb aufsteigend nach hw_model,
    # bei Gleichstand nach Anzahl der Grafiken.
    found.sort(key=lambda d: (min(d.support_level, 3), d.hw_model, d.image_count,
                              d.name.lower()))
    return found


# ------------------------------------------------------------------- Cache

_catalogs: dict[str, list[Device]] = {}
_lock = threading.Lock()
_MAX_CACHED = 8


def for_ref(firmware_ref: str) -> list[Device]:
    """Katalog fuer eine konkrete Firmware-Version, gecacht."""
    with _lock:
        if firmware_ref in _catalogs:
            return _catalogs[firmware_ref]
    catalog = _scan(firmware_ref)
    with _lock:
        if len(_catalogs) >= _MAX_CACHED:
            _catalogs.pop(next(iter(_catalogs)))
        _catalogs[firmware_ref] = catalog
    return catalog


def get(device_id: str, firmware_ref: str) -> Device:
    for device in for_ref(firmware_ref):
        if device.id == device_id:
            return device
    raise KeyError(f"Unbekanntes Gerät für {firmware_ref}: {device_id}")


def ready(firmware_ref: str) -> bool:
    return bool(for_ref(firmware_ref))
