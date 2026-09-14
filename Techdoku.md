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
| `v2.7.26.54e0d8d` | 121 |
| `v2.8.0.47db0e3` | 139 |
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

### Geerbte Metadaten

Varianten wie `t-deck-tft` tragen **keine eigenen** `custom_meshtastic_*`-Felder,
sondern erben sie über `extends = env:t-deck`. Wer nur das Feld direkt am Env
liest, verliert genau die device-ui-Builds — im Katalog fehlten dadurch **19
TFT-Envs**, mehrere davon auf `release`-Level (`picomputer-s3-tft`,
`thinknode_m9-tft`, `rak_wismesh_tap_v2-tft`, `seeed_wio_tracker_L2-tft`).

Aufgelöst werden deshalb `display_name`, `architecture`, `actively_supported`,
`support_level` und `tags` über die `extends`-Kette. Geerbte Anzeigenamen
bekommen einen Zusatz aus dem Env-Suffix (`LILYGO T-Deck (TFT)`), sonst hießen
Basis und Variante gleich.

**Die Architektur ist dabei nicht kosmetisch.** `collect()` entscheidet über
`arch.startswith("esp32")`, ob ein ESP32-Partitionsmanifest gebaut wird. Bleibt
sie `"unbekannt"`, läuft der Nicht-ESP32-Zweig, sucht `.uf2`/`.hex`, findet
nichts — und liefert ein Manifest mit **null Partitionen** bei Build-Status
`done`. Genau das passierte bei `t-deck-tft`.

`collect()` wirft deshalb, wenn keine flashbare Datei zusammenkommt. Ein Build
ohne Artefakte ist ein Fehler, kein Erfolg.

### Envs ganz ohne Metadaten

Manche Envs tragen **gar keine** `custom_meshtastic_*`-Felder und erben auch
keine, weil sie direkt von einer Architektur-Basis abstammen (`extends =
nrf52840_base`). `rak4631_eink` — die RAK14000-E-Paper-Variante — ist so ein
Fall.

Dafür gibt es `EXTRA_ENVS`: eine kurze, gepflegte Zuordnung von Env-Kennung auf
Anzeigename. Bewusst eine Liste und keine Automatik — alle namenlosen Envs
aufzunehmen würde `native-*`- und Debug-Ziele mit einsammeln.

Die Architektur wird für solche Envs aus der `extends`-Kette abgeleitet
(`_infer_arch()`): die Basis heißt nach der Architektur, `[nrf52840_base]` liegt
in `variants/nrf52840/nrf52840.ini`. Ohne das bliebe `arch` auf `"unbekannt"` —
mit den Folgen aus dem vorigen Abschnitt.

### board_level — was es wirklich bedeutet

Aus `bin/generate_ci_matrix.py`, nicht aus dem Bauchgefühl:

```python
# Always include board_level = 'pr'
if env["board_level"] == "pr":
    outlist.append(env["ci"])
# Include board_level = 'extra' when requested
elif "extra" in args.level and env["board_level"] == "extra":
    outlist.append(env["ci"])
# If no board level is specified, include in release builds (not PR)
elif "pr" not in args.level and not env["board_level"]:
    outlist.append(env["ci"])
```

| Wert | Bedeutung |
|---|---|
| `pr` | wird in **jedem** CI-Lauf gebaut — bei jedem Pull Request *und* im Release |
| *nicht gesetzt* | wird in Release-Builds gebaut |
| `extra` | nur auf ausdrückliche Anforderung |

`pr` bezeichnet also die **am häufigsten** gebauten Boards, nicht die
schlechtesten. Ein Hinweis „nicht im Release-Stand" wäre dort falsch — im
Frontend warnt deshalb nur `extra`.

Verteilung bei `v2.8.0`: 102 ohne Angabe, 28 `extra`, 11 `pr`.

### Varianten unterscheidbar machen

Viele Boards existieren mehrfach: sieben Einträge „Seeed Xiao NRF52840 Kit",
vier „Heltec Mesh Pocket", dazu Debug-, Gateway- und TFT-Ableger. In einer
Kachelliste sind identische Namen nicht auswählbar — und die Env-Kennung
dahinterzusetzen hilft nur, wer sie ohnehin kennt.

`_variant_label()` baut deshalb eine sprechende Kurzbezeichnung aus bis zu zwei
Merkmalen, vom Spezifischen zum Allgemeinen:

