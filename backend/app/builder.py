"""Build-Orchestrierung: Firmware-Repo, userPrefs, Splash, PlatformIO, Cache.

Wichtig beim Artefakt-Manifest: wir liefern bewusst *keine* factory.bin aus.
Sie endet bei rund 3,7 MB, das LittleFS-Image liegt aber bei 0xc90000 (13,2 MB)
und ist darin schlicht nicht enthalten. Ein Gerät, das nur mit factory.bin
geflasht wird, hat danach ein leeres Dateisystem - und damit keinen Splash.

Stattdessen flashen wir die Einzelteile an ihren echten Offsets aus der
Partitionstabelle des Boards. boot_app0.bin ist dabei die Stock-Datei von
Arduino-ESP32 (ota_seq=1 -> app0); sie steckt inhaltsgleich auch in der
factory.bin und wird hier nur explizit mitgeführt.
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from . import devices, logo, settings, versions

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
FIRMWARE_DIR = Path(os.environ.get("FIRMWARE_DIR", "/firmware"))
CACHE_DIR = DATA_DIR / "cache"
ASSET_LOGO = Path(os.environ.get("LOGO_PATH", "/assets/meshhessen_logo.png"))
# Fuer 1-Bit-Displays braucht es Strichgrafik. Das volle Logo mit Schriftzug
# zerfaellt bei 128x64 zu Rauschen.
ASSET_ICON = Path(os.environ.get("ICON_PATH", "/assets/meshhessen_icon.png"))
BASE_PREFS = Path(__file__).with_name("userprefs.base.jsonc")

FIRMWARE_REPO = os.environ.get(
    "FIRMWARE_REPO", "https://github.com/meshtastic/firmware.git")

# Vorschlag fuer die Versionsauswahl. Darf ein exakter Tag, eine Minor-Reihe
# ("2.7" -> neuester 2.7er) oder "latest" sein.
DEFAULT_FIRMWARE_REF = os.environ.get("FIRMWARE_REF", "2.7")

BUILD_TIMEOUT = int(os.environ.get("BUILD_TIMEOUT", "3600"))

# Fliesst in den Cache-Key ein. Hochzaehlen, sobald sich die Splash-Erzeugung
# aendert - sonst liefert der Cache Artefakte, die noch mit der alten Logik
# gebaut wurden. Asset-Hashes allein reichen dafuer nicht.
SPLASH_GENERATION = 6

# PlatformIO liegt im Container in einem eigenen venv (Starlette-Konflikt mit
# FastAPI). Lokal ohne die Variable greift das platformio vom PATH.
PIO_BIN = os.environ.get("PIO_BIN", "platformio")

# ------------------------------------------------------------------ Zustand


@dataclass
class Build:
    id: str
    device_id: str
    display_name: str
    firmware_ref: str
    cache_key: str
    personalized: bool
    status: str = "queued"          # queued | running | done | error
    error: str | None = None
    started: float = field(default_factory=time.time)
    finished: float | None = None
    log: list[str] = field(default_factory=list)
    manifest: dict | None = None
    _subscribers: list[queue.Queue] = field(default_factory=list)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def emit(self, line: str) -> None:
        with self._lock:
            self.log.append(line)
            if len(self.log) > 5000:
                del self.log[:1000]
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.put(line)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for line in self.log:
                q.put(line)
            if self.status in ("done", "error"):
                # Fertiger Build: close_streams() ist laengst gelaufen und wird
                # niemanden mehr wecken. Ohne dieses None haengt ein spaeter
                # dazukommender Client nach dem Verlauf endlos - etwa wenn die
                # Seite nach dem Build neu geladen wird.
                q.put(None)
            else:
                self._subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self._subscribers:
                self._subscribers.remove(q)

    def close_streams(self) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for sub in subscribers:
            sub.put(None)

    def public(self) -> dict:
        return {
            "id": self.id,
            "device": self.device_id,
            "status": self.status,
            "error": self.error,
            "personalized": self.personalized,
            "firmware_ref": self.firmware_ref,
            "cache_key": self.cache_key,
            "elapsed": round((self.finished or time.time()) - self.started, 1),
            "manifest": self.manifest,
        }


BUILDS: dict[str, Build] = {}
_builds_lock = threading.Lock()
_build_serializer = threading.Lock()   # PlatformIO-Läufe nacheinander


# ---------------------------------------------------------------- Cache-Key

def cache_key(device_id: str, firmware_ref: str, splash_text: str,
              overrides: dict) -> str:
    # Beide Bildquellen fliessen ein: tauscht eine davon, muss neu gebaut
    # werden, sonst bliebe ein alter Splash im Cache haengen.
    digest = hashlib.sha256()
    for asset in (ASSET_LOGO, ASSET_ICON):
        if asset.exists():
            digest.update(asset.read_bytes())
    logo_hash = digest.hexdigest()[:12]
    payload = json.dumps({
        "device": device_id,
        "ref": firmware_ref,
        "gen": SPLASH_GENERATION,
        "splash": splash_text,
        "overrides": overrides,
        "logo": logo_hash,
    }, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def cached_manifest(key: str) -> dict | None:
    path = CACHE_DIR / key / "manifest.json"
    if path.exists():
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            return None
    return None


# --------------------------------------------------------------- Repo-Setup

def _run(cmd: list[str], cwd: Path, emit, env: dict | None = None) -> tuple[int, str]:
    """Kommando ausfuehren, Ausgabe streamen und die letzten Zeilen zurueckgeben."""
    emit(f"$ {' '.join(cmd)}")
    proc = subprocess.Popen(
        cmd, cwd=str(cwd), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1, env={**os.environ, **(env or {})},
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for line in proc.stdout:
        line = line.rstrip("\n")
        emit(line)
        tail.append(line)
        if len(tail) > 200:
            del tail[:100]
    return proc.wait(), "\n".join(tail)


# pioarduino entscheidet manchmal, das Arduino-Framework neu zu installieren,
# und stolpert dabei ueber die eigene Fuesse: FRAMEWORK_DIR ist None, exists()
# bekommt kein Pfadobjekt. Nach dem Durchlauf ist die Installation vollstaendig,
# ein zweiter Anlauf geht durch. Trat bei jedem Wechsel der Plattformversion auf.
_TRANSIENT_MARKERS = (
    "Reinstall Arduino framework",
    "safe_framework_cleanup",
    "path should be string, bytes, os.PathLike or integer, not NoneType",
)


def _is_transient_framework_error(output: str) -> bool:
    return sum(marker in output for marker in _TRANSIENT_MARKERS) >= 2


def ensure_firmware(firmware_ref: str, emit) -> None:
    """Repo klonen bzw. auf den gewuenschten Tag setzen. Idempotent."""
    if not (FIRMWARE_DIR / ".git").exists():
        FIRMWARE_DIR.mkdir(parents=True, exist_ok=True)
        emit(f"Klone {FIRMWARE_REPO} ...")
        if _run(["git", "clone", "--recursive", FIRMWARE_REPO, "."],
                FIRMWARE_DIR, emit)[0] != 0:
            raise RuntimeError("git clone fehlgeschlagen")

    current = subprocess.run(["git", "describe", "--tags", "--always"],
                             cwd=FIRMWARE_DIR, capture_output=True, text=True)
    if current.stdout.strip() == firmware_ref:
        emit(f"Firmware-Repo steht bereits auf {firmware_ref}")
        return

    emit(f"Wechsle auf {firmware_ref} ...")
    _run(["git", "fetch", "--tags", "--force"], FIRMWARE_DIR, emit)
    _run(["git", "checkout", "--force", firmware_ref], FIRMWARE_DIR, emit)
    _run(["git", "submodule", "update", "--init", "--recursive"], FIRMWARE_DIR, emit)


# ------------------------------------------------------------- userPrefs

# Marker und Einschub fuer den device-ui-Patch (siehe patch_device_ui).
_PIN_MARKER = 'lv_label_set_text_fmt(objects.firmware_label, "%06d"'
_PIN_FIX = "lv_obj_remove_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);"


def install_deps(device: devices.Device, emit) -> None:
    """Bibliotheken aufloesen, damit .pio/libdeps vor dem Patchen existiert."""
    code, _ = _run([PIO_BIN, "pkg", "install", "-e", device.env],
                   FIRMWARE_DIR, emit)
    if code != 0:
        emit("WARNUNG: pkg install fehlgeschlagen - Patch wird uebersprungen")


def patch_device_ui(device: devices.Device, emit) -> bool:
    """device-ui so korrigieren, dass die BLE-PIN auch bei vollem Logo erscheint.

    TFTView versteckt beim Booten firmware_label, wenn das Logo hoeher als der
    halbe Bildschirm ist. Dasselbe Label traegt spaeter die Pairing-PIN, und der
    Programming-Mode-Zweig macht das Verstecken nicht rueckgaengig. Dort ist das
    Logo ohnehin ausgeblendet - das Label gehoert also sichtbar.

    Rueckgabe: True, wenn der Fix sitzt. Nur dann darf das Logo bildschirm-
    fuellend sein; sonst faellt der Aufrufer auf halbe Hoehe zurueck.
    """
    lib = FIRMWARE_DIR / ".pio" / "libdeps" / device.env / "meshtastic-device-ui"
    sources = sorted(lib.glob("source/graphics/TFT/TFTView_*.cpp"))
    if not sources:
        emit("device-ui nicht gefunden - Logo bleibt auf halber Hoehe")
        return False

    patched_any = False
    for source in sources:
        text = source.read_text(errors="replace")
        if _PIN_FIX in text:
            patched_any = True
            continue
        if _PIN_MARKER not in text:
            continue

        out = []
        for line in text.splitlines(keepends=True):
            out.append(line)
            if _PIN_MARKER in line:
                indent = line[: len(line) - len(line.lstrip())]
                out.append(f"{indent}{_PIN_FIX}\n")
        source.write_text("".join(out))
        emit(f"  {source.name} gepatcht: firmware_label im Programming Mode "
             "wieder eingeblendet")
        patched_any = True

    if not patched_any:
        emit("Erwartete Stelle in device-ui nicht gefunden - Logo bleibt auf "
             "halber Hoehe, damit die BLE-PIN sichtbar bleibt")
    return patched_any


def write_userprefs(device: devices.Device, splash_text: str,
                    overrides: dict, emit) -> None:
    """Basis-userPrefs + Admin-Overrides + Splash-Konfiguration schreiben."""
    site_prefix_fallback = settings.load_site()["splash_prefix"]
    prefs: dict[str, str] = {}
    for raw in BASE_PREFS.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if '"' not in line:
            continue
        try:
            key = line.split('"')[1]
            value = line.split(":", 1)[1].strip().rstrip(",").strip()
            if value.startswith('"') and value.endswith('"'):
                value = value[1:-1]
            prefs[key] = value
        except IndexError:
            continue

    # Splash-relevante Keys neu setzen
    for key in list(prefs):
        if key.startswith("USERPREFS_OEM_"):
            del prefs[key]

    if device.splash == "xbm":
        firmware_draws_text = logo.draws_oem_text(device.width, device.height)
        emit(f"Erzeuge {device.display}-Splash ({device.width}x{device.height} XBM, "
             f"{logo.screen_resolution(device.width, device.height)}) ...")
        if not firmware_draws_text and splash_text:
            emit("  Schriftzug wird ins Bild gebacken - drawOEMIconScreen "
                 "zeichnet USERPREFS_OEM_TEXT nur auf High-Res-Displays.")
        prefs["USERPREFS_OEM_IMAGE_DATA"] = logo.render_oled_xbm(
            ASSET_ICON, device.width, device.height, splash_text)
        prefs["USERPREFS_OEM_IMAGE_WIDTH"] = str(device.width)
        prefs["USERPREFS_OEM_IMAGE_HEIGHT"] = str(device.height)
        prefs["USERPREFS_OEM_FONT_SIZE"] = "0"
        # MUSS immer gesetzt sein: Screen.cpp klammert den kompletten
        # OEM-Bootscreen in "#ifdef USERPREFS_OEM_TEXT". Ohne das Define wird
        # drawOEMBootScreen nie eingeplant und es erscheint gar kein Splash.
        # Doppelter Text droht nicht - drawOEMIconScreen zeichnet den Titel
        # ohnehin nur bei High.
        prefs["USERPREFS_OEM_TEXT"] = splash_text or site_prefix_fallback

    prefs.update(settings.overrides_to_prefs(overrides))

    body = ",\n".join(f'  "{k}": "{v}"' for k, v in sorted(prefs.items()))
    (FIRMWARE_DIR / "userPrefs.jsonc").write_text("{\n" + body + "\n}\n")
    emit(f"userPrefs.jsonc geschrieben ({len(prefs)} Werte)")


def write_tft_logo(device: devices.Device, splash_text: str, emit,
                   full_height: bool = False) -> None:
    """Farb-Splash an die von der Firmware vorgesehene Stelle legen.

    bin/platformio-custom.py registriert fuer HAS_TFT-Builds die Vorabaktion
    load_boot_logo(): sie kopiert branding/logo_<breite>x<hoehe>.png nach
    data/boot/logo.png, bevor das LittleFS-Image gebaut wird. Im Image liegt
    die Datei dann als /boot/logo.png - genau der Pfad, den device-ui in
    FileLoader::loadBootImage() oeffnet.

    Der Dateiname muss DISPLAY_SIZE des Boards treffen, sonst findet
    load_boot_logo() nichts und kopiert stillschweigend gar nichts.
    """
    if device.splash != "png":
        return

    # Der Dateiname muss die volle DISPLAY_SIZE tragen, das Bild selbst aber
    # hoechstens den halben Bildschirm hoch sein. Grund ist TFTView_320x240:
    #
    #   if (lv_obj_get_height(boot_logo) > vertical_resolution / 2) {
    #       lv_obj_add_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);
    #
    # Genau dieses firmware_label traegt spaeter die Bluetooth-Pairing-PIN
    # ("%06d", bluetooth.fixed_pin). Der Programming-Mode-Zweig entfernt das
    # Hidden-Flag nicht wieder - ein bildschirmfuellendes Logo macht die PIN
    # also dauerhaft unsichtbar und das Geraet praktisch nicht koppelbar.
    logo_height = device.height if full_height else device.height // 2
    target = (FIRMWARE_DIR / "branding"
              / f"logo_{device.width}x{device.height}.png")
    hint = "voll" if full_height else "halbe Hoehe, haelt die BLE-PIN sichtbar"
    emit(f"Erzeuge Farb-Splash {device.width}x{logo_height} ({hint}) -> {target}")
    logo.render_tft_png(ASSET_LOGO, splash_text, target,
                        device.width, logo_height)

    # Altlast: frueher landete die Datei unter data/static/boot/. Das Image
    # bekommt data/ als Wurzel, der Pfad hiess dort also /static/boot/logo.png
    # und wurde nie gefunden - die Firmware zeigte ihr eingebautes Logo.
    stale = FIRMWARE_DIR / "data" / "static" / "boot" / "logo.png"
    if stale.exists():
        stale.unlink()
        emit("  alte Datei aus data/static/boot/ entfernt")


def force_fs_rebuild(device: devices.Device, emit) -> None:
    """Vorhandenes LittleFS-Image loeschen, damit es neu gebaut wird.

    SCons kennt keine Abhaengigkeit zwischen dem Dateisystem-Image und
    branding/logo_*.png. Aendert sich nur das Logo, gilt das Image als aktuell,
    das Ziel wird uebersprungen - und damit auch die daran haengende
    Vorabaktion load_boot_logo(), die erst data/boot/logo.png anlegt. Das Image
    behaelt dann den alten Splash oder gar keinen.
    """
    build_dir = FIRMWARE_DIR / ".pio" / "build" / device.env
    for stale in build_dir.glob("littlefs-*.bin"):
        stale.unlink()
        emit(f"  {stale.name} entfernt - erzwingt Neubau des Dateisystems")


# ------------------------------------------------------- Partitions/Manifest

def partition_offsets(device: devices.Device, emit) -> dict[str, int]:
    """Offsets aus der Partition-CSV lesen, die das Board laut platformio.ini nutzt."""
    csv_name = "default_16MB.csv"
    for ini in FIRMWARE_DIR.glob("variants/**/platformio.ini"):
        text = ini.read_text()
        if f"[env:{device.env}]" not in text:
            continue
        # board_build.partitions kann im env oder im geerbten *_base stehen
        for line in text.splitlines():
            if "board_build.partitions" in line and "=" in line:
                csv_name = line.split("=", 1)[1].strip()
        break

    csv_path = FIRMWARE_DIR / csv_name
    offsets: dict[str, int] = {}
    if csv_path.exists():
        for line in csv_path.read_text().splitlines():
            line = line.split("#")[0].strip()
            if not line:
                continue
            cols = [c.strip() for c in line.split(",")]
            if len(cols) >= 4 and cols[3].startswith("0x"):
                offsets[cols[0]] = int(cols[3], 16)
        emit(f"Partition-Tabelle {csv_name}: "
             + ", ".join(f"{k}@0x{v:x}" for k, v in offsets.items()))
    else:
        emit(f"WARNUNG: {csv_name} nicht gefunden, nutze Standard-Offsets")
    return offsets


def find_boot_app0() -> Path | None:
    roots = [Path(os.environ.get("PLATFORMIO_CORE_DIR", "/root/.platformio")),
             FIRMWARE_DIR / ".platformio"]
    for root in roots:
        for hit in root.glob("packages/framework-arduinoespressif32*/tools/partitions/boot_app0.bin"):
            return hit
    return None


def collect(device: devices.Device, key: str, firmware_ref: str, emit) -> dict:
    """Artefakte in den Cache kopieren und Flash-Manifest bauen."""
    build_dir = FIRMWARE_DIR / ".pio" / "build" / device.env
    out_dir = CACHE_DIR / key
    out_dir.mkdir(parents=True, exist_ok=True)

    offsets = partition_offsets(device, emit)
    app0 = offsets.get("app0", 0x10000)
    spiffs = offsets.get("spiffs", offsets.get("littlefs", 0xc90000))
    otadata = offsets.get("otadata", 0xe000)

    parts: list[dict] = []

    def take(src: Path, name: str, offset: int, role: str) -> None:
        shutil.copy2(src, out_dir / name)
        size = (out_dir / name).stat().st_size
        parts.append({"name": name, "offset": offset, "size": size, "role": role})
        emit(f"  {name} -> 0x{offset:06x} ({size/1024:.0f} KB, {role})")

    if device.arch.startswith("esp32"):
        bootloader = build_dir / "bootloader.bin"
        if bootloader.exists():
            take(bootloader, "bootloader.bin", 0x0, "bootloader")

        partitions = build_dir / "partitions.bin"
        if partitions.exists():
            take(partitions, "partitions.bin", 0x8000, "partitions")

        boot_app0 = find_boot_app0()
        if boot_app0:
            take(boot_app0, "boot_app0.bin", otadata, "otadata")
        else:
            emit("WARNUNG: boot_app0.bin nicht gefunden - OTA-Zeiger wird nicht gesetzt!")

        app = next((f for f in build_dir.glob("firmware-*.bin")
                    if not f.name.endswith(".factory.bin")), None)
        if app is None:
            app = build_dir / "firmware.bin"
        if not app.exists():
            raise RuntimeError("Keine App-Firmware im Build-Verzeichnis gefunden")
        take(app, "firmware.bin", app0, "app")

        fs = next(iter(build_dir.glob("littlefs-*.bin")), None)
        if fs and fs.exists():
            take(fs, "littlefs.bin", spiffs, "filesystem")
        elif device.splash == "png":
            emit("WARNUNG: kein littlefs-Image - der Splash fehlt dann!")
    else:
        for pattern in ("firmware*.uf2", "firmware*.hex"):
            for f in build_dir.glob(pattern):
                shutil.copy2(f, out_dir / f.name)
                parts.append({"name": f.name, "offset": None,
                              "size": f.stat().st_size, "role": "image"})
                emit(f"  {f.name} ({f.stat().st_size/1024:.0f} KB)")

    if not parts:
        raise RuntimeError(
            f"Build lieferte keine flashbaren Dateien fuer {device.id} "
            f"(arch={device.arch!r}). Ein Manifest ohne Partitionen waere "
            "als 'fertig' ausgeliefert worden, ohne dass es etwas zu flashen "
            "gibt."
        )

    manifest = {
        "cache_key": key,
        "device": device.id,
        "device_name": device.name,
        "chip": device.arch,
        "firmware_ref": firmware_ref,
        "erase_all": True,
        "parts": parts,
        "built_at": time.time(),
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


# ------------------------------------------------------------------- Runner

def _execute(build: Build, device: devices.Device, splash_text: str,
             overrides: dict) -> None:
    emit = build.emit
    try:
        # Builds laufen nacheinander. Ohne Hinweis starrt der Benutzer sonst
        # auf ein leeres Log, solange ein anderer Build noch laeuft.
        if _build_serializer.locked():
            emit("Ein anderer Build läuft gerade - dieser startet gleich danach.")
        with _build_serializer:
            build.status = "running"
            emit(f"=== Build {build.id} | {device.name} | {build.firmware_ref} ===")
            if splash_text:
                emit(f"Splash-Text: {splash_text!r}")
            if overrides:
                emit(f"Admin-Overrides aktiv: {sorted(overrides)}")

            ensure_firmware(build.firmware_ref, emit)
            write_userprefs(device, splash_text, overrides, emit)
            full_logo = False
            if device.splash == "png":
                install_deps(device, emit)
                full_logo = patch_device_ui(device, emit)
            write_tft_logo(device, splash_text, emit, full_height=full_logo)
            if device.splash == "png":
                force_fs_rebuild(device, emit)

            emit("")
            emit(f">>> PlatformIO: {device.env} (das dauert typisch 5-15 Minuten)")
            code, output = _run([PIO_BIN, "run", "-e", device.env],
                                FIRMWARE_DIR, emit)
            if code != 0 and _is_transient_framework_error(output):
                emit("")
                emit("PlatformIO ist beim Neuinstallieren des Arduino-Frameworks "
                     "abgebrochen. Das passiert einmalig nach einem Plattform-"
                     "wechsel - zweiter Anlauf:")
                code, _ = _run([PIO_BIN, "run", "-e", device.env],
                               FIRMWARE_DIR, emit)
            if code != 0:
                raise RuntimeError(f"PlatformIO-Build fehlgeschlagen (Exit {code})")

            emit("")
            emit("=== Artefakte sammeln ===")
            build.manifest = collect(device, build.cache_key,
                                     build.firmware_ref, emit)
            build.status = "done"
            emit("")
            emit("✓ Build fertig")
    except Exception as exc:                    # noqa: BLE001 - an den Client melden
        build.status = "error"
        build.error = str(exc)
        emit(f"✗ Fehler: {exc}")
    finally:
        build.finished = time.time()
        build.close_streams()


def start(device_id: str, name: str | None, overrides: dict | None = None,
          firmware_ref: str | None = None) -> Build:
    resolved_ref = versions.resolve(firmware_ref or DEFAULT_FIRMWARE_REF)
    device = devices.get(device_id, resolved_ref)
    site = settings.load_site()
    overrides = overrides or {}

    if "splash_text" in overrides:
        splash_text = overrides["splash_text"]
    elif device.splash is None:
        splash_text = ""
    elif name:
        splash_text = f"{site['splash_prefix']} - {name}"
    else:
        splash_text = site["splash_prefix"]

    key = cache_key(device_id, resolved_ref, splash_text, overrides)
    build = Build(
        id=uuid.uuid4().hex[:12],
        device_id=device_id,
        display_name=device.name,
        firmware_ref=resolved_ref,
        cache_key=key,
        personalized=bool(name) or bool(overrides),
    )

    hit = cached_manifest(key)
    if hit:
        build.status = "done"
        build.manifest = hit
        build.finished = time.time()
        build.emit(f"Cache-Treffer für {device.name} - kein Neubau nötig.")
        build.close_streams()
    with _builds_lock:
        BUILDS[build.id] = build
    if hit:
        return build

    build.emit(f"Build {build.id} angelegt: {device.name}, {resolved_ref}")
    threading.Thread(target=_execute, daemon=True,
                     args=(build, device, splash_text, overrides)).start()
    return build


def get(build_id: str) -> Build | None:
    with _builds_lock:
        return BUILDS.get(build_id)
