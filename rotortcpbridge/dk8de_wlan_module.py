"""DK8DE WLAN_to_RS485: UDP-Discovery (8880), AT-Session und Konfiguration."""

from __future__ import annotations

import base64
import concurrent.futures
import ipaddress
import json
import re
import select
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

VENDOR_DK8DE = "dk8de_wlan"

DK8DE_CONFIG_PORT = 8880
DK8DE_DISCOVERY_CLIENT_PORT = 8889  # Host lauscht hier (wie Ebyte 1902)
DK8DE_DEFAULT_DATA_PORT = 8886
DK8DE_DEFAULT_WEB_USER = "admin"
DK8DE_DEFAULT_WEB_PASSWORD = "Rotorconfig"

_HOST_SRC_MAC = b"\x00\x00\x00\x00\x00\x01"
_DISCOVER_MIN_WAIT_S = 0.35

_CONFIG_SYNC0 = 0xAA
_CONFIG_SYNC1 = 0x55
_CONFIG_HEADER_SIZE = 20
_CONFIG_MAX_PAYLOAD = 512
_CONFIG_PROTO_VERSION = 1

_MSG_DISCOVER = 0x01
_MSG_DISCOVER_RESPONSE = 0x02
_MSG_GET_STATUS = 0x04

_BROADCAST_MAC = b"\xff\xff\xff\xff\xff\xff"
_ZERO_MAC = b"\x00\x00\x00\x00\x00\x00"

_NETMODE_NUM_TO_SOCK = {
    0: "TCPS",
    1: "TCPC",
    2: "DISABLE",
    3: "UDPS",
    4: "UDPC",
}

_SOCK_TO_NETMODE = {
    "TCPS": "TCP_SERVER",
    "TCPC": "TCP_CLIENT",
    "UDPS": "UDP_SERVER",
    "UDPC": "UDP_CLIENT",
    "DISABLE": "DISABLED",
}

_UID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


def dk8de_crc16(data: bytes) -> int:
    """CRC-16/CCITT-FALSE (poly 0x1021, init 0xFFFF)."""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def config_frame_encode(
    msg_type: int,
    *,
    dst_mac: bytes = _BROADCAST_MAC,
    src_mac: bytes = _HOST_SRC_MAC,
    seq: int = 1,
    payload: bytes = b"",
) -> bytes:
    if len(payload) > _CONFIG_MAX_PAYLOAD:
        raise ValueError("payload too large")
    if len(dst_mac) != 6 or len(src_mac) != 6:
        raise ValueError("mac must be 6 bytes")
    header = bytearray(_CONFIG_HEADER_SIZE)
    header[0] = _CONFIG_SYNC0
    header[1] = _CONFIG_SYNC1
    header[2] = _CONFIG_PROTO_VERSION
    header[3] = msg_type & 0xFF
    header[4:10] = dst_mac
    header[10:16] = src_mac
    struct.pack_into(">H", header, 16, seq & 0xFFFF)
    struct.pack_into(">H", header, 18, len(payload))
    body = bytes(header) + payload
    crc = dk8de_crc16(body)
    return body + struct.pack(">H", crc)


def config_frame_try_parse(buf: bytes) -> Tuple[int, Optional[Dict[str, Any]]]:
    """Parst einen Frame. Returns (consumed_bytes, frame_or_none)."""
    if not buf:
        return 0, None
    if buf[0] != _CONFIG_SYNC0:
        return 1, None
    if len(buf) < 2:
        return 0, None
    if buf[1] != _CONFIG_SYNC1:
        return 1, None
    if len(buf) < _CONFIG_HEADER_SIZE:
        return 0, None
    plen = struct.unpack(">H", buf[18:20])[0]
    if plen > _CONFIG_MAX_PAYLOAD:
        return 1, None
    total = _CONFIG_HEADER_SIZE + plen + 2
    if len(buf) < total:
        return 0, None
    expect = dk8de_crc16(buf[: _CONFIG_HEADER_SIZE + plen])
    got = struct.unpack(">H", buf[_CONFIG_HEADER_SIZE + plen : total])[0]
    if expect != got:
        return 1, None
    frame = {
        "version": buf[2],
        "type": buf[3],
        "dst_mac": buf[4:10],
        "src_mac": buf[10:16],
        "seq": struct.unpack(">H", buf[16:18])[0],
        "payload": buf[20 : 20 + plen],
    }
    return total, frame


def parse_dk8de_kv_text(text: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s or "=" not in s:
            continue
        key, val = s.split("=", 1)
        out[key.strip().upper()] = val.strip()
    return out


def _at_response_to_kv_text(info_lines: List[str], kv_lines: List[str]) -> str:
    """Vereinheitlicht KEY=VALUE- und +KEY:-Zeilen fuer Stats/Parser."""
    parts: List[str] = []
    for line in info_lines:
        s = str(line or "").strip()
        if s:
            parts.append(s)
    for line in kv_lines:
        s = str(line or "").strip()
        if not s.startswith("+") or ":" not in s:
            continue
        name, val = s[1:].split(":", 1)
        key = name.strip().upper()
        if key:
            parts.append(f"{key}={val.strip()}")
    return "\n".join(parts)


DK8DE_INFO_STAT_KEYS = (
    "UID",
    "NAME",
    "AP",
    "MAC",
    "BUS",
    "FW",
    "HW",
    "IP",
    "NETMODE",
    "WIFIMODE",
    "LPORT",
    "DISCOVERY_UDP",
)
DK8DE_STATUS_UI_SECTIONS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("settings.network_dk8de_stats_group_status", ("WIFI", "IP", "RSSI", "LINK", "HEAP")),
    ("settings.network_dk8de_stats_group_packetizer", ("PACKETTIME", "PACKETSIZE")),
    (
        "settings.network_dk8de_stats_group_traffic",
        ("RS485_RX", "RS485_TX", "NET_RX", "NET_TX", "NET_TX_DROPS", "NET_RX_DROPS"),
    ),
    (
        "settings.network_dk8de_stats_group_bridge",
        ("RS485_TX_ALLOWED", "RS485_RX_ALLOWED", "BRIDGE"),
    ),
)
DK8DE_STATUS_STAT_KEYS = tuple(
    key for _title, keys in DK8DE_STATUS_UI_SECTIONS for key in keys
)