| Quelle | Beispiel |
|---|---|
| `HAS_TFT` / `extends … inkhud` | `TFT`, `InkHUD` |
| Env-Suffix | `Ethernet-Gateway, Debug`, `RAK14000 E-Paper`, `mit Display-Shield` |
| Hardware-Flags | `E22-900M30S (30 dBm)`, `Wio-BTB-Anschluss`, `Hardware-Rev. 1.1` |
| `SEEED_XIAO_NRF52840_KIT` / Pfad `variants/*/diy/` | `Kit-Aufbau`, `DIY-Aufbau` |
| nichts davon | `Standard` |

Zwei Reihenfolgen sind dabei bedeutsam:

**Suffix vor Flag.** `USE_SEMIHOSTING` setzt „Debug" — damit hießen
`rak4631_dbg` und `rak4631_eth_gw_dbg` gleich. Das Suffix `_eth_gw_dbg` ist
spezifischer und muss zuerst greifen.

**DIY zuletzt.** Alle Xiao-Varianten liegen unter `variants/nrf52840/diy/`.
Ein vorgezogenes „DIY-Aufbau" hätte sie wieder ununterscheidbar gemacht.

Der Herkunftspfad trägt hier Information, die in keinem Flag steht — deshalb
gibt `_collect_sections()` ihn mit zurück.

Bleibt ein Name doppelt, kommt zuletzt doch die Env-Kennung dahinter. Bei
`v2.8.0` ist das für kein einziges Board nötig.

### Gerätebilder

`custom_meshtastic_images` nennt den Dateinamen der Board-Grafik. Die SVGs
stammen aus `meshtastic/web-flasher` (`public/img/devices/`, GPL-3.0 wie das
übrige Projekt) und liegen unter `frontend/public/img/devices/` — 92 Dateien,
rund 5,8 MB.

Nicht jedes Board hat eine: bei `v2.8.0` nennen 112 von 141 eine Grafik, davon
existieren 98 tatsächlich als Datei. Neuere Boards wie `heltec-v4-r8-tft`
fehlen im Bestand. Die Kachel fällt per `onError` auf `unknown-new.svg`
zurück, statt ein kaputtes Bild zu zeigen.

### Displayklassifikation

Reihenfolge ist bedeutsam:

| Prüfung | Ergebnis |
|---|---|
| `-D HAS_TFT=1` | Farbdisplay, Größe aus `DISPLAY_SIZE` bzw. `TFT_WIDTH/HEIGHT` |
| `-D USE_EINK` / `HAS_EINK` / `EINK_DISPLAY_MODEL` | E-Ink, Größe aus `EINK_WIDTH`/`EINK_HEIGHT` |
| `-D HAS_SCREEN=0` | kein Display |
| sonst | Standard-UI |

**`HAS_TFT` muss vor `HAS_SCREEN` geprüft werden.** Die Elecrow-TFT-Boards
setzen beides — sie rendern über `device-ui` statt über das klassische
Screen-Modul. In der falschen Reihenfolge landen sie als „kein Display".

**Flags werden geparst, nicht per Teilstring gesucht.** `_flags_to_defines()`
baut ein Dict aus `-D NAME=WERT`. Ohne das matcht `MESHTASTIC_USE_EINK_UI=0`
auf `USE_EINK` und deklariert das Board fälschlich als E-Ink.

**Das Label sagt nichts über das Panel.** Aus den Build-Flags lässt sich nur
ablesen, *welcher Renderer* läuft: `HAS_TFT=1` heißt device-ui (LVGL, farbig),
sonst rendert das klassische Screen-Modul in 1 Bit — auch auf Farb-Panels. Der
T-Deck hat ein 320×240-Farbdisplay, der `t-deck`-Build nutzt aber den
klassischen Renderer. Ihn „OLED" zu nennen wäre eine Behauptung, die die Daten
nicht hergeben; das Label heißt deshalb **Standard-UI**.

Wer auf so einem Gerät den Farb-Splash will, wählt die `-tft`-Variante.

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
| Farbdisplay | `device-ui` lädt `/boot/logo.png` aus LittleFS | `assets/meshhessen_logo.png` → `branding/logo_<W>x<H>.png` |
| OLED / E-Ink | `USERPREFS_OEM_IMAGE_DATA` (XBM) via `drawOEMIconScreen` | `assets/meshhessen_icon.png` |

`USERPREFS_OEM_IMAGE_DATA` wirkt **nur** auf dem XBM-Pfad. Auf einem TFT-Board
zeigt die Firmware sonst ihr eingebautes grünes Meshtastic-Logo.

### 6.1 Der Farb-Splash gehört nach `branding/`, nicht nach `data/`

