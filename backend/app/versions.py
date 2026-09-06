"""Verfügbare Firmware-Versionen aus den Git-Tags des Meshtastic-Repos.

Tags haben die Form v<major>.<minor>.<patch>.<commit>, z.B. v2.7.26.54e0d8d.
Rein lexikalisch sortieren sie falsch (v2.7.2 landet hinter v2.7.19), und
dieselbe Version kann mit zwei Commits getaggt sein (v2.8.0.47db0e3 und
v2.8.0.7239fe8). Deshalb sortieren wir nach Commit-Datum, wo verfügbar, sonst
numerisch nach Versionsbestandteilen.

FIRMWARE_REF darf sein:
  "v2.7.26.54e0d8d" - genau dieser Tag
  "2.7" / "v2.7"    - neuester Tag dieser Minor-Reihe
  "latest"          - neuester Tag überhaupt
"""

from __future__ import annotations

import os
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

FIRMWARE_DIR = Path(os.environ.get("FIRMWARE_DIR", "/firmware"))
FIRMWARE_REPO = os.environ.get(
    "FIRMWARE_REPO", "https://github.com/meshtastic/firmware.git")

_TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)(?:\.([0-9a-f]+))?$")
_CACHE_TTL = 900

_lock = threading.Lock()
_cache: list[Tag] = []
_cache_time = 0.0


@dataclass(frozen=True)
class Tag:
    name: str
    major: int
    minor: int
    patch: int
    commit: str

    @property
    def series(self) -> str:
        return f"{self.major}.{self.minor}"

    @property
    def version(self) -> str:
        return f"{self.major}.{self.minor}.{self.patch}"

    def as_dict(self) -> dict:
        return {"ref": self.name, "version": self.version, "series": self.series}


def _parse(name: str) -> Tag | None:
    match = _TAG.match(name.strip())
    if not match:
        return None
    return Tag(name, int(match.group(1)), int(match.group(2)),
               int(match.group(3)), match.group(4) or "")


def _from_local() -> list[Tag]:
    """Tags aus dem geklonten Repo, nach Commit-Datum absteigend."""
    if not (FIRMWARE_DIR / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "tag", "--list", "v*", "--sort=-creatordate"],
        cwd=FIRMWARE_DIR, capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        return []
    return [t for t in (_parse(line) for line in result.stdout.splitlines()) if t]


def _from_remote() -> list[Tag]:
    """Fallback, solange das Repo noch nicht geklont ist. Ohne Datums-
    information, deshalb numerisch sortiert."""
    result = subprocess.run(
        ["git", "ls-remote", "--tags", "--refs", FIRMWARE_REPO],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return []
    tags = []
    for line in result.stdout.splitlines():
        _, _, ref = line.partition("refs/tags/")
        parsed = _parse(ref)
        if parsed:
            tags.append(parsed)
    tags.sort(key=lambda t: (t.major, t.minor, t.patch, t.commit), reverse=True)
    return tags


def all_tags(force: bool = False) -> list[Tag]:
    global _cache, _cache_time
    with _lock:
        fresh = _cache and (time.time() - _cache_time) < _CACHE_TTL
        if fresh and not force:
            return _cache
        tags = _from_local() or _from_remote()
        if tags:
            _cache = tags
            _cache_time = time.time()
        return _cache


def resolve(spec: str) -> str:
    """Spezifikation auf einen konkreten Tag abbilden.

    Ein unbekannter, aber plausibel aussehender Wert wird unverändert
    durchgereicht - so lassen sich auch Branches oder Commit-Hashes bauen.
    """
    spec = (spec or "").strip()
    tags = all_tags()

    if not spec or spec == "latest":
        return tags[0].name if tags else "master"

    if any(t.name == spec for t in tags):
        return spec

    series = spec.lstrip("vV")
    if re.fullmatch(r"\d+\.\d+", series):
        matching = [t for t in tags if t.series == series]
        if matching:
            return matching[0].name

    if re.fullmatch(r"\d+\.\d+\.\d+", series):
        matching = [t for t in tags if t.version == series]
        if matching:
            return matching[0].name

    return spec


def series_overview() -> list[dict]:
    """Versionen fürs UI: je Minor-Reihe die neuesten Tags."""
    tags = all_tags()
    grouped: dict[str, list[Tag]] = {}
    for tag in tags:
        grouped.setdefault(tag.series, []).append(tag)

    overview = []
    for series in sorted(grouped, key=lambda s: tuple(int(p) for p in s.split(".")),
                         reverse=True):
        entries = grouped[series]
        overview.append({
            "series": series,
            "latest": entries[0].as_dict(),
            "tags": [t.as_dict() for t in entries[:25]],
        })
    return overview
