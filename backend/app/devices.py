"""Geräte-Katalog - wird aus dem Firmware-Repo abgeleitet, nicht gepflegt.

Jede Board-Variante trägt in ihrer platformio.ini `custom_meshtastic_*`-Felder
(dieselben, aus denen Meshtastic seinen eigenen Flasher speist). Wir lesen die
aus und leiten den Splash-Mechanismus aus den build_flags ab. Dadurch stimmt der
Katalog automatisch mit dem gebauten FIRMWARE_REF überein.

Splash-Mechanismen:
  "png" - device-ui lädt /boot/logo.png aus dem LittleFS (Farb-TFT)
  "xbm" - USERPREFS_OEM_IMAGE_DATA via drawOEMIconScreen (OLED und E-Ink)
  None  - kein Display
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path

FIRMWARE_DIR = Path(os.environ.get("FIRMWARE_DIR", "/firmware"))

_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")
_REF = re.compile(r"\$\{([^.}]+)\.([^}]+)\}")
_DEFINE = re.compile(r"-D\s*([A-Za-z_][A-Za-z0-9_]*)(?:=(\S+))?")

# Meshtastic erkennt OLEDs zur Laufzeit per I2C-Scan - dafür gibt es kein
# Build-Flag. Deshalb ist "OLED" der Normalfall und nur TFT, E-Ink und ein
# explizites HAS_SCREEN=0 lassen sich aus den Flags ablesen.
_EINK_FLAGS = ("USE_EINK", "HAS_EINK")


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
    supported: bool = True
    support_level: int = 3
    tags: list[str] = field(default_factory=list)
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


def _collect_sections(root: Path) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    top = root / "platformio.ini"
    if top.exists():
        sections.update(_parse_ini(top))
    for pattern in ("variants/**/*.ini", "arch/**/*.ini"):
        for ini in sorted(root.glob(pattern)):
            for name, body in _parse_ini(ini).items():
                sections.setdefault(name, {}).update(body)
    return sections


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


def _classify(flags: str) -> tuple[str | None, str, int, int]:
    """(splash, Anzeigename des Displays, Breite, Höhe) aus den build_flags."""
    defines = _flags_to_defines(flags)

    def size(default_w: int, default_h: int) -> tuple[int, int]:
        raw = defines.get("DISPLAY_SIZE", "")
        match = re.fullmatch(r"(\d+)x(\d+)", raw)
        if match:
            return int(match.group(1)), int(match.group(2))
        try:
            return int(defines["TFT_WIDTH"]), int(defines["TFT_HEIGHT"])
        except (KeyError, ValueError):
            return default_w, default_h

    # HAS_TFT zuerst: TFT-Boards setzen teils zusätzlich HAS_SCREEN=0, weil das
    # Rendern über device-ui statt über das klassische Screen-Modul läuft.
    if defines.get("HAS_TFT") == "1":
        width, height = size(240, 320)
        return "png", "Farbdisplay", width, height

    if defines.get("HAS_SCREEN") == "0":
        return None, "kein Display", 0, 0

    if any(defines.get(flag, "0") != "0" for flag in _EINK_FLAGS):
        # Echte E-Ink-Grösse steht nicht in den Flags. 128x64 passt auf jedes
        # Panel, drawOEMIconScreen zentriert das Bild ohnehin.
        width, height = size(128, 64)
        return "xbm", "E-Ink", width, height

    return "xbm", "OLED", 128, 64


def _scan(firmware_ref: str | None) -> list[Device]:
    """Katalog fuer ein Ref. Ohne Ref (oder wenn der Export scheitert) wird der
    Arbeitsbaum gelesen."""
    if firmware_ref:
        with tempfile.TemporaryDirectory(prefix="mh-catalog-") as tmp:
            root = Path(tmp)
            if _export_ref(firmware_ref, root):
                return _scan_root(root)
    return _scan_root(FIRMWARE_DIR)


def _scan_root(root: Path) -> list[Device]:
    sections = _collect_sections(root)
    found: list[Device] = []

    for name, body in sections.items():
        if not name.startswith("env:"):
            continue
        display_name = body.get("custom_meshtastic_display_name")
        if not display_name:
            continue

        env = name[4:]
        flags = _resolve(sections, name, "build_flags")
        splash, display, width, height = _classify(flags)

        try:
            level = int(body.get("custom_meshtastic_support_level", "3"))
        except ValueError:
            level = 3

        found.append(Device(
            id=env,
            name=display_name,
            env=env,
            arch=body.get("custom_meshtastic_architecture", "unbekannt"),
            splash=splash,
            display=display,
            width=width,
            height=height,
            supported=body.get("custom_meshtastic_actively_supported", "").strip() == "true",
            support_level=level,
            tags=[t.strip() for t in body.get("custom_meshtastic_tags", "").split(",") if t.strip()],
        ))

    found.sort(key=lambda d: (not d.supported, d.support_level, d.name.lower()))
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