Die Firmware hat dafür einen vorgesehenen Weg, dokumentiert in
`branding/README.md`. `bin/platformio-custom.py` registriert für
`HAS_TFT`-Builds die Vorabaktion `load_boot_logo()`:

```
branding/logo_<breite>x<hoehe>.png   →   data/boot/logo.png
```

Das Dateisystem-Image bekommt `data/` als Wurzel, die Datei liegt darin also
als `/boot/logo.png` — genau der Pfad, den `FileLoader::loadBootImage()`
öffnet.

**Die Größe im Dateinamen muss `DISPLAY_SIZE` des Boards treffen.** Trifft sie
nicht, findet `load_boot_logo()` nichts und kopiert stillschweigend gar nichts
— ohne Fehlermeldung.

Wer stattdessen direkt nach `data/static/boot/logo.png` schreibt, landet im
Image unter `/static/boot/logo.png`. Die Firmware findet dort nichts und zeigt
ihr eingebautes grünes Logo. Das sieht aus wie „der Splash funktioniert nicht"
und ist in Wahrheit ein Pfadfehler.

### 6.2 Das Dateisystem-Image wird nicht von allein neu gebaut

SCons kennt keine Abhängigkeit zwischen dem LittleFS-Image und
`branding/logo_*.png`. Ändert sich nur das Logo, gilt das Image als aktuell,
das Ziel wird übersprungen — und mit ihm die daran hängende Vorabaktion, die
`data/boot/logo.png` überhaupt erst anlegt.

`force_fs_rebuild()` löscht deshalb vor jedem Farb-Splash-Build das vorhandene
`littlefs-*.bin`. Ohne das liefert der Build in ~65 s ein Image mit dem Stand
von vorgestern.

Erkennungsmerkmal: fehlt `/firmware/data/boot/logo.png` nach einem Build, ist
die Vorabaktion nicht gelaufen.

### 6.2a Vollbild-Logo braucht einen Patch an device-ui

`TFTView_320x240.cpp` versteckt beim Booten das `firmware_label`, wenn das
Boot-Logo höher als der halbe Bildschirm ist:

```c
if (lv_obj_get_height(objects.boot_logo) > vertical_resolution / 2) {
    lv_obj_set_pos(objects.boot_logo, 0, 0);
    lv_obj_add_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);
}
```

Dasselbe Label trägt später die **Bluetooth-Pairing-PIN**:

```c
lv_label_set_text_fmt(objects.firmware_label, "%06d", db.config.bluetooth.fixed_pin);
```

Der Programming-Mode-Zweig entfernt das Hidden-Flag nicht wieder. Ein
bildschirmfüllendes Logo macht die PIN damit dauerhaft unsichtbar — das Gerät
lässt sich nicht mehr koppeln.

Im Programming Mode wird das Logo ohnehin ausgeblendet, das Label gehört dort
also sichtbar. `patch_device_ui()` fügt deshalb genau eine Zeile ein:

```c
lv_obj_remove_flag(objects.firmware_label, LV_OBJ_FLAG_HIDDEN);
```

**Der Patch muss nach dem Auflösen der Abhängigkeiten sitzen.** Er landet in
`.pio/libdeps/<env>/meshtastic-device-ui/`, das PlatformIO jederzeit neu holen
kann. Ablauf pro Build:

1. `pio pkg install -e <env>` — stellt sicher, dass `libdeps` existiert
2. `patch_device_ui()` — content-basiert, idempotent (prüft auf die eingefügte
   Zeile, nicht auf Zeilennummern)
3. Logo rendern — **volle Höhe nur, wenn der Patch sitzt**
4. Build

Findet der Patch seine Stelle nicht — andere Firmware-Version, umbenannte
Datei —, gibt er `False` zurück und das Logo wird auf halbe Höhe gerendert.
Dann ist der Splash kleiner, aber die PIN bleibt sichtbar. Der Fehlerfall ist
damit harmlos statt gerätelähmend.

`updateBootMessage()` schreibt ebenfalls in dieses Label; Boot-Meldungen bleiben
bei vollem Logo weiterhin unsichtbar. Das ist so gewollt und wird nicht
gepatcht.

### 6.2f Auch das XBM darf den Schirm nicht füllen

Dieselbe Klasse Fehler auf der 1-Bit-Seite. `drawOEMIconScreen` positioniert:

```c
y = (SCREEN_HEIGHT - FONT_HEIGHT_MEDIUM - IMAGE_HEIGHT) / 2 + 2
```

