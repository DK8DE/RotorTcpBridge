"""CAT-Responder: simulierten Funkgeraet-CAT-Kanal auf einer virtuellen
Schnittstelle bedienen.

Die Responder uebersetzen eingehende CAT-Befehle (ASCII ``FA;`` etc. oder
Icom-CI-V-Binaerframes) in Lese- oder Schreiboperationen am zentralen
``RadioStateCache`` / ``RadioConnectionManager``. Schreibzugriffe werden
in dieselbe Kommando-Queue (``SETFREQ``/``SETMODE``/``SETPTT``) gelegt,
die auch die FLRig- und Hamlib-Server benutzen — so kann der Worker das
vorhandene SETFREQ-Gap und CAT-Drain-Timing anwenden, unabhaengig vom
Ursprung des Kommandos.

Lesezugriffe nutzen ``get_state()`` und — wenn verfuegbar —
``refresh_frequency_for_read()``, damit die Antwort die aktuelle Frequenz
vom echten TRX widerspiegelt (dasselbe Muster wie im Hamlib-Server).

Leistungsumfang (Version 1):

- Yaesu newcat (FT-991/950/891/710/FTdx…): ``FA;``/``FB;``, ``FA<digits>;``,
  ``MD;``/``MD0<ch>;``, ``IF;``, ``TX;``/``TX0;``/``TX1;``, ``PS;``, ``ID;``.
- Yaesu legacy (FT-817/857/897…): ``FA;`` mit 11 Stellen.
- Kenwood/Elecraft: ``FA;`` 11 Stellen, ``MD;``, ``IF;``, ``TX;``/``RX;``.
- Icom CI-V: ``FE FE DST SRC CMD [SUB] [DATA] FD`` — lesbar nur
  Frequenz (0x03) und PTT-Status (0x1C 0x00); schreibend Frequenz (0x05)
  und PTT (0x1C 0x00 mit Datenbyte). Unbekannte Befehle werden mit NAK
  (0xFA) beantwortet.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from .cat_commands import (
    _normalize_hamlib_mode_name,
    _yaesu_fa_digit_count,
    _yaesu_newcat_mode_char,
)


# --------------------------------------------------------------------- helpers

_NEWCAT_MODE_CHAR_TO_NAME: dict[str, str] = {
    "1": "LSB",
    "2": "USB",
    "3": "CW",
    "4": "FM",
    "5": "AM",
    "6": "RTTY",
    "7": "CWR",
    "8": "PKTLSB",
    "9": "RTTYR",
    "A": "PKTFM",
    "B": "FMN",
    "C": "PKTUSB",
    "D": "AMN",
    "E": "C4FM",
    "F": "PKTFMN",
}

_KENWOOD_MODE_CHAR_TO_NAME: dict[str, str] = {
    "1": "LSB",
    "2": "USB",
    "3": "CW",
    "4": "FM",
    "5": "AM",
    "6": "RTTY",
    "7": "CWR",
    "9": "RTTYR",
}


class CatResponder:
    """Schnittstelle fuer alle Responder.

    ``feed(data)`` konsumiert Byte-Eingaben vom virtuellen COM, liefert
    eine Liste der zu sendenden Antwort-Byte-Pakete zurueck.
    """

    def feed(self, data: bytes) -> list[bytes]:  # pragma: no cover - abstract
        raise NotImplementedError


class _AsciiResponderBase(CatResponder):
    """Gemeinsame Basis fuer alle ``;``-terminierten ASCII-CAT-Protokolle."""

    def __init__(
        self,
        *,
        get_state: Callable[[], dict],
        enqueue_write: Callable[[str, str], None],
        refresh_frequency_for_read: Optional[Callable[[float], bool]] = None,
        on_state_patch: Optional[Callable[[dict], None]] = None,
        log_label: str = "CAT-Sim",
    ) -> None:
        self._get_state = get_state
        self._enqueue_write = enqueue_write
        self._refresh_frequency = refresh_frequency_for_read
        # Optimistischer State-Patch vor dem Enqueue (analog Hamlib-Server):
        # ohne ihn lesen externe Programme unmittelbar nach ihrem eigenen
        # ``FA<neu>;`` einen noch unveraenderten Cache-Wert und werten das
        # als "SET nicht angenommen" → Anzeige springt / blockiert.
        self._on_state_patch = on_state_patch
        self._log_label = log_label
        self._buf = bytearray()
        #: FA-Stellen (9 fuer newcat, 11 fuer legacy/Kenwood).
        self._fa_digits: int = 9
        #: Throttle: Spam-Poller (Ham Radio Deluxe, Logger32 …) triggern sonst
        #: bei jedem ``FA;`` einen synchronen READFREQ und blockieren den
        #: Listener-Thread. Innerhalb dieses Fensters wird der Cache-Wert
        #: direkt zurueckgeliefert.
        self._last_refresh_mono: float = 0.0
        self._refresh_min_interval_s: float = 0.15

    # ------------------------------------------------------------ framing
    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buf.extend(data)
        out: list[bytes] = []
        while True:
            try:
                idx = self._buf.index(b";")
            except ValueError:
                break
            cmd = bytes(self._buf[: idx + 1])
            del self._buf[: idx + 1]
            try:
                resp = self._handle(cmd)
            except Exception:
                resp = None
            if resp:
                out.append(resp)
        # Aufraeumen: wenn der Puffer ueber eine vernuenftige Groesse
        # hinauswaechst, ohne dass je ein `;` kam, droht er ohne Bremse
        # zu wachsen — verwerfen.
        if len(self._buf) > 256:
            self._buf.clear()
        return out

    # ---------------------------------------------------- command dispatch
    def _handle(self, cmd: bytes) -> Optional[bytes]:
        try:
            s = cmd.decode("ascii", errors="ignore").strip()
        except Exception:
            return None
        if not s or not s.endswith(";"):
            return None
        body = s[:-1]
        head = body[:2].upper()
        payload = body[2:]
        method = getattr(self, f"_cmd_{head}", None)
        if method is None:
            return self._unsupported(head, payload)
        return method(payload)

    def _unsupported(self, head: str, _payload: str) -> Optional[bytes]:
        # Viele Yaesu- und Kenwood-TRX liefern bei unbekanntem Befehl "?;".
        return b"?;"

    # ------------------------------------------------------------- helpers
    def _state(self) -> dict:
        return self._get_state() or {}

    def _refresh(self) -> None:
        """Bei Lese-Kommandos (``FA;``/``FB;``/``IF;``) *gegebenenfalls* einen
        frischen READFREQ am TRX triggern.

        Wichtig: wir blockieren hier den Listener-Thread (er liest und schreibt
        den virtuellen COM zur Gegenstelle). Spam-Poller wuerden sonst jeden
        Poll 100–650 ms anhalten → OS-Serial-Puffer der com0com-Seite laeuft
        voll, nachfolgende SETFREQ-Befehle werden verspaetet, die Frequenz am
        externen Programm wirkt „zerhackt".

        Strategie: innerhalb von ``_refresh_min_interval_s`` seit dem letzten
        Refresh sofort den aktuellen Cache-Wert nutzen (der Cache wird vom
        Manager optimistisch beim SETFREQ und vom Worker nach READFREQ-Reply
        aktualisiert; er ist dadurch praktisch nie mehr als einen Poll-Zyklus
        alt). Nur ausserhalb dieses Fensters wird der Refresh auf die
        Worker-Queue gelegt — und dann nur mit kurzem Timeout (200 ms), damit
        ein mal ueberlasteter TRX nicht den Listener zum Stehen bringt."""
        if self._refresh_frequency is None:
            return
        now = time.monotonic()
        if (now - self._last_refresh_mono) < self._refresh_min_interval_s:
            return
        self._last_refresh_mono = now
        try:
            self._refresh_frequency(0.20)
        except Exception:
            pass

    def _patch_state(self, patch: dict) -> None:
        """State-Cache sofort mit der eben geschriebenen Groesse aktualisieren.

        Wird vor dem Enqueue aufgerufen, damit der naechste Lese-Poll vom
        externen Programm *seinen eigenen* SET-Wert zurueckbekommt — nicht
        den noch alten Cache-Inhalt. Ohne das wirkt die Anzeige beim
        Benutzer "zerhackt" oder "stockend", weil zwischen ``FA<neu>;`` und
        dem tatsaechlichen CAT-TX zum TRX bis zu ~400 ms vergehen koennen
        (Worker-Queue + SETFREQ-TX + Drain + Post-SET-Suppress)."""
        if not patch or self._on_state_patch is None:
            return
        try:
            self._on_state_patch(dict(patch))
        except Exception:
            pass

    def _freq_hz_str(self, hz: int, digits: int | None = None) -> str:
        nd = int(digits if digits is not None else self._fa_digits)
        nd = max(6, min(12, nd))
        hz = max(0, int(hz))
        return f"{hz:0{nd}d}"

    # --------------------------------------------- default command handlers
    def _cmd_FA(self, payload: str) -> Optional[bytes]:
        """VFO-A Frequenz: ``FA;`` lesen, ``FA<n>;`` schreiben."""
        if payload == "":
            self._refresh()
            hz = int(self._state().get("frequency_hz", 0) or 0)
            return f"FA{self._freq_hz_str(hz)};".encode("ascii")
        p = "".join(ch for ch in payload if ch.isdigit())
        if not p:
            return b"?;"
        try:
            hz = int(p)
        except ValueError:
            return b"?;"
        self._patch_state({"frequency_hz": int(hz)})
        self._enqueue_write(f"SETFREQ {hz}", f"{self._log_label}: FA{p};")
        return None

    def _cmd_FB(self, payload: str) -> Optional[bytes]:
        """VFO-B: aus ``frequency_hz`` gespiegelt (wir halten nur einen VFO)."""
        if payload == "":
            self._refresh()
            hz = int(self._state().get("frequency_hz", 0) or 0)
            return f"FB{self._freq_hz_str(hz)};".encode("ascii")
        # Setzen von VFO-B ignorieren (State kennt kein FB).
        return None


# ---------------------------------------------------------------- Yaesu newcat

class YaesuNewcatResponder(_AsciiResponderBase):
    """FT-991/950/891/710/FTdx — 9-stelliges FA, ``MD0<ch>;``, ``IF;``, ``TX;``."""

    def __init__(self, *args, rig_model: str = "", hamlib_rig_id: int = 0, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fa_digits = _yaesu_fa_digit_count(rig_model, hamlib_rig_id)
        self._rig_model = str(rig_model or "")
        self._hamlib_rig_id = int(hamlib_rig_id or 0)

    def _cmd_MD(self, payload: str) -> Optional[bytes]:
        """``MD0;`` lesen, ``MD0<ch>;`` schreiben (Yaesu: stets VFO-Block ``0``)."""
        if payload == "" or payload == "0":
            mode = _normalize_hamlib_mode_name(str(self._state().get("mode", "USB")))
            ch = _yaesu_newcat_mode_char(mode)
            return f"MD0{ch};".encode("ascii")
        p = payload
        if len(p) >= 2 and p[0] == "0":
            ch = p[1].upper()
            mode = _NEWCAT_MODE_CHAR_TO_NAME.get(ch, "USB")
            self._patch_state({"mode": mode})
            self._enqueue_write(f"SETMODE {mode}", f"{self._log_label}: MD0{ch};")
            return None
        # Kurzform ``MD<ch>;``
        ch = p[0].upper()
        mode = _NEWCAT_MODE_CHAR_TO_NAME.get(ch, "USB")
        self._patch_state({"mode": mode})
        self._enqueue_write(f"SETMODE {mode}", f"{self._log_label}: MD{ch};")
        return None

    def _cmd_IF(self, _payload: str) -> Optional[bytes]:
        """``IF;`` — Kompakt-Status im Yaesu-newcat-Format.

        Format laut FT-991/FT-991A/FT-891/FT-950-Referenz (26 Byte inkl.
        ``IF``/``;``):
        ``IF`` + P1 MEM(3) + P2 VFO_Freq(9) + P3 Clar_Off(5 signed)
              + P4 RX_Clar(1) + P5 TX_Clar(1) + P6 Mode(1) + P7 VFO/MEM(1)
              + P8 CTCSS(1) + P9 Tone(1) + ``;``

        Hamlib parst in WSJT-X das ``IF;``-Echo als Verifikation nach jedem
        ``FA<neu>;`` — eine abweichende Gesamtlaenge kann dazu fuehren, dass
        der letzte SETFREQ als „nicht angekommen" verworfen wird und die
        Anzeige auf den vorherigen Wert zurueckspringt.
        """
        st = self._state()
        hz = int(st.get("frequency_hz", 0) or 0)
        mode = _normalize_hamlib_mode_name(str(st.get("mode", "USB")))
        ch = _yaesu_newcat_mode_char(mode)
        p1 = "000"
        p2 = self._freq_hz_str(hz, 9)
        p3 = "+0000"
        p4 = "0"  # RX Clarifier OFF
        p5 = "0"  # TX Clarifier OFF
        p6 = ch   # Mode
        p7 = "0"  # 0 = VFO (nicht Memory/Tune)
        p8 = "0"  # CTCSS OFF
        p9 = "0"  # Tone/Shift OFF
        # Laengen-Check: 2 + 3 + 9 + 5 + 1 + 1 + 1 + 1 + 1 + 1 + 1 = 26 Byte total
        return f"IF{p1}{p2}{p3}{p4}{p5}{p6}{p7}{p8}{p9};".encode("ascii")

    def _cmd_PS(self, payload: str) -> Optional[bytes]:
        """``PS;`` — Power-Status (immer 1, wenn wir reagieren koennen)."""
        if payload == "":
            return b"PS1;"
        # ``PS0;``/``PS1;`` schreiben wir nicht weiter (kein TRX-Power-Toggle).
        return None

    def _cmd_TX(self, payload: str) -> Optional[bytes]:
        """``TX;`` lesen, ``TX0;``/``TX1;`` schreiben (Yaesu newcat)."""
        if payload == "":
            return b"TX1;" if bool(self._state().get("ptt", False)) else b"TX0;"
        if payload in ("0", "1"):
            on = payload == "1"
            self._patch_state({"ptt": bool(on)})
            self._enqueue_write("SETPTT 1" if on else "SETPTT 0", f"{self._log_label}: TX{payload};")
            return None
        return b"?;"

    def _cmd_ID(self, _payload: str) -> Optional[bytes]:
        """``ID;`` — TRX-Modell-ID (3 Stellen, Hamlib-konform best effort).

        Fuer echte Yaesu-Modelle gibt es definierte ID-Nummern
        (FT-991 -> ``0670``, FT-991A -> ``0570``, FT-710 -> ``0776``). Wir
        liefern Pseudo-IDs anhand des Modellstrings; WSJT-X/fldigi werten
        die Antwort in der Regel nur auf Laenge.
        """
        m = self._rig_model.upper().replace(" ", "").replace("-", "")
        if "FT991A" in m:
            s = "0570"
        elif "FT991" in m:
            s = "0670"
        elif "FT710" in m:
            s = "0776"
        elif "FT891" in m:
            s = "0541"
        elif "FT950" in m:
            s = "0460"
        elif "FTDX10" in m:
            s = "0761"
        else:
            s = "0670"
        return f"ID{s};".encode("ascii")

    def _cmd_AI(self, payload: str) -> Optional[bytes]:
        """``AI;`` Auto-Information: wir antworten ``AI0;`` (aus)."""
        if payload == "":
            return b"AI0;"
        return None


# ---------------------------------------------------------------- Yaesu legacy

class YaesuLegacyResponder(_AsciiResponderBase):
    """FT-817/857/897 etc. — 11-stelliges ``FA``, keine ``MD0..``-Syntax."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fa_digits = 11

    def _cmd_TX(self, payload: str) -> Optional[bytes]:
        if payload == "":
            return b"TX1;" if bool(self._state().get("ptt", False)) else b"TX0;"
        if payload in ("0", "1"):
            on = payload == "1"
            self._patch_state({"ptt": bool(on)})
            self._enqueue_write("SETPTT 1" if on else "SETPTT 0", f"{self._log_label}: TX{payload};")
            return None
        return b"?;"


