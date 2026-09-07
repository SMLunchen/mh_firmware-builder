# Techdoku — MeshHessen Firmware-Builder

Interne Dokumentation: wie das System aufgebaut ist, welche Annahmen es trifft
und wo die nicht offensichtlichen Fallstricke liegen. Für Betrieb und
Bedienung siehe [README.md](README.md).

Stand: 2026-09-06 · ~2.500 Zeilen Code (Backend Python, Frontend TypeScript)

---

## 1. Architektur

```
Browser
  │  HTTPS 3337 (self-signed)  /  HTTP 3336 (hinter Reverse Proxy)
  ▼
mh-builder-web  ── nginx ──────────────────────────────────────┐
  │  statische SPA (Vite/React)                                │
  │  Web Serial API → flasht direkt aufs Gerät                 │
  │                                                            │ /api/*
  ▼                                                            ▼
Gerät (USB)                                          mh-builder-api (FastAPI)
                                                       │
                                    ┌──────────────────┼──────────────────┐
                                    ▼                  ▼                  ▼
                              /firmware           PlatformIO            /data
                          (Git-Klon des        (eigenes venv         (Cache der
                       Meshtastic-Repos)        /opt/pio)          fertigen Images)
```

Zwei Container, vier Volumes. Der Browser flasht selbst — der Server liefert
nur die Images und ein Manifest mit den Zieladressen.

### Volumes (gemessen im Betrieb)

| Volume | Inhalt | Größe |
|---|---|---|
| `builder-pio` | PlatformIO-Toolchains | **3,2 GB** |
| `builder-firmware` | Git-Klon des Firmware-Repos | 631 MB |
| `builder-data` | Cache, Manifeste, Site-Config | wächst mit Builds (~7 MB je Build) |
| `builder-certs` | Self-signed Zertifikat | 12 KB |

Der erste Build eines Boards ist deshalb deutlich langsamer als alle folgenden.
Mit warmem `builder-pio` liegen ESP32-Builds bei rund **100–130 s**.

---

## 2. Module

### Backend (`backend/app/`)

| Modul | Verantwortung |
|---|---|
| `main.py` | HTTP-API, Admin-Token, SSE-Streaming, Startup-Hook |
| `builder.py` | Build-Orchestrierung, Cache, Artefaktsammlung, Flash-Manifest |
| `devices.py` | Gerätekatalog aus dem Firmware-Repo ableiten, Displayklassifikation |
| `versions.py` | Git-Tags auflösen (`2.7` → `v2.7.26.54e0d8d`) |
| `logo.py` | Splash-Erzeugung für beide Displaypfade |
| `settings.py` | Admin-Overrides (pro Build) und Site-Config (global) |
| `userprefs.base.jsonc` | MeshHessen-Basiskonfiguration, Grundlage jedes Builds |

### Frontend (`frontend/src/`)

| Datei | Verantwortung |
|---|---|
| `App.tsx` | Wizard-State, Versionswechsel, Admin-Routing über `#admin` |
| `lib/api.ts` | API-Client, Admin-Token in `sessionStorage`, SSE-Abo |
| `lib/flasher.ts` | esptool-js, flasht Einzelpartitionen nach Manifest |
| `steps/*.tsx` | Die fünf Wizard-Schritte plus Admin-Panel |

---

## 3. Build-Pipeline

`builder.start()` → Cache-Prüfung → bei Treffer sofortige Antwort, sonst Thread:

1. **`ensure_firmware(ref)`** — klonen bzw. `git checkout` auf das Ref.
   Idempotent: steht das Repo schon richtig, passiert nichts.
2. **`write_userprefs()`** — `userprefs.base.jsonc` einlesen, alle
   `USERPREFS_OEM_*` verwerfen, Splash neu erzeugen, Admin-Overrides
   einmischen, nach `/firmware/userPrefs.jsonc` schreiben.
3. **`write_tft_logo()`** — nur bei Farbdisplay: PNG nach
   `data/static/boot/logo.png`, wird beim Build ins LittleFS gepackt.
