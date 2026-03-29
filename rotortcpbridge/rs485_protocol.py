# RS485 ASCII Protokoll
# Format: #SRC:DST:CMD:PARAMS:CS$
# CS = SRC + DST + letzte Zahl in PARAMS (wenn keine Zahl -> 0)
# Zahlen in PARAMS dürfen Komma oder Punkt haben. Firmware antwortet meist mit Komma.
#
# WICHTIG:
# - Viele Beispiele zeigen die Checksumme ohne Nachkommastellen (z.B. ":27$").
# - Daher formatieren wir CS so:
#   * wenn CS (nahezu) ganzzahlig -> als Integer ohne Dezimalteil
#   * sonst -> mit Komma und max. 2 Nachkommastellen, ohne unnötige Nullen

from __future__ import annotations
import re
from dataclasses import dataclass

NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")

# RS485: Broadcast-Ziel (alle Teilnehmer), z. B. SETASELECT
BROADCAST_DST = 255


@dataclass
class Telegram:
    src: int
    dst: int
    cmd: str
    params: str
    cs: float
    ok: bool


def _last_number(params: str) -> float:
    m = NUM_RE.findall(params or "")
    if not m:
        return 0.0
    return float(m[-1].replace(",", "."))


def calc_checksum(src: int, dst: int, params: str) -> float:
    return float(src + dst) + _last_number(params)


def _fmt_cs(cs: float) -> str:
    # Ganzzahl? -> ohne Nachkommastellen
    if abs(cs - round(cs)) < 0.005:
        return str(int(round(cs)))
    # Sonst max. 2 Nachkommastellen, dann trailing zeros entfernen
    s = f"{cs:.2f}".replace(".", ",")
    # "12,30" -> "12,3", "12,00" -> "12"
    if "," in s:
        s = s.rstrip("0").rstrip(",")
    return s


def build(src: int, dst: int, cmd: str, params: str) -> str:
    cs = calc_checksum(src, dst, params)
    return f"#{src}:{dst}:{cmd}:{params}:{_fmt_cs(cs)}$"


def parse(line: str) -> Telegram | None:
    line = line.strip()
    if not (line.startswith("#") and line.endswith("$")):
        return None
    body = line[1:-1]
    parts = body.split(":")
    if len(parts) < 5:
        return None
    try:
        src = int(parts[0])
        dst = int(parts[1])
    except Exception:
        return None
    cmd = parts[2].strip()
    params = ":".join(parts[3:-1]).strip()
    try:
        cs = float(parts[-1].strip().replace(",", "."))
    except Exception:
        return None
    expected = calc_checksum(src, dst, params)
    ok = abs(cs - expected) <= 0.02
    return Telegram(src=src, dst=dst, cmd=cmd, params=params, cs=cs, ok=ok)
