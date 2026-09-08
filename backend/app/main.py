"""HTTP-API des MeshHessen Firmware-Builders."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path

from fastapi import Body, Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from . import builder, catalog, devices, fonts, settings, versions

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
TOKEN_TTL = 12 * 3600

app = FastAPI(title="MeshHessen Firmware Builder", docs_url=None, redoc_url=None)


@app.on_event("startup")
def _prepare_firmware() -> None:
    """Repo im Hintergrund holen, damit der Gerätekatalog bereitsteht, ohne
    den Serverstart zu blockieren."""
    def work() -> None:
        try:
            ref = versions.resolve(builder.DEFAULT_FIRMWARE_REF)
            builder.ensure_firmware(ref, lambda line: print(line, flush=True))
            catalog = devices.for_ref(ref)
            print(f"Gerätekatalog {ref}: {len(catalog)} Boards", flush=True)
        except Exception as exc:                # noqa: BLE001
            print(f"Firmware-Vorbereitung fehlgeschlagen: {exc}", flush=True)

    threading.Thread(target=work, daemon=True).start()

_tokens: dict[str, float] = {}


def _issue_token() -> str:
    token = secrets.token_urlsafe(32)
    _tokens[token] = time.time() + TOKEN_TTL
    return token


def require_admin(authorization: str = Header(default="")) -> None:
    token = authorization.removeprefix("Bearer ").strip()
    expiry = _tokens.get(token)
    if not expiry or expiry < time.time():
        _tokens.pop(token, None)
        raise HTTPException(status_code=401, detail="Nicht angemeldet")


def _optional_admin(authorization: str = Header(default="")) -> bool:
    token = authorization.removeprefix("Bearer ").strip()
    expiry = _tokens.get(token)
    return bool(expiry and expiry >= time.time())


# ------------------------------------------------------------------ Öffentlich

@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "firmware_ref": versions.resolve(builder.DEFAULT_FIRMWARE_REF)}


@app.get("/api/config")
def config() -> dict:
    site = settings.load_site()
    return {
        "config_url": site["config_url"],
        "splash_prefix": site["splash_prefix"],
        "firmware_ref_spec": builder.DEFAULT_FIRMWARE_REF,
        "firmware_ref": versions.resolve(builder.DEFAULT_FIRMWARE_REF),
        "admin_enabled": bool(ADMIN_PASSWORD),
        # Zeichenbreiten, damit das Frontend exakt vorhersagen kann, ob ein
        # Splash-Text auf das Panel passt, statt zu schaetzen.
        "fonts": {kind: {str(c): w for c, w in table.items()}
                  for kind, table in fonts.FONTS.items()},
    }


@app.get("/api/versions")
def list_versions() -> dict:
    """Verfuegbare Firmware-Versionen, nach Minor-Reihe gruppiert."""
    overview = [
        series for series in versions.series_overview()
        # Vor 2.7 fehlen die custom_meshtastic_*-Felder, aus denen der
        # Gerätekatalog kommt - solche Versionen sind nicht baubar.
        if devices.for_ref(series["latest"]["ref"])
    ]
    if not overview:
        raise HTTPException(
            status_code=503,
            detail="Versionsliste noch nicht verfuegbar. Gleich nochmal versuchen.",
        )
    return {
        "default": versions.resolve(builder.DEFAULT_FIRMWARE_REF),
        "default_spec": builder.DEFAULT_FIRMWARE_REF,
        "series": overview,
    }


@app.get("/api/devices")
def list_devices(firmware_ref: str = "") -> dict:
    """Gerätekatalog der gewählten Firmware-Version. Welche Boards es gibt,
    unterscheidet sich zwischen den Versionen."""
    ref = versions.resolve(firmware_ref or builder.DEFAULT_FIRMWARE_REF)
    listing = catalog.apply(devices.for_ref(ref))
    if not listing:
        raise HTTPException(
            status_code=503,
            detail=f"Für {ref} liegt kein Gerätekatalog vor. "
                   "Entweder wird das Repo noch vorbereitet, oder die Version "
                   "ist zu alt für die automatische Erkennung.",
        )
    return {"firmware_ref": ref, "devices": [d.as_dict() for d in listing]}


@app.post("/api/build")
def start_build(payload: dict = Body(...), is_admin: bool = Depends(_optional_admin)) -> dict:
    device_id = str(payload.get("device", "")).strip()
    if not device_id:
        raise HTTPException(status_code=400, detail="device fehlt")
    name = (payload.get("name") or "").strip()[:24] or None

    raw_overrides = payload.get("overrides") or {}
    if raw_overrides and not is_admin:
        raise HTTPException(status_code=403,
                            detail="Overrides sind nur für angemeldete Admins")
    overrides = settings.clean_overrides(raw_overrides) if is_admin else {}

    firmware_ref = (payload.get("firmware_ref") or "").strip() or None
    # Ein Alias baut dieselbe Firmware wie sein Ziel und teilt dessen
    # Cache-Eintrag - sonst wuerde je Anzeigename neu kompiliert.
    device_id = catalog.resolve(device_id)
    try:
        build = builder.start(device_id, name, overrides, firmware_ref)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return build.public()


@app.get("/api/build/{build_id}")
def build_status(build_id: str) -> dict:
    build = builder.get(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="Build unbekannt")
    return build.public()


@app.get("/api/build/{build_id}/logs")
async def build_logs(build_id: str) -> StreamingResponse:
    build = builder.get(build_id)
    if build is None:
        raise HTTPException(status_code=404, detail="Build unbekannt")

    async def stream():
        q = build.subscribe()
        loop = asyncio.get_running_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, q.get)
                if line is None:
                    payload = json.dumps(build.public())
                    yield f"event: done\ndata: {payload}\n\n"
                    return
                yield f"data: {json.dumps(line)}\n\n"
        finally:
            build.unsubscribe(q)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@app.get("/api/artifact/{cache_key}/manifest.json")
def artifact_manifest(cache_key: str) -> dict:
    manifest = builder.cached_manifest(_safe(cache_key))
    if manifest is None:
        raise HTTPException(status_code=404, detail="Manifest unbekannt")
    return manifest


@app.get("/api/artifact/{cache_key}/{name}")
def artifact(cache_key: str, name: str) -> FileResponse:
    path = builder.CACHE_DIR / _safe(cache_key) / _safe(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Datei unbekannt")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


def _safe(part: str) -> str:
    if "/" in part or "\\" in part or part.startswith("."):
        raise HTTPException(status_code=400, detail="Ungültiger Pfad")
    return part


# ----------------------------------------------------------------- Admin

@app.post("/api/admin/login")
def admin_login(payload: dict = Body(...)) -> dict:
    if not ADMIN_PASSWORD:
        raise HTTPException(status_code=503, detail="Admin-Bereich ist nicht konfiguriert")
    supplied = str(payload.get("password", ""))
    if not hmac.compare_digest(supplied, ADMIN_PASSWORD):
        raise HTTPException(status_code=401, detail="Falsches Passwort")
    return {"token": _issue_token(), "ttl": TOKEN_TTL}


@app.get("/api/admin/schema", dependencies=[Depends(require_admin)])
def admin_schema() -> dict:
    return {"overrides": settings.OVERRIDE_SCHEMA, "site": settings.load_site()}


@app.get("/api/admin/catalog", dependencies=[Depends(require_admin)])
def admin_catalog(firmware_ref: str = "") -> dict:
    """Katalog-Anpassungen plus die vollstaendige Boardliste zum Auswaehlen."""
    ref = versions.resolve(firmware_ref or builder.DEFAULT_FIRMWARE_REF)
    everything = catalog.apply(devices.for_ref(ref), include_hidden=True)
    return {
        "firmware_ref": ref,
        "settings": catalog.load(),
        "devices": [{"id": d.id, "name": d.name, "image": d.image,
                     "display": d.display, "arch": d.arch}
                    for d in everything],
    }


@app.put("/api/admin/catalog", dependencies=[Depends(require_admin)])
def admin_catalog_save(payload: dict = Body(...)) -> dict:
    return catalog.save(payload)


@app.post("/api/admin/catalog/image", dependencies=[Depends(require_admin)])
async def admin_catalog_image(file: UploadFile = File(...)) -> dict:
    """Eigenes Board-Bild hochladen.

    Rastergrafiken werden serverseitig verkleinert - ein Gerätefoto mit
    mehreren MB ist der Normalfall und soll nicht am Upload scheitern.
    """
    content = await file.read(catalog.MAX_UPLOAD_BYTES + 1)
    if len(content) > catalog.MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Datei ist groesser als "
                   f"{catalog.MAX_UPLOAD_BYTES // (1024 * 1024)} MB.",
        )
    try:
        reference = catalog.store_image(content, file.content_type or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"image": reference}


@app.get("/api/admin/catalog/export", dependencies=[Depends(require_admin)])
def admin_catalog_export() -> dict:
    return catalog.export()


@app.post("/api/admin/catalog/import", dependencies=[Depends(require_admin)])
def admin_catalog_import(payload: dict = Body(...)) -> dict:
    try:
        return catalog.import_(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/catalog/image/{name}")
def catalog_image(name: str) -> FileResponse:
    """Hochgeladene Board-Bilder ausliefern."""
    path = catalog.UPLOAD_DIR / _safe(name)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Bild unbekannt")
    return FileResponse(path)


@app.put("/api/admin/site", dependencies=[Depends(require_admin)])
def admin_site(payload: dict = Body(...)) -> dict:
    return settings.save_site(payload)
