"""Admin-Anpassungen am Gerätekatalog.

Zwei Dinge lassen sich hier festlegen und werden in DATA_DIR/catalog.json
gehalten:

* **Ausgeblendete Boards** - Modelle, die im öffentlichen Katalog nicht
  erscheinen sollen. Der Firmware-Katalog kennt 141 Boards; die wenigsten
  davon sind für ein konkretes Netz relevant.
* **Aliase** - eigene Einträge mit verständlichem Namen und eigenem Bild, die
  auf ein vorhandenes Board zeigen. "Heltec WiFi LoRa 32 Expansion Kit V2 mit
  LoRa 32 V4-R8" ist der Name, den Leute auf der Verpackung lesen;
  "heltec-v4-r8-tft" ist er nicht.

Ein Alias baut dieselbe Firmware wie sein Ziel und teilt sich dessen
Cache-Eintrag - sonst würde für jeden Anzeigenamen dieselbe Firmware erneut
kompiliert.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import secrets
import threading
from pathlib import Path

from PIL import Image

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
CATALOG_FILE = DATA_DIR / "catalog.json"
UPLOAD_DIR = DATA_DIR / "uploads"

_lock = threading.Lock()

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{1,48}$")
_UPLOAD_NAME = re.compile(r"^[0-9a-f]{16}$")
ALLOWED_IMAGE_TYPES = {
    "image/svg+xml": ".svg",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}
# Rohgroesse beim Hochladen. Gerätefotos liegen regelmaessig bei mehreren MB -
# eine enge Grenze hier hiesse nur, dass der Upload scheitert.
MAX_UPLOAD_BYTES = 8 * 1024 * 1024
# SVG ist Text und wird nicht skaliert, deshalb eigene Grenze.
MAX_SVG_BYTES = 512 * 1024
# Kantenlaenge, auf die Rastergrafiken heruntergerechnet werden. Die Kacheln
# zeigen sie mit 120 px Hoehe; mehr als das Doppelte bringt nichts.
IMAGE_BOX = 512


def _empty() -> dict:
    return {"disabled": [], "aliases": []}


def load() -> dict:
    with _lock:
        if not CATALOG_FILE.exists():
            return _empty()
        try:
            raw = json.loads(CATALOG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return _empty()
    data = _empty()
    data["disabled"] = [str(x) for x in raw.get("disabled", []) if isinstance(x, str)]
    for alias in raw.get("aliases", []):
        if isinstance(alias, dict) and alias.get("id") and alias.get("target"):
            data["aliases"].append({
                "id": str(alias["id"]),
                "name": str(alias.get("name") or alias["id"]),
                "target": str(alias["target"]),
                "image": str(alias.get("image") or ""),
                "note": str(alias.get("note") or ""),
            })
    return data


def save(data: dict) -> dict:
    clean = _empty()
    clean["disabled"] = sorted({str(x) for x in data.get("disabled", []) if isinstance(x, str)})
    seen: set[str] = set()
    for alias in data.get("aliases", []):
        if not isinstance(alias, dict):
            continue
        alias_id = str(alias.get("id", "")).strip().lower()
        target = str(alias.get("target", "")).strip()
        name = str(alias.get("name", "")).strip()
        if not _ID.match(alias_id) or not target or not name or alias_id in seen:
            continue
        seen.add(alias_id)
        clean["aliases"].append({
            "id": alias_id,
            "name": name,
            "target": target,
            "image": str(alias.get("image") or "").strip(),
            "note": str(alias.get("note") or "").strip(),
        })
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        CATALOG_FILE.write_text(json.dumps(clean, indent=2, ensure_ascii=False))
    return clean


def store_image(content: bytes, content_type: str) -> str:
    """Bild ablegen und die Referenz ("upload:<name>") zurueckgeben.

    Rastergrafiken werden auf IMAGE_BOX heruntergerechnet und als PNG neu
    geschrieben. Damit ist es gleich, wie gross das Original war - ein
    Handyfoto mit 4 MB landet als wenige Dutzend KB im Datenverzeichnis.
    """
    suffix = ALLOWED_IMAGE_TYPES.get(content_type)
    if suffix is None:
        raise ValueError("Nur SVG, PNG, JPEG oder WebP.")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    if suffix == ".svg":
        if len(content) > MAX_SVG_BYTES:
            raise ValueError(f"SVG ist groesser als {MAX_SVG_BYTES // 1024} KB.")
        name = f"{secrets.token_hex(8)}.svg"
        (UPLOAD_DIR / name).write_bytes(content)
        return f"upload:{name}"

    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except Exception as exc:  # noqa: BLE001 - Pillow wirft sehr verschiedenes
        raise ValueError("Datei liess sich nicht als Bild lesen.") from exc

    image = image.convert("RGBA")
    image.thumbnail((IMAGE_BOX, IMAGE_BOX), Image.LANCZOS)
    name = f"{secrets.token_hex(8)}.png"
    image.save(UPLOAD_DIR / name, "PNG", optimize=True)
    return f"upload:{name}"


def export() -> dict:
    """Katalog samt Bildern als eine Datei - fuer den Umzug auf einen anderen
    Server. Die Bilder liegen im Docker-Volume und wuerden sonst fehlen."""
    data = load()
    images: dict[str, str] = {}
    for alias in data["aliases"]:
        ref = alias.get("image", "")
        if not ref.startswith("upload:"):
            continue
        path = UPLOAD_DIR / ref[7:]
        if path.is_file():
            images[ref[7:]] = base64.b64encode(path.read_bytes()).decode()
    return {"version": 1, "catalog": data, "images": images}


def import_(payload: dict) -> dict:
    """Gegenstueck zu export(). Bilder werden unter ihrem urspruenglichen
    Namen wiederhergestellt, damit die Referenzen in den Aliasen stimmen."""
    raw = payload.get("catalog")
    if not isinstance(raw, dict):
        raise ValueError("Keine Katalogdaten in der Datei.")

    images = payload.get("images") or {}
    if isinstance(images, dict):
        UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        for name, encoded in images.items():
            if not _UPLOAD_NAME.match(Path(name).stem) or Path(name).suffix not in (
                    ".svg", ".png", ".jpg", ".webp"):
                continue
            try:
                (UPLOAD_DIR / Path(name).name).write_bytes(base64.b64decode(encoded))
            except (ValueError, TypeError, OSError):
                continue

    return save(raw)


def resolve(device_id: str) -> str:
    """Alias-Kennung auf das Ziel-Board abbilden, sonst unveraendert lassen."""
    for alias in load()["aliases"]:
        if alias["id"] == device_id:
            return alias["target"]
    return device_id


def apply(devices: list, include_hidden: bool = False) -> list:
    """Ausblendungen und Aliase auf einen Gerätekatalog anwenden.

    `include_hidden` liefert alles inklusive der ausgeblendeten Boards - das
    braucht der Admin-Bereich, um sie wieder einschalten zu können.
    """
    data = load()
    disabled = set(data["disabled"])
    by_id = {d.id: d for d in devices}

    out = []
    for device in devices:
        if device.id in disabled and not include_hidden:
            continue
        out.append(device)

    for alias in data["aliases"]:
        target = by_id.get(alias["target"])
        if target is None:
            continue  # Ziel gibt es in dieser Firmware-Version nicht
        entry = type(target)(**{**target.__dict__})
        entry.id = alias["id"]
        entry.name = alias["name"]
        if alias["image"]:
            entry.image = alias["image"]
        entry.note = alias["note"] or target.note
        out.append(entry)

    return out
