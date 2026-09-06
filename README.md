# MeshHessen Firmware-Builder

Webinterface, das für ein ausgewähltes Gerät eine MeshHessen-Firmware baut,
sie direkt im Browser aufs Gerät flasht und anschließend zur
[Schnellkonfiguration](https://config.meshhessen.de) weiterleitet.

Alles läuft self-contained in Docker — kein PlatformIO, kein Python, kein
esptool auf dem Host nötig.

---

## Ablauf für den Benutzer

1. **Version und Gerät wählen** — vorausgewählt ist die Version aus
   `FIRMWARE_REF`, der Katalog richtet sich nach der gewählten Version.
2. **Personalisierung** (nur bei Geräten mit Display) — mit Namen oder ohne.
   - ohne Namen → Splash zeigt `Mesh Hessen`
   - mit Namen → Splash zeigt `Mesh Hessen - <Name>`
3. **Build** — Standard-Firmware kommt aus dem Cache und ist sofort da;
   personalisierte Firmware wird frisch gebaut (5–15 Min) mit Live-Log.
4. **Flashen** — Gerät per USB anschließen, Browser flasht direkt.
5. **Konfigurieren** — auf Wunsch Weiterleitung in die Schnellkonfiguration.

Geräte ohne Display überspringen Schritt 2 und gehen direkt in den Build.

---

## Schnellstart

```bash
cp .env.example .env
# ADMIN_PASSWORD setzen, wenn der Admin-Bereich gebraucht wird
docker compose up -d --build
```

Danach erreichbar unter `http://localhost:3336`.

Der erste Build eines Boards dauert deutlich länger, weil PlatformIO die
Toolchain herunterlädt (~1,5 GB, landet im Volume `builder-pio` und wird
danach wiederverwendet).

### HTTPS ist Pflicht fürs Flashen

Die Web-Serial-API gibt es nur unter **HTTPS** oder auf `localhost`, und nur in
Chromium-Browsern (Chrome, Edge).

Der Stack bringt beides gleichzeitig mit:

| Port | Protokoll | Wofür |
|---|---|---|
| `3336` | HTTP | Der Reverse Proxy hängt hier dran |
| `3337` | HTTPS, self-signed | Direktzugriff ohne Proxy, z. B. zum Testen |

Das Zertifikat wird beim ersten Start erzeugt und liegt im Volume
`builder-certs` — es überlebt Neustarts, sonst müsste man es im Browser jedes
Mal neu bestätigen. Für einen anderen Hostnamen als `localhost`:

```bash
TLS_HOST=firmware.intern docker compose up -d
```

Ein self-signed Zertifikat muss im Browser einmal akzeptiert werden. Für den
Produktivbetrieb gehört ein echtes Zertifikat auf den Reverse Proxy:

```nginx
server {
    listen 443 ssl;
    server_name firmware.meshhessen.de;

    location / {
        proxy_pass http://127.0.0.1:3336;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;

        # Build-Logs werden gestreamt (SSE) - nicht puffern
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
```

---

## Warum keine factory.bin

Die von PlatformIO erzeugte `factory.bin` endet bei rund 3,7 MB. Das
LittleFS-Image liegt aber bei `0xc90000` (13,2 MB) und ist darin schlicht nicht
enthalten. Ein Gerät, das nur mit `factory.bin` geflasht wird, hat danach ein
leeres Dateisystem — und damit keinen Startbildschirm.

Der Builder schreibt deshalb die Einzelteile an ihre echten Adressen. Die
Offsets kommen aus der Partitionstabelle des jeweiligen Boards, nicht aus fest
verdrahteten Konstanten:

| Datei | Adresse | Zweck |
|---|---|---|
| `bootloader.bin` | `0x0` | Bootloader |
| `partitions.bin` | `0x8000` | Partitionstabelle |
| `boot_app0.bin` | `0xe000` | OTA-Zeiger auf app0 |
| `firmware.bin` | `0x10000` | Anwendung |
| `littlefs.bin` | `0xc90000` | Dateisystem mit dem Splash-Logo |

`boot_app0.bin` ist die unveränderte Stock-Datei von Arduino-ESP32
(`ota_seq = 1` → app0). Inhaltlich steckt derselbe Block auch schon in der
`factory.bin`; wir führen ihn nur explizit mit, damit im Manifest sichtbar ist,
worauf der Bootloader zeigt.

Spätere OTA-Updates schreiben nur die App-Partition. Das LittleFS und damit das
Logo bleiben erhalten.

> **Offen:** Beim manuellen Test bootete ein Gerät auch nach einem sauberen
> `erase_flash` + `factory.bin` nicht durch. Die Ursache ist nicht
> abschließend geklärt — der Verdacht auf einen falschen OTA-Zeiger hat sich
> jedenfalls **nicht** bestätigt. Der Weg über die Einzelpartitionen umgeht das
> Thema und löst zusätzlich das Splash-Problem, das die `factory.bin`
> prinzipbedingt nicht lösen kann.

---

## Splash-Mechanismen

Die Displaytypen nutzen völlig verschiedene Wege — der Builder bedient beide
automatisch:

| Typ | Mechanismus | Quelle | Erzeugt |
|---|---|---|---|
| **Farbdisplay** | `device-ui` lädt `/boot/logo.png` aus LittleFS | `assets/meshhessen_logo.png` | Farbiges PNG in Displaygröße, Schriftzug in MeshHessen-Grün |
| **OLED / E-Ink** | `USERPREFS_OEM_IMAGE_DATA` (XBM) via `drawOEMIconScreen` | `assets/meshhessen_icon.png` | 1-Bit-XBM in Displaygröße |
| **kein Display** | — | — | nichts |

`USERPREFS_OEM_IMAGE_DATA` wirkt **nur** auf OLED-/E-Ink-Geräten. Auf einem
TFT-Board zeigt die Firmware sonst das eingebaute grüne Meshtastic-Logo.

### Zwei Fallstricke beim 1-Bit-Splash

**Es braucht Strichgrafik.** Das volle Logo mit „MESH HESSEN"-Schriftzug ist
für 128×64 in einem Bit viel zu detailliert — die Hessen-Karte zerfällt zu
einem Rauschfleck. Für den XBM-Pfad wird deshalb `meshhessen_icon.png`
verwendet (Umriss-Zeichnung), nicht `meshhessen_logo.png`.

Die Polarität wird nicht geraten: nach dem Graustufen-Konvertieren entscheidet
das Histogramm. Das Motiv ist immer die Minderheit der Pixel, also wird bei
überwiegend hellem Bild invertiert. So funktionieren beide Quellarten — helle
Grafik auf dunklem wie dunkle Linien auf hellem Grund.

**`USERPREFS_OEM_TEXT` ist der Compile-Schalter für den ganzen Splash.**
`Screen.cpp` klammert den kompletten OEM-Bootscreen in `#ifdef
USERPREFS_OEM_TEXT`. Fehlt das Define, wird `drawOEMBootScreen` nie eingeplant
und es erscheint **gar kein** Splash — auch kein Bild. Das Define muss also
immer gesetzt sein, selbst wenn der Text gar nicht gezeichnet wird.

**Gezeichnet wird der Text trotzdem nur auf High-Res-Displays.**
`drawOEMIconScreen` rendert den Titel nur bei `currentResolution == High`, und
`determineScreenResolution()` verlangt dafür `screenwidth > 128`. Ein
klassisches 128×64-OLED ist damit `Low` — der Text erschiene nie.

Der Builder spiegelt diese Logik (`logo.screen_resolution()`) und backt den
Schriftzug bei `Low`/`UltraLow` ins XBM. Doppelt steht er dadurch nie: bei
`Low` zeichnet die Firmware ihn nicht, bei `High` wird er nicht eingebacken.

**Das XBM braucht geschweifte Klammern.** `bin/platformio-custom.py` reicht nur
Werte roh als Define durch, die mit `{` beginnen. Alles andere läuft durch
`env.StringifyMacro()`. Ohne Klammern wird aus dem Array ein String-Literal:

```c
static const uint8_t xbm[] = "0x00, 0x00, ...";   // Bildmüll
static const uint8_t xbm[] = {0x00, 0x00, ...};   // richtig
```

Verifizieren lässt sich das am fertigen Binary — das komplette XBM muss dort am
Stück auftauchen:

```bash
docker compose exec mh-builder-api python -c "
import json,re
p=json.load(open('/firmware/userPrefs.jsonc'))
xbm=bytes(int(x,16) for x in re.findall(r'0x([0-9a-fA-F]{2})',p['USERPREFS_OEM_IMAGE_DATA']))
print(xbm in open('/data/cache/<key>/firmware.bin','rb').read())"
```

---

## Admin-Bereich (Spezialprojekte)

Erreichbar über `/#admin`, geschützt durch `ADMIN_PASSWORD`. Ohne gesetztes
Passwort ist er komplett deaktiviert.

Dort lassen sich für **einen einzelnen Build** Funkparameter abweichend setzen —
TX-Power, Region, Modem-Preset, Hop-Limit, Kanalname/PSK, Splash-Text. Gedacht
für Sondernodes wie Relais-Standorte.

Diese Overrides sind bewusst **nicht** global: sie gelten nur für den Build, in
dem sie gesetzt wurden, gehen in den Cache-Key ein und können deshalb nie in
einen öffentlichen Build durchschlagen. Ein Build-Request mit Overrides ohne
gültiges Admin-Token wird mit `403` abgelehnt.

---

## Caching

Cache-Key = SHA256 über Board, **aufgelöste** Firmware-Version, Splash-Text,
Overrides, die Hashes **beider** Bildquellen und `SPLASH_GENERATION`.

`SPLASH_GENERATION` in `builder.py` muss hochgezählt werden, sobald sich die
Splash-**Logik** ändert. Asset-Hashes allein reichen nicht: ändert man nur
Code, bleibt der Key gleich und der Cache liefert weiter Artefakte, die noch
mit der alten Logik gebaut wurden — ein Fehler, der sich als „der Fix wirkt
nicht" tarnt. Aufgelöst heißt: `2.7` und `v2.7.26.54e0d8d` landen im
selben Eintrag, solange `2.7` auf diesen Tag zeigt.

Die Standard-Firmware pro Board und Version wird also genau einmal gebaut und
danach sofort ausgeliefert. Personalisierte Builds unterscheiden sich im
Splash-Text und bekommen einen eigenen Eintrag — derselbe Name auf demselben
Board trifft beim zweiten Mal den Cache.

Cache leeren:

```bash
docker compose exec mh-builder-api rm -rf /data/cache
```

---

## Firmware-Versionen

Die Version ist **im Webinterface auswählbar**. `FIRMWARE_REF` in der `.env`
legt nur fest, was vorausgewählt ist:

```bash
FIRMWARE_REF=2.7               # neueste Version der 2.7er-Reihe (Vorgabe)
FIRMWARE_REF=latest            # neuester Tag überhaupt
FIRMWARE_REF=v2.7.26.54e0d8d   # genau dieser Tag
```

Die Auswahl zeigt standardmäßig nur die neueste Version je Reihe; über
„Genaue Version wählen" kommt man an alle Tags.

Angeboten werden nur Versionen, die tatsächlich baubar sind. Vor 2.7 fehlen im
Firmware-Repo die `custom_meshtastic_*`-Felder, aus denen der Gerätekatalog
kommt — solche Reihen werden automatisch ausgeblendet, ohne dass eine
Untergrenze fest verdrahtet wäre.

---

## Gerätekatalog

Der Katalog wird **nicht gepflegt, sondern aus dem Firmware-Repo abgeleitet**.
Jede Board-Variante trägt in ihrer `platformio.ini` `custom_meshtastic_*`-Felder
— dieselben, aus denen auch Meshtastic seinen eigenen Flasher speist. Der
Builder liest sie beim Start aus und leitet den Splash-Mechanismus aus den
`build_flags` ab.

**Der Katalog hängt an der Version.** `v2.7.26` kennt 97 Boards, `v2.8.0`
bereits 116 — 19 Boards gibt es ausschließlich in 2.8. Wählt jemand eine
andere Version, wird die Boardliste neu geladen; ein Build für ein Board, das
die gewählte Version nicht kennt, wird mit `404` abgelehnt statt später im
Compiler zu scheitern.

Gelesen wird direkt aus dem Git-Objektspeicher (`git archive <ref>`), ohne
Checkout. Das läuft parallel zu einem Build, der im Arbeitsbaum gerade eine
andere Version ausgecheckt hat, und ist pro Version gecacht (~50 ms).

Die Erkennung im Einzelnen:

| Erkannt an | Ergebnis |
|---|---|
| `-D HAS_TFT=1` | Farbdisplay, Größe aus `DISPLAY_SIZE` bzw. `TFT_WIDTH/HEIGHT` |
| `-D USE_EINK` / `-D HAS_EINK` | E-Ink |
| `-D HAS_SCREEN=0` (und kein TFT) | kein Display |
| sonst | OLED |

OLED ist bewusst der Normalfall: Meshtastic erkennt angeschlossene Displays zur
Laufzeit per I2C-Scan, dafür gibt es gar kein Build-Flag. Ein gesetztes
`USERPREFS_OEM_IMAGE_DATA` schadet auf einem Board ohne Display nicht.

---

## Nicht alle Boards flashen im Browser

Web Serial spricht nur ESP32-Chips an. Für nRF52840- und RP2040-Boards baut der
Builder `.uf2`- bzw. `.hex`-Dateien und bietet sie zum Download an — die
kopiert man im Bootloader-Modus per Drag-and-drop auf das Gerät. Der letzte
Schritt (Weiterleitung in die Schnellkonfiguration) funktioniert danach genauso.

---

## Stolperstelle in der nginx-Config

Der Location-Block für den Build-Endpunkt steht bewusst **ohne** abschließenden
Slash:

```nginx
location /api/build { ... }     # richtig
location /api/build/ { ... }    # falsch
```

Mit Slash beantwortet nginx eine Anfrage an `/api/build` mit einem `301` auf
`/api/build/`. Bei einem `POST` gehen dabei Methode und Body verloren — im
Browser landet das als nacktes `Failed to fetch`, ohne dass die Anfrage je im
Backend ankommt. GET-Endpunkte sind davon nicht betroffen, der Fehler fällt
also erst beim ersten echten Build auf.

---

## Services

| Service | Aufgabe | Port |
|---|---|---|
| `mh-builder-api` | FastAPI: Build-Orchestrierung, PlatformIO, Cache, Artefakte | 8000 (intern) |
| `mh-builder-web` | Nginx: SPA + Proxy auf `/api/` | 3336 |

| Volume | Inhalt |
|---|---|
| `builder-data` | Fertige Images, Manifeste, Site-Config |
| `builder-firmware` | Klon des Meshtastic-Firmware-Repos |
| `builder-pio` | PlatformIO-Toolchains |

---

## API

| Endpunkt | Zweck |
|---|---|
| `GET /api/versions` | Baubare Versionen, nach Reihe gruppiert |
| `GET /api/devices?firmware_ref=…` | Gerätekatalog dieser Version |
| `POST /api/build` | Build starten (`device`, `name`, `firmware_ref`, `overrides`) |
| `GET /api/build/{id}/logs` | Build-Log als SSE-Stream |
| `GET /api/artifact/{key}/manifest.json` | Flash-Manifest mit Offsets |
| `GET /api/artifact/{key}/{datei}` | Einzelnes Image |
| `POST /api/admin/login` | Admin-Token |

---

## Lokale Entwicklung

```bash
# Backend - PlatformIO gehört in ein eigenes venv, es pinnt starlette<0.40
# und kollidiert sonst mit FastAPI.
cd backend
pip install -r requirements.txt
python -m venv /opt/pio && /opt/pio/bin/pip install -r requirements-pio.txt
export PIO_BIN=/opt/pio/bin/platformio
DATA_DIR=../data FIRMWARE_DIR=../../buildtest/meshtastic-firmware \
  LOGO_PATH=../assets/meshhessen_logo.png \
  uvicorn app.main:app --reload

# Frontend (proxied /api auf :8000)
cd frontend && npm install && npm run dev
```

---

## Lizenz

GPL-3.0, entsprechend der verwendeten Meshtastic-Komponenten.
