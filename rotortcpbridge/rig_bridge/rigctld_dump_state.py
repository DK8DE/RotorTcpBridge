"""Statischer rigctld-Protokoll-0-Block für ``\\dump_state``.

Hamlib ``netrigctl_open`` (WSJT-X, Modell „NET rigctl“) sendet nacheinander
``\\chk_vfo`` und ``\\dump_state`` und parst die Antwort zeilenweise
(siehe ``rigs/dummy/netrigctl.c``). Ohne gültigen Dump antwortet der Client mit
``RPRT -11`` / „Feature not available“.

Die Werte orientieren sich an Hamlibs Dummy-Rig (Breitband-RX/TX, gängige
Modi/VFOs); für Rig-Bridge reicht das, damit libhamlib den Socket öffnet.
"""

from __future__ import annotations

# Hamlib rig.h: DUMMY_MODES (AM|CW|RTTY|SSB|FM|WFM|CWR|RTTYR) ≈ 0x1ff
_MODES_ALL = 0x1FF
# DUMMY_VFOS (A|B|C|MEM|MAIN|SUB|MAIN_A|MAIN_B|SUB_A|SUB_B)
_VFOS = 400556039
# RIG_ANT_1..5
_ANT = 0x1F


def build_rigctld_dump_state_block() -> str:
    """Mehrzeilige Antwort auf ``\\dump_state`` (ohne abschließendes Zusatz-\\n vom Aufrufer nötig).

    Erste Zeile: Protokollversion 0 (``RIGCTLD_PROT_VER``). Danach exakt die
    von ``netrigctl_open`` erwartete Zeilenfolge bis ``has_set_parm``.
    """
    rx1 = f"150000 1500000000 {_MODES_ALL:#x} -1 -1 {_VFOS:#x} {_ANT:#x}"
    rx_end = "0 0 0 0 0 0 0"
    tx1 = f"150000 1500000000 {_MODES_ALL:#x} 5000 100000 {_VFOS:#x} {_ANT:#x}"
    tx_end = rx_end
    ts1 = f"{_MODES_ALL:#x} 1"
    ts2 = f"{_MODES_ALL:#x} 0"
    ts_end = "0 0"
    flt1 = "0xc 2400"
    flt_end = "0 0"
    max_rit = "9990"
    max_xit = "9990"
    max_ifshift = "10000"
    announces = "0"
    preamp = "10 0 0 0 0 0 0"
    att = "10 20 30 0 0 0 0"
    # Dummy: alle get-Funktionen/-Level/-Parms (uint64 „alles“)
    u64max = "0xffffffffffffffff"
    # DUMMY_LEVEL: alle Level außer SQLSTAT (Bit 27 aus)
    u64_level = "0xfffffffff7ffffff"
    lines = [
        "0",  # rigctld protocol version
        "0",  # erste Zeile nach Version (wird von Hamlib verworfen)
        "0",  # deprecated ITU region
        rx1,
        rx_end,
        tx1,
        tx_end,
        ts1,
        ts2,
        ts_end,
        flt1,
        flt_end,
        max_rit,
        max_xit,
        max_ifshift,
        announces,
        preamp,
        att,
        u64max,  # has_get_func
        u64max,  # has_set_func
        u64_level,  # has_get_level
        u64_level,  # has_set_level (vereinfacht wie get_level)
        u64max,  # has_get_parm
        u64max,  # has_set_parm
    ]
    return "\n".join(lines) + "\n"