Ein bildschirmhohes Bild ergibt ein **negatives y**: auf dem RAK14000 (122 px,
`FONT_HEIGHT_MEDIUM` 28) war das `(122-28-122)/2+2 = -12`. Das Logo ragte oben
aus dem Schirm und verdeckte unten den Titel bei y=94.

`xbm_size()` begrenzt die **deklarierte** Bildhöhe deshalb auf

```
IMAGE_HEIGHT <= H - FONT_HEIGHT_MEDIUM - 2*FONT_HEIGHT_SMALL + 4
```

sodass das Bild unter der Statuszeile beginnt. Für den RAK: 250×60 bei y=19,
Titel bei y=94 — beides frei. Wichtig ist, dass `USERPREFS_OEM_IMAGE_HEIGHT`
den **gerenderten** Wert trägt; die Firmware rechnet mit dem deklarierten, ein
kleiner gezeichnetes Icon in einem großen Rahmen hilft nicht.

Bei `Low` bleibt die volle Fläche, weil wir den Schriftzug dort selbst setzen
und ihn mit positionieren müssen.

### 6.2b Der Text richtet sich nach der Bildquelle

Das Farb-Logo (`meshhessen_logo.png`) trägt den Schriftzug „MESH HESSEN"
bereits im Bild. Darunter noch einmal „Mesh Hessen" zu setzen stünde doppelt
da — beim `png`-Pfad erscheint deshalb nur die Personalisierung, sonst nichts.
Die Icon-Grafik des `xbm`-Pfads hat keinen Schriftzug, dort bleibt es bei
„Mesh Hessen - Name".

### 6.2c Displaygrößen stehen oft in variant.h

Nicht jedes Board nennt seine Auflösung in den build_flags. Der Heltec T114
definiert `TFT_WIDTH 240` / `TFT_HEIGHT 135` in
`variants/nrf52840/heltec_mesh_node_t114/variant.h`. Wer nur die Flags liest,
rät 128×64 — und daraus folgt die falsche Auflösungsklasse: die Firmware hält
das Panel für `High` und zeichnet `USERPREFS_OEM_TEXT` selbst, während wir ihn
zusätzlich einbacken. **Der Schriftzug stand doppelt auf dem Schirm.**

`_variant_defines()` liest deshalb die `variant.h` aus jedem `-I variants/...`
des Boards — mit einem **minimalen Präprozessor**. Ohne den holt man sich
Defines aus Blöcken, die gar nicht übersetzt werden: der T-Beam definiert
`TFT_WIDTH 480` in einem `#ifdef USE_ST7796` für ein optionales Display,
tatsächlich hat er ein 128×64-OLED.

Ausgewertet werden `#ifdef`, `#ifndef`, `#if defined(X)`, `#else`, `#endif`.
Alles Komplexere gilt als **inaktiv** — lieber die Standardgröße als eine
womöglich falsche.

### 6.2d Die Schriftwahl hängt nicht nur an E-Ink

`ScreenFonts.h` schaltet auf die größeren Schriften (`FONT_SMALL` wird
`ArialMT_Plain_16` statt `_10`) nicht nur bei `USE_EINK`, sondern auch bei
`ST7789`, `ST7796`, `ILI9341` und weiteren Treibern. Der T114 fällt über
`USE_ST7789` darunter. `_font_kind()` prüft die ganze Liste.

Das entscheidet über zwei Dinge: die reservierten Ränder (Zeilenhöhen 19/28
statt 13/19) und die Textbreite.

### 6.2e Wie breit der Text werden darf

`app/fonts.py` enthält die **echten Zeichenbreiten** aus der Sprungtabelle von
`OLEDDisplayFonts.cpp` (4 Byte je Zeichen, das vierte ist die Breite). Damit
lässt sich exakt vorhersagen, ob ein Text auf ein Panel passt:

| Text | `ArialMT_Plain_16` |
|---|---|
| `Mesh Hessen` | 98 px |
| `Mesh Hessen - Manuel` | 164 px |
| `Mesh Hessen - Taunusmesh - Gerrit` | **256 px** |

Auf dem 250 px breiten RAK14000 ist das letzte 6 px zu breit —
`drawOEMIconScreen` zentriert und schneidet dann **beidseitig** ab. Die
Tabellen gehen über `/api/config` ans Frontend, das die Breite live beim Tippen
misst und „Weiter" sperrt, statt hinterher ein abgeschnittenes Display zu
liefern.

### 6.3 Zwei Bildquellen für den 1-Bit-Pfad

