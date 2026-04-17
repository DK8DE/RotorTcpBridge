"""CAT-Befehle für einfache Funkgeräte-Tests (Marke → Rohbytes).

Hinweis: Viele Geräte haben unterschiedliche Protokolle. Für den
„Verbindung testen“-Knopf reicht zunächst eine sinnvolle Heuristik
nach Hersteller; Icom CI-V ist binär und wird separat behandelt.
"""

from __future__ import annotations

import sys


def normalize_com_port(port: str) -> str:
    """Windows: reine Ziffer '8' → 'COM8'; sonst unverändert."""
    s = (port or "").strip()
    if not s:
        return s
    if sys.platform == "win32":
        up = s.upper()
        if up.startswith("COM"):
            return s
        if s.isdigit():
            return f"COM{s}"
    return s


def _yaesu_fa_digit_count(rig_model: str, hamlib_rig_id: int) -> int:
    """Anzahl Ziffern nach „FA“ für Yaesu-CAT (Hamlib newcat-orientiert).

    FT-991 / FT-950 / FTdx… nutzen laut Hamlib ``newcat_set_freq`` typisch **9** Stellen
    (z. B. ``FA144300000;``). Ältere/Portable-Serien erwarten oft **11** Stellen.
    """
    m = (rig_model or "").upper().replace(" ", "")
    # Ältere Modelle (häufig 11-stelliges FA in Literatur)
    if any(
        x in m
        for x in (
            "FT-817",
            "FT817",
            "FT-857",
            "FT857",
            "FT-897",
            "FT897",
            "FT-847",
            "FT847",
            "FT-920",
            "FT920",
            "FT-900",
            "FT900",
            "FT-1000",
            "FT1000",
        )
    ):
        return 11
    # FT-991 (Hamlib 1035), FT-991A, FT-950, FT-891, FT-710, FTDX … → 9 Stellen
    if any(
        x in m
        for x in (
            "FT-991",
            "FT991",
            "FT-950",
            "FT950",
            "FT-891",
            "FT891",
            "FT-710",
            "FT710",
            "FTDX",
        )
    ):
        return 9
    # Hamlib: FT-991 = 1035, FT-991A = 1036 (falls Modellstring fehlt)
    if int(hamlib_rig_id or 0) in (1035, 1036):
        return 9
    # Standard: 9 (newcat-Generation); bei Bedarf später konfigurierbar
    return 9


def build_set_frequency_payload(
    brand: str,
    freq_hz: int,
    rig_model: str = "",
    hamlib_rig_id: int = 0,
) -> tuple[bytes, str]:
    """Set-Frequenz-Befehl als Bytes und kurze Beschreibung.

    Args:
        brand: Herstellername (z. B. aus Hamlib-Liste).
        freq_hz: Zielfrequenz in Hz (z. B. 144_300_000).
        rig_model: Modellname (für Yaesu: Ziffernanzahl nach FA).
        hamlib_rig_id: Hamlib-Rig-# (Hilfe für Yaesu newcat vs. alt).

    Returns:
        (payload, beschreibung)
    """
    hz = int(freq_hz)
    if hz <= 0:
        raise ValueError("Frequenz muss > 0 Hz sein")

    b = (brand or "").strip().lower()

    # Yaesu / Vertex: ASCII-CAT, Stellenzahl nach Modell (FT-991 → 9, nicht 11!)
    if "yaesu" in b or "vertex" in b:
        nd = _yaesu_fa_digit_count(rig_model, hamlib_rig_id)
        if nd < 8 or nd > 11:
            nd = 9
        s = f"FA{hz:0{nd}d};"
        return (
            s.encode("ascii", errors="strict"),
            f"Yaesu/Vertex CAT ({nd} Stellen nach FA, Hamlib-newcat-orientiert): {s!r}",
        )

    # Kenwood (typische HF-KAT): oft gleiches FA-Schema wie Yaesu
    if "kenwood" in b:
        s = f"FA{hz:011d};"
        return s.encode("ascii", errors="strict"), f"Kenwood CAT (FA-Schema): {s!r}"

    # Elecraft K2/K3/K4: oft ähnliche ASCII-Kette (vereinfacht gleiches Schema)
    if "elecraft" in b:
        s = f"FA{hz:011d};"
        return s.encode("ascii", errors="strict"), f"Elecraft (FA-Schema, vereinfacht): {s!r}"

    # Icom CI-V: binär; minimale Frequenz-Set-Sequenz (5 BCD-Bytes, Subadresse 0x05)
    if "icom" in b:
        frame = _icom_set_freq_frame(hz)
        return frame, f"Icom CI-V (binär, {len(frame)} Bytes): {frame.hex()}"

    # Standard-Fallback: newcat-typisches 9-stelliges FA
    s = f"FA{hz:09d};"
    return s.encode("ascii", errors="strict"), f"Generisch (FA 9 Stellen): {s!r}"