4. **PlatformIO** — `$PIO_BIN run -e <env>`, Ausgabe zeilenweise in den
   SSE-Stream.
5. **`collect()`** — Artefakte in den Cache kopieren, Manifest schreiben.

### Nebenläufigkeit

Builds laufen **seriell** (`_build_serializer`, ein globaler Lock). Grund: alle
teilen sich einen Arbeitsbaum unter `/firmware`, und ein `git checkout` während
eines laufenden Compiles wäre fatal.

Wartende Builds melden das sofort im Log (`_build_serializer.locked()`) — sonst
sähe der Benutzer minutenlang ein leeres Fenster.

Der Gerätekatalog liest **nie** aus dem Arbeitsbaum, sondern per `git archive`
aus dem Objektspeicher (siehe §4). Deshalb kann er eine andere Version
beantworten, während ein Build gerade eine dritte ausgecheckt hat.

---

## 4. Gerätekatalog

Nicht gepflegt, sondern abgeleitet. Jede Board-Variante trägt in ihrer
`platformio.ini` `custom_meshtastic_*`-Felder — dieselbe Quelle, aus der auch
der offizielle Meshtastic-Flasher gespeist wird.

### Versionsabhängigkeit

Der Katalog gehört zur Version, nicht zum Projekt:

| Ref | Boards |
|---|---|
| `v2.7.26.54e0d8d` | 97 |
| `v2.8.0.47db0e3` | 116 (19 davon **nur** hier) |
| `v2.6.13.0561f2c` | 0 — Metadaten existieren noch nicht |

Ein Build gegen eine Version, die das Board nicht kennt, wird mit `404`
abgelehnt, statt später im Compiler zu scheitern.

### Lesen ohne Checkout

```python
git ls-tree --name-only <ref>          # welche Pfade gibt es?
git archive <ref> <vorhandene Pfade>   # → tar → Temp-Verzeichnis
```

Der `ls-tree`-Schritt ist nicht optional: `git archive` bricht **komplett** ab,
sobald ein Pfad im Ref fehlt. `arch/` existiert nur in manchen Versionen — ohne
die Vorprüfung schlägt der Export für 2.7/2.8 fehl und fällt still auf den
Arbeitsbaum zurück, der dann eine ganz andere Version zeigt.

Ergebnis ist pro Ref gecacht (max. 8 Refs, ~50 ms pro Scan).

### Displayklassifikation

Reihenfolge ist bedeutsam:

| Prüfung | Ergebnis |
|---|---|
| `-D HAS_TFT=1` | Farbdisplay, Größe aus `DISPLAY_SIZE` bzw. `TFT_WIDTH/HEIGHT` |
| `-D USE_EINK` / `HAS_EINK` | E-Ink |
| `-D HAS_SCREEN=0` | kein Display |
| sonst | OLED |

**`HAS_TFT` muss vor `HAS_SCREEN` geprüft werden.** Die Elecrow-TFT-Boards
setzen beides — sie rendern über `device-ui` statt über das klassische
Screen-Modul. In der falschen Reihenfolge landen sie als „kein Display".

**Flags werden geparst, nicht per Teilstring gesucht.** `_flags_to_defines()`
baut ein Dict aus `-D NAME=WERT`. Ohne das matcht `MESHTASTIC_USE_EINK_UI=0`
auf `USE_EINK` und deklariert das Board fälschlich als E-Ink.

**OLED ist der Normalfall, nicht der Rest.** Meshtastic erkennt angeschlossene
Displays zur Laufzeit per I²C-Scan — dafür gibt es kein Build-Flag. Ein
gesetztes `USERPREFS_OEM_IMAGE_DATA` schadet auf einem Board ohne Display
nicht. (`OLED_PL=1` steht auf praktisch allen Boards und taugt nicht als
Signal.)

---

## 5. Versionsauflösung

Tags haben die Form `v<major>.<minor>.<patch>.<commit>`, z. B. `v2.7.26.54e0d8d`.