Das volle Logo mit „MESH HESSEN"-Schriftzug (2171×1200) ist für 128×64 in einem
Bit viel zu detailliert — die Hessen-Karte zerfällt zu einem Rauschfleck. Der
XBM-Pfad nutzt deshalb die Umriss-Zeichnung `meshhessen_icon.png`.

### 6.4 Polarität wird gemessen, nicht geraten

Nach dem Graustufen-Konvertieren entscheidet das Histogramm: das Motiv ist
immer die Minderheit der Pixel, bei überwiegend hellem Bild wird invertiert.
So funktionieren helle Grafik auf dunklem Grund und dunkle Linien auf hellem
gleichermaßen.

### 6.5 `USERPREFS_OEM_TEXT` ist der Compile-Schalter

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

### 6.6 Gezeichnet wird der Text nur bei High-Res

`drawOEMIconScreen` rendert den Titel nur bei `currentResolution == High`.
`determineScreenResolution()` verlangt dafür `screenwidth > 128` — ein
klassisches 128×64-OLED ist damit `Low`, der Text erschiene nie.

`logo.screen_resolution()` spiegelt diese Logik. Bei `Low`/`UltraLow` wird der
Schriftzug **ins XBM gebacken**. Doppelt steht er dadurch nie: bei `Low`
zeichnet die Firmware ihn nicht, bei `High` wird er nicht eingebacken.

Ohne das wäre die Personalisierung auf OLED-Geräten unsichtbar.

### 6.7 Das XBM braucht geschweifte Klammern

`bin/platformio-custom.py` reicht nur Werte roh als Define durch, die mit `{`
beginnen. Alles andere läuft durch `env.StringifyMacro()`:

```c
static const uint8_t xbm[] = "0x00, 0x00, ...";   // Bildmuell
static const uint8_t xbm[] = {0x00, 0x00, ...};   // richtig
```

### 6.8 XBM-Format

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

## 7a. Reset und Download-Modus

Beides übernommen aus dem offiziellen Web-Flasher, weil dort die Sequenzen
erprobt sind.

### Neustart nach dem Flashen

Nach `writeFlash()` wird der Chip über die RTS-Leitung zurückgesetzt:

```ts
await transport.setRTS(true)    // EN=LOW  - Chip im Reset
await new Promise(r => setTimeout(r, 100))
await transport.setRTS(false)   // EN=HIGH - Chip bootet
```

`loader.after()` allein reicht nicht zuverlässig; der explizite RTS-Wechsel ist
das, was der Web-Flasher nach einem Regressionsfall wieder eingeführt hat.

### 1200-Baud-Reset (nur ESP32)

Manche ESP32-S3-Boards mit nativem USB — das T-Deck vor allem — gehen ohne
Hilfe nicht in den Download-Modus. Öffnet man den Port kurz mit **1200 Baud**
und schließt ihn wieder, startet die Firmware selbst in den Bootloader:

```ts
const port = await navigator.serial.requestPort()
await port.open({ baudRate: 1200 })
await new Promise(r => setTimeout(r, 500))   // Gerät muss es erkennen
await port.close()
```

Das ist bewusst eine **Schaltfläche**, kein Automatismus: es öffnet einen
eigenen Port-Dialog, und der Benutzer muss dasselbe Gerät zweimal auswählen.

### DFU bei nRF52 geht über das Protokoll, nicht über 1200 Baud

Naheliegend war, denselben Griff für die RAK-Boards zu nehmen — PlatformIO
verlässt sich beim Upload schließlich darauf. **In der Praxis funktioniert das
nicht:** das Gerät startet zwar neu, landet aber nicht im UF2-Bootloader,
sondern in einem unbrauchbaren Zwischenzustand.

Der offizielle Flasher verbindet sich stattdessen als Meshtastic-Client und
lässt die laufende Firmware sich selbst in den Bootloader starten:

```ts
const transport = await TransportWebSerial.createFromPort(port, 115200)
const device = new MeshDevice(transport, id)
await Promise.race([device.configure(), timeout(5000)])  // configure() haengt gern
await device.enterDfuMode()
```

`configure()` blockiert gelegentlich, obwohl die Konfiguration ankommt — das
Rennen gegen einen 5-Sekunden-Timeout ist aus dem Original übernommen, ebenso
das schrittweise Aufräumen der Streams samt `port.forget()`.

Das kostet zwei Abhängigkeiten (`@meshtastic/core`,
`@meshtastic/transport-web-serial`, beide aus der **JSR-Registry**) und lässt
das Bundle von 84 auf 154 KB gzip wachsen. Dafür ist es der Weg, der
tatsächlich funktioniert.

