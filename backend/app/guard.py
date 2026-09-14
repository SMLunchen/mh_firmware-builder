"""Missbrauchsschutz für den Build-Endpunkt.

Ein Build kostet 5–15 Minuten CPU, ein Cache-Treffer nichts. Geschützt werden
muss deshalb nur der Fall, in dem tatsächlich kompiliert wird.

Zwei Ebenen, die verschiedene Dinge tun:

* **Ressourcengrenzen** schützen die Maschine. Beschränkte Warteschlange und
  ein Limit pro IP sorgen dafür, dass niemand den Dienst blockieren kann —
  auch nicht jemand, der die Rechenaufgabe löst.
* **Proof of Work** ist die Hürde davor. Sie macht Massenabfragen spürbar
  teuer und hält Gelegenheits-Skripte und Crawler fern. Gegen einen
  entschlossenen Angreifer mit Rechenleistung hilft sie nicht — dafür sind
  die Grenzen da.

Angemeldete Admins umgehen beides.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import threading
import time

# Führende Nullbits, die der Hash haben muss. 0 schaltet die Aufgabe ab.
# 20 Bits sind rund eine Million Versuche - im Browser wenige Sekunden.
POW_DIFFICULTY = int(os.environ.get("POW_DIFFICULTY", "20"))
CHALLENGE_TTL = int(os.environ.get("POW_TTL", "600"))

# Wieviele Builds gleichzeitig warten dürfen, bevor abgelehnt wird.
MAX_QUEUE = int(os.environ.get("MAX_QUEUE", "5"))
# Neue Builds je IP und Stunde. Cache-Treffer zählen nicht mit.
BUILDS_PER_HOUR = int(os.environ.get("BUILDS_PER_HOUR", "3"))

_lock = threading.Lock()
_challenges: dict[str, float] = {}      # Token -> Ablaufzeitpunkt
_recent: dict[str, list[float]] = {}    # IP -> Zeitpunkte gestarteter Builds


# ------------------------------------------------------------ Proof of Work

def issue_challenge() -> dict:
    """Neue Aufgabe ausgeben. Jede ist einmalig verwendbar und läuft ab."""
    token = secrets.token_hex(16)
    now = time.time()
    with _lock:
        # Abgelaufene mitnehmen, damit der Speicher nicht wächst
        for old, expiry in list(_challenges.items()):
            if expiry < now:
                del _challenges[old]
        _challenges[token] = now + CHALLENGE_TTL
    return {
        "challenge": token,
        "difficulty": POW_DIFFICULTY,
        "expires_in": CHALLENGE_TTL,
    }


def _leading_zero_bits(digest: bytes) -> int:
    bits = 0
    for byte in digest:
        if byte == 0:
            bits += 8
            continue
        bits += 8 - byte.bit_length()
        break
    return bits


def verify_challenge(token: str, nonce: str) -> None:
    """Lösung prüfen und die Aufgabe verbrauchen. Wirft bei Fehlschlag."""
    if POW_DIFFICULTY <= 0:
        return
    if not token or not nonce:
        raise ValueError("Rechenaufgabe fehlt.")

    now = time.time()
    with _lock:
        expiry = _challenges.get(token)
        if expiry is None:
            raise ValueError("Rechenaufgabe unbekannt oder bereits verwendet.")
        if expiry < now:
            del _challenges[token]
            raise ValueError("Rechenaufgabe abgelaufen. Bitte neu anfordern.")

    digest = hashlib.sha256(f"{token}{nonce}".encode()).digest()
    if _leading_zero_bits(digest) < POW_DIFFICULTY:
        raise ValueError("Rechenaufgabe nicht korrekt gelöst.")

    # Erst nach erfolgreicher Prüfung verbrauchen: eine falsche Lösung soll
    # die Aufgabe nicht entwerten, sonst wäre das ein Denial-of-Service
    # gegen den Benutzer, der gerade rechnet.
    with _lock:
        _challenges.pop(token, None)


# ------------------------------------------------------- Grenzen pro Absender

def check_rate(client: str) -> None:
    """Prüft das Stundenlimit der IP. Wirft bei Überschreitung."""
    if BUILDS_PER_HOUR <= 0:
        return
    now = time.time()
    with _lock:
        stamps = [t for t in _recent.get(client, []) if now - t < 3600]
        if len(stamps) >= BUILDS_PER_HOUR:
            oldest = min(stamps)
            wait = int((3600 - (now - oldest)) / 60) + 1
            _recent[client] = stamps
            raise ValueError(
                f"Zu viele Builds von dieser Adresse. In etwa {wait} Minuten "
                "geht es weiter. Fertige Firmware aus dem Cache ist davon "
                "nicht betroffen."
            )
        _recent[client] = stamps


def note_build(client: str) -> None:
    """Einen gestarteten Build auf die IP buchen."""
    if BUILDS_PER_HOUR <= 0:
        return
    now = time.time()
    with _lock:
        stamps = [t for t in _recent.get(client, []) if now - t < 3600]
        stamps.append(now)
        _recent[client] = stamps
        # Ruhige Adressen wieder vergessen
        for ip, values in list(_recent.items()):
            if not values or now - max(values) > 3600:
                del _recent[ip]


def status() -> dict:
    with _lock:
        return {
            "difficulty": POW_DIFFICULTY,
            "max_queue": MAX_QUEUE,
            "builds_per_hour": BUILDS_PER_HOUR,
            "open_challenges": len(_challenges),
            "tracked_clients": len(_recent),
        }