# -------------------------------------------------------------------- Kenwood

class KenwoodResponder(_AsciiResponderBase):
    """Kenwood HF-Linie (TS-590/480/2000…) — 11-stelliges FA, ``MD<ch>;``, ``TX;``/``RX;``."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._fa_digits = 11

    def _cmd_MD(self, payload: str) -> Optional[bytes]:
        if payload == "":
            mode = _normalize_hamlib_mode_name(str(self._state().get("mode", "USB")))
            from .cat_commands import _kenwood_style_md_char

            ch = _kenwood_style_md_char(mode)
            return f"MD{ch};".encode("ascii")
        ch = payload[0].upper()
        mode = _KENWOOD_MODE_CHAR_TO_NAME.get(ch, "USB")
        self._patch_state({"mode": mode})
        self._enqueue_write(f"SETMODE {mode}", f"{self._log_label}: MD{ch};")
        return None

    def _cmd_IF(self, _payload: str) -> Optional[bytes]:
        """Kenwood ``IF;`` (37 Byte inkl. IF/;), minimalistisch gefuellt."""
        st = self._state()
        hz = int(st.get("frequency_hz", 0) or 0)
        mode = _normalize_hamlib_mode_name(str(st.get("mode", "USB")))
        from .cat_commands import _kenwood_style_md_char

        mch = _kenwood_style_md_char(mode)
        ptt = "1" if bool(st.get("ptt", False)) else "0"
        p_freq = self._freq_hz_str(hz, 11)
        # Platzhalterfelder (Frequenzabweichung, RIT/XIT, Memory, etc.).
        s = f"IF{p_freq}00000+0000000{ptt}00000{mch}0{0:03d}0;"
        # Laenge grob auf 38 ausrichten (2 + 11 + 5 + 8 + 1 + 5 + 1 + 3 + 1 + 1 = 38).
        return s.encode("ascii")

    def _cmd_TX(self, _payload: str) -> Optional[bytes]:
        self._patch_state({"ptt": True})
        self._enqueue_write("SETPTT 1", f"{self._log_label}: TX;")
        return None

    def _cmd_RX(self, _payload: str) -> Optional[bytes]:
        self._patch_state({"ptt": False})
        self._enqueue_write("SETPTT 0", f"{self._log_label}: RX;")
        return None

    def _cmd_PS(self, _payload: str) -> Optional[bytes]:
        return b"PS1;"

    def _cmd_ID(self, _payload: str) -> Optional[bytes]:
        # TS-590S ID = 021, TS-2000 = 019. Wir liefern 021 als gaengigen Default.
        return b"ID021;"

    def _cmd_AI(self, payload: str) -> Optional[bytes]:
        if payload == "":
            return b"AI0;"
        return None


# ------------------------------------------------------------------ Elecraft

class ElecraftResponder(KenwoodResponder):
    """K3/K4 — im Wesentlichen Kenwood-kompatibles CAT; ID laut Elecraft-Doku."""

    def _cmd_ID(self, _payload: str) -> Optional[bytes]:
        return b"ID017;"  # K3 reports "017"


# ------------------------------------------------------------------ Icom CI-V

class IcomCivResponder(CatResponder):
    """Icom CI-V Binaer-Responder (minimal).

    Unterstuetzt: Lesen/Setzen der Frequenz (0x03/0x05) und Lesen/Setzen
    von PTT (0x1C 0x00). Alles andere wird mit NAK (0xFA) beantwortet.
    """

    _PREAMBLE = b"\xFE\xFE"
    _EOM = 0xFD

    def __init__(
        self,
        *,
        get_state: Callable[[], dict],
        enqueue_write: Callable[[str, str], None],
        refresh_frequency_for_read: Optional[Callable[[float], bool]] = None,
        on_state_patch: Optional[Callable[[dict], None]] = None,
        civ_address: int = 0x94,
        controller_address: int = 0xE0,
        log_label: str = "CAT-Sim-CIV",
    ) -> None:
        self._get_state = get_state
        self._enqueue_write = enqueue_write
        self._refresh_frequency = refresh_frequency_for_read
        self._on_state_patch = on_state_patch
        self._log_label = log_label
        self._civ = int(civ_address) & 0xFF
        self._ctrl = int(controller_address) & 0xFF
        self._buf = bytearray()
        #: Siehe ``_AsciiResponderBase._refresh`` — Throttle, damit schnelle
        #: CI-V-Poller (0x03 Frequenz lesen) den Listener-Thread nicht ueber
        #: Gebuehr blockieren.
        self._last_refresh_mono: float = 0.0
        self._refresh_min_interval_s: float = 0.15

    def _refresh(self) -> None:
        if self._refresh_frequency is None:
            return
        now = time.monotonic()
        if (now - self._last_refresh_mono) < self._refresh_min_interval_s:
            return
        self._last_refresh_mono = now
        try:
            self._refresh_frequency(0.20)
        except Exception:
            pass

    def _patch_state(self, patch: dict) -> None:
        if not patch or self._on_state_patch is None:
            return
        try:
            self._on_state_patch(dict(patch))
        except Exception:
            pass

    def feed(self, data: bytes) -> list[bytes]:
        if not data:
            return []
        self._buf.extend(data)
        out: list[bytes] = []
        while True:
            frame = self._take_frame()
            if frame is None:
                break
            resp = self._handle_frame(frame)
            if resp:
                out.append(resp)
        if len(self._buf) > 1024:
            self._buf.clear()
        return out

    def _take_frame(self) -> Optional[bytes]:
        # Sync auf Preamble.
        pre_idx = self._buf.find(self._PREAMBLE)
        if pre_idx < 0:
            # Alles vor dem naechsten moeglichen Preamble verwerfen.
            if len(self._buf) >= 2:
                self._buf[: len(self._buf) - 1] = bytes([self._buf[-1]])
            return None
        if pre_idx > 0:
            del self._buf[:pre_idx]
        # Ab hier: self._buf startet mit FE FE.
        eom = self._buf.find(bytes([self._EOM]), 2)
        if eom < 0:
            return None
        frame = bytes(self._buf[: eom + 1])
        del self._buf[: eom + 1]
        return frame

    def _nak(self) -> bytes:
        return bytes([0xFE, 0xFE, self._ctrl, self._civ, 0xFA, self._EOM])

    def _ack(self) -> bytes:
        return bytes([0xFE, 0xFE, self._ctrl, self._civ, 0xFB, self._EOM])

    def _freq_to_bcd5(self, hz: int) -> bytes:
        hz = max(0, int(hz))
        s = f"{hz:010d}"  # 10 Ziffern
        # Little-endian BCD (pro Byte: hohe Ziffer = hoeherwertige Ziffer der Ziffernpaare);
        # Bytes-Reihenfolge umgedreht (Byte0 = niedrigstwertige 2 Ziffern).
        pairs = [s[i : i + 2] for i in range(0, 10, 2)][::-1]
        return bytes(int(p[1] + p[0], 16) for p in pairs)

    def _bcd5_to_freq(self, data: bytes) -> int:
        if len(data) < 5:
            return 0
        pairs = [f"{b:02X}" for b in data[:5]]
        # Reverse die Bytes und tausche Nibbles je Byte.
        digits = "".join(p[1] + p[0] for p in pairs[::-1])
        try:
            return int(digits)
        except ValueError:
            return 0

    def _handle_frame(self, frame: bytes) -> Optional[bytes]:
        # Struktur: FE FE DST SRC CMD [SUB] [DATA...] FD
        if len(frame) < 6 or frame[:2] != self._PREAMBLE or frame[-1] != self._EOM:
            return None
        dst = frame[2]
        # src = frame[3]  # Absender, wir antworten an diesen zurueck
        src = frame[3]
        cmd = frame[4]
        rest = frame[5:-1]  # ohne EOM

        # Nur Frames annehmen, die fuer unsere CI-V-Adresse oder Broadcast (0x00) sind.
        if dst not in (self._civ, 0x00):
            return None
        # Absender als neue Zielsadresse fuer die Antwort spiegeln.
        reply_dst = src & 0xFF

        def _wrap(payload: bytes) -> bytes:
            return bytes([0xFE, 0xFE, reply_dst, self._civ]) + payload + bytes([self._EOM])

        # 0x03: Frequenz lesen
        if cmd == 0x03 and not rest:
            self._refresh()
            hz = int(self._get_state().get("frequency_hz", 0) or 0)
            return _wrap(bytes([0x03]) + self._freq_to_bcd5(hz))

        # 0x05: Frequenz setzen
        if cmd == 0x05 and len(rest) >= 5:
            hz = self._bcd5_to_freq(rest)
            if hz > 0:
                self._patch_state({"frequency_hz": int(hz)})
                self._enqueue_write(f"SETFREQ {hz}", f"{self._log_label}: CI-V 0x05")
                return _wrap(bytes([0xFB]))
            return _wrap(bytes([0xFA]))

        # 0x1C 0x00: PTT lesen/setzen
        if cmd == 0x1C and len(rest) >= 1 and rest[0] == 0x00:
            if len(rest) == 1:
                ptt = bool(self._get_state().get("ptt", False))
                return _wrap(bytes([0x1C, 0x00, 0x01 if ptt else 0x00]))
            on = rest[1] != 0
            self._patch_state({"ptt": bool(on)})
            self._enqueue_write(
                "SETPTT 1" if on else "SETPTT 0",
                f"{self._log_label}: CI-V 0x1C 0x00 {'TX' if on else 'RX'}",
            )
            return _wrap(bytes([0xFB]))

        # Unbekannt → NAK.
        return _wrap(bytes([0xFA]))


# --------------------------------------------------------------------- factory

def build_responder(
    profile: dict,
    *,
    get_state: Callable[[], dict],
    enqueue_write: Callable[[str, str], None],
    refresh_frequency_for_read: Optional[Callable[[float], bool]] = None,
    on_state_patch: Optional[Callable[[dict], None]] = None,
    log_label: str = "",
) -> CatResponder:
    """Aus einem Rig-Profil einen passenden Responder bauen.

    Heuristik analog zu ``cat_commands.build_set_frequency_payload``:
    Marke entscheidet (Yaesu/Kenwood/Elecraft/Icom/Generic); Modell
    beeinflusst bei Yaesu die FA-Stellenzahl (newcat 9 vs. legacy 11).
    """
    brand = str(profile.get("rig_brand", "") or "").strip().lower()
    rig_model = str(profile.get("rig_model", "") or "")
    hamlib_id = int(profile.get("hamlib_rig_id", 0) or 0)
    label = log_label or f"CAT-Sim[{profile.get('id', '?')}]"

    if "icom" in brand:
        return IcomCivResponder(
            get_state=get_state,
            enqueue_write=enqueue_write,
            refresh_frequency_for_read=refresh_frequency_for_read,
            on_state_patch=on_state_patch,
            log_label=label,
        )
    if "kenwood" in brand:
        return KenwoodResponder(
            get_state=get_state,
            enqueue_write=enqueue_write,
            refresh_frequency_for_read=refresh_frequency_for_read,
            on_state_patch=on_state_patch,
            log_label=label,
        )
    if "elecraft" in brand:
        return ElecraftResponder(
            get_state=get_state,
            enqueue_write=enqueue_write,
            refresh_frequency_for_read=refresh_frequency_for_read,
            on_state_patch=on_state_patch,
            log_label=label,
        )
    if "yaesu" in brand or "vertex" in brand:
        # Modellspezifisch newcat vs. legacy.
        nd = _yaesu_fa_digit_count(rig_model, hamlib_id)
        if nd >= 11:
            return YaesuLegacyResponder(
                get_state=get_state,
                enqueue_write=enqueue_write,
                refresh_frequency_for_read=refresh_frequency_for_read,
                on_state_patch=on_state_patch,
                log_label=label,
            )
        return YaesuNewcatResponder(
            get_state=get_state,
            enqueue_write=enqueue_write,
            refresh_frequency_for_read=refresh_frequency_for_read,
            on_state_patch=on_state_patch,
            rig_model=rig_model,
            hamlib_rig_id=hamlib_id,
            log_label=label,
        )
    # Fallback: Yaesu newcat (weil das Programm heute bereits als Generic Fallback FA;9 nutzt).
    return YaesuNewcatResponder(
        get_state=get_state,
        enqueue_write=enqueue_write,
        refresh_frequency_for_read=refresh_frequency_for_read,
        on_state_patch=on_state_patch,
        rig_model=rig_model,
        hamlib_rig_id=hamlib_id,
        log_label=label,
    )