**Grenze:** setzt eine laufende Firmware ab 2.2.17 (nRF52) voraus. Ist sie
älter oder antwortet das Gerät nicht, bleibt nur doppeltes Drücken der
RST-Taste — das steht als Hinweis direkt an der Schaltfläche.

> `.npmrc` mit `@jsr:registry=https://npm.jsr.io` muss im Docker-Build **vor**
> `npm install` liegen. Ohne sie klappt es nur, solange die Lockfile die URLs
> bereits auflöst.

### Offene Ports blockieren das Flashen

Web Serial gibt für dasselbe Gerät **dasselbe Port-Objekt** zurück. Bleibt es
offen, scheitert jeder weitere `open()` mit `Failed to open serial port`, bis
die Seite neu geladen wird.

`baud1200Reset()` schließt den Port deshalb in einem `finally`, und beide
Einstiegspunkte rufen vorher `releaseOpenPort()`. Die Fehlermeldung nennt
außerdem die üblichen Verursacher: zweiter Tab, Serial-Monitor, Terminal.

---

## 7b. Flash-Modi

Nicht jedes Aufspielen soll das Gerät zurücksetzen. Es gibt drei Modi, aus dem
Manifest abgeleitet (`partsForMode()`):

| Modus | Geschriebene Rollen | Erase | Wirkung |
|---|---|---|---|
| `update` | `app` | nein | Nur die Firmware. Konfiguration, Schlüssel und Dateisystem bleiben. |
| `update_fs` | `app`, `filesystem` | nein | Zusätzlich der Splash von Farbdisplays. Konfiguration bleibt. |
| `full` | alle | **ja** | Alles neu. Gerät im Auslieferungszustand. |

`update_fs` wird nur angeboten, wenn das Board überhaupt ein Dateisystem-Image
hat.

### Warum das Manifest `splash` mitführt

Was ein Update erneuert, hängt davon ab, **wo der Splash liegt**:

- `"xbm"` — in die App einkompiliert, ein einfaches Update erneuert ihn mit
- `"png"` — im LittleFS, ein einfaches Update lässt ihn auf dem alten Stand

Ohne diese Information könnte das Frontend nicht sagen, ob „Update" für das
gewählte Board reicht. Deshalb steht `splash` im Manifest, nicht nur im
Gerätekatalog.

### Schlüssel sichern

Vor einem `full` warnt die Oberfläche deutlich: der Vollerase löscht den
öffentlichen und privaten Schlüssel des Geräts mitsamt allen Einstellungen.
Ohne Sicherung ist der Node danach eine neue Identität im Netz — gespeicherte
Kontakte und Direktnachrichten erreichen ihn nicht mehr. Der offizielle
Web-Flasher warnt an derselben Stelle.

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

## 8a. Katalog-Anpassungen im Admin-Bereich

Der Firmware-Katalog kennt 141 Boards; die wenigsten sind für ein konkretes
Netz relevant, und die Namen aus dem Repo sind nicht die von der Verpackung.
Beides lässt sich im Admin-Bereich anpassen, persistiert in
`DATA_DIR/catalog.json`.

### Ausblenden

`disabled` ist eine Liste von Board-Kennungen, die im öffentlichen Katalog
nicht erscheinen. Der Admin-Endpunkt liefert sie weiterhin mit
(`include_hidden=True`) — sonst ließen sie sich nicht wieder einschalten.

### Aliase

Ein Alias ist ein eigener Eintrag mit verständlichem Namen, eigenem Bild und
Hinweis, der auf ein vorhandenes Board zeigt:

```json
{
  "id": "heltec-expansion-kit-v2",
  "name": "Heltec WiFi LoRa 32 Expansion Kit V2 mit LoRa 32 V4-R8",
  "target": "heltec-v4-r8-tft",
  "note": "mit Touchscreen"
}
```

Alle übrigen Eigenschaften — Displaytyp, Auflösung, Architektur, Splash-Pfad —
erbt der Alias vom Ziel. Zeigt er auf ein Board, das die gewählte
Firmware-Version nicht kennt, wird er stillschweigend weggelassen.

**Ein Alias baut dieselbe Firmware wie sein Ziel.** `POST /api/build` löst die
Kennung über `catalog.resolve()` auf, bevor der Build startet. Dadurch teilen
sich Alias und Ziel einen Cache-Eintrag — sonst würde für jeden Anzeigenamen
dieselbe Firmware erneut kompiliert. Nachprüfbar am Cache-Key: beide Wege
liefern denselben.