def _web_json_to_stats_kv(data: Dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Mappt /api/status-JSON auf INFO-/STATUS-KV (Web-Fallback)."""
    uid = normalize_uid(str(data.get("uid") or ""))
    info = {
        "UID": uid,
        "NAME": str(data.get("device_name") or ""),
        "AP": f"ROTOR-{uid}" if uid else "",
        "MAC": str(data.get("mac") or ""),
        "BUS": str(data.get("bus_address") or ""),
        "FW": str(data.get("fw") or ""),
        "HW": str(data.get("hw") or ""),
        "IP": str(data.get("sta_ip") or ""),
        "NETMODE": str(data.get("net_mode") if data.get("net_mode") is not None else ""),
        "WIFIMODE": str(
            data.get("wifi_mode_name")
            or data.get("wifi_mode")
            or data.get("wifimode")
            or ""
        ),
        "LPORT": str(data.get("local_port") or ""),
        "DISCOVERY_UDP": str(data.get("discovery_udp_port") or DK8DE_CONFIG_PORT),
    }
    wifi = "STA" if data.get("wifi_up") else "AP/DOWN"
    if str(data.get("wifi_mode_name") or "").strip():
        wifi = str(data.get("wifi_mode_name") or wifi)
    status = {
        "WIFI": wifi,
        "IP": str(data.get("sta_ip") or ""),
        "RSSI": str(data.get("rssi") if data.get("rssi") is not None else ""),
        "LINK": "1" if data.get("tcp_connected") else "0",
        "HEAP": str(data.get("free_heap") if data.get("free_heap") is not None else ""),
        "PACKETTIME": str(data.get("packet_timeout_ms") if data.get("packet_timeout_ms") is not None else ""),
        "PACKETSIZE": str(data.get("packet_size") if data.get("packet_size") is not None else ""),
        "RS485_RX": str(data.get("rs485_rx") if data.get("rs485_rx") is not None else ""),
        "RS485_TX": str(data.get("rs485_tx") if data.get("rs485_tx") is not None else ""),
        "NET_RX": str(data.get("net_rx") if data.get("net_rx") is not None else ""),
        "NET_TX": str(data.get("net_tx") if data.get("net_tx") is not None else ""),
        "NET_TX_DROPS": str(data.get("net_tx_drops") if data.get("net_tx_drops") is not None else ""),
        "NET_RX_DROPS": str(data.get("net_rx_drops") if data.get("net_rx_drops") is not None else ""),
        "RS485_TX_ALLOWED": "1" if data.get("rs485_tx_allowed") else "0",
        "RS485_RX_ALLOWED": "1" if data.get("rs485_rx_allowed") else "0",
        "BRIDGE": "1" if data.get("bridge") else "0",
    }
    return info, status


def parse_dk8de_stats_payload(
    info_text: str,
    status_text: str,
) -> Tuple[Dict[str, str], Dict[str, str]]:
    """Parst AT-INFO/STATUS-Text oder Web-JSON-Fallback."""
    info = parse_dk8de_kv_text(info_text)
    status = parse_dk8de_kv_text(status_text)
    raw_info = str(info_text or "").strip()
    if not info and raw_info.startswith("{"):
        try:
            data = json.loads(raw_info)
            if isinstance(data, dict):
                return _web_json_to_stats_kv(data)
        except json.JSONDecodeError:
            pass
    return info, status


def dk8de_netmode_to_sock(mode: str | int) -> str:
    if isinstance(mode, int):
        return _NETMODE_NUM_TO_SOCK.get(mode, "TCPS")
    s = str(mode or "").strip().upper()
    if s.isdigit():
        return _NETMODE_NUM_TO_SOCK.get(int(s), "TCPS")
    mapping = {
        "TCP_SERVER": "TCPS",
        "TCP_CLIENT": "TCPC",
        "UDP_SERVER": "UDPS",
        "UDP_CLIENT": "UDPC",
        "DISABLED": "DISABLE",
        "NET_OFF": "DISABLE",
    }
    return mapping.get(s, "TCPS")


def dk8de_sock_to_netmode(mode: str) -> str:
    return _SOCK_TO_NETMODE.get(str(mode or "TCPS").strip().upper(), "TCP_SERVER")


def normalize_uid(uid: str) -> str:
    s = str(uid or "").strip().upper()
    if s.startswith("ROTOR-"):
        s = s[6:]
    return s


def uid_is_valid(uid: str) -> bool:
    return bool(_UID_RE.match(normalize_uid(uid)))


def _normalize_contact_ip(ip: str) -> str:
    """Gueltige Unicast-Kontakt-IP oder leer."""
    s = str(ip or "").strip()
    if not s or s in ("-", "0.0.0.0"):
        return ""
    try:
        addr = ipaddress.ip_address(s)
    except ValueError:
        return ""
    if addr.is_loopback or addr.is_unspecified or addr.is_multicast:
        return ""
    return s


def _pick_device_ip(reported: str, reply: str) -> str:
    """Kontakt-IP: UDP-Antwortadresse hat Vorrang vor gemeldeter Modul-IP."""
    reply_n = _normalize_contact_ip(reply)
    if reply_n:
        return reply_n
    return _normalize_contact_ip(reported)


@dataclass
class Dk8deDevice:
    """Ergebnis von UDP-Discovery auf Port 8880."""

    uid: str
    mac: str = ""
    name: str = ""
    fw: str = ""
    hw: str = ""
    ip: str = ""
    ap: str = ""
    bus: str = ""
    netmode: str = ""
    wifimode: str = ""
    lport: int = DK8DE_DEFAULT_DATA_PORT
    src_mac: bytes = field(default=b"", repr=False)
    info: Dict[str, str] = field(default_factory=dict)

    @property
    def uid_norm(self) -> str:
        return normalize_uid(self.uid)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid_norm,
            "mac": self.mac,
            "name": self.name,
            "fw": self.fw,
            "hw": self.hw,
            "ip": self.ip,
            "ap": self.ap,
            "lport": self.lport,
        }


def _device_from_info(info: Dict[str, str], src_mac: bytes = b"") -> Dk8deDevice:
    uid = info.get("UID", "")
    try:
        lport = int(info.get("LPORT", DK8DE_DEFAULT_DATA_PORT))
    except (TypeError, ValueError):
        lport = DK8DE_DEFAULT_DATA_PORT
    return Dk8deDevice(
        uid=uid,
        mac=info.get("MAC", ""),
        name=info.get("NAME", ""),
        fw=info.get("FW", ""),
        hw=info.get("HW", ""),
        ip=info.get("IP", ""),
        ap=info.get("AP", ""),
        bus=info.get("BUS", ""),
        netmode=info.get("NETMODE", ""),
        wifimode=info.get("WIFIMODE", ""),
        lport=lport if 1 <= lport <= 65535 else DK8DE_DEFAULT_DATA_PORT,
        src_mac=src_mac,
        info=dict(info),
    )


def _apply_discover_contact_ip(dev: Dk8deDevice, reply_ip: str) -> None:
    reported = dev.ip or str(dev.info.get("IP") or "")
    contact = _pick_device_ip(reported, reply_ip)
    if contact:
        dev.ip = contact
    if reply_ip and _normalize_contact_ip(reply_ip) and reply_ip != reported:
        if reported:
            dev.info["REPORTED_IP"] = reported
        dev.info["CONTACT_IP"] = contact or reply_ip


def _bind_udp_listen(sock: socket.socket, host: str, port: int) -> None:
    """Bindet Discovery-Port; Fallback auf ephemeral bei Belegung."""
    try:
        sock.bind((host, int(port)))
    except OSError:
        sock.bind((host, 0))


def _open_udp_socket(listen: bool = True) -> socket.socket:
    """Discovery-Empfang (Port 8889)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    except OSError:
        pass
    if listen:
        _bind_udp_listen(sock, "0.0.0.0", DK8DE_DISCOVERY_CLIENT_PORT)
    sock.settimeout(0.2)
    return sock


def _open_at_udp_socket() -> socket.socket:
    """AT-Session auf Discovery-Client-Port (8889) — gleicher Empfang wie Discovery.

    Ephemeral Ports werden unter Windows oft von der Firewall blockiert; Discovery
    funktioniert bereits auf 8889. Kein Fallback auf Port 0 — sonst kommt
    ``CONFIG,READY`` nie an.
    """
    last_err: Optional[OSError] = None
    for _ in range(8):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        try:
            sock.bind(("0.0.0.0", DK8DE_DISCOVERY_CLIENT_PORT))
            sock.settimeout(0.2)
            return sock
        except OSError as exc:
            last_err = exc
            try:
                sock.close()
            except OSError:
                pass
            time.sleep(0.05)
    raise OSError(
        f"DK8DE AT: UDP-Port {DK8DE_DISCOVERY_CLIENT_PORT} belegt "
        f"(Firewall/anderer Prozess?): {last_err}"
    )


def _at_listen_socks(session: "Dk8deAtSession") -> List[socket.socket]:
    socks: List[socket.socket] = []
    if session._sock is not None:
        socks.append(session._sock)
    socks.extend(session._extra_socks)
    return socks


def _at_command_timeout(body: str, default: float) -> float:
    """SAVE/NVS braucht auf dem ESP laenger; sonst Timeout → Modul bleibt in AT."""
    b = str(body or "").strip().upper()
    if b in ("SAVE", "AT+SAVE") or b.startswith("SAVE"):
        return max(float(default), 12.0)
    if b in ("REBOOT", "AT+REBOOT", "FACTORY", "AT+FACTORY"):
        return max(float(default), 4.0)
    return float(default)


def _at_session_targets(host: str, config_port: int) -> List[Tuple[str, int]]:
    """Ziele fuer +++CFG (Unicast + Broadcast), falls Modul-IP falsch ist."""
    seen: set[Tuple[str, int]] = set()
    out: List[Tuple[str, int]] = []

    def _add(h: str) -> None:
        key = (h, int(config_port))
        if key not in seen:
            seen.add(key)
            out.append(key)

    h = str(host or "").strip()
    if h and h not in ("0.0.0.0", "-", "255.255.255.255"):
        _add(h)
    _add("255.255.255.255")
    for net in _local_ipv4_subnets():
        try:
            _add(str(net.broadcast_address))
        except ValueError:
            continue
    for addr in _dk8de_local_ipv4_addrs():
        bc = _ipv4_subnet_broadcast(addr)
        if bc:
            _add(bc)
    return out


def _dk8de_local_ipv4_addrs() -> List[str]:
    from .net_utils import get_local_ipv4_addresses

    return sorted(a for a in get_local_ipv4_addresses() if a and not a.startswith("127."))


def _ipv4_subnet_broadcast(ip: str) -> str:
    parts = str(ip or "").split(".")
    if len(parts) != 4:
        return ""
    try:
        socket.inet_pton(socket.AF_INET, ip)
    except OSError:
        return ""
    return f"{parts[0]}.{parts[1]}.{parts[2]}.255"


def _local_ipv4_subnets() -> List[ipaddress.IPv4Network]:
    """Lokale /24-Subnetze aller Adapter (fuer Broadcast + HTTP-Scan)."""
    nets: Dict[str, ipaddress.IPv4Network] = {}
    for ip in _dk8de_local_ipv4_addrs():
        try:
            addr = ipaddress.ip_address(ip)
            if addr.is_link_local:
                continue
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            nets[str(net)] = net
        except ValueError:
            continue
    nets.setdefault("192.168.4.0/24", ipaddress.ip_network("192.168.4.0/24"))
    return list(nets.values())


def _broadcast_targets() -> List[Tuple[str, int]]:
    out: List[Tuple[str, int]] = []
    seen: set[Tuple[str, int]] = set()

    def _add(host: str) -> None:
        key = (host, DK8DE_CONFIG_PORT)
        if key not in seen:
            seen.add(key)
            out.append(key)

    _add("255.255.255.255")
    for net in _local_ipv4_subnets():
        try:
            _add(str(net.broadcast_address))
        except ValueError:
            continue
    for addr in _dk8de_local_ipv4_addrs():
        bc = _ipv4_subnet_broadcast(addr)
        if bc:
            _add(bc)
    return out


def _dk8de_open_interface_sockets(*, listen_port: int = DK8DE_DISCOVERY_CLIENT_PORT) -> List[socket.socket]:
    """Zusaetzliche UDP-Sockets pro lokalem Adapter (Windows: Broadcast-Routing)."""
    extra: List[socket.socket] = []
    for addr in _dk8de_local_ipv4_addrs():
        s: Optional[socket.socket] = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            if listen_port > 0:
                try:
                    s.bind((addr, listen_port))
                except OSError:
                    s.bind((addr, 0))
            else:
                s.bind((addr, 0))
            s.settimeout(0.2)
            extra.append(s)
        except OSError:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
    return extra


def _looks_like_config_frame(data: bytes) -> bool:
    return bool(data) and len(data) >= 2 and data[0] == _CONFIG_SYNC0 and data[1] == _CONFIG_SYNC1


def _at_udp_to_text(data: bytes) -> str:
    """AT-Antworten aus UDP extrahieren; Discovery-Binärframes ignorieren.

    KEY=VALUE-Text (auch UID=…) wird bewusst durchgelassen: AT+INFO? liefert
    dieselben Zeilen wie eine Discovery-Ankuendigung, oft in einem eigenen
    UDP-Datagramm vor ``@UID:OK``. Frueher wurden reine ``UID=``-Pakete
    verworfen — dann blieb der INFO-Bereich in der Statistik leer, waehrend
    STATUS? (andere Schluessel) weiterhin funktionierte.
    """
    if not data or _looks_like_config_frame(data):
        return ""
    return data.decode("utf-8", errors="replace")


def _at_error_snippet(buf: str, limit: int = 160) -> str:
    clean = "".join(ch if (ch.isprintable() or ch in "\r\n\t") else "·" for ch in str(buf or ""))
    clean = clean.strip()
    if len(clean) > limit:
        return clean[: limit - 3] + "..."
    return clean or "(leer)"


def _udp_recv_until_socks(
    socks: List[socket.socket],
    deadline: float,
    done_fn: Callable[[str], bool],
    *,
    idle_s: float = 0.35,
) -> Tuple[str, str, Optional[socket.socket]]:
    buf = ""
    peer_ip = ""
    active_sock: Optional[socket.socket] = None
    last_rx = 0.0
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        pkt = _dk8de_recv_any(socks, min(0.2, remaining))
        if pkt is not None:
            data, addr, sock = pkt
            chunk = _at_udp_to_text(data)
            if not chunk:
                continue
            buf += chunk
            peer_ip = str(addr[0] or "").strip()
            active_sock = sock
            last_rx = time.time()
            if done_fn(buf):
                return buf, peer_ip, active_sock
        elif buf and last_rx and (time.time() - last_rx) >= idle_s:
            # Siehe _udp_recv_until: ohne @OK/@ERROR weiter bis Deadline warten.
            if done_fn(buf):
                return buf, peer_ip, active_sock
    return buf, peer_ip, active_sock


def _dk8de_send_discover(
    socks: List[socket.socket],
    frame: bytes,
    targets: List[Tuple[str, int]],
) -> bool:
    sent = False
    for sock in socks:
        for target in targets:
            try:
                sock.sendto(frame, target)
                sent = True
            except OSError:
                continue
    return sent


def _dk8de_recv_any(
    socks: List[socket.socket],
    timeout: float,
) -> Optional[Tuple[bytes, Tuple[str, int], socket.socket]]:
    if not socks:
        return None
    try:
        ready, _, _ = select.select(socks, [], [], max(0.0, timeout))
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    sock = ready[0]
    try:
        data, addr = sock.recvfrom(4096)
        return data, addr, sock
    except OSError:
        return None


def _http_get_json(
    host: str,
    path: str,
    *,
    port: int = 80,
    user: str = DK8DE_DEFAULT_WEB_USER,
    password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    timeout: float = 0.8,
) -> Optional[Dict[str, Any]]:
    """GET /api/... mit HTTP Basic Auth; None wenn kein DK8DE-Modul."""
    import http.client

    auth = base64.b64encode(f"{user}:{password}".encode("ascii", errors="ignore")).decode("ascii")
    headers = {"Authorization": f"Basic {auth}", "Connection": "close"}
    try:
        conn = http.client.HTTPConnection(host, int(port), timeout=float(timeout))
        conn.request("GET", path, headers=headers)
        resp = conn.getresponse()
        body = resp.read(8192)
        conn.close()
        if resp.status != 200:
            return None
        data = json.loads(body.decode("utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _device_from_web_status(host: str, status: Dict[str, Any], cfg: Optional[Dict[str, Any]] = None) -> Dk8deDevice:
    uid = normalize_uid(str(status.get("uid") or ""))
    ip = str(status.get("sta_ip") or host or "").strip()
    if not ip or ip == "-":
        ip = host
    lport = DK8DE_DEFAULT_DATA_PORT
    if cfg:
        try:
            lport = int(cfg.get("local_port") or lport)
        except (TypeError, ValueError):
            pass
    return Dk8deDevice(
        uid=uid,
        mac=str(status.get("mac") or ""),
        name=str(status.get("device_name") or ""),
        fw=str(status.get("fw") or ""),
        hw=str(status.get("hw") or ""),
        ip=ip,
        ap=f"ROTOR-{uid}" if uid else "",
        netmode=str(status.get("net_mode") or ""),
        lport=lport,
        info={"UID": uid, "IP": ip, "MAC": str(status.get("mac") or ""), "FW": str(status.get("fw") or "")},
    )


def dk8de_http_discover(
    *,
    web_port: int = 80,
    user: str = DK8DE_DEFAULT_WEB_USER,
    password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    timeout: float = 0.45,
    max_workers: int = 48,
    max_hosts: int = 256,
) -> List[Dk8deDevice]:
    """Findet DK8DE-Module per /api/status im lokalen /24 (Web-Fallback)."""
    devices: Dict[str, Dk8deDevice] = {}
    hosts: List[str] = []
    for net in _local_ipv4_subnets():
        for host in net.hosts():
            hosts.append(str(host))
            if len(hosts) >= max(1, int(max_hosts)):
                break
        if len(hosts) >= max(1, int(max_hosts)):
            break

    def _probe(ip: str) -> Optional[Dk8deDevice]:
        st = _http_get_json(ip, "/api/status", port=web_port, user=user, password=password, timeout=timeout)
        if not st:
            return None
        uid = normalize_uid(str(st.get("uid") or ""))
        if not uid_is_valid(uid):
            return None
        cfg = _http_get_json(ip, "/api/config", port=web_port, user=user, password=password, timeout=timeout)
        return _device_from_web_status(ip, st, cfg)

    workers = max(8, min(int(max_workers), 96))
    scan_deadline = max(12.0, float(timeout) * min(len(hosts), 64) / max(workers, 1))
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futs = {pool.submit(_probe, ip): ip for ip in hosts}
        try:
            for fut in concurrent.futures.as_completed(futs, timeout=scan_deadline):
                try:
                    dev = fut.result()
                except Exception:
                    dev = None
                if dev is not None and dev.uid_norm:
                    devices[dev.uid_norm] = dev
        except concurrent.futures.TimeoutError:
            pass
    return sorted(devices.values(), key=lambda d: (d.ip or "", d.uid_norm))


def map_web_to_status(
    host: str,
    status: Dict[str, Any],
    cfg: Dict[str, Any],
) -> Dict[str, Any]:
    uid = normalize_uid(str(status.get("uid") or ""))
    sta_ip = str(status.get("sta_ip") or host or "").strip()
    if sta_ip in ("", "-", "0.0.0.0"):
        sta_ip = host
    dhcp = bool(cfg.get("wifi_dhcp"))
    wan = {
        "mode": "DHCP" if dhcp else "STATIC",
        "ip": sta_ip if not dhcp else str(cfg.get("wifi_ip") or sta_ip),
        "mask": str(cfg.get("wifi_mask") or ""),
        "gateway": str(cfg.get("wifi_gw") or ""),
        "dns": str(cfg.get("wifi_dns") or ""),
        "dns2": "",
    }
    if dhcp and sta_ip not in ("", "-", "0.0.0.0"):
        wan["ip"] = sta_ip
    sock_mode = dk8de_netmode_to_sock(cfg.get("net_mode", status.get("net_mode", 0)))
    local_port = int(cfg.get("local_port") or status.get("local_port") or DK8DE_DEFAULT_DATA_PORT)
    remote_port = int(cfg.get("remote_port") or status.get("remote_port") or local_port)
    remote_ip = str(cfg.get("remote_ip") or status.get("remote_ip") or "")
    if sock_mode in ("TCPS", "UDPS"):
        port = local_port
    else:
        port = remote_port
    link = "UP" if status.get("tcp_connected") else "DOWN"
    return {
        "model": str(status.get("device_name") or ""),
        "mac": str(status.get("mac") or ""),
        "ver": str(status.get("fw") or ""),
        "wan": wan,
        "sock": {
            "mode": sock_mode,
            "remote_ip": remote_ip,
            "remote_port": str(port),
            "local_port": str(local_port),
            "link_id": "",
        },
        "link": link,
        "uid": uid,
        "raw": {"status": status, "config": cfg},
        "source": "dk8de_web",
    }


def read_status_web_dk8de(
    host: str,
    *,
    web_port: int = 80,
    user: str = DK8DE_DEFAULT_WEB_USER,
    password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    st = _http_get_json(host, "/api/status", port=web_port, user=user, password=password, timeout=timeout)
    if not st:
        raise RuntimeError("Web /api/status lieferte keine Daten (Auth/Port?)")
    cfg = _http_get_json(host, "/api/config", port=web_port, user=user, password=password, timeout=timeout)
    if not cfg:
        raise RuntimeError("Web /api/config lieferte keine Daten")
    return map_web_to_status(host, st, cfg)


def _collect_udp(sock: socket.socket, deadline: float) -> bytes:
    chunks: List[bytes] = []
    while time.time() < deadline:
        try:
            data, _addr = sock.recvfrom(4096)
            chunks.append(data)
        except socket.timeout:
            continue
        except OSError:
            break
    return b"".join(chunks)


def _parse_discover_payloads(data: bytes) -> List[Dk8deDevice]:
    devices: Dict[str, Dk8deDevice] = {}
    offset = 0
    while offset < len(data):
        consumed, frame = config_frame_try_parse(data[offset:])
        if consumed == 0:
            break
        offset += consumed
        if frame is None:
            continue
        if frame["type"] != _MSG_DISCOVER_RESPONSE:
            continue
        text = frame["payload"].decode("utf-8", errors="replace")
        info = parse_dk8de_kv_text(text)
        uid = normalize_uid(info.get("UID", ""))
        if not uid:
            continue
        dev = _device_from_info(info, src_mac=frame["src_mac"])
        devices[uid] = dev
    return list(devices.values())


def dk8de_udp_discover(*, timeout: float = 2.0) -> List[Dk8deDevice]:
    """UDP-Discovery auf Port 8880 (Broadcast + Subnetz-Broadcast).

    Broadcast erreicht nur Geraete im gleichen IP-Subnetz (typisch /24). Die
    Kontakt-IP stammt aus der UDP-Antwortadresse (recvfrom), nicht nur aus
    dem IP=-Feld der Payload.
    """
    sock = _open_udp_socket()
    extra_socks = _dk8de_open_interface_sockets()
    recv_socks = [sock] + extra_socks
    devices: Dict[str, Dk8deDevice] = {}
    wait_s = max(2.0, float(timeout))
    frame = config_frame_encode(_MSG_DISCOVER, seq=1)
    targets = _broadcast_targets()

    try:
        if not _dk8de_send_discover(recv_socks, frame, targets):
            return []
        deadline = time.time() + wait_s
        resend_s = max(0.5, min(1.0, wait_s / 3.0))
        next_resend = time.time() + resend_s
        while time.time() < deadline:
            if time.time() >= next_resend:
                _dk8de_send_discover(recv_socks, frame, targets)
                next_resend = time.time() + resend_s
            remaining = deadline - time.time()
            if remaining <= 0:
                break
            pkt = _dk8de_recv_any(recv_socks, min(0.2, remaining))
            if pkt is None:
                continue
            data, addr, _sock = pkt
            reply_ip = str(addr[0] if addr else "")
            for dev in _parse_discover_payloads(data):
                _apply_discover_contact_ip(dev, reply_ip)
                devices[dev.uid_norm] = dev
    finally:
        for s in extra_socks:
            try:
                s.close()
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass
    return sorted(devices.values(), key=lambda d: (d.ip or "", d.uid_norm))


def dk8de_discover(
    *,
    timeout: float = 3.0,
    web_port: int = 80,
    web_user: str = DK8DE_DEFAULT_WEB_USER,
    web_password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    http_fallback: bool = True,
) -> List[Dk8deDevice]:
    """Kombinierte Suche: UDP 8880 (primaer, Broadcast) + Web /api/status (Fallback)."""
    merged: Dict[str, Dk8deDevice] = {}
    udp_wait = max(1.5, min(3.0, float(timeout) * 0.55))
    for dev in dk8de_udp_discover(timeout=udp_wait):
        merged[dev.uid_norm] = dev
    if not http_fallback:
        return sorted(merged.values(), key=lambda d: (d.ip or "", d.uid_norm))
    needs_http = not merged or any(
        not _normalize_contact_ip(d.ip) for d in merged.values()
    )
    if not needs_http:
        return sorted(merged.values(), key=lambda d: (d.ip or "", d.uid_norm))
    http_timeout = max(0.4, min(1.0, float(timeout) * 0.1))
    for dev in dk8de_http_discover(
        web_port=web_port,
        user=web_user,
        password=web_password,
        timeout=http_timeout,
    ):
        prev = merged.get(dev.uid_norm)
        if prev is None:
            merged[dev.uid_norm] = dev
            continue
        if not _normalize_contact_ip(prev.ip) and _normalize_contact_ip(dev.ip):
            prev.ip = dev.ip
        if not prev.name and dev.name:
            prev.name = dev.name
        if not prev.fw and dev.fw:
            prev.fw = dev.fw
    return sorted(merged.values(), key=lambda d: (d.ip or "", d.uid_norm))


def _parse_at_response(text: str, uid: str) -> Tuple[List[str], List[str], Optional[str]]:
    """Returns (info_lines, kv_lines, error)."""
    uid_u = normalize_uid(uid)
    info_lines: List[str] = []
    kv_lines: List[str] = []
    error: Optional[str] = None
    for line in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        if s.startswith("+") and ":" in s:
            kv_lines.append(s)
            continue
        if s.startswith("@"):
            body = s.split(":", 1)[-1]
            if body.startswith("ERROR"):
                error = body
            continue
        if "=" in s and not s.startswith("@"):
            info_lines.append(s)
            continue
    if error and not uid_u:
        pass
    return info_lines, kv_lines, error


def _at_session_ready(buf: str, uid: str = "") -> bool:
    compact = str(buf or "").upper().replace("\r", "").replace("\n", "")
    if "CONFIG,READY" not in compact:
        return False
    u = normalize_uid(uid).upper()
    if u and f"@{u}:" not in compact:
        return False
    return True


def _at_command_done(buf: str, uid: str) -> bool:
    u = normalize_uid(uid).upper()
    compact = str(buf or "").upper().replace("\r", "").replace("\n", "")
    if f"@{u}:ERROR" in compact:
        return True
    if f"@{u}:OK" in compact:
        return True
    return False


def _udp_recv_until(
    sock: socket.socket,
    deadline: float,
    done_fn: Callable[[str], bool],
    *,
    idle_s: float = 0.35,
) -> str:
    buf = ""
    last_rx = 0.0
    while time.time() < deadline:
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sock.settimeout(min(0.2, remaining))
        try:
            data, _addr = sock.recvfrom(4096)
            chunk = _at_udp_to_text(data)
            if not chunk:
                continue
            buf += chunk
            last_rx = time.time()
            if done_fn(buf):
                return buf
        except socket.timeout:
            # Nur bei fertiger AT-Antwort vorzeitig beenden. Unvollstaendige
            # KEY=VALUE-Fragmente (z. B. INFO-Zeilen vor @OK, oder Discovery-
            # Rauschen) duerfen die Wartezeit nicht abbrechen.
            if buf and last_rx and (time.time() - last_rx) >= idle_s:
                if done_fn(buf):
                    return buf
        except OSError:
            break
    return buf


def _wait_at_complete(buf: str, uid: str) -> bool:
    return _at_command_done(buf, uid)


class Dk8deAtSession:
    """Kurze AT-Session ueber UDP 8880."""

    def __init__(
        self,
        host: str,
        uid: str,
        *,
        config_port: int = DK8DE_CONFIG_PORT,
        timeout: float = 2.5,
    ):
        self.host = host.strip()
        self.uid = normalize_uid(uid)
        self.config_port = int(config_port)
        self.timeout = float(timeout)
        self._sock: Optional[socket.socket] = None
        self._extra_socks: List[socket.socket] = []
        self._active_sock: Optional[socket.socket] = None
        self._skip_exit = False
        self._rx = ""

    def __enter__(self) -> "Dk8deAtSession":
        if not self.host:
            raise ValueError("host empty")
        if not uid_is_valid(self.uid):
            raise ValueError(f"invalid uid: {self.uid!r}")
        self._sock = _open_at_udp_socket()
        self._extra_socks = _dk8de_open_interface_sockets(listen_port=DK8DE_DISCOVERY_CLIENT_PORT)
        try:
            self._open_session()
        except Exception:
            self._close_sockets()
            raise
        return self

    def __exit__(self, *_args) -> None:
        if self._sock is not None and not self._skip_exit:
            try:
                self.command("EXIT")
            except Exception:
                pass
        self._close_sockets()

    def _close_sockets(self) -> None:
        for s in self._extra_socks:
            try:
                s.close()
            except OSError:
                pass
        self._extra_socks = []
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def _recv_until(self, deadline: float, done_fn: Callable[[str], bool]) -> None:
        self._rx = _udp_recv_until(self._sock, deadline, done_fn)  # type: ignore[arg-type]

    def _all_socks(self) -> List[socket.socket]:
        return _at_listen_socks(self)

    def _send_to_targets(self, payload: bytes, socks: Optional[List[socket.socket]] = None) -> None:
        targets = _at_session_targets(self.host, self.config_port)
        for sock in socks or self._all_socks():
            for target in targets:
                try:
                    sock.sendto(payload, target)
                except OSError:
                    continue

    def _clear_stale_at_mode(self) -> None:
        """Falls das Modul noch in einer AT-Session haengt: EXIT, sonst bleibt +++CFG tot.

        Firmware: im AT-Modus wird ``+++CFG`` nicht mehr ausgewertet (nur AT-Zeilen).
        Nach Timeout (z. B. SAVE) ohne EXIT schlaegt jede neue Session mit leerer Antwort fehl.
        """
        socks = self._all_socks()
        if not socks:
            return
        for _ in range(2):
            self._send_to_targets(b"AT+EXIT\r", socks)
            deadline = time.time() + 0.6
            _udp_recv_until_socks(
                socks,
                deadline,
                lambda buf: "OK" in str(buf or "").upper() or "ERROR" in str(buf or "").upper(),
                idle_s=0.2,
            )
        time.sleep(0.35)

    def _open_session(self) -> None:
        assert self._sock is not None
        socks = self._all_socks()
        escapes = (
            f"+++CFG:{self.uid}\r".encode("ascii"),
            f"+++CFG:ROTOR-{self.uid}\r".encode("ascii"),
        )
        ready_fn = lambda buf: _at_session_ready(buf, self.uid)
        last_rx = ""

        # Zombie-Session beenden, bevor +++CFG erneut gesendet wird.
        self._clear_stale_at_mode()

        # Ein Versuch nach dem anderen, aber Antworten auf ALLEN Sockets lesen —
        # unter Windows kann SO_REUSEADDR die Unicast-Antwort einem anderen
        # Interface-Socket zustellen als dem Sender.
        for attempt in range(3):
            time.sleep(0.55 if attempt == 0 else 0.4)
            for esc in escapes:
                self._send_to_targets(esc, socks)
            deadline = time.time() + max(2.5, min(self.timeout, 6.0))
            rx, peer_ip, active = _udp_recv_until_socks(
                socks,
                deadline,
                ready_fn,
                idle_s=0.45,
            )
            last_rx = rx or last_rx
            if not ready_fn(rx):
                continue
            contact = _normalize_contact_ip(peer_ip)
            if contact:
                self.host = contact
            self._active_sock = active or self._sock
            self._rx = rx
            return
        raise RuntimeError(f"AT-Session nicht bereit: {_at_error_snippet(last_rx)!r}")

    def command(self, body: str) -> str:
        """Sendet AT+<body> und liefert die komplette Antwort."""
        socks = self._all_socks()
        if not socks:
            raise RuntimeError("AT-Socket nicht offen")
        b = str(body or "").strip()
        if b.upper().startswith("AT+"):
            line = f"{b}\r"
            cmd_name = b[3:]
        elif b.upper() == "AT":
            line = "AT\r"
            cmd_name = "AT"
        else:
            line = f"AT+{b}\r"
            cmd_name = b
        payload = line.encode("ascii", errors="ignore")
        # Primär vom aktiven Peer-Socket; Fallback: alle Sockets (Windows-Demux).
        send_socks = [self._active_sock] if self._active_sock is not None else socks
        send_socks = [s for s in send_socks if s is not None]
        if not send_socks:
            send_socks = socks
        target = (self.host, self.config_port)
        for sock in send_socks:
            try:
                sock.sendto(payload, target)
            except OSError:
                continue
        wait_s = _at_command_timeout(cmd_name, self.timeout)
        deadline = time.time() + wait_s
        done_fn = lambda buf: _at_command_done(buf, self.uid)
        self._rx, peer_ip, active = _udp_recv_until_socks(socks, deadline, done_fn, idle_s=0.35)
        if active is not None:
            self._active_sock = active
        contact = _normalize_contact_ip(peer_ip)
        if contact:
            self.host = contact
        if not _at_command_done(self._rx, self.uid):
            raise RuntimeError(f"Keine AT-Antwort fuer {body}: {self._rx!r}")
        err = _parse_at_response(self._rx, self.uid)[2]
        if err:
            raise RuntimeError(f"AT-Fehler: {err}")
        return self._rx

    def query_lines(self, body: str) -> Tuple[List[str], List[str]]:
        resp = self.command(body)
        info, kv, err = _parse_at_response(resp, self.uid)
        if err:
            raise RuntimeError(err)
        return info, kv


def _kv_map(kv_lines: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in kv_lines:
        if not line.startswith("+") or ":" not in line:
            continue
        name, val = line[1:].split(":", 1)
        out[name.strip().upper()] = val.strip()
    return out


def read_status_dk8de(
    host: str,
    uid: str,
    *,
    config_port: int = DK8DE_CONFIG_PORT,
    timeout: float = 3.0,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "model": "",
        "mac": "",
        "ver": "",
        "wan": {"mode": "", "ip": "", "mask": "", "gateway": "", "dns": "", "dns2": ""},
        "sock": {"mode": "", "remote_ip": "", "remote_port": "", "link_id": "", "local_port": ""},
        "link": "",
        "uid": normalize_uid(uid),
        "raw": {},
        "source": "dk8de_at",
    }
    with Dk8deAtSession(host, uid, config_port=config_port, timeout=timeout) as ses:
        info_lines, info_kv = ses.query_lines("INFO?")
        info = parse_dk8de_kv_text(_at_response_to_kv_text(info_lines, info_kv))
        out["raw"]["INFO?"] = info

        kv_all: Dict[str, str] = {}
        live_ip = ""
        for cmd in (
            "NETMODE?",
            "WIFIMODE?",
            "SSID?",
            "NAME?",
            "MAC?",
            "UID?",
            "REMOTEIP?",
            "REMOTEHOST?",
            "LOCALPORT?",
            "REMOTEPORT?",
            "DHCP?",
        ):
            try:
                _info, kv = ses.query_lines(cmd)
                kv_all.update(_kv_map(kv))
            except Exception as exc:
                out["raw"][cmd] = str(exc)

        try:
            stat_lines, _ = ses.query_lines("STATUS?")
            status_info = parse_dk8de_kv_text("\n".join(stat_lines))
            out["raw"]["STATUS?"] = status_info
            link = status_info.get("LINK", "")
            out["link"] = "UP" if str(link).strip() in ("1", "UP", "CONNECTED") else "DOWN"
            live_ip = status_info.get("IP", "")
        except Exception as exc:
            out["raw"]["STATUS?"] = str(exc)
            live_ip = ""

    out["model"] = info.get("NAME") or kv_all.get("NAME", "")
    out["mac"] = info.get("MAC") or kv_all.get("MAC", "")
    out["ver"] = info.get("FW", "")
    out["uid"] = normalize_uid(info.get("UID") or kv_all.get("UID") or uid)

    ip = live_ip or info.get("IP") or kv_all.get("IP", "")
    if ip in ("-", "0.0.0.0"):
        ip = info.get("IP", "") if info.get("IP") not in ("-", "0.0.0.0") else ip
    wan_mode = "STATIC"
    if str(ip or "").strip() in ("", "0.0.0.0", "-"):
        wan_mode = "DHCP"
    out["wan"] = {
        "mode": wan_mode,
        "ip": ip if ip not in ("-",) else "",
        "mask": "",
        "gateway": "",
        "dns": "",
        "dns2": "",
    }

    sock_mode = dk8de_netmode_to_sock(
        kv_all.get("NETMODE") or info.get("NETMODE", "0")
    )
    local_port = kv_all.get("LOCALPORT") or info.get("LPORT", "")
    remote_port = kv_all.get("REMOTEPORT", "")
    remote_ip = kv_all.get("REMOTEIP", "")
    if sock_mode in ("TCPS", "UDPS"):
        port = local_port or remote_port or str(DK8DE_DEFAULT_DATA_PORT)
    else:
        port = remote_port or local_port or str(DK8DE_DEFAULT_DATA_PORT)
    out["sock"] = {
        "mode": sock_mode,
        "remote_ip": remote_ip,
        "remote_port": str(port),
        "local_port": str(local_port or port),
        "link_id": "",
    }
    return out


def write_config_dk8de(
    host: str,
    uid: str,
    wan: Dict[str, Any],
    sock: Dict[str, Any],
    *,
    config_port: int = DK8DE_CONFIG_PORT,
    reboot: bool = True,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    results: Dict[str, Any] = {"commands": [], "ok": True, "error": ""}
    mode = str(wan.get("mode", "STATIC") or "STATIC").upper()
    ip = str(wan.get("ip", "") or "")
    mask = str(wan.get("mask", "") or "")
    gw = str(wan.get("gateway", "") or "")
    dns = str(wan.get("dns", "") or "")
    sock_mode = str(sock.get("mode", "TCPS") or "TCPS").upper()
    remote_ip = str(sock.get("remote_ip", "") or "")
    try:
        port = int(sock.get("remote_port") or DK8DE_DEFAULT_DATA_PORT)
    except (TypeError, ValueError):
        port = DK8DE_DEFAULT_DATA_PORT

    cmds: List[str] = []
    if mode == "DHCP":
        cmds.append("DHCP=1")
    else:
        cmds.append("DHCP=0")
        if ip:
            cmds.append(f'IP="{ip}"' if " " in ip else f"IP={ip}")
        if mask:
            cmds.append(f'MASK="{mask}"' if " " in mask else f"MASK={mask}")
        if gw:
            cmds.append(f'GW="{gw}"' if " " in gw else f"GW={gw}")
        if dns:
            cmds.append(f'DNS="{dns}"' if " " in dns else f"DNS={dns}")

    netmode = dk8de_sock_to_netmode(sock_mode)
    cmds.append(f"NETMODE={netmode}")
    if sock_mode in ("TCPS", "UDPS"):
        cmds.append(f"LOCALPORT={port}")
    else:
        if remote_ip:
            cmds.append(f'REMOTEIP="{remote_ip}"' if " " in remote_ip else f"REMOTEIP={remote_ip}")
        cmds.append(f"REMOTEPORT={port}")

    cmds.append("SAVE")
    if reboot:
        cmds.append("REBOOT")

    try:
        with Dk8deAtSession(host, uid, config_port=config_port, timeout=timeout) as ses:
            for cmd in cmds:
                if cmd == "REBOOT":
                    try:
                        resp = ses.command("REBOOT")
                        results["commands"].append({"cmd": cmd, "resp": resp})
                    except Exception:
                        results["commands"].append({"cmd": cmd, "resp": "(reboot)"})
                    ses._skip_exit = True
                else:
                    resp = ses.command(cmd)
                    results["commands"].append({"cmd": cmd, "resp": resp})
    except Exception as exc:
        results["ok"] = False
        results["error"] = str(exc)
    return results


def _at_quoted_assign(name: str, value: str) -> str:
    """AT-Zuweisung mit Anführungszeichen (SSID/Passwort, Firmware parse_quoted)."""
    return f'{name}="{str(value or "")}"'


def build_wan_cmds_dk8de(
    *,
    ip: str = "",
    mask: str = "",
    gateway: str = "",
    dns: str = "",
    dhcp: bool = False,
    ssid: str = "",
    password: Optional[str] = None,
    wifi_band: str = "",
    wifi_mode: str = "",
    reboot: bool = True,
) -> List[str]:
    """AT-Kommandos fuer SoftAP-Erstinbetriebnahme und IP-Wechsel.

    Reihenfolge: Zugangsdaten und IP zuerst, dann SAVE, zuletzt WIFIMODE=STA
    (Firmware speichert STA in NVS und wendet WLAN an — SoftAP bricht ab).
    """
    cmds: List[str] = []
    ssid_s = str(ssid or "").strip()
    if ssid_s:
        cmds.append(_at_quoted_assign("SSID", ssid_s))
    if password is not None:
        cmds.append(_at_quoted_assign("PASS", str(password)))
    band = str(wifi_band or "").strip().upper()
    if band in ("AUTO", "2G", "5G"):
        cmds.append(f"WIFIBAND={band}")
    if dhcp:
        cmds.append("DHCP=1")
    else:
        cmds.append("DHCP=0")
        if ip:
            cmds.append(_at_quoted_assign("IP", ip) if " " in ip else f"IP={ip}")
        if mask:
            cmds.append(_at_quoted_assign("MASK", mask) if " " in mask else f"MASK={mask}")
        if gateway:
            cmds.append(_at_quoted_assign("GW", gateway) if " " in gateway else f"GW={gateway}")
        if dns:
            cmds.append(_at_quoted_assign("DNS", dns) if " " in dns else f"DNS={dns}")
    cmds.append("SAVE")
    mode = str(wifi_mode or "").strip().upper()
    if mode in ("AP", "STA", "APSTA"):
        cmds.append(f"WIFIMODE={mode}")
    if reboot:
        cmds.append("REBOOT")
    return cmds


def write_wan_dk8de(
    host: str,
    uid: str,
    *,
    ip: str,
    mask: str,
    gateway: str,
    dns: str = "",
    dhcp: bool = False,
    ssid: str = "",
    password: Optional[str] = None,
    wifi_band: str = "",
    wifi_mode: str = "",
    config_port: int = DK8DE_CONFIG_PORT,
    reboot: bool = True,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """Schreibt WLAN/IP per AT (SSID/PASS/Band, DHCP/IP/MASK/GW/DNS, SAVE, optional STA+REBOOT)."""
    results: Dict[str, Any] = {"commands": [], "ok": True, "error": ""}
    cmds = build_wan_cmds_dk8de(
        ip=ip,
        mask=mask,
        gateway=gateway,
        dns=dns,
        dhcp=dhcp,
        ssid=ssid,
        password=password,
        wifi_band=wifi_band,
        wifi_mode=wifi_mode,
        reboot=reboot,
    )

    try:
        with Dk8deAtSession(host, uid, config_port=config_port, timeout=timeout) as ses:
            for cmd in cmds:
                if cmd == "REBOOT":
                    try:
                        resp = ses.command("REBOOT")
                        results["commands"].append({"cmd": cmd, "resp": resp})
                    except Exception:
                        results["commands"].append({"cmd": cmd, "resp": "(reboot)"})
                    ses._skip_exit = True
                else:
                    resp = ses.command(cmd)
                    results["commands"].append({"cmd": cmd, "resp": resp})
    except Exception as exc:
        results["ok"] = False
        results["error"] = str(exc)
    return results


def read_dk8de_statistics(
    host: str,
    uid: str,
    *,
    config_port: int = DK8DE_CONFIG_PORT,
    web_port: int = 80,
    web_user: str = DK8DE_DEFAULT_WEB_USER,
    web_password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    timeout: float = 3.0,
) -> Dict[str, str]:
    """Liest AT+INFO?/STATUS?; Fallback Web /api/status."""
    out = {"info": "", "status": "", "error": ""}
    if uid_is_valid(uid):
        try:
            with Dk8deAtSession(host, uid, config_port=config_port, timeout=timeout) as ses:
                info_lines, info_kv = ses.query_lines("INFO?")
                out["info"] = _at_response_to_kv_text(info_lines, info_kv)
                stat_lines, stat_kv = ses.query_lines("STATUS?")
                out["status"] = _at_response_to_kv_text(stat_lines, stat_kv)
            return out
        except Exception as exc:
            out["error"] = str(exc)
    try:
        st = _http_get_json(host, "/api/status", port=web_port, user=web_user, password=web_password, timeout=timeout)
        if st:
            out["info"] = json.dumps(st, indent=2, ensure_ascii=False)
            out["status"] = ""
            out["error"] = ""
            return out
    except Exception as exc:
        if not out["error"]:
            out["error"] = str(exc)
    return out


def probe_dk8de(
    host: str,
    uid: str = "",
    *,
    config_port: int = DK8DE_CONFIG_PORT,
    web_port: int = 80,
    web_user: str = DK8DE_DEFAULT_WEB_USER,
    web_password: str = DK8DE_DEFAULT_WEB_PASSWORD,
    timeout: float = 0.8,
) -> bool:
    st = _http_get_json(host, "/api/status", port=web_port, user=web_user, password=web_password, timeout=timeout)
    if st and uid_is_valid(str(st.get("uid") or "")):
        return True
    if not host.strip() or not uid_is_valid(uid):
        return False
    try:
        with Dk8deAtSession(host, uid, config_port=config_port, timeout=max(timeout, 1.5)) as ses:
            ses.command("UID?")
        return True
    except Exception:
        return False