def build_read_vfo_frequency_query(brand: str) -> tuple[bytes, str]:
    """ASCII-CAT: VFO-A-Frequenz lesen (Yaesu/Kenwood/Elecraft: ``FA;``).

    Returns:
        (payload, beschreibung). Payload leer → Lesebefehl für diese Marke nicht vorgesehen.
    """
    b = (brand or "").strip().lower()
    if "icom" in b:
        return (
            b"",
            "Icom CI-V: Frequenz-Lesebefehl im Verbindungstest nicht implementiert",
        )
    q = "FA;"
    return q.encode("ascii", errors="strict"), f"CAT Lesebefehl VFO-A (ASCII): {q!r}"


def _normalize_hamlib_mode_name(mode: str) -> str:
    """Hamlib-/Rigctld-Modusbezeichner vereinheitlichen (Großschreibung, gängige Aliase)."""
    m = (mode or "").strip().upper().replace("-", "_")
    if not m:
        return "USB"
    aliases = {
        "DIGU": "PKTUSB",
        "DIGL": "PKTLSB",
        "DATA_U": "PKTUSB",
        "DATA_L": "PKTLSB",
        "USB_D": "PKTUSB",
        "LSB_D": "PKTLSB",
        "RTTY_R": "RTTYR",
    }
    return aliases.get(m, m)


def _yaesu_newcat_mode_char(hamlib_mode: str) -> str:
    """Ein-Zeichen-Modus wie Hamlib ``newcat_modechar`` (Yaesu newcat, u. a. FT-991)."""
    m = _normalize_hamlib_mode_name(hamlib_mode)
    # Hamlib rigs/yaesu/newcat.c newcat_mode_conv
    table: dict[str, str] = {
        "LSB": "1",
        "USB": "2",
        "CW": "3",
        "FM": "4",
        "WFM": "4",
        "AM": "5",
        "RTTY": "6",
        "CWR": "7",
        "PKTLSB": "8",
        "RTTYR": "9",
        "PKTFM": "A",
        "FMN": "B",
        "PKTUSB": "C",
        "AMN": "D",
        "C4FM": "E",
        "PKTFMN": "F",
    }
    return table.get(m, "2")


def _kenwood_style_md_char(hamlib_mode: str) -> str:
    """Kenwood-typisch: ``MD`` + ein Zeichen (0–9, A–F); HF-Standard nach Hamlib-Kenwood-Pfad.

    Vereinfacht: DATA-Modi werden auf die zugehörige SSB-/FM-Grundlage gemappt, wenn kein
    separates ``DT``-Kommando gesendet wird (wie bei SETMODE über Rig-Bridge üblich).
    """
    m = _normalize_hamlib_mode_name(hamlib_mode)
    # Häufige Zuordnung (u. a. TS-2000/480/590-Familie, Elecraft K3/K4-ähnlich)
    table: dict[str, str] = {
        "LSB": "1",
        "PKTLSB": "1",
        "USB": "2",
        "PKTUSB": "2",
        "CW": "3",
        "FM": "4",
        "WFM": "4",
        "PKTFM": "4",
        "FMN": "4",
        "AM": "5",
        "AMN": "5",
        "RTTY": "6",
        "CWR": "7",
        "RTTYR": "7",
        "C4FM": "4",
        "PKTFMN": "4",
    }
    return table.get(m, "2")