Lexikalisch sortieren sie falsch (`v2.7.2` hinter `v2.7.19`), und dieselbe
Version kann doppelt getaggt sein (`v2.8.0.47db0e3` **und** `v2.8.0.7239fe8`).
Sortiert wird deshalb nach Commit-Datum (`git tag --sort=-creatordate`), mit
numerischem Fallback für `git ls-remote`, solange das Repo noch nicht geklont
ist.

`resolve()` akzeptiert `2.7` (neueste der Reihe), `latest`, einen exakten Tag —
und reicht Unbekanntes unverändert durch, sodass auch Branches oder
Commit-Hashes baubar bleiben.

Die Versionsliste blendet Reihen ohne Gerätekatalog automatisch aus. Keine fest
verdrahtete Untergrenze: vor 2.7 fehlen schlicht die `custom_meshtastic_*`-
Felder, und das fällt datengetrieben auf.

---

## 6. Splash-Erzeugung

Der aufwendigste Teil, weil die beiden Displaytypen völlig verschiedene Wege
gehen und der 1-Bit-Pfad mehrere nicht offensichtliche Fallen hat.

| Typ | Mechanismus | Quelle |
|---|---|---|
| Farbdisplay | `device-ui` lädt `/boot/logo.png` aus LittleFS | `assets/meshhessen_logo.png` |
| OLED / E-Ink | `USERPREFS_OEM_IMAGE_DATA` (XBM) via `drawOEMIconScreen` | `assets/meshhessen_icon.png` |

`USERPREFS_OEM_IMAGE_DATA` wirkt **nur** auf dem XBM-Pfad. Auf einem TFT-Board
zeigt die Firmware sonst ihr eingebautes grünes Meshtastic-Logo.

### 6.1 Zwei Bildquellen, kein Zufall

Das volle Logo mit „MESH HESSEN"-Schriftzug (2171×1200) ist für 128×64 in einem
Bit viel zu detailliert — die Hessen-Karte zerfällt zu einem Rauschfleck. Der
XBM-Pfad nutzt deshalb die Umriss-Zeichnung `meshhessen_icon.png`.

### 6.2 Polarität wird gemessen, nicht geraten

Nach dem Graustufen-Konvertieren entscheidet das Histogramm: das Motiv ist
immer die Minderheit der Pixel, bei überwiegend hellem Bild wird invertiert.
So funktionieren helle Grafik auf dunklem Grund und dunkle Linien auf hellem
gleichermaßen.

### 6.3 `USERPREFS_OEM_TEXT` ist der Compile-Schalter

`Screen.cpp`:

```c
#ifdef USERPREFS_OEM_TEXT
    static bool showingOEMBootScreen = true;
    ...   // drawOEMBootScreen wird ausschliesslich hier eingeplant
#endif
```

Fehlt das Define, existiert der OEM-Bootscreen im Build **gar nicht** — auch
das Bild nicht. Das Define muss also immer gesetzt sein, selbst wenn der Text
nie gezeichnet wird.

### 6.4 Gezeichnet wird der Text nur bei High-Res

`drawOEMIconScreen` rendert den Titel nur bei `currentResolution == High`.
`determineScreenResolution()` verlangt dafür `screenwidth > 128` — ein
klassisches 128×64-OLED ist damit `Low`, der Text erschiene nie.

`logo.screen_resolution()` spiegelt diese Logik. Bei `Low`/`UltraLow` wird der
Schriftzug **ins XBM gebacken**. Doppelt steht er dadurch nie: bei `Low`
zeichnet die Firmware ihn nicht, bei `High` wird er nicht eingebacken.

Ohne das wäre die Personalisierung auf OLED-Geräten unsichtbar.

### 6.5 Das XBM braucht geschweifte Klammern

`bin/platformio-custom.py` reicht nur Werte roh als Define durch, die mit `{`
beginnen. Alles andere läuft durch `env.StringifyMacro()`:

```c
static const uint8_t xbm[] = "0x00, 0x00, ...";   // Bildmuell
static const uint8_t xbm[] = {0x00, 0x00, ...};   // richtig
```

### 6.6 XBM-Format

1 Bit pro Pixel, **LSB zuerst**, jede Zeile auf volle Bytes aufgefüllt
(`(width + 7) // 8`). Für 128×64 also exakt 1024 Byte.

Der Text wird **nach** der Schwellwertbildung gezeichnet — sonst frisst die
Quantisierung ihn bei kleinen Schriftgraden auf.

---

## 7. Flash-Manifest

### Warum keine factory.bin

Die von PlatformIO erzeugte `factory.bin` ist **3.903.168 Byte** groß
(`0x3B8EC0`). Das LittleFS-Image liegt bei `0xC90000` (13,2 MB) und ist darin
schlicht nicht enthalten. Ein Gerät, das nur damit geflasht wird, hat ein
leeres Dateisystem — und auf Farbdisplays keinen Splash.

Geflasht werden deshalb die Einzelteile:

| Datei | Adresse | Rolle |
|---|---|---|
| `bootloader.bin` | `0x0` | Bootloader |
| `partitions.bin` | `0x8000` | Partitionstabelle |
| `boot_app0.bin` | `0xE000` | OTA-Zeiger auf app0 |
| `firmware.bin` | `0x10000` | Anwendung |
| `littlefs.bin` | `0xC90000` | Dateisystem (Splash bei Farbdisplays) |

Die Offsets stammen aus der Partitionstabelle des jeweiligen Boards
(`board_build.partitions` → CSV), nicht aus Konstanten.

### `boot_app0.bin`

Die unveränderte Stock-Datei von Arduino-ESP32: `ota_seq = 1`, was auf **app0**
zeigt. Inhaltlich steckt derselbe Block bereits in der `factory.bin` — wir
führen ihn nur explizit mit, damit im Manifest sichtbar ist, worauf der
Bootloader zeigt.

> **Korrektur einer früheren Fehldiagnose:** Es lag zwischenzeitlich der
> Verdacht nahe, `seq=1` zeige auf app1 und verursache damit einen Bootloop.
> Das ist **falsch** — `boot_app0.bin`, deren einziger Zweck „boote von app0"
> ist, hat exakt diesen Inhalt. Warum ein Testgerät nach `erase_flash` +
> `factory.bin` nicht durchbootete, ist weiterhin **ungeklärt**. Der Weg über
> Einzelpartitionen umgeht das Thema und löst zusätzlich das Splash-Problem,
> das die `factory.bin` prinzipbedingt nicht lösen kann.

### Nicht-ESP32-Boards

nRF52840 und RP2040 können nicht über Web Serial geflasht werden. Für sie
sammelt `collect()` `.uf2`/`.hex` mit `offset = null`; das Frontend bietet sie
zum Download an statt einen Flash-Vorgang anzubieten.

---

## 8. Cache

Cache-Key = SHA256 über:

- Board-ID
- **aufgelöstes** Firmware-Ref (`2.7` und `v2.7.26.54e0d8d` teilen sich einen
  Eintrag, solange `2.7` darauf zeigt)
- Splash-Text
- Admin-Overrides
- Hashes **beider** Bildquellen
- `SPLASH_GENERATION`

### `SPLASH_GENERATION` hochzählen nicht vergessen

Asset-Hashes allein reichen nicht. Ändert man nur die Splash-**Logik**, bleibt
der Key gleich und der Cache liefert weiter Artefakte, die mit der alten Logik
gebaut wurden. Das tarnt sich als „der Fix wirkt nicht" und hat in der
Entwicklung genau einmal Zeit gekostet.

Faustregel: jede Änderung an `logo.py` oder an `write_userprefs()` → Konstante
in `builder.py` erhöhen.

---

## 9. Admin-Overrides

Login-geschützter Build-Pfad für Spezialprojekte (z. B. Relais-Standorte mit
abweichender Sendeleistung) — **kein** generisches Einstellungssystem.