### Eigene Bilder

Hochgeladene Grafiken landen unter `DATA_DIR/uploads/` mit zufälligem
Dateinamen und werden im Alias als `upload:<name>` referenziert. Das Frontend
unterscheidet daran, ob es `/api/catalog/image/<name>` oder die mitgelieferte
Grafik unter `/img/devices/` lädt (`deviceImageUrl()`).

**Rastergrafiken werden serverseitig verkleinert.** Ein Gerätefoto hat
regelmäßig mehrere MB — eine enge Upload-Grenze hieße nur, dass der Upload
scheitert. Angenommen werden bis **8 MB**, dann rechnet Pillow auf 512 px
Kantenlänge herunter und schreibt als PNG neu (3,4 MB → 335 KB im Test). SVG
ist Text und wird nicht skaliert, dort bleibt eine Grenze von 512 KB.

Der Content-Type wird gegen eine Whitelist geprüft und bestimmt die
Dateiendung — der vom Browser gemeldete Dateiname geht nicht in den Pfad ein.

### Export und Import

`catalog.json` und die Bilder liegen im Volume `builder-data`, nicht im Repo.
Bei einem Serverumzug wären Aliase und Ausblendungen weg. `GET
/api/admin/catalog/export` liefert deshalb alles in **einer** Datei — die
Bilder base64-kodiert eingebettet — und `POST /api/admin/catalog/import`
stellt es wieder her.

Die Bilder werden unter ihrem ursprünglichen Namen zurückgeschrieben, damit
die `upload:`-Referenzen in den Aliasen weiter stimmen. Namen werden gegen
`^[0-9a-f]{16}$` plus erlaubte Endung geprüft; alles andere wird
übersprungen, statt einen Pfad aus der Importdatei zu übernehmen.

---

## 8b. Missbrauchsschutz

Ein Build kostet 5–15 Minuten CPU, ein Cache-Treffer nichts. Geschützt wird
deshalb nur der Fall, in dem wirklich kompiliert wird.

### Der eigentliche Fehler war die Warteschlange

Vorher legte **jeder** Build-Request sofort einen Thread an, der dann auf dem
Serialisierungs-Lock wartete. Tausend Anfragen ergaben tausend Threads und eine
Warteschlange ohne Obergrenze, in der echte Nutzer nie drankamen — unabhängig
von Bots.

Jetzt: eine `queue.Queue(maxsize=MAX_QUEUE)` und **ein** Worker-Thread, der sie
seriell abarbeitet. Ist sie voll, antwortet der Endpunkt mit `503` und einer
Erklärung. Wartende Builds melden ihre Position.

### Warum Proof of Work und nicht nur ein Captcha

Die Asymmetrie steht gegen uns: ein Angreifer investiert Sekunden, wir
antworten mit Minuten. Ein gelöstes Captcha je Build ist für jemanden mit
Vorsatz billig. **Die Grenzen schützen die Maschine, die Rechenaufgabe hält
Gelegenheits-Skripte und Crawler fern** — in dieser Reihenfolge, nicht
umgekehrt.

Gewählt wurde Proof of Work statt eines externen Dienstes: kein Dritter, keine
personenbezogenen Daten, kein Bruch des Ziels, alles self-contained zu
deployen.

### Ablauf

```
POST /api/build            ohne Aufgabe
  → 200, wenn Cache-Treffer   (kostet nichts, wird nicht gebremst)
  → 428 sonst

POST /api/challenge        → {challenge, difficulty}
  Browser sucht nonce mit sha256(challenge + nonce) ≥ N Nullbits
POST /api/build            mit {challenge, nonce} → 200
```

Erst ohne Aufgabe zu versuchen ist der Kern: der Client weiß nicht, ob es ein
Cache-Treffer wird, der Server schon. Eine zusätzliche Rundreise kostet nur,
wer tatsächlich baut.

`428 Precondition Required` statt `403`, weil `403` bereits für abgelehnte
Admin-Overrides steht und das Frontend beides unterscheiden muss.

### Eigenes SHA-256 im Browser

`SubtleCrypto` scheidet aus — es ist asynchron, und eine Million `await` dauern
ein Vielfaches der Rechnung selbst. `src/lib/pow.ts` enthält daher eine
synchrone Implementierung, die in Stücken von 20 000 Versuchen läuft und
dazwischen ans Fenster zurückgibt, damit die Seite nicht einfriert.