def build_set_mode_payload(
    brand: str,
    mode: str,
    _rig_model: str = "",
    _hamlib_rig_id: int = 0,
) -> tuple[bytes, str]:
    """Betriebsart per CAT setzen (Hamlib-Modusname, z. B. ``USB``, ``CW``).

    Returns:
        (payload, beschreibung). Leeres Payload → kein CAT (z. B. Icom CI-V-Moduswechsel
        ist gerätespezifisch binär und hier nicht implementiert).
    """
    b = (brand or "").strip().lower()
    label = (mode or "").strip() or "USB"
    norm = _normalize_hamlib_mode_name(label)

    if "icom" in b:
        return (
            b"",
            "Icom CI-V: Modus-CAT im Rig-Bridge-Pfad nicht implementiert (nur State)",
        )

    if "yaesu" in b or "vertex" in b:
        ch = _yaesu_newcat_mode_char(norm)
        s = f"MD0{ch};"
        return (
            s.encode("ascii", errors="strict"),
            f"Yaesu/Vertex newcat-Modus-CAT: {s!r} (Hamlib-Modus {norm!r})",
        )

    if "kenwood" in b or "elecraft" in b:
        ch = _kenwood_style_md_char(norm)
        s = f"MD{ch};"
        return (
            s.encode("ascii", errors="strict"),
            f"Kenwood/Elecraft-Modus-CAT (vereinfacht): {s!r} (Hamlib-Modus {norm!r})",
        )

    ch = _yaesu_newcat_mode_char(norm)
    s = f"MD0{ch};"
    return (
        s.encode("ascii", errors="strict"),
        f"Generisch newcat-ähnlich: {s!r} (Hamlib-Modus {norm!r})",
    )


def build_ptt_payload(
    brand: str,
    on: bool,
    _rig_model: str = "",
    _hamlib_rig_id: int = 0,
) -> tuple[bytes, str]:
    """PTT per ASCII-CAT.

    - Yaesu/Vertex (newcat): ``TX0;`` = RX, ``TX1;`` = TX (wie Hamlib newcat).
    - Kenwood / Elecraft (Hamlib ``kenwood_set_ptt``): ``TX;`` = TX, ``RX;`` = RX.

    Args:
        brand: Hersteller (z. B. Yaesu).
        on: True = Senden, False = Empfang.
        _rig_model / _hamlib_rig_id: für spätere Modell-Sonderfälle reserviert.

    Returns:
        (payload, beschreibung). Leeres Payload → kein CAT (z. B. Icom CI-V).
    """
    b = (brand or "").strip().lower()
    if "icom" in b:
        return (
            b"",
            "Icom CI-V: PTT-CAT im Rig-Bridge-Pfad nicht implementiert",
        )
    if "kenwood" in b or "elecraft" in b:
        s = "TX;" if on else "RX;"
        who = "Kenwood" if "kenwood" in b else "Elecraft"
        return (
            s.encode("ascii", errors="strict"),
            f"PTT CAT ({who}, Hamlib kenwood_set_ptt): {s!r}",
        )
    ch = "1" if on else "0"
    s = f"TX{ch};"
    return (
        s.encode("ascii", errors="strict"),
        f"PTT CAT (Yaesu/Vertex newcat): {s!r}",
    )


def parse_fa_style_frequency_hz(raw: bytes) -> int | None:
    """Aus CAT-Antwort Hz extrahieren, sobald ein ``FA`` + Ziffern vorkommt (letztes Vorkommen).

    Typische Yaesu-Antwort auf ``FA;``: ``FA144300000;``
    """
    if not raw:
        return None
    s = raw.decode("ascii", errors="replace").upper()
    best: int | None = None
    idx = 0
    while True:
        p = s.find("FA", idx)
        if p < 0:
            break
        j = p + 2
        digits: list[str] = []
        while j < len(s) and s[j].isdigit():
            digits.append(s[j])
            j += 1
        if digits:
            try:
                best = int("".join(digits))
            except ValueError:
                pass
        idx = p + 2
    return best


def _icom_bcd_5_from_hz(freq_hz: int) -> bytes:
    """10 Hz-Schritte in 5 BCD-Bytes (wie viele Icom-Rigs)."""
    step = max(1, int(round(int(freq_hz) / 10)))
    digits = f"{step:010d}"  # 10 Ziffern für 5 Byte BCD
    out = bytearray()
    for i in range(0, 10, 2):
        hi = int(digits[i])
        lo = int(digits[i + 1])
        out.append((hi << 4) | lo)
    return bytes(out)


def _icom_set_freq_frame(freq_hz: int) -> bytes:
    """Sehr vereinfachte CI-V-Sequenz: Zieladresse 0x94 (typ. HF-TRX), Controller 0xE0.

    Wenn dein Gerät eine andere Adresse nutzt, muss die später konfigurierbar werden.
    """
    bcd = _icom_bcd_5_from_hz(freq_hz)
    # FE FE 94 E0 05 [bcd5] FD
    return bytes([0xFE, 0xFE, 0x94, 0xE0, 0x05, *bcd, 0xFD])