Sicherheitsmodell:

- `ADMIN_PASSWORD` leer → Admin-Bereich vollständig deaktiviert
- Login gibt ein Bearer-Token (12 h, nur im Prozessspeicher)
- Passwortvergleich über `hmac.compare_digest`
- Ein Build-Request **mit** Overrides und **ohne** gültiges Token → `403`
- Overrides gehen in den Cache-Key ein und können deshalb konstruktiv nie in
  einen öffentlichen Build durchschlagen
- `clean_overrides()` verwirft unbekannte Keys, prüft Enum-Werte gegen die
  Whitelist und Zahlen gegen `min`/`max`; leere Werte bedeuten „kein Override"

Overrides gelten **pro Build**. Global persistiert wird nur die Site-Config
(Schriftzug, Ziel-URL) in `/data/site.json`.

---

## 10. Betrieb

### nginx: kein Slash am Build-Endpunkt

```nginx
location /api/build { ... }     # richtig
location /api/build/ { ... }    # falsch
```

Mit Slash beantwortet nginx `/api/build` mit `301` auf die Slash-Variante. Bei
`POST` gehen dabei Methode und Body verloren — im Browser landet das als
nacktes `Failed to fetch`, ohne dass die Anfrage je das Backend erreicht.
GET-Endpunkte sind nicht betroffen, der Fehler fällt also erst beim ersten
echten Build auf.

### SSE darf nicht gepuffert werden

`proxy_buffering off` und `proxy_read_timeout 3600s` auf dem Build-Endpunkt,
sonst kommen alle Logzeilen erst am Ende an. Gilt genauso für einen
vorgelagerten Reverse Proxy.

### TLS

nginx bedient HTTP (80) und HTTPS (443) gleichzeitig aus derselben
`site.conf`. Das self-signed Zertifikat wird beim ersten Start erzeugt und
liegt im Volume — bei jedem Start ein neues müsste im Browser jedes Mal neu
bestätigt werden.

Web Serial gibt es nur unter HTTPS oder auf `localhost`, und nur in
Chromium-Browsern.

### PlatformIO in eigenem venv

PlatformIO pinnt `starlette<0.40`, FastAPI braucht `>=0.40` — zusammen nicht
installierbar. PlatformIO liegt deshalb in `/opt/pio`.

**`/opt/pio/bin` gehört nicht auf den `PATH`**: dort liegt ein `uvicorn`, das
das der API-Umgebung überschattet und den Container in einen Restart-Loop
schickt (`ModuleNotFoundError: No module named 'fastapi'`). Aufgerufen wird
über `PIO_BIN` mit absolutem Pfad.

### Die PlatformIO-Version liegt in einem engen Fenster

`platformio==6.1.19` ist nicht beliebig gewählt — nach beiden Seiten bricht es:

| Version | Fehler |
|---|---|
| ≤ 6.1.18 | `IncompatiblePlatform: ... depends on PlatformIO Core >=6.1.19` |
| 6.1.19 | funktioniert |
| ≥ 6.2.0 | `ModuleNotFoundError: No module named 'SCons.Tool.FortranCommon'` |

Boards mit klassischer Plattform (`platformio/espressif32@6.13.0`, z. B.
`heltec-v4`) bauen auch mit älteren Cores. Boards, die auf Meshtastics eigene
pioarduino-Plattform zeigen (`heltec-v4-r8-tft` und alles Neuere), verlangen
den Mindest-Core. Die obere Grenze kommt aus dem SCons, das PlatformIO
mitbringt: ab 6.2.0 fehlt das Modul, das das IDF-Build-Skript der Plattform
importiert.

Ein Test mit einem klassischen Board allein deckt das **nicht** auf — dafür
braucht es ein pioarduino-Board.

---

## 11. Grenzen und offene Punkte

- **Bootloop-Ursache bei `factory.bin` ungeklärt** (siehe §7). Umgangen, nicht
  verstanden.