Gegen Nodes `crypto` verifiziert. Gemessen mit ~1,2 M Hashes/s:

| Schwierigkeit | Dauer (3 Läufe) |
|---|---|
| 18 Bits | 0,1 · 0,2 · 0,1 s |
| **20 Bits** | 0,4 · 1,0 · 0,4 s |
| 22 Bits | 3,1 · 14,3 · 3,5 s |

Die Streuung ist hoch (geometrische Verteilung) — 22 Bits wären im schlechten
Fall unzumutbar. 20 Bits ist der Standard: verglichen mit dem Build danach
vernachlässigbar.

### Grenzen

| Variable | Standard | Wirkung |
|---|---|---|
| `POW_DIFFICULTY` | 20 | Nullbits; `0` schaltet die Aufgabe ab |
| `MAX_QUEUE` | 5 | wartende Builds, dann `503` |
| `BUILDS_PER_HOUR` | 3 | neue Builds je IP; `0` = kein Limit |

**Angemeldete Admins umgehen beides.**

### Absender-Adresse

`_client_ip()` nimmt den ersten Eintrag aus `X-Forwarded-For`, sonst
`X-Real-IP`, sonst die Socket-Adresse. Unser nginx hängt an `X-Forwarded-For`
an, damit die Kette hinter einem weiteren Proxy erhalten bleibt.

Beide Header sind fälschbar. Das Limit ist eine **Bremse gegen
Massenabfragen, keine Zugangskontrolle** — wer IPs rotiert, umgeht es. Dagegen
steht die Warteschlangen-Grenze, die unabhängig vom Absender greift.

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

### nginx: API-Präfixe brauchen `^~`

```nginx
location ^~ /api/build { ... }     # richtig
location    /api/build { ... }     # falsch
```

Ein Regex-`location` hat in nginx Vorrang vor einem Präfix-`location`. Weiter
unten steht:

```nginx
location ~* \.(js|css|png|svg|ico|woff2?)$ { expires 1y; }
```

Ohne `^~` gewinnt dieser Block, sobald ein API-Pfad auf `.png` oder `.svg`
endet — etwa `/api/catalog/image/<name>.png`. Er hat kein `proxy_pass`, nginx
sucht die Datei im Webroot und antwortet mit **404**. Im Browser sah das aus,
als sei der Bild-Upload fehlgeschlagen; tatsächlich lag die Datei längst auf
dem Server, nur die Vorschau kam nicht zurück.

`^~` unterbindet die Regex-Auswertung für diesen Präfix.

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
- **E-Ink-Größen nur teilweise erkannt.** Viele Varianten nennen ihre echte
  Panelgröße in `EINK_WIDTH`/`EINK_HEIGHT` (rak4631_eink: 250×122), die wird
  genutzt. Wo das fehlt, bleibt 128×64 als sicherer Rückfall.
- **Ein Arbeitsbaum, serielle Builds.** Für mehr Durchsatz bräuchte es einen
  Klon je Worker.
- **Erster Build nach einem Plattformwechsel scheitert** — pioarduino meldet
  „Reinstall Arduino framework" und bricht mit `FRAMEWORK_DIR = None` ab.
  `_is_transient_framework_error()` erkennt das an der Ausgabe und startet
  genau einen zweiten Anlauf, der durchgeht.
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

**Liegt der Farb-Splash im Image?** Nicht am Dateinamen festmachen, sondern am
Pfad — `logo.png` allein beweist nichts, wenn es unter `/static/boot/` liegt:

```bash
docker compose exec mh-builder-api python -c "
png=open('/firmware/data/boot/logo.png','rb').read()
fs=open('/data/cache/<key>/littlefs.bin','rb').read()
print('IHDR:', png[12:29] in fs, '| IEND:', png[-12:] in fs)
print('Vorkommen von logo.png:', fs.count(b'logo.png'))"
```

Existiert `/firmware/data/boot/logo.png` nicht, ist `load_boot_logo()` nicht
gelaufen — dann war das Dateisystem-Image nicht als veraltet erkannt.

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
| Build heltec-v4-r8-tft (warm) | 290 s |
| Cache-Treffer (API-Antwort) | ~23 ms |
| Katalog-Scan je Ref | ~50 ms |
| Boards 2.7.26 / 2.8.0 | 123 / 141 |
| davon Farbdisplay / E-Ink (2.8.0) | 14 / 24 |
| Build rak4631_eink (UF2) | 134 s |
| Build t-deck-tft | 300 s (Erstlauf 577 s) |
