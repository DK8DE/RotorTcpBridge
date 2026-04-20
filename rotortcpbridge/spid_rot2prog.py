# ROT2PROG / SPID Protokoll
# - Commands: 13 Bytes: 'W' + 4 ASCII H + PH + 4 ASCII V + PV + CMD + ' '
# - Replies:  12 Bytes: 'W' + 4 DIGITS(0..9) + PH + 4 DIGITS + PV + ' '
#
# Auflösung 0,1°: PH = PV = 10

from dataclasses import dataclass

START = 0x57  # 'W'
END = 0x20  # Space

CMD_STOP = 0x0F
CMD_STATUS = 0x1F
CMD_SET = 0x2F  # '/'


@dataclass
class Rot2ProgCommand:
    cmd: int
    az_d10: int | None = None
    el_d10: int | None = None
    ph: int | None = None
    pv: int | None = None


def _ascii_digits_to_int(b: bytes) -> int | None:
    try:
        s = b.decode("ascii")
    except Exception:
        return None
    if len(s) != 4 or any(ch < "0" or ch > "9" for ch in s):
        return None
    return int(s)


def parse_command_packet(pkt: bytes) -> Rot2ProgCommand | None:
    if len(pkt) != 13:
        return None
    if pkt[0] != START or pkt[12] != END:
        return None
    H = _ascii_digits_to_int(pkt[1:5])
    V = _ascii_digits_to_int(pkt[6:10])
    ph = pkt[5]
    pv = pkt[10]
    cmd = pkt[11]
    az_d10 = None
    el_d10 = None
    if cmd == CMD_SET:
        # ROT2PROG / PstRotator / SatPC32 / Hamlib senden H und V je nach
        # gewaehltem Rotator-Profil in zwei unterschiedlichen Aufloesungen:
        #
        #   * 0,1°-Aufloesung (Alfaspid RAS, RAS AZ):   H = 10*(az+360)
        #   * 1°-Aufloesung   (Alfaspid BIG-RAS AZ/EL): H = (az+360)
        #
        # Die Header-Bytes PH/PV sind dabei oft nicht eindeutig (PstRotator
        # schickt in beiden Faellen PH=PV=1), daher entscheiden wir anhand
        # des Wertebereichs: Werte >= 1000 liegen sicher im 0,1°-Bereich
        # (1800..8100 fuer AZ = -180°..+450°), Werte < 1000 liegen sicher im
        # 1°-Bereich (180..810 fuer denselben Winkelbereich). Die beiden
        # Bereiche ueberlappen sich nicht.
        #
        # Ist das Feld nicht mit ASCII-Digits belegt (z.B. binaere Nullen,
        # wenn die Gegenstelle nur eine Achse setzen will), bleibt der Wert
        # None und die Achse wird nicht verfahren.
        def _decode(raw: int | None) -> int | None:
            if raw is None:
                return None
            if raw >= 1000:
                return raw - 3600
            return (raw - 360) * 10

        az_d10 = _decode(H)
        el_d10 = _decode(V)
    return Rot2ProgCommand(cmd=cmd, az_d10=az_d10, el_d10=el_d10, ph=ph, pv=pv)


def encode_reply(az_d10: int, el_d10: int, ph: int = 10, pv: int = 10) -> bytes:
    # Reply verwendet DIGITS als Bytewerte 0..9, NICHT ASCII!
    H = int(ph * (az_d10 / 10 + 360))
    V = int(pv * (el_d10 / 10 + 360))

    def digs(x: int):
        x = max(0, min(9999, x))
        s = f"{x:04d}"
        return [int(s[0]), int(s[1]), int(s[2]), int(s[3])]

    hd = digs(H)
    vd = digs(V)

    b = bytearray(12)
    b[0] = START
    b[1:5] = bytes(hd)
    b[5] = ph
    b[6:10] = bytes(vd)
    b[10] = pv
    b[11] = END
    return bytes(b)