- **E-Ink-Größen werden nicht erkannt.** Die tatsächliche Panelgröße steht
  nicht in den Build-Flags; es wird konservativ 128×64 verwendet.
  `drawOEMIconScreen` zentriert, ein kleineres Bild passt also immer — nutzt
  aber große Panels nicht aus.
- **Ein Arbeitsbaum, serielle Builds.** Für mehr Durchsatz bräuchte es einen
  Klon je Worker.
- **Kein Cache-GC.** `/data/cache` wächst mit ~7 MB je Build-Variante.
  Aufräumen bislang manuell.
- **Admin-Tokens leben im Prozess.** Nach einem Neustart der API muss man sich
  neu anmelden.
- **Der Katalog kennt keine Board-Bilder.** Die `custom_meshtastic_images`-SVGs
  werden nicht ausgeliefert.

---

## 12. Verifikationsrezepte

Prüfen, was tatsächlich passiert — nicht, was passieren sollte.

**Landet das XBM wirklich im Binary?**

```bash
docker compose exec mh-builder-api python -c "
import json,re
p=json.load(open('/firmware/userPrefs.jsonc'))
xbm=bytes(int(x,16) for x in re.findall(r'0x([0-9a-fA-F]{2})',p['USERPREFS_OEM_IMAGE_DATA']))
fw=open('/data/cache/<key>/firmware.bin','rb').read()
print('XBM im Binary:', xbm in fw)"
```

Das ist der aussagekräftigste Test des ganzen Splash-Pfads — er deckt Quelle,
Polarität, Klammern und Compile-Schalter in einem ab.

**Werden die Defines richtig klassifiziert?**

```bash
docker compose exec mh-builder-api python -c "
import json
p=json.load(open('/firmware/userPrefs.jsonc'))
for k,v in sorted(p.items()):
    if k.startswith('USERPREFS_OEM'):
        print(k, '->', 'C-Array' if v.startswith('{') else
              'Zahl' if v.lstrip('-').replace('.','').isdigit() else 'String')"
```

`USERPREFS_OEM_IMAGE_DATA` muss `C-Array` sein, `USERPREFS_OEM_TEXT` muss
vorhanden sein.

**Kommt der POST durch nginx?**

```bash
curl -sk -w "\n[%{http_code}]\n" -X POST https://localhost:3337/api/build \
  -H 'Content-Type: application/json' -d '{"device":"heltec-v4"}'
```

`301` bedeutet, der Location-Block hat wieder einen Slash zu viel.

**Log eines fertigen Builds abrufen?** Der Stream muss sofort mit dem
`done`-Event enden. Hängt er, fehlt in `Build.subscribe()` das `None` für
bereits abgeschlossene Builds. Builds leben nur im Prozessspeicher — nach einem
Neustart der API antwortet der Endpunkt mit `Build unbekannt`.

**Streamt SSE ungepuffert?** Zeitstempel der ersten Zeilen vergleichen — sie
müssen sich unterscheiden. Nicht über `sed` prüfen, das puffert selbst und
täuscht Batching vor.

**XBM ansehen statt raten:** Bytes aus `userPrefs.jsonc` extrahieren, als
PIL-Bild dekodieren (LSB-first, `(w+7)//8` Byte je Zeile), vergrößert
speichern und anschauen.

---

## 13. Anhang: verifizierte Kennzahlen

| Wert | Messung |
|---|---|
| `factory.bin` (heltec-v4-r8-tft, 2.8.0) | 3.903.168 B = `0x3B8EC0` |
| LittleFS-Offset | `0xC90000` = 13.172.736 B |
| XBM 128×64 | 1024 B |
| Build heltec-v4, warmer Cache | 99–128 s |
| Build heltec-v4-r8-tft (inkl. Plattform-Download) | 401 s |
| Cache-Treffer (API-Antwort) | ~23 ms |
| Katalog-Scan je Ref | ~50 ms |
| Boards 2.7.26 / 2.8.0 | 97 / 116 |
