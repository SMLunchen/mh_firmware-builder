"""Zwei getrennte Dinge:

1. OVERRIDE_SCHEMA - userPrefs-Werte, die ein eingeloggter Admin *pro Build*
   überschreiben kann. Diese Werte gelten NUR für den jeweiligen Build und
   fließen in den Cache-Key ein, damit sie niemals in einen öffentlichen
   Build durchschlagen.

2. Site-Config - global gültige Betriebsparameter (Schriftzug, Ziel-URL der
   Schnellkonfiguration). Persistiert unter DATA_DIR/site.json.
"""

import json
import os
import threading
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
SITE_FILE = DATA_DIR / "site.json"

_lock = threading.Lock()

# ---------------------------------------------------------------- Overrides

OVERRIDE_SCHEMA: list[dict] = [
    {
        "key": "tx_power",
        "pref": "USERPREFS_CONFIG_LORA_TX_POWER",
        "label": "TX-Power (dBm)",
        "type": "int",
        "min": 0,
        "max": 30,
        "placeholder": "Standard der Region",
        "help": "Nur setzen wenn bewusst abgewichen werden soll. Leer = Firmware-Default.",
    },
    {
        "key": "region",
        "pref": "USERPREFS_CONFIG_LORA_REGION",
        "label": "LoRa-Region",
        "type": "enum",
        "options": [
            "meshtastic_Config_LoRaConfig_RegionCode_EU_868",
            "meshtastic_Config_LoRaConfig_RegionCode_EU_433",
            "meshtastic_Config_LoRaConfig_RegionCode_US",
        ],
        "help": "In Deutschland gesetzlich EU_868.",
    },
    {
        "key": "modem_preset",
        "pref": "USERPREFS_LORACONFIG_MODEM_PRESET",
        "label": "Modem-Preset",
        "type": "enum",
        "options": [
            "meshtastic_Config_LoRaConfig_ModemPreset_SHORT_SLOW",
            "meshtastic_Config_LoRaConfig_ModemPreset_SHORT_FAST",
            "meshtastic_Config_LoRaConfig_ModemPreset_MEDIUM_SLOW",
            "meshtastic_Config_LoRaConfig_ModemPreset_MEDIUM_FAST",
            "meshtastic_Config_LoRaConfig_ModemPreset_LONG_FAST",
        ],
        "help": "MeshHessen-Standard ist SHORT_SLOW.",
    },
    {
        "key": "hop_limit",
        "pref": "USERPREFS_CONFIG_LORA_HOP_LIMIT",
        "label": "Hop Limit",
        "type": "int",
        "min": 1,
        "max": 7,
    },
    {
        "key": "channel_1_name",
        "pref": "USERPREFS_CHANNEL_1_NAME",
        "label": "Kanal 1 Name",
        "type": "str",
    },
    {
        "key": "channel_1_psk",
        "pref": "USERPREFS_CHANNEL_1_PSK",
        "label": "Kanal 1 PSK",
        "type": "str",
        "placeholder": "{ 0xfa, 0xe4, ... }",
        "help": "C-Array-Form. Leer = PSK aus der Basis-userPrefs.",
    },
    {
        "key": "splash_text",
        "pref": None,
        "label": "Splash-Schriftzug (komplett)",
        "type": "str",
        "help": "Ersetzt den gesamten Text unter dem Logo, inkl. Personalisierung.",
    },
]

OVERRIDE_BY_KEY = {s["key"]: s for s in OVERRIDE_SCHEMA}


def clean_overrides(raw: dict | None) -> dict:
    """Validiert Admin-Overrides. Leere Werte werden verworfen (= kein Override)."""
    if not raw:
        return {}
    out: dict = {}
    for key, value in raw.items():
        spec = OVERRIDE_BY_KEY.get(key)
        if spec is None:
            continue
        if value is None or (isinstance(value, str) and not value.strip()):
            continue
        if spec["type"] == "int":
            try:
                value = int(value)
            except (TypeError, ValueError):
                continue
            lo, hi = spec.get("min"), spec.get("max")
            if lo is not None and value < lo:
                continue
            if hi is not None and value > hi:
                continue
        elif spec["type"] == "enum":
            if value not in spec["options"]:
                continue
        else:
            value = str(value).strip()
        out[key] = value
    return out


def overrides_to_prefs(overrides: dict) -> dict:
    """Mappt validierte Overrides auf userPrefs-Keys (ohne die reinen UI-Keys)."""
    prefs: dict = {}
    for key, value in overrides.items():
        pref = OVERRIDE_BY_KEY[key]["pref"]
        if pref:
            prefs[pref] = str(value)
    return prefs


# -------------------------------------------------------------- Site-Config

SITE_DEFAULTS = {
    "splash_prefix": "Mesh Hessen",
    "config_url": "https://config.meshhessen.de",
}


def load_site() -> dict:
    with _lock:
        if not SITE_FILE.exists():
            return dict(SITE_DEFAULTS)
        try:
            stored = json.loads(SITE_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return dict(SITE_DEFAULTS)
    merged = dict(SITE_DEFAULTS)
    merged.update({k: str(v) for k, v in stored.items() if k in SITE_DEFAULTS})
    return merged


def save_site(values: dict) -> dict:
    current = load_site()
    for key, value in values.items():
        if key in SITE_DEFAULTS and str(value).strip():
            current[key] = str(value).strip()
    with _lock:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SITE_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False))
    return current
