"""RS485-Netzwerk-Konverter: AT/Web-APIs, Ebyte-UDP-Discovery und Legacy-TCP-Scan.

Unterstuetzte Hersteller:
  * Ebyte NE2 / NA11x – Web-API, optional AT; Suche/IP-Vergabe per UDP 1901/1902
  * USR-DR164 – Web (HTTP Basic) bzw. Transparent-AT
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import select
import socket
import struct
import concurrent.futures
import time
import zlib
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlencode

VENDOR_NE2 = "ne2"
VENDOR_NA11X = "na11x"
VENDOR_USR = "usr_dr164"
VENDOR_DK8DE = "dk8de_wlan"
VENDOR_GENERIC = "generic"

VALID_VENDORS = (VENDOR_NE2, VENDOR_NA11X, VENDOR_USR, VENDOR_DK8DE, VENDOR_GENERIC)
VALID_ROLES = ("server", "client", "bus_gateway")

DEFAULT_AT_PORTS = {
    VENDOR_NE2: 8886,
    VENDOR_NA11X: 8886,  # NA111-M typisch 8886 (aeltere Docs: 8887)
    VENDOR_USR: 8899,
    VENDOR_DK8DE: 8886,  # Nutzdaten-Port; AT/Discovery auf config_port (8880)
    VENDOR_GENERIC: 8886,
}
DEFAULT_CONFIG_PORTS = {
    VENDOR_DK8DE: 8880,
}
DEFAULT_WEB_PORTS = {
    VENDOR_NE2: 80,
    VENDOR_NA11X: 80,
    VENDOR_USR: 80,
    VENDOR_DK8DE: 80,
    VENDOR_GENERIC: 80,
}


@dataclass
class NetworkModule:
    """Ein konfiguriertes RS485-Netzwerk-Modul."""

    name: str = ""
    vendor: str = VENDOR_NE2
    host: str = ""
    at_port: int = 8886
    web_port: int = 80
    role: str = "bus_gateway"
    netat_header: str = "NETAT"
    cmdpw: str = "USR"
    web_user: str = "admin"
    web_password: str = "admin"
    uid: str = ""  # DK8DE: 8-stellige Hex-Geraete-ID
    config_port: int = 8880  # DK8DE: UDP AT/Discovery
    contact_host: str = ""  # DK8DE: zuletzt erreichbare IP fuer AT (nicht persistiert)
    # Zuletzt gelesene Live-Werte (nicht persistent noetig, aber praktisch)
    last_status: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "name": str(self.name or ""),
            "vendor": str(self.vendor or VENDOR_GENERIC).strip().lower(),
            "host": str(self.host or "").strip(),
            "at_port": int(self.at_port),
            "web_port": int(self.web_port),
            "role": str(self.role or "bus_gateway").strip().lower(),
            "netat_header": str(self.netat_header or "NETAT").strip() or "NETAT",
            "cmdpw": str(self.cmdpw or "USR").strip() or "USR",
            "web_user": str(self.web_user or "admin").strip() or "admin",
            "web_password": str(self.web_password or "admin"),
            "uid": str(self.uid or "").strip().upper(),
            "config_port": int(self.config_port),
        }
        if d["vendor"] not in VALID_VENDORS:
            d["vendor"] = VENDOR_GENERIC
        if d["role"] not in VALID_ROLES:
            d["role"] = "bus_gateway"
        if d["vendor"] != VENDOR_DK8DE:
            d.pop("uid", None)
            d.pop("config_port", None)
        return d

    @classmethod
    def from_dict(cls, raw: Any) -> "NetworkModule":
        if not isinstance(raw, dict):
            return cls()
        vendor = str(raw.get("vendor", VENDOR_NE2) or VENDOR_NE2).strip().lower()
        if vendor not in VALID_VENDORS:
            vendor = VENDOR_GENERIC
        try:
            at_port = int(raw.get("at_port", DEFAULT_AT_PORTS.get(vendor, 8886)))
        except (TypeError, ValueError):
            at_port = DEFAULT_AT_PORTS.get(vendor, 8886)
        try:
            web_port = int(raw.get("web_port", DEFAULT_WEB_PORTS.get(vendor, 80)))
        except (TypeError, ValueError):
            web_port = DEFAULT_WEB_PORTS.get(vendor, 80)
        role = str(raw.get("role", "bus_gateway") or "bus_gateway").strip().lower()
        if role not in VALID_ROLES:
            role = "bus_gateway"
        try:
            config_port = int(
                raw.get("config_port", DEFAULT_CONFIG_PORTS.get(vendor, 8880))
            )
        except (TypeError, ValueError):
            config_port = DEFAULT_CONFIG_PORTS.get(vendor, 8880)
        return cls(
            name=str(raw.get("name", "") or ""),
            vendor=vendor,
            host=str(raw.get("host", "") or "").strip(),
            at_port=max(1, min(65535, at_port)),
            web_port=max(1, min(65535, web_port)),
            role=role,
            netat_header=str(raw.get("netat_header", "NETAT") or "NETAT").strip() or "NETAT",
            cmdpw=str(raw.get("cmdpw", "USR") or "USR").strip() or "USR",
            web_user=str(raw.get("web_user", "admin") or "admin").strip() or "admin",
            web_password=str(raw.get("web_password", "admin") if raw.get("web_password") is not None else "admin"),
            uid=str(raw.get("uid", "") or "").strip().upper(),
            config_port=max(1, min(65535, config_port)),
        )


def modules_from_cfg(cfg: Dict[str, Any]) -> List[NetworkModule]:
    raw = cfg.get("network_modules") if isinstance(cfg, dict) else None
    if not isinstance(raw, list):
        return []
    return [NetworkModule.from_dict(item) for item in raw]


def modules_to_cfg(modules: Sequence[NetworkModule]) -> List[Dict[str, Any]]:
    return [m.to_dict() for m in modules]


# ---------------------------------------------------------------------------
# Parser / Builder (ohne Sockets – unit-testbar)
# ---------------------------------------------------------------------------

_OK_RE = re.compile(r"(?:\+OK|\+ok)\s*=\s*(.*)", re.IGNORECASE | re.DOTALL)


def extract_ok_payload(text: str) -> Optional[str]:
    """Extrahiert den Payload hinter ``+OK=`` / ``+ok=`` aus einer AT-Antwort."""
    if not text:
        return None
    # Mehrzeilig: erste passende Zeile bevorzugen
    for line in str(text).replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        s = line.strip()
        if not s:
            continue
        m = _OK_RE.search(s)
        if m:
            return m.group(1).strip()
    m = _OK_RE.search(str(text))
    return m.group(1).strip() if m else None


def parse_wan_response(payload: str) -> Dict[str, str]:
    """Parst WAN-Payload: Mode,IP,Mask,GW[,DNS1[,DNS2]].

    NE2 liefert oft zwei DNS; NA11x einen. Fehlende Felder bleiben leer.
    """
    parts = [p.strip() for p in str(payload or "").split(",")]
    while len(parts) < 6:
        parts.append("")
    mode = parts[0].upper() if parts[0] else ""
    if mode not in ("DHCP", "STATIC"):
        # USR nutzt manchmal "static"/"DHCP" – schon upper
        if mode.lower() == "static":
            mode = "STATIC"
        elif mode.lower() == "dhcp":
            mode = "DHCP"
    return {
        "mode": mode,
        "ip": parts[1],
        "mask": parts[2],
        "gateway": parts[3],
        "dns": parts[4],
        "dns2": parts[5],
    }


def build_wan_cmd(
    mode: str,
    ip: str,
    mask: str,
    gateway: str,
    dns: str = "",
    dns2: str = "",
    *,
    with_dns2: bool = True,
) -> str:
    """Baut ``AT+WAN=...`` (ohne Zeilenende)."""
    m = str(mode or "STATIC").strip().upper()
    if m not in ("DHCP", "STATIC"):
        m = "STATIC"
    parts = [m, str(ip or "").strip(), str(mask or "").strip(), str(gateway or "").strip()]
    d1 = str(dns or "").strip() or "8.8.8.8"
    parts.append(d1)
    if with_dns2:
        parts.append(str(dns2 or "").strip() or "8.8.4.4")
    return "AT+WAN=" + ",".join(parts)


def parse_wann_response(payload: str) -> Dict[str, str]:
    """USR ``AT+WANN``: mode,address,mask,gateway (ohne DNS)."""
    parts = [p.strip() for p in str(payload or "").split(",")]
    while len(parts) < 4:
        parts.append("")
    mode = parts[0].upper()
    if mode.lower() == "static":
        mode = "STATIC"
    elif mode.lower() == "dhcp":
        mode = "DHCP"
    return {
        "mode": mode,
        "ip": parts[1],
        "mask": parts[2],
        "gateway": parts[3],
        "dns": "",
        "dns2": "",
    }


def build_wann_cmd(mode: str, ip: str, mask: str, gateway: str) -> str:
    m = str(mode or "STATIC").strip().upper()
    if m not in ("DHCP", "STATIC"):
        m = "STATIC"
    # USR erwartet oft "static"/"DHCP"
    m_out = "static" if m == "STATIC" else "DHCP"
    return f"AT+WANN={m_out},{ip},{mask},{gateway}"


def build_wsdns_cmd(dns: str) -> str:
    return f"AT+WSDNS={str(dns or '').strip()}"


def parse_sock_response(payload: str) -> Dict[str, str]:
    """Parst SOCK-Payload (NE2 mit linkId oder NA11x ohne).

    NE2: ``0,TCPC,192.168.3.3,8888``
    NA11x: ``TCPC,192.168.3.3,8888``
    """
    parts = [p.strip() for p in str(payload or "").split(",")]
    link_id = ""
    mode = ""
    remote_ip = ""
    remote_port = ""
    if not parts:
        return {"link_id": "", "mode": "", "remote_ip": "", "remote_port": ""}
    # Erstes Feld Zahl → linkId
    if parts[0].isdigit() and len(parts) >= 4:
        link_id = parts[0]
        mode = parts[1].upper()
        remote_ip = parts[2]
        remote_port = parts[3]
    elif len(parts) >= 3:
        mode = parts[0].upper()
        remote_ip = parts[1]
        remote_port = parts[2]
    return {
        "link_id": link_id,
        "mode": mode,
        "remote_ip": remote_ip,
        "remote_port": remote_port,
    }


def build_sock_cmd(
    mode: str,
    remote_ip: str,
    remote_port: int | str,
    *,
    link_id: Optional[int] = 0,
) -> str:
    """Baut ``AT+SOCK=...``. ``link_id=None`` → NA11x-Stil ohne Link-ID."""
    m = str(mode or "TCPS").strip().upper()
    if m not in ("TCPC", "TCPS", "UDPC", "UDPS", "DISABLE", "MQTTC", "HTTPC"):
        m = "TCPS"
    port = str(int(remote_port) if str(remote_port).isdigit() else remote_port)
    ip = str(remote_ip or "0.0.0.0").strip() or "0.0.0.0"
    if link_id is None:
        return f"AT+SOCK={m},{ip},{port}"
    return f"AT+SOCK={int(link_id)},{m},{ip},{port}"


def parse_netp_response(payload: str) -> Dict[str, str]:
    """USR ``AT+NETP``: protocol,CS,port,IP."""
    parts = [p.strip() for p in str(payload or "").split(",")]
    while len(parts) < 4:
        parts.append("")
    proto = parts[0].upper()
    cs = parts[1].upper()
    # Map auf Ebyte-aehnliche Modi
    mode = ""
    if proto == "TCP" and cs == "SERVER":
        mode = "TCPS"
    elif proto == "TCP" and cs == "CLIENT":
        mode = "TCPC"
    elif proto == "UDP" and cs == "SERVER":
        mode = "UDPS"
    elif proto == "UDP" and cs == "CLIENT":
        mode = "UDPC"
    else:
        mode = f"{proto}_{cs}".strip("_")
    return {
        "protocol": proto,
        "cs": cs,
        "mode": mode,
        "remote_port": parts[2],
        "remote_ip": parts[3],
        "link_id": "",
    }


def build_netp_cmd(mode: str, remote_ip: str, remote_port: int | str) -> str:
    """Baut ``AT+NETP=TCP,CLIENT|SERVER,port,ip`` aus Ebyte-Modus."""
    m = str(mode or "TCPS").strip().upper()
    if m == "TCPS":
        proto, cs = "TCP", "SERVER"
    elif m == "TCPC":
        proto, cs = "TCP", "CLIENT"
    elif m == "UDPS":
        proto, cs = "UDP", "SERVER"
    elif m == "UDPC":
        proto, cs = "UDP", "CLIENT"
    else:
        proto, cs = "TCP", "SERVER"
    port = str(int(remote_port) if str(remote_port).isdigit() else remote_port)
    ip = str(remote_ip or "0.0.0.0").strip() or "0.0.0.0"
    return f"AT+NETP={proto},{cs},{port},{ip}"


def build_netat_line(header: str, at_cmd: str) -> str:
    """NE2 Network Fast AT: ``NETAT+WAN`` aus Header + ``AT+WAN``."""
    h = str(header or "NETAT").strip() or "NETAT"
    cmd = str(at_cmd or "").strip()
    if cmd.upper().startswith("AT+"):
        body = cmd[2:]  # "+WAN=..." bzw. "+WAN"
    elif cmd.upper().startswith("AT"):
        body = cmd[2:]
        if not body.startswith("+"):
            body = "+" + body.lstrip("+")
    else:
        body = cmd if cmd.startswith("+") else ("+" + cmd)
    return f"{h}{body}"


def build_usr_line(cmdpw: str, at_cmd: str) -> str:
    """USR Transparent-AT: ``USRAT+WANN`` (kein CR hier)."""
    pw = str(cmdpw or "USR").strip() or "USR"
    cmd = str(at_cmd or "").strip()
    if not cmd.upper().startswith("AT"):
        if cmd.startswith("+"):
            cmd = "AT" + cmd
        else:
            cmd = "AT+" + cmd.lstrip("+")
    return f"{pw}{cmd}"


def parse_linksta_response(payload: str) -> str:
    """``Connect`` / ``Disconnect`` bzw. ``0,Connect`` → normalisiert."""
    parts = [p.strip() for p in str(payload or "").split(",")]
    sta = parts[-1] if parts else ""
    s = sta.strip().lower()
    if s in ("connect", "connected", "on"):
        return "Connect"
    if s in ("disconnect", "disconnected", "off"):
        return "Disconnect"
    return sta


# ---------------------------------------------------------------------------
# Ebyte Web-API (NE2 / NA111) – Login HMAC-SHA1
#   NE2:   login.js + loginsubmit.json + basic/static/socketA.json
#   NA111: login.json + loginsubmit + 3..7.json (JS-Variablen) + ok.html
# ---------------------------------------------------------------------------

WEB_FLAVOR_NE2 = "ne2"
WEB_FLAVOR_NA111 = "na111"

# sock_mode in NE2 Web-JSON (status.html Optionen)
_EBYTE_SOCK_MODE_TO_AT = {
    0: "DISABLE",
    1: "TCPC",
    2: "TCPS",
    3: "UDPC",
    4: "UDPS",
    5: "MQTTC",
    6: "HTTPC",
}
_EBYTE_AT_TO_SOCK_MODE = {v: k for k, v in _EBYTE_SOCK_MODE_TO_AT.items()}

# NA111 paraconfig.html Arbeitsmodus (__07)
_NA111_SOCK_MODE_TO_AT = {
    0: "TCPC",
    1: "TCPS",
    2: "UDPC",
    3: "UDPS",
    4: "MQTTC",
    5: "HTTPC",
}
_NA111_AT_TO_SOCK_MODE = {v: k for k, v in _NA111_SOCK_MODE_TO_AT.items()}


def ip_octets_to_str(octets: Any) -> str:
    """``[192,168,0,246]`` oder String → ``192.168.0.246``."""
    if isinstance(octets, str):
        return octets.strip()
    if isinstance(octets, (list, tuple)) and len(octets) >= 4:
        try:
            return ".".join(str(int(x)) for x in octets[:4])
        except (TypeError, ValueError):
            return ""
    return ""


def ip_str_to_octets(ip: str) -> List[int]:
    parts = str(ip or "").strip().split(".")
    if len(parts) != 4:
        raise ValueError(f"invalid ipv4: {ip!r}")
    out = [int(p) for p in parts]
    if any(o < 0 or o > 255 for o in out):
        raise ValueError(f"invalid ipv4: {ip!r}")
    return out


def ebyte_sock_mode_to_at(mode: Any) -> str:
    try:
        return _EBYTE_SOCK_MODE_TO_AT.get(int(mode), "TCPS")
    except (TypeError, ValueError):
        return "TCPS"


def ebyte_at_to_sock_mode(mode: str) -> int:
    m = str(mode or "TCPS").strip().upper()
    return int(_EBYTE_AT_TO_SOCK_MODE.get(m, 2))


def status_has_data(st: Dict[str, Any]) -> bool:
    """True wenn Auslesen mindestens ein sinnvolles Feld geliefert hat."""
    if not isinstance(st, dict):
        return False
    wan = st.get("wan") if isinstance(st.get("wan"), dict) else {}
    sock = st.get("sock") if isinstance(st.get("sock"), dict) else {}
    if str(wan.get("ip") or "").strip():
        return True
    if any(str(st.get(k) or "").strip() for k in ("model", "mac", "ver")):
        return True
    if str(sock.get("mode") or "").strip() and str(sock.get("mode")).upper() != "DISABLE":
        return True
    return False


def map_ebyte_web_to_status(
    basic: Dict[str, Any],
    static: Dict[str, Any],
    socket_a: Dict[str, Any],
) -> Dict[str, Any]:
    """Mappt Ebyte Web-JSON auf das einheitliche Status-Dict."""
    dhcp = int(basic.get("net_DHCP", 0) or 0)
    wan = {
        "mode": "DHCP" if dhcp else "STATIC",
        "ip": ip_octets_to_str(basic.get("net_localIP")),
        "mask": ip_octets_to_str(basic.get("net_mask")),
        "gateway": ip_octets_to_str(basic.get("net_getway") or basic.get("net_gateway")),
        "dns": ip_octets_to_str(basic.get("net_dns")),
        "dns2": ip_octets_to_str(basic.get("net_dns2")),
    }
    # Live-IP aus static bevorzugen wenn vorhanden
    live_ip = str(static.get("static_devip") or "").strip()
    if live_ip and not wan["ip"]:
        wan["ip"] = live_ip
    sock = {
        "mode": ebyte_sock_mode_to_at(socket_a.get("sock_mode", 2)),
        "remote_ip": str(socket_a.get("sock_desname") or "").strip(),
        "remote_port": str(socket_a.get("sock_desport") or socket_a.get("sock_localport") or ""),
        "link_id": "0",
        "local_port": str(socket_a.get("sock_localport") or ""),
    }
    # Server-Modus: Anzeige-Port = lokaler Listen-Port
    if sock["mode"] in ("TCPS", "UDPS"):
        sock["remote_port"] = str(socket_a.get("sock_localport") or sock["remote_port"])
    return {
        "model": str(static.get("static_moudle") or static.get("static_module") or ""),
        "mac": str(static.get("static_MAC") or ""),
        "ver": str(static.get("static_FW") or ""),
        "wan": wan,
        "sock": sock,
        "link": "",
        "raw": {"basic": basic, "static": static, "socketA": socket_a},
        "source": "web",
    }


def _http_req(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timeout: float = 4.0,
    content_type: str = "application/json",
    basic_auth: Optional[Tuple[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, bytes]:
    """Minimaler HTTP/1.0-Client mit Content-Length (Ebyte/USR-Webserver)."""
    import base64

    hdrs = {
        "Host": host,
        "Connection": "close",
        "User-Agent": "RotorTcpBridge",
        "Accept": "*/*",
    }
    if basic_auth is not None:
        user, password = basic_auth
        token = base64.b64encode(f"{user}:{password}".encode("utf-8")).decode("ascii")
        hdrs["Authorization"] = f"Basic {token}"
    if extra_headers:
        hdrs.update(extra_headers)
    if body:
        hdrs["Content-Length"] = str(len(body))
        hdrs["Content-Type"] = content_type
    elif method.upper() == "POST":
        hdrs["Content-Length"] = "0"
        hdrs["Content-Type"] = content_type
    req = (
        f"{method} {path} HTTP/1.0\r\n"
        + "".join(f"{k}: {v}\r\n" for k, v in hdrs.items())
        + "\r\n"
    )
    sock = socket.create_connection((host, int(port)), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(req.encode("latin1") + body)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
        if b"\r\n\r\n" not in buf:
            return 0, b""
        head_b, data = buf.split(b"\r\n\r\n", 1)
        head = head_b.decode("latin1", "replace")
        m = re.match(r"HTTP/\d\.\d\s+(\d+)", head)
        status = int(m.group(1)) if m else 0
        content_len = None
        for line in head.split("\r\n")[1:]:
            if line.lower().startswith("content-length:"):
                try:
                    content_len = int(line.split(":", 1)[1].strip())
                except ValueError:
                    content_len = None
                break
        while content_len is not None and len(data) < content_len:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        if content_len is not None:
            data = data[:content_len]
        else:
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                data += chunk
                if len(data) > 500_000:
                    break
        if data[:2] == b"\x1f\x8b":
            try:
                data = zlib.decompress(data, 16 + zlib.MAX_WBITS)
            except Exception:
                deco = zlib.decompressobj(16 + zlib.MAX_WBITS)
                data = deco.decompress(data)
        return status, data
    finally:
        try:
            sock.close()
        except OSError:
            pass


def parse_ebyte_script_object(body: bytes) -> Dict[str, Any]:
    """Parst ``var dat0={...}`` / ``dat4={...}`` aus NA111-Script-Antworten."""
    text = body.decode("utf-8", "replace").strip()
    m = re.search(r"=\s*(\{.*\})\s*;?\s*$", text, re.DOTALL)
    if not m:
        raise RuntimeError(f"Kein JSON-Objekt in Script-Antwort: {text[:80]!r}")
    raw = m.group(1)
    try:
        return json.loads(raw, strict=False)
    except json.JSONDecodeError:
        # gelegentlich kaputte Steuerzeichen / abgeschnittene Antworten
        cleaned = "".join(ch if ord(ch) >= 32 or ch in "\t\n\r" else " " for ch in raw)
        return json.loads(cleaned, strict=False)


def na111_sock_mode_to_at(mode: Any) -> str:
    try:
        return _NA111_SOCK_MODE_TO_AT.get(int(mode), "TCPS")
    except (TypeError, ValueError):
        return "TCPS"


def na111_at_to_sock_mode(mode: str) -> int:
    m = str(mode or "TCPS").strip().upper()
    return int(_NA111_AT_TO_SOCK_MODE.get(m, 1))


def map_na111_para_to_status(para: Dict[str, Any]) -> Dict[str, Any]:
    """Mappt NA111 ALL_para (__08 IP, __07 Modus, …) auf Status-Dict."""
    dhcp = str(para.get("__06", "0") or "0").strip()
    sock_mode = na111_sock_mode_to_at(para.get("__07", 1))
    local_port = str(para.get("__09") or "")
    remote_port = str(para.get("__0F") or "")
    display_port = local_port if sock_mode in ("TCPS", "UDPS") else remote_port
    return {
        "model": str(para.get("__02") or ""),
        "mac": str(para.get("__05") or ""),
        "ver": str(para.get("__04") or ""),
        "wan": {
            "mode": "DHCP" if dhcp == "1" else "STATIC",
            "ip": str(para.get("__08") or "").strip(),
            "mask": str(para.get("__0B") or "").strip(),
            "gateway": str(para.get("__0C") or "").strip(),
            "dns": str(para.get("__0D") or "").strip(),
            "dns2": "",
        },
        "sock": {
            "mode": sock_mode,
            "remote_ip": str(para.get("__0E") or "").strip(),
            "remote_port": display_port,
            "link_id": "",
            "local_port": local_port,
            "web_port": str(para.get("__0A") or ""),
        },
        "link": "",
        "raw": {"para": para},
        "source": "web_na111",
        "web_flavor": WEB_FLAVOR_NA111,
    }


def _http_req_retry(
    host: str,
    port: int,
    method: str,
    path: str,
    body: bytes = b"",
    *,
    timeout: float = 4.0,
    content_type: str = "application/json",
    basic_auth: Optional[Tuple[str, str]] = None,
    extra_headers: Optional[Dict[str, str]] = None,
    retries: int = 3,
    retry_delay: float = 0.15,
    require_body: bool = False,
) -> Tuple[int, bytes]:
    """Wie ``_http_req``, mit Retries bei HTTP 0 / Verbindungsabbruechen (NA111-Webserver)."""
    last_st, last_body = 0, b""
    last_exc: Optional[BaseException] = None
    attempts = max(1, int(retries))
    for i in range(attempts):
        try:
            st, data = _http_req(
                host,
                port,
                method,
                path,
                body,
                timeout=timeout,
                content_type=content_type,
                basic_auth=basic_auth,
                extra_headers=extra_headers,
            )
            last_st, last_body = st, data
            last_exc = None
            if st == 0 or (require_body and st == 200 and not data):
                if i + 1 < attempts:
                    time.sleep(retry_delay * (i + 1))
                    continue
            return st, data
        except OSError as exc:
            last_exc = exc
            if i + 1 < attempts:
                time.sleep(retry_delay * (i + 1))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return last_st, last_body


def ebyte_flavor_hint_for_vendor(vendor: str) -> Optional[str]:
    """Vendor → bevorzugter Web-Flavor (vermeidet unnoetige 404 auf dem NA111)."""
    v = str(vendor or "").strip().lower()
    if v == VENDOR_NA11X:
        return WEB_FLAVOR_NA111
    if v == VENDOR_NE2:
        return WEB_FLAVOR_NE2
    return None


def ebyte_detect_web_flavor(
    host: str,
    web_port: int,
    *,
    timeout: float = 4.0,
    prefer: Optional[str] = None,
) -> Tuple[str, str]:
    """Liefert ``(flavor, rand_key)``. NE2: login.js, NA111: login.json."""
    order = [
        (WEB_FLAVOR_NA111, "/login.json"),
        (WEB_FLAVOR_NE2, "/login.js"),
    ]
    if prefer == WEB_FLAVOR_NE2:
        order = [
            (WEB_FLAVOR_NE2, "/login.js"),
            (WEB_FLAVOR_NA111, "/login.json"),
        ]
    elif prefer == WEB_FLAVOR_NA111:
        # NA111: nur login.json – login.js (404) stoert den schwachen Webserver
        order = [(WEB_FLAVOR_NA111, "/login.json")]

    errors: List[str] = []
    for flavor, path in order:
        try:
            st, body = _http_req_retry(
                host, web_port, "GET", path, timeout=timeout, retries=3, require_body=True
            )
        except OSError as exc:
            errors.append(f"{path}: {exc}")
            time.sleep(0.2)
            continue
        if st != 200 or not body:
            errors.append(f"{path}: HTTP {st}")
            time.sleep(0.2)
            continue
        text = body.decode("utf-8", "replace")
        m = re.search(r"RAND_KEY\s*=\s*['\"]([^'\"]+)['\"]", text)
        if m:
            return flavor, m.group(1)
        errors.append(f"{path}: kein RAND_KEY")
        time.sleep(0.1)
    raise RuntimeError("Web-Login-Seite nicht gefunden (" + "; ".join(errors) + ")")


def ebyte_web_login(
    host: str,
    web_port: int = 80,
    user: str = "admin",
    password: str = "admin",
    *,
    timeout: float = 4.0,
    flavor: Optional[str] = None,
) -> Tuple[str, str]:
    """Web-Login: HMAC-SHA1(user+RAND_KEY, key=password) → ``(token, flavor)``."""
    if flavor in (WEB_FLAVOR_NE2, WEB_FLAVOR_NA111):
        path = "/login.js" if flavor == WEB_FLAVOR_NE2 else "/login.json"
        st, body = _http_req_retry(
            host, web_port, "GET", path, timeout=timeout, retries=4, require_body=True
        )
        if st != 200 or not body:
            raise RuntimeError(f"{path} HTTP {st}")
        m = re.search(r"RAND_KEY\s*=\s*['\"]([^'\"]+)['\"]", body.decode("utf-8", "replace"))
        if not m:
            raise RuntimeError(f"RAND_KEY nicht in {path}")
        detected = flavor
        rand_key = m.group(1)
    else:
        detected, rand_key = ebyte_detect_web_flavor(host, web_port, timeout=timeout)

    digest_hex = hmac.new(
        str(password).encode("utf-8"),
        (str(user) + rand_key).encode("utf-8"),
        hashlib.sha1,
    ).hexdigest().encode("ascii")

    submit_paths = (
        ("/loginsubmit.json", "/loginsubmit")
        if detected == WEB_FLAVOR_NE2
        else ("/loginsubmit", "/loginsubmit.json")
    )
    last_st = 0
    for path in submit_paths:
        st, tok = _http_req_retry(
            host,
            web_port,
            "POST",
            path,
            digest_hex,
            timeout=timeout,
            content_type="application/octet-stream",
            retries=3,
            require_body=True,
        )
        last_st = st
        if st == 200 and tok and tok.strip():
            time.sleep(0.05)
            return tok.decode("utf-8", "replace").strip(), detected
    raise RuntimeError(f"Web-Login fehlgeschlagen (HTTP {last_st}) – Benutzer/Passwort prüfen")


def ebyte_web_get_json(
    host: str,
    web_port: int,
    token: str,
    name: str,
    *,
    timeout: float = 4.0,
) -> Dict[str, Any]:
    path = f"/{name}.json?token={token}&date={int(time.time() * 1000)}"
    st, body = _http_req_retry(
        host, web_port, "GET", path, timeout=timeout, retries=3, require_body=True
    )
    if st == 401:
        raise RuntimeError("Web-Token ungültig (401)")
    if st != 200:
        raise RuntimeError(f"{name}.json HTTP {st}")
    return json.loads(body.decode("utf-8"))


def ebyte_web_post_json(
    host: str,
    web_port: int,
    token: str,
    name: str,
    payload: Dict[str, Any],
    *,
    timeout: float = 4.0,
) -> None:
    path = f"/{name}.json?token={token}&date={int(time.time() * 1000)}"
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    st, _ = _http_req_retry(
        host, web_port, "POST", path, body, timeout=timeout, retries=3
    )
    if st == 401:
        raise RuntimeError("Web-Token ungültig (401)")
    if st != 200:
        raise RuntimeError(f"POST {name}.json HTTP {st}")


def ebyte_web_apply(host: str, web_port: int, token: str, *, timeout: float = 4.0) -> None:
    """NE2: ``DeviceContrl`` nach dem Speichern."""
    path = f"/DeviceContrl?token={token}&date={int(time.time() * 1000)}"
    st, _ = _http_req_retry(
        host,
        web_port,
        "POST",
        path,
        b"parameter send completed",
        timeout=timeout,
        content_type="text/plain",
        retries=2,
    )
    if st not in (200, 0):
        raise RuntimeError(f"DeviceContrl HTTP {st}")


def ebyte_na111_get_para(
    host: str,
    web_port: int,
    token: str,
    *,
    timeout: float = 4.0,
) -> Dict[str, Any]:
    """Laedt NA111-Parameter aus 3.json…7.json (Script-Variablen dat0…dat4)."""
    para: Dict[str, Any] = {}
    # 3..6 sind fuer WAN/Socket noetig; 7 (Modbus-Liste) optional
    for n in (3, 4, 5, 6, 7):
        path = f"/{n}.json?{token}"
        time.sleep(0.05)
        last_err: Optional[str] = None
        for attempt in range(4):
            try:
                st, body = _http_req_retry(
                    host,
                    web_port,
                    "GET",
                    path,
                    timeout=timeout,
                    retries=2,
                    retry_delay=0.2,
                    require_body=True,
                )
                if st == 401:
                    raise RuntimeError("Web-Token ungültig (401)")
                if st != 200 or not body:
                    last_err = f"{n}.json HTTP {st}"
                    time.sleep(0.2 * (attempt + 1))
                    continue
                para.update(parse_ebyte_script_object(body))
                last_err = None
                break
            except (OSError, RuntimeError, json.JSONDecodeError, ValueError) as exc:
                last_err = str(exc)
                time.sleep(0.2 * (attempt + 1))
        if last_err:
            if n == 7:
                break
            raise RuntimeError(last_err)
    if "__08" not in para:
        raise RuntimeError("NA111-Parameter unvollstaendig (kein __08/IP)")
    return para


def ebyte_na111_post_line(
    host: str,
    web_port: int,
    token: str,
    line: str,
    *,
    timeout: float = 4.0,
) -> None:
    """NA111: einzelne Parameterzeile an ``/ok.html?token`` (wie Web-UI SMG)."""
    path = f"/ok.html?{token}"
    st, _ = _http_req_retry(
        host,
        web_port,
        "POST",
        path,
        str(line).encode("utf-8"),
        timeout=timeout,
        content_type="text/plain;charset=UTF-8",
        retries=4,
        retry_delay=0.25,
    )
    if st == 401:
        raise RuntimeError("Web-Token ungültig (401)")
    if st != 200:
        raise RuntimeError(f"ok.html HTTP {st} für {line!r}")


def ebyte_na111_apply(
    host: str,
    web_port: int,
    token: str,
    para: Dict[str, Any],
    *,
    timeout: float = 4.0,
    reboot: bool = False,
    keys: Optional[Sequence[str]] = None,
) -> None:
    """Schreibt Parameterzeilen und schliesst mit ``parameter send completed``.

    ``keys``: wenn gesetzt, nur diese Felder (sonst alle ausser Geraete-Info).
    Fuer WAN/Socket reichen typisch ``__06``…``__0F``.
    """
    skip = {"__02", "__03", "__04", "__05"}
    if keys is None:
        key_list = [k for k in sorted(para.keys()) if k not in skip]
    else:
        key_list = [k for k in keys if k in para]
    for key in key_list:
        val = para.get(key, "")
        ebyte_na111_post_line(host, web_port, token, f"{key}={val}", timeout=timeout)
        time.sleep(0.05)
    ebyte_na111_post_line(host, web_port, token, "parameter send completed", timeout=timeout)
    if reboot:
        try:
            time.sleep(0.15)
            ebyte_na111_post_line(host, web_port, token, "web set device reboot", timeout=timeout)
        except Exception:
            pass


def read_status_web(module: NetworkModule, *, timeout: float = 4.0) -> Dict[str, Any]:
    """Liest Konfiguration ueber Ebyte-Web-API (NE2 oder NA111, admin/admin)."""
    host = module.host.strip()
    if not host:
        raise ValueError("host empty")
    hint = ebyte_flavor_hint_for_vendor(module.vendor)
    token, flavor = ebyte_web_login(
        host,
        module.web_port,
        module.web_user or "admin",
        module.web_password if module.web_password is not None else "admin",
        timeout=timeout,
        flavor=hint,
    )
    if flavor is None:
        # sollte nicht vorkommen
        flavor = hint or WEB_FLAVOR_NE2
    if flavor == WEB_FLAVOR_NA111:
        para = ebyte_na111_get_para(host, module.web_port, token, timeout=timeout)
        return map_na111_para_to_status(para)

    basic = ebyte_web_get_json(host, module.web_port, token, "basic", timeout=timeout)
    static = ebyte_web_get_json(host, module.web_port, token, "static", timeout=timeout)
    socket_a = ebyte_web_get_json(host, module.web_port, token, "socketA", timeout=timeout)
    st = map_ebyte_web_to_status(basic, static, socket_a)
    st["web_flavor"] = WEB_FLAVOR_NE2
    return st


def write_config_web(
    module: NetworkModule,
    wan: Dict[str, Any],
    sock: Dict[str, Any],
    *,
    timeout: float = 4.0,
) -> Dict[str, Any]:
    """Schreibt WAN + Socket ueber Ebyte-Web-API (NE2 oder NA111)."""
    host = module.host.strip()
    if not host:
        raise ValueError("host empty")
    hint = ebyte_flavor_hint_for_vendor(module.vendor)
    token, flavor = ebyte_web_login(
        host,
        module.web_port,
        module.web_user or "admin",
        module.web_password if module.web_password is not None else "admin",
        timeout=timeout,
        flavor=hint,
    )

    mode = str(wan.get("mode", "STATIC") or "STATIC").upper()
    sock_mode = str(sock.get("mode", "TCPS") or "TCPS").upper()
    remote_port = int(sock.get("remote_port") or module.at_port or 8886)

    if flavor == WEB_FLAVOR_NA111:
        para = ebyte_na111_get_para(host, module.web_port, token, timeout=timeout)
        para["__06"] = "1" if mode == "DHCP" else "0"
        para["__08"] = str(wan.get("ip") or host)
        para["__0B"] = str(wan.get("mask") or "255.255.255.0")
        para["__0C"] = str(wan.get("gateway") or "0.0.0.0")
        para["__0D"] = str(wan.get("dns") or "8.8.8.8")
        para["__07"] = str(na111_at_to_sock_mode(sock_mode))
        if sock_mode in ("TCPS", "UDPS"):
            para["__09"] = str(remote_port)
        else:
            para["__0E"] = str(sock.get("remote_ip") or "0.0.0.0")
            para["__0F"] = str(remote_port)
        # Kompletten Parametersatz schreiben (Einzel-Felder korrumpieren die NA111-Firmware)
        ebyte_na111_apply(
            host,
            module.web_port,
            token,
            para,
            timeout=timeout,
            reboot=True,
        )
        return {"ok": True, "source": "web_na111", "para": para}

    basic = ebyte_web_get_json(host, module.web_port, token, "basic", timeout=timeout)
    socket_a = ebyte_web_get_json(host, module.web_port, token, "socketA", timeout=timeout)
    basic["net_DHCP"] = 1 if mode == "DHCP" else 0
    basic["net_localIP"] = ip_str_to_octets(str(wan.get("ip") or host))
    basic["net_mask"] = ip_str_to_octets(str(wan.get("mask") or "255.255.255.0"))
    basic["net_getway"] = ip_str_to_octets(str(wan.get("gateway") or "0.0.0.0"))
    basic["net_dns"] = ip_str_to_octets(str(wan.get("dns") or "8.8.8.8"))
    basic["net_dns2"] = ip_str_to_octets(str(wan.get("dns2") or "8.8.4.4"))
    socket_a["sock_mode"] = ebyte_at_to_sock_mode(sock_mode)
    if sock_mode in ("TCPS", "UDPS"):
        socket_a["sock_localport"] = remote_port
    else:
        socket_a["sock_desname"] = str(sock.get("remote_ip") or "0.0.0.0")
        socket_a["sock_desport"] = remote_port
    ebyte_web_post_json(host, module.web_port, token, "basic", basic, timeout=timeout)
    ebyte_web_post_json(host, module.web_port, token, "socketA", socket_a, timeout=timeout)
    ebyte_web_apply(host, module.web_port, token, timeout=timeout)
    return {"ok": True, "source": "web", "basic": basic, "socketA": socket_a}


# ---------------------------------------------------------------------------
# USR-DR164 Web-UI – HTTP Basic Auth, Config als JS-Variablen in HTML
# ---------------------------------------------------------------------------

def parse_html_js_string_vars(html: str) -> Dict[str, str]:
    """Extrahiert ``var name = "value";`` aus USR-HTML-Seiten."""
    out: Dict[str, str] = {}
    for m in re.finditer(r'\bvar\s+(\w+)\s*=\s*"([^"]*)"\s*;', str(html or "")):
        out[m.group(1)] = m.group(2)
    return out


def format_usr_mac(raw: str) -> str:
    s = re.sub(r"[^0-9A-Fa-f]", "", str(raw or ""))
    if len(s) == 12:
        return "-".join(s[i : i + 2] for i in range(0, 12, 2)).upper()
    return str(raw or "").strip()


def usr_web_sock_mode(net_pro: str, net_cs: str) -> str:
    """USR Web: net_pro/net_cs → TCPS/TCPC/…"""
    pro = str(net_pro or "").strip().upper()
    cs = str(net_cs or "").strip().upper()
    if pro in ("TCPS", "TCPC", "UDPS", "UDPC"):
        return pro
    if pro == "MQTT":
        return "MQTTC"
    if pro == "HTTP":
        return "HTTPC"
    if pro == "TCP":
        return "TCPS" if cs == "SERVER" else "TCPC"
    if pro == "UDP":
        return "UDPS" if cs == "SERVER" else "UDPC"
    return "TCPS"


def usr_sock_mode_to_web(mode: str) -> str:
    """Unser Modus → USR ``net_pro``-Feld (TCPS/TCPC/…)."""
    m = str(mode or "TCPS").strip().upper()
    if m == "MQTTC":
        return "MQTT"
    if m == "HTTPC":
        return "HTTP"
    if m in ("TCPS", "TCPC", "UDPS", "UDPC", "MQTT", "HTTP"):
        return m
    return "TCPS"


def map_usr_web_to_status(
    status_vars: Dict[str, str],
    wan_vars: Dict[str, str],
    net_vars: Dict[str, str],
) -> Dict[str, Any]:
    sock_mode = usr_web_sock_mode(net_vars.get("net_pro", ""), net_vars.get("net_cs", ""))
    port = str(net_vars.get("net_port") or "")
    return {
        "model": str(status_vars.get("cover_mid") or "USR-DR164"),
        "mac": format_usr_mac(status_vars.get("cover_sta_mac") or status_vars.get("cover_ap_mac") or ""),
        "ver": str(status_vars.get("cover_ver") or ""),
        "wan": {
            "mode": str(wan_vars.get("wan_setting_dhcp") or "STATIC").upper(),
            "ip": str(wan_vars.get("wan_setting_ip") or status_vars.get("cover_sta_ip") or "").strip(),
            "mask": str(wan_vars.get("wan_setting_msk") or "").strip(),
            "gateway": str(wan_vars.get("wan_setting_gw") or "").strip(),
            "dns": str(wan_vars.get("wan_setting_dns") or "").strip(),
            "dns2": "",
        },
        "sock": {
            "mode": sock_mode,
            "remote_ip": str(net_vars.get("net_ip") or "").strip(),
            "remote_port": port,
            "link_id": "",
            "local_port": port if sock_mode in ("TCPS", "UDPS") else "",
        },
        "link": "",
        "raw": {"status": status_vars, "wan": wan_vars, "net": net_vars},
        "source": "web_usr",
        "web_flavor": "usr",
    }


def _usr_auth(module: NetworkModule) -> Tuple[str, str]:
    return (
        str(module.web_user or "admin").strip() or "admin",
        module.web_password if module.web_password is not None else "admin",
    )


def usr_web_get_page(
    host: str,
    web_port: int,
    path: str,
    auth: Tuple[str, str],
    *,
    timeout: float = 4.0,
) -> str:
    st, body = _http_req(
        host, web_port, "GET", path, timeout=timeout, basic_auth=auth
    )
    if st == 401:
        raise RuntimeError("Web-Login fehlgeschlagen (401) – Benutzer/Passwort prüfen (Basic Auth)")
    if st != 200 or not body:
        raise RuntimeError(f"{path} HTTP {st}")
    return body.decode("utf-8", "replace")


def usr_web_post_form(
    host: str,
    web_port: int,
    path: str,
    fields: Dict[str, Any],
    auth: Tuple[str, str],
    *,
    timeout: float = 4.0,
    referer: str = "",
) -> None:
    """POST Formular an USR-Web-UI. Referer ist Pflicht (sonst ignoriert die Firmware den POST)."""
    body = urlencode({k: str(v) for k, v in fields.items()}).encode("utf-8")
    extra: Dict[str, str] = {}
    if referer:
        extra["Referer"] = referer
    st, _resp = _http_req(
        host,
        web_port,
        "POST",
        path,
        body,
        timeout=timeout,
        content_type="application/x-www-form-urlencoded",
        basic_auth=auth,
        extra_headers=extra or None,
    )
    if st == 401:
        raise RuntimeError("Web-Login fehlgeschlagen (401) – Benutzer/Passwort prüfen")
    if st not in (200, 302, 303):
        raise RuntimeError(f"POST {path} HTTP {st}")


def read_status_web_usr(module: NetworkModule, *, timeout: float = 4.0) -> Dict[str, Any]:
    """Liest USR-DR164-Config aus status/wireless/net HTML (Basic Auth admin/admin)."""
    host = module.host.strip()
    if not host:
        raise ValueError("host empty")
    auth = _usr_auth(module)
    status_html = usr_web_get_page(host, module.web_port, "/status_en.html", auth, timeout=timeout)
    wan_html = usr_web_get_page(host, module.web_port, "/wireless_en.html", auth, timeout=timeout)
    net_html = usr_web_get_page(host, module.web_port, "/net_en.html", auth, timeout=timeout)
    return map_usr_web_to_status(
        parse_html_js_string_vars(status_html),
        parse_html_js_string_vars(wan_html),
        parse_html_js_string_vars(net_html),
    )


def write_config_web_usr(
    module: NetworkModule,
    wan: Dict[str, Any],
    sock: Dict[str, Any],
    *,
    timeout: float = 4.0,
    reboot: bool = True,
) -> Dict[str, Any]:
    """Schreibt WAN + Socket A ueber USR do_cmd_en.html (Basic Auth + Referer)."""
    host = module.host.strip()
    if not host:
        raise ValueError("host empty")
    auth = _usr_auth(module)
    web_port = int(module.web_port)
    base = f"http://{host}:{web_port}" if web_port not in (80, 443) else f"http://{host}"

    wan_vars = parse_html_js_string_vars(
        usr_web_get_page(host, web_port, "/wireless_en.html", auth, timeout=timeout)
    )
    net_vars = parse_html_js_string_vars(
        usr_web_get_page(host, web_port, "/net_en.html", auth, timeout=timeout)
    )

    mode = str(wan.get("mode", "STATIC") or "STATIC").upper()
    wan_fields = {
        "sta_setting_ssid": wan_vars.get("sta_setting_ssid", ""),
        "sta_setting_auth": wan_vars.get("sta_setting_auth", "WPA2PSK"),
        "sta_setting_encry": wan_vars.get("sta_setting_encry", "AES"),
        "sta_setting_wpakey": wan_vars.get("sta_setting_wpakey", ""),
        "wan_setting_dhcp": "DHCP" if mode == "DHCP" else "STATIC",
        "wan_setting_ip": str(wan.get("ip") or host),
        "wan_setting_msk": str(wan.get("mask") or "255.255.255.0"),
        "wan_setting_gw": str(wan.get("gateway") or "0.0.0.0"),
        "wan_setting_dns": str(wan.get("dns") or "8.8.8.8"),
    }
    usr_web_post_form(
        host,
        web_port,
        "/do_cmd_en.html",
        wan_fields,
        auth,
        timeout=timeout,
        referer=f"{base}/wireless_en.html",
    )

    # Verifizieren: Seite sagt immer „Saved“, auch wenn ohne Referer nichts gespeichert wurde
    wan_check = parse_html_js_string_vars(
        usr_web_get_page(host, web_port, "/wireless_en.html", auth, timeout=timeout)
    )
    if (
        str(wan_check.get("wan_setting_ip") or "") != wan_fields["wan_setting_ip"]
        or str(wan_check.get("wan_setting_gw") or "") != wan_fields["wan_setting_gw"]
        or str(wan_check.get("wan_setting_dns") or "") != wan_fields["wan_setting_dns"]
        or str(wan_check.get("wan_setting_msk") or "") != wan_fields["wan_setting_msk"]
    ):
        raise RuntimeError(
            "USR hat WAN-Werte nicht übernommen. "
            f"Soll GW/DNS={wan_fields['wan_setting_gw']}/{wan_fields['wan_setting_dns']}, "
            f"ist {wan_check.get('wan_setting_gw')}/{wan_check.get('wan_setting_dns')}"
        )

    sock_mode = str(sock.get("mode", "TCPS") or "TCPS").upper()
    remote_port = int(sock.get("remote_port") or module.at_port or 8899)
    net_fields = {
        "net_pro": usr_sock_mode_to_web(sock_mode),
        "net_port": str(remote_port),
        "net_ip": str(sock.get("remote_ip") or net_vars.get("net_ip") or "0.0.0.0"),
        "net_to": net_vars.get("net_to", "300"),
        "netb_pro": net_vars.get("netb_pro", "NONE"),
        "netb_port": net_vars.get("netb_port", "0"),
        "netb_ip": net_vars.get("netb_ip", " "),
        "netb_to": net_vars.get("netb_to", "300"),
    }
    usr_web_post_form(
        host,
        web_port,
        "/do_cmd_en.html",
        net_fields,
        auth,
        timeout=timeout,
        referer=f"{base}/net_en.html",
    )

    if reboot:
        usr_web_post_form(
            host,
            web_port,
            "/success_en.html",
            {"HF_PROCESS_CMD": "RESTART"},
            auth,
            timeout=timeout,
            referer=f"{base}/do_cmd_en.html",
        )
    return {
        "ok": True,
        "source": "web_usr",
        "wan": wan_fields,
        "net": net_fields,
        "rebooted": bool(reboot),
    }


# ---------------------------------------------------------------------------
# TCP-Hilfen
# ---------------------------------------------------------------------------

def probe_online(host: str, port: int, timeout: float = 0.4) -> bool:
    """Schneller TCP-Connect-Probe (fuer Status-LED)."""
    h = str(host or "").strip()
    if not h:
        return False
    try:
        p = int(port)
    except (TypeError, ValueError):
        return False
    if p < 1 or p > 65535:
        return False
    try:
        with socket.create_connection((h, p), timeout=float(timeout)):
            return True
    except OSError:
        return False


def dk8de_resolve_connect_host(
    *,
    contact_host: str = "",
    configured_host: str = "",
) -> str:
    """Erreichbare IP fuer AT/Web — nicht die noch nicht gesetzte Ziel-IP."""
    from .dk8de_wlan_module import _normalize_contact_ip

    for candidate in (contact_host, configured_host):
        c = _normalize_contact_ip(candidate)
        if c:
            return c
    return "255.255.255.255"


def dk8de_module_connect_host(
    module: NetworkModule,
    *,
    at_host: str = "",
) -> str:
    return dk8de_resolve_connect_host(
        contact_host=str(at_host or module.contact_host or ""),
        configured_host=str(module.host or ""),
    )


def probe_module(module: NetworkModule, timeout: float = 0.4) -> bool:
    if module.vendor == VENDOR_DK8DE:
        from .dk8de_wlan_module import probe_dk8de

        host = dk8de_module_connect_host(module)
        return probe_dk8de(
            host,
            module.uid,
            config_port=int(module.config_port or 8880),
            web_port=int(module.web_port or 80),
            web_user=str(module.web_user or "admin"),
            web_password=str(module.web_password or "Rotorconfig"),
            timeout=max(timeout, 0.8),
        )
    return probe_online(module.host, module.at_port, timeout=timeout)


def _tcp_transact(
    host: str,
    port: int,
    payload: bytes,
    *,
    timeout: float = 2.0,
    read_idle: float = 0.25,
    max_bytes: int = 4096,
) -> str:
    """Eine kurze TCP-Transaktion: senden, Antwort lesen, schliessen."""
    sock = socket.create_connection((host, int(port)), timeout=float(timeout))
    try:
        sock.settimeout(float(timeout))
        sock.sendall(payload)
        chunks: List[bytes] = []
        total = 0
        sock.settimeout(float(read_idle))
        while total < max_bytes:
            try:
                data = sock.recv(min(1024, max_bytes - total))
            except socket.timeout:
                break
            if not data:
                break
            chunks.append(data)
            total += len(data)
            if b"\n" in data and (b"+OK" in data or b"+ok" in data or b"+ERR" in data):
                # Noch kurz nachlesen
                sock.settimeout(0.15)
                continue
        return b"".join(chunks).decode("utf-8", errors="replace")
    finally:
        try:
            sock.close()
        except OSError:
            pass


def _send_at(
    module: NetworkModule,
    at_cmd: str,
    *,
    timeout: float = 2.0,
) -> str:
    """Sendet einen AT-Befehl herstellerspezifisch ueber den Daten-Port."""
    host = module.host.strip()
    if not host:
        raise ValueError("host empty")
    vendor = module.vendor
    if vendor == VENDOR_USR:
        line = build_usr_line(module.cmdpw, at_cmd) + "\r"
    else:
        # NE2 / NA11x / generic: Network Fast AT
        line = build_netat_line(module.netat_header, at_cmd) + "\r\n"
    return _tcp_transact(host, module.at_port, line.encode("ascii", errors="ignore"), timeout=timeout)


def _ok_or_raise(resp: str, cmd: str) -> str:
    payload = extract_ok_payload(resp)
    if payload is None:
        # Manche Schreibbefehle antworten nur mit +OK ohne =
        if re.search(r"\+OK\b|\+ok\b", resp or "", re.IGNORECASE):
            return ""
        raise RuntimeError(f"Keine AT-Antwort fuer {cmd}: {resp!r}")
    if payload.upper().startswith("ERR") or payload.startswith("-"):
        raise RuntimeError(f"AT-Fehler fuer {cmd}: {payload}")
    return payload


def read_status(
    module: NetworkModule,
    *,
    timeout: float = 2.5,
    at_host: str = "",
) -> Dict[str, Any]:
    """Liest Modell/MAC/WAN/SOCK/Link-Status vom Modul.

    NE2/NA11x/USR: primaer Web-API (AT auf dem Daten-Port oft unbrauchbar wegen
    transparentem RS485-Verkehr). USR: HTTP Basic Auth (admin/admin).
    """
    out: Dict[str, Any] = {
        "model": "",
        "mac": "",
        "ver": "",
        "wan": {"mode": "", "ip": "", "mask": "", "gateway": "", "dns": "", "dns2": ""},
        "sock": {"mode": "", "remote_ip": "", "remote_port": "", "link_id": ""},
        "link": "",
        "raw": {},
    }
    vendor = module.vendor
    errors: List[str] = []

    if vendor == VENDOR_DK8DE:
        from .dk8de_wlan_module import read_status_dk8de, read_status_web_dk8de

        connect = dk8de_module_connect_host(module, at_host=at_host)
        web_st: Optional[Dict[str, Any]] = None
        try:
            web_st = read_status_web_dk8de(
                connect,
                web_port=int(module.web_port or 80),
                user=str(module.web_user or "admin"),
                password=str(module.web_password or "Rotorconfig"),
                timeout=max(timeout, 3.0),
            )
            if status_has_data(web_st):
                return web_st
            errors.append("DK8DE-Web lieferte keine Daten")
        except Exception as exc:
            errors.append(f"Web: {exc}")

        uid = (module.uid or "").strip()
        if not uid and isinstance(web_st, dict):
            uid = str(web_st.get("uid") or "").strip()
        if not uid:
            out["error"] = "; ".join(errors) if errors else "UID fehlt (DK8DE-Modul)"
            return out
        try:
            st = read_status_dk8de(
                connect,
                uid,
                config_port=int(module.config_port or 8880),
                timeout=max(timeout, 3.0),
            )
            if status_has_data(st):
                return st
            errors.append("DK8DE-AT lieferte keine Daten")
        except Exception as exc:
            errors.append(f"DK8DE-AT: {exc}")
        out["error"] = "; ".join(errors) if errors else "Keine Konfigurationsdaten empfangen"
        return out

    if vendor == VENDOR_USR:
        try:
            web_st = read_status_web_usr(module, timeout=max(timeout, 4.0))
            if status_has_data(web_st):
                return web_st
            errors.append("USR-Web lieferte keine Daten")
        except Exception as exc:
            errors.append(f"Web: {exc}")
    elif vendor in (VENDOR_NE2, VENDOR_NA11X, VENDOR_GENERIC):
        try:
            web_st = read_status_web(module, timeout=max(timeout, 4.0))
            if status_has_data(web_st):
                return web_st
            errors.append("Web-API lieferte keine Daten")
        except Exception as exc:
            errors.append(f"Web: {exc}")

    def _q(cmd: str) -> str:
        resp = _send_at(module, cmd, timeout=timeout)
        out["raw"][cmd] = resp
        return _ok_or_raise(resp, cmd)

    try:
        if vendor == VENDOR_USR:
            try:
                out["model"] = _q("AT+MID")
            except Exception:
                try:
                    out["model"] = _q("AT+VER")
                except Exception:
                    pass
            try:
                out["ver"] = _q("AT+VER")
            except Exception:
                pass
            try:
                out["mac"] = _q("AT+WSMAC")
            except Exception:
                pass
            try:
                wann = parse_wann_response(_q("AT+WANN"))
                try:
                    dns = _q("AT+WSDNS")
                    wann["dns"] = dns
                except Exception:
                    pass
                out["wan"] = wann
            except Exception:
                pass
            try:
                netp = parse_netp_response(_q("AT+NETP"))
                out["sock"] = {
                    "mode": netp.get("mode", ""),
                    "remote_ip": netp.get("remote_ip", ""),
                    "remote_port": netp.get("remote_port", ""),
                    "link_id": "",
                }
            except Exception:
                pass
            try:
                out["link"] = parse_linksta_response(_q("AT+TCPLK"))
            except Exception:
                try:
                    out["link"] = parse_linksta_response(_q("AT+WSLK"))
                except Exception:
                    pass
            if status_has_data(out):
                out["source"] = "at"
        elif vendor in (VENDOR_NE2, VENDOR_NA11X, VENDOR_GENERIC):
            for key, cmd in (("model", "AT+MODEL"), ("mac", "AT+MAC"), ("ver", "AT+VER")):
                try:
                    out[key] = _q(cmd)
                except Exception:
                    pass
            try:
                out["wan"] = parse_wan_response(_q("AT+WAN"))
            except Exception:
                pass
            sock_payload = None
            try:
                sock_payload = _q("AT+SOCK=0")
            except Exception:
                try:
                    sock_payload = _q("AT+SOCK")
                except Exception:
                    pass
            if sock_payload is not None:
                out["sock"] = parse_sock_response(sock_payload)
            try:
                out["link"] = parse_linksta_response(_q("AT+LINKSTA=0"))
            except Exception:
                try:
                    out["link"] = parse_linksta_response(_q("AT+LINKSTA"))
                except Exception:
                    pass
            if status_has_data(out):
                out["source"] = "at"
    except Exception as exc:
        errors.append(str(exc))

    if not status_has_data(out):
        out["error"] = "; ".join(errors) if errors else "Keine Konfigurationsdaten empfangen"
    return out


def write_config(
    module: NetworkModule,
    wan: Dict[str, Any],
    sock: Dict[str, Any],
    *,
    reboot: bool = True,
    timeout: float = 3.0,
    at_host: str = "",
) -> Dict[str, Any]:
    """Schreibt WAN + SOCK. NE2/NA11x/USR primaer via Web-API."""
    results: Dict[str, Any] = {"commands": [], "ok": True, "error": ""}
    vendor = module.vendor

    if vendor == VENDOR_DK8DE:
        from .dk8de_wlan_module import write_config_dk8de

        if not (module.uid or "").strip():
            return {"ok": False, "error": "UID fehlt (DK8DE-Modul)", "commands": []}
        try:
            connect = dk8de_module_connect_host(module, at_host=at_host)
            res = write_config_dk8de(
                connect,
                module.uid,
                wan,
                sock,
                config_port=int(module.config_port or 8880),
                reboot=reboot,
                timeout=max(timeout, 12.0),
            )
            return res
        except Exception as exc:
            return {"ok": False, "error": str(exc), "commands": []}

    if vendor == VENDOR_USR:
        try:
            web_res = write_config_web_usr(
                module, wan, sock, timeout=max(timeout, 4.0), reboot=reboot
            )
            results.update(web_res)
            results["ok"] = True
            return results
        except Exception as exc:
            results["ok"] = False
            results["error"] = f"Web-Schreiben fehlgeschlagen: {exc}"
            # AT-Fallback weiter unten

    if vendor in (VENDOR_NE2, VENDOR_NA11X, VENDOR_GENERIC):
        try:
            web_res = write_config_web(module, wan, sock, timeout=max(timeout, 4.0))
            results.update(web_res)
            results["ok"] = True
            return results
        except Exception as exc:
            results["ok"] = False
            results["error"] = f"Web-Schreiben fehlgeschlagen: {exc}"
            return results

    def _w(cmd: str) -> None:
        resp = _send_at(module, cmd, timeout=timeout)
        results["commands"].append({"cmd": cmd, "resp": resp})
        _ok_or_raise(resp, cmd)

    try:
        mode = str(wan.get("mode", "STATIC") or "STATIC")
        ip = str(wan.get("ip", "") or "")
        mask = str(wan.get("mask", "") or "")
        gw = str(wan.get("gateway", "") or "")
        dns = str(wan.get("dns", "") or "")
        dns2 = str(wan.get("dns2", "") or "")
        sock_mode = str(sock.get("mode", "TCPS") or "TCPS")
        remote_ip = str(sock.get("remote_ip", "0.0.0.0") or "0.0.0.0")
        remote_port = sock.get("remote_port", 8886)

        if vendor == VENDOR_USR:
            _w(build_wann_cmd(mode, ip, mask, gw))
            if dns:
                _w(build_wsdns_cmd(dns))
            _w(build_netp_cmd(sock_mode, remote_ip, remote_port))
            if reboot:
                try:
                    _w("AT+Z")
                except Exception:
                    results["commands"].append({"cmd": "AT+Z", "resp": "(reboot)"})
            results["ok"] = True
            results["error"] = ""
        else:
            with_dns2 = vendor != VENDOR_NA11X
            _w(build_wan_cmd(mode, ip, mask, gw, dns, dns2, with_dns2=with_dns2))
            if vendor == VENDOR_NA11X:
                _w(build_sock_cmd(sock_mode, remote_ip, remote_port, link_id=None))
            else:
                _w(build_sock_cmd(sock_mode, remote_ip, remote_port, link_id=0))
            if reboot:
                try:
                    _w("AT+REBT")
                except Exception:
                    results["commands"].append({"cmd": "AT+REBT", "resp": "(reboot)"})
    except Exception as exc:
        results["ok"] = False
        results["error"] = str(exc)
    return results


# ---------------------------------------------------------------------------
# Ebyte UDP-Discovery / Read / Write (Ports 1901/1902)
# ---------------------------------------------------------------------------

EBYTE_UDP_CMD_PORT = 1901  # PC → Geraet (Broadcast)
EBYTE_UDP_LISTEN_PORT = 1902  # Geraet → PC
EBYTE_DISCOVER_PING = b"www.cdebyte.comwww.cdebyte.com"
EBYTE_CRC16_INIT = 0xB001
EBYTE_CRC16_POLY = 0xA001

# Netzfeld-Offsets im Seiten-Body (nach dem 14-Byte-UDP-Header)
_NE2_NET_OFF = 154  # Seite 1: IP, GW, Mask, DNS1, DNS2 (je 4 B)
_NA111_NET_OFF = 14  # Seite 0: IP, GW, Mask, DNS (je 4 B)
_NA111_SAVE_FLAG_OFF = 171  # Tool setzt 0x0A beim Speichern
_NA111_SAVE_PAGE3_FLAG = 0x1E


def ebyte_crc16(data: bytes, init: int = EBYTE_CRC16_INIT) -> int:
    """CRC-16 (IBM/Modbus-Poly 0xA001), Init 0xB001 – aus Ebyte-Mitschnitten."""
    c = init & 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8):
            c = (c >> 1) ^ EBYTE_CRC16_POLY if c & 1 else (c >> 1)
    return c & 0xFFFF


def ebyte_mac_str(mac: bytes) -> str:
    return ":".join(f"{b:02X}" for b in mac[:6])


def ebyte_mac_from_str(text: str) -> bytes:
    parts = str(text or "").replace("-", ":").split(":")
    if len(parts) != 6:
        raise ValueError(f"invalid MAC: {text!r}")
    return bytes(int(p, 16) for p in parts)


def _ipv4_at(body: bytes, off: int) -> str:
    if off < 0 or off + 4 > len(body):
        return ""
    return ".".join(str(b) for b in body[off : off + 4])


def _put_ipv4(body: bytearray, off: int, ip: str) -> None:
    parts = ip_str_to_octets(ip)
    if off + 4 > len(body):
        raise ValueError(f"invalid IPv4 offset: {ip!r} @{off}")
    body[off : off + 4] = bytes(parts)


def _c_str_at(body: bytes, off: int, max_len: int = 32) -> str:
    if off < 0 or off >= len(body):
        return ""
    end = min(len(body), off + max_len)
    chunk = body[off:end]
    z = chunk.find(b"\x00")
    if z >= 0:
        chunk = chunk[:z]
    try:
        return chunk.decode("ascii", errors="ignore").strip()
    except Exception:
        return ""


@dataclass
class EbytePage:
    """Eine gelesene Konfigurationsseite inkl. Checksummen-Kontext fuer Writes."""

    page: int
    body: bytes
    crc_k: int = 0  # lo XOR crc16(body) – seitenkonstant
    crc_hi: bytes = b"\x00\x00"  # 2 Bytes ab Geraete-Antwort (NE2: 00 00)

    def checksum_bytes(self, body: Optional[bytes] = None) -> bytes:
        data = self.body if body is None else body
        lo = (ebyte_crc16(data) ^ int(self.crc_k)) & 0xFFFF
        return struct.pack("<H", lo) + bytes(self.crc_hi[:2]).ljust(2, b"\x00")


@dataclass
class EbyteDevice:
    """Ergebnis von UDP-Discovery + optionalem Seitenlesen."""

    mac: bytes
    model: str = ""
    fw: str = ""
    sn: str = ""
    ip: str = ""
    mask: str = ""
    gateway: str = ""
    dns: str = ""
    dns2: str = ""
    vendor: str = VENDOR_GENERIC
    pages: Dict[int, EbytePage] = field(default_factory=dict)
    announce_tail: bytes = b""

    @property
    def mac_str(self) -> str:
        return ebyte_mac_str(self.mac)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mac": self.mac_str,
            "model": self.model,
            "fw": self.fw,
            "sn": self.sn,
            "ip": self.ip,
            "mask": self.mask,
            "gateway": self.gateway,
            "dns": self.dns,
            "dns2": self.dns2,
            "vendor": self.vendor,
            "page_count": len(self.pages),
        }


def _ebyte_parse_page_payload(payload: bytes) -> Optional[Tuple[int, bytes, int, bytes, bytes]]:
    """Parst ``fd 00``/``fe 01``: (page, body, crc_lo, crc_hi, mac)."""
    if len(payload) < 14 or payload[0] not in (0xFD, 0xFE) or payload[1] not in (0x00, 0x01):
        return None
    mac = payload[2:8]
    page = struct.unpack(">H", payload[8:10])[0]
    crc_lo = struct.unpack("<H", payload[10:12])[0]
    crc_hi = payload[12:14]
    body = payload[14:]
    return page, body, crc_lo, crc_hi, mac


def ebyte_page_from_payload(payload: bytes) -> Optional[EbytePage]:
    parsed = _ebyte_parse_page_payload(payload)
    if parsed is None:
        return None
    page, body, crc_lo, crc_hi, _mac = parsed
    k = (crc_lo ^ ebyte_crc16(body)) & 0xFFFF
    return EbytePage(page=page, body=body, crc_k=k, crc_hi=bytes(crc_hi))


def _ebyte_open_socket(listen_port: int = EBYTE_UDP_LISTEN_PORT) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("0.0.0.0", int(listen_port)))
    except OSError:
        sock.bind(("0.0.0.0", 0))
    sock.settimeout(0.2)
    return sock


def _ebyte_send_broadcast(sock: socket.socket, payload: bytes) -> None:
    sock.sendto(payload, ("255.255.255.255", EBYTE_UDP_CMD_PORT))


def ebyte_udp_discover(
    *,
    timeout: float = 1.5,
    read_pages: bool = True,
    max_pages: int = 16,
) -> List[EbyteDevice]:
    """Broadcast-Discovery auf UDP 1901/1902; optional Seiten lesen und Netzfelder parsen."""
    devices: Dict[bytes, EbyteDevice] = {}
    sock = _ebyte_open_socket()
    try:
        deadline = time.time() + max(0.3, float(timeout))
        # Zwei Pings wie im Mitschnitt
        for _ in range(2):
            _ebyte_send_broadcast(sock, EBYTE_DISCOVER_PING)
            time.sleep(0.05)
        while time.time() < deadline:
            try:
                data, _addr = sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                break
            if len(data) >= 10 and data[0] == 0xFD and data[1] == 0x06:
                mac = data[2:8]
                if mac not in devices:
                    devices[mac] = EbyteDevice(mac=mac, announce_tail=data[8:10])
        if read_pages:
            for mac, dev in list(devices.items()):
                pages: Dict[int, EbytePage] = {}
                # Direkt nach der Discovery-Antwort ist das Geraet manchmal noch
                # mit dem Senden/Verarbeiten der Broadcast-Pings beschaeftigt
                # (v. a. bei WLAN oder Mehrfach-Interface-Broadcast); ein
                # einzelner Fehlversuch beim Seiten-Lesen fuehrt sonst dazu,
                # dass Modell/IP/Firmware leer bleiben, obwohl das Modul
                # eindeutig online ist (MAC wurde ja bereits empfangen).
                # Deshalb den kompletten Seiten-Read mehrfach versuchen, bevor
                # aufgegeben wird.
                for attempt in range(3):
                    try:
                        pages = ebyte_udp_read_pages(
                            mac, sock=sock, max_pages=max_pages, timeout=0.8
                        )
                    except Exception:
                        pages = {}
                    if pages:
                        break
                    time.sleep(0.25)
                try:
                    ebyte_apply_pages(dev, pages)
                except Exception:
                    pass
    finally:
        try:
            sock.close()
        except OSError:
            pass
    return sorted(devices.values(), key=lambda d: (d.ip or "", d.mac_str))


def ebyte_udp_read_pages(
    mac: bytes,
    *,
    sock: Optional[socket.socket] = None,
    max_pages: int = 16,
    timeout: float = 0.8,
    page_count: Optional[int] = None,
    unicast_ip: str = "",
) -> Dict[int, EbytePage]:
    """Liest Konfigurationsseiten ``fe 00`` / ``fd 00`` fuer eine MAC."""
    if len(mac) != 6:
        raise ValueError("mac must be 6 bytes")
    own = sock is None
    if own:
        sock = _ebyte_open_socket()
    assert sock is not None
    pages: Dict[int, EbytePage] = {}
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
        except OSError:
            pass
        limit = int(page_count) if page_count is not None else int(max_pages)
        limit = max(1, min(limit, 32))
        for page in range(limit):
            req = bytes([0xFE, 0x00]) + mac + struct.pack(">H", page)
            got: Optional[EbytePage] = None
            for _try in range(3):
                _ebyte_drain(sock)
                extra_socks = _ebyte_send_cmd(
                    sock, req, unicast_ip=unicast_ip, all_interfaces=_try > 0
                )
                try:
                    all_socks = [sock] + extra_socks
                    end = time.time() + max(0.25, float(timeout))
                    while time.time() < end:
                        entry = _ebyte_recv_from_any(all_socks, end - time.time())
                        if entry is None:
                            continue
                        data, _addr = entry
                        parsed = _ebyte_parse_page_payload(data)
                        if parsed is None:
                            continue
                        pidx, body, crc_lo, crc_hi, rmac = parsed
                        if data[0] != 0xFD or data[1] != 0x00:
                            continue
                        if rmac != mac or pidx != page:
                            continue
                        k = (crc_lo ^ ebyte_crc16(body)) & 0xFFFF
                        got = EbytePage(page=page, body=body, crc_k=k, crc_hi=bytes(crc_hi))
                        break
                finally:
                    for es in extra_socks:
                        try:
                            es.close()
                        except OSError:
                            pass
                if got is not None:
                    break
            if got is None:
                break
            pages[page] = got
            # Letzte Seite typischerweise deutlich kuerzer
            if page_count is None and page > 0 and len(got.body) < 200:
                break
    finally:
        if own:
            try:
                sock.close()
            except OSError:
                pass
    return pages


def _ebyte_local_ipv4_addrs() -> List[str]:
    """Alle lokalen IPv4-Adressen (fuer Multi-NIC-Broadcast).

    Windows waehlt fuer ``255.255.255.255`` nur EIN Interface (die
    Default-Route). Ist das Ebyte-Modul ueber ein anderes Interface /
    Subnetz erreichbar, kommt der Broadcast dort nie an, obwohl Antworten
    (vom Geraet initiiert) trotzdem auf allen Interfaces empfangen werden.
    Deshalb senden wir zusaetzlich explizit von jeder lokalen Adresse aus.
    """
    addrs: List[str] = []
    try:
        _, _, addr_list = socket.gethostbyname_ex(socket.gethostname())
        for a in addr_list:
            if a and a not in addrs and not a.startswith("127."):
                addrs.append(a)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            a = info[4][0]
            if a and a not in addrs and not a.startswith("127."):
                addrs.append(a)
    except OSError:
        pass
    return addrs


def _ebyte_send_from_all_interfaces(payload: bytes, port: int) -> List[socket.socket]:
    """Sendet ``payload`` zusaetzlich von jeder lokalen Interface-Adresse aus.

    Die dabei erzeugten Sockets werden bewusst NICHT sofort wieder
    geschlossen, sondern an den Aufrufer zurueckgegeben: Sie sind (wie der
    Haupt-Socket) an ``EBYTE_UDP_LISTEN_PORT`` gebunden. Antwortet das Geraet
    sehr schnell, kann Windows das eingehende Paket dem spezifischeren
    Interface-Socket zustellen statt dem Wildcard-Haupt-Socket - schliesst man
    diesen Socket sofort nach dem Senden, geht genau diese Antwort verloren.
    Das erklaert das unregelmaessige "manchmal keine/unvollstaendige
    Modul-Infos"-Verhalten bei der Suche. Der Aufrufer muss die
    zurueckgegebenen Sockets zusammen mit dem Haupt-Socket auf Antworten
    abhorchen (``_ebyte_recv_from_any``) und danach schliessen.
    """
    socks: List[socket.socket] = []
    for addr in _ebyte_local_ipv4_addrs():
        s = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.bind((addr, EBYTE_UDP_LISTEN_PORT))
            s.settimeout(0.05)
            s.sendto(payload, ("255.255.255.255", port))
            socks.append(s)
        except OSError:
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass
    return socks


def _ebyte_recv_from_any(
    socks: Sequence[socket.socket], timeout: float
) -> Optional[Tuple[bytes, Any]]:
    """Wartet ueber alle uebergebenen Sockets gemeinsam auf die erste Antwort.

    Notwendig, weil eine Antwort des Geraets nicht zuverlaessig auf dem
    Wildcard-Haupt-Socket ankommt, sondern manchmal auf einem der zusaetzlich
    pro Interface geoeffneten Sockets (siehe ``_ebyte_send_from_all_interfaces``).
    """
    if not socks:
        return None
    try:
        ready, _wr, _err = select.select(list(socks), [], [], max(0.0, timeout))
    except (OSError, ValueError):
        return None
    if not ready:
        return None
    try:
        return ready[0].recvfrom(4096)
    except OSError:
        return None


def _ebyte_drain(sock: socket.socket) -> None:
    """Leert Empfangsreste (Discovery / vorherige Seiten)."""
    prev = sock.gettimeout()
    sock.settimeout(0.01)
    try:
        while True:
            sock.recvfrom(4096)
    except (socket.timeout, OSError):
        pass
    try:
        sock.settimeout(prev)
    except OSError:
        pass


def _ebyte_send_cmd(
    sock: socket.socket,
    payload: bytes,
    *,
    unicast_ip: str = "",
    netmask: str = "",
    all_interfaces: bool = False,
) -> List[socket.socket]:
    """Wie Mitschnitt: limited Broadcast an 255.255.255.255:1901 (Source-Port 1902).

    ``all_interfaces=True`` sendet zusaetzlich explizit von JEDER lokalen
    IPv4-Adresse aus, da Windows fuer einen an ``0.0.0.0`` gebundenen Socket
    beim Senden an ``255.255.255.255`` sonst nur ein einziges
    (Default-Route-)Interface waehlt. Bei Mehrfach-NIC-PCs oder wenn
    PC/Geraet in unterschiedlichen Subnetzen auf verschiedenen Interfaces
    haengen, waere der Broadcast sonst fuer das Geraet nicht sichtbar.

    Standardmaessig (``all_interfaces=False``) wird NUR der eine einfache
    Broadcast gesendet, wie im Mitschnitt. Grund: Hat der PC mehrere lokale
    Adressen, die (z. B. ueber virtuelle Adapter/VPN/gebruecktes WLAN) alle
    im selben Broadcast-Segment wie das Geraet landen, bekaeme das Geraet
    sonst bei JEDEM Versuch mehrere Kopien der gleichen Anfrage innerhalb
    von Millisekunden. Manche Module mit schwachem Netzwerk-Stack (v. a.
    NE2) verarbeiten das nicht zuverlaessig und antworten dann gar nicht -
    das aeussert sich als voellig unregelmaessiges Fehlschlagen der Suche
    bzw. des Auslesens. Deshalb wird der Multi-Interface-Broadcast nur bei
    einem erneuten Versuch (nach einem bereits fehlgeschlagenen einfachen
    Broadcast) zugeschaltet, als Fallback fuer den Mehrfach-Subnetz-Fall.

    Gibt die dabei zusaetzlich erzeugten (noch offenen) Sockets zurueck (leer,
    wenn ``all_interfaces=False``). Der Aufrufer MUSS diese zusammen mit
    ``sock`` auf eine Antwort abhorchen (``_ebyte_recv_from_any``) und danach
    schliessen - sonst kann eine schnell eintreffende Antwort verloren gehen
    (siehe ``_ebyte_send_from_all_interfaces``).
    """
    del netmask  # API-Kompatibilitaet; Mitschnitt nutzt nur limited Broadcast
    n = sock.sendto(payload, ("255.255.255.255", EBYTE_UDP_CMD_PORT))
    if n != len(payload):
        raise OSError(f"UDP broadcast truncated ({n}/{len(payload)})")
    extra_socks: List[socket.socket] = []
    if all_interfaces:
        extra_socks = _ebyte_send_from_all_interfaces(payload, EBYTE_UDP_CMD_PORT)
    # Zusaetzlicher Unicast schadet nicht, wenn Geraet erreichbar ist
    ip = str(unicast_ip or "").strip()
    if ip and ip not in ("0.0.0.0", "255.255.255.255"):
        try:
            sock.sendto(payload, (ip, EBYTE_UDP_CMD_PORT))
        except OSError:
            pass
    return extra_socks


def ebyte_udp_write_pages(
    mac: bytes,
    pages: Dict[int, EbytePage],
    *,
    bodies: Optional[Dict[int, bytes]] = None,
    timeout: float = 1.2,
    retries: int = 3,
    priority_last: Optional[Sequence[int]] = None,
    unicast_ip: str = "",
    netmask: str = "",
    sock: Optional[socket.socket] = None,
) -> Dict[str, Any]:
    """Schreibt Seiten per ``fe 01`` wie im Mitschnitt (Broadcast, Seiten 0..N)."""
    del priority_last, netmask
    if len(mac) != 6:
        raise ValueError("mac must be 6 bytes")
    result: Dict[str, Any] = {"ok": True, "acked": [], "error": ""}
    # Mitschnitt: streng aufsteigend 0,1,2,...
    order = sorted(pages.keys())

    own = sock is None
    if own:
        sock = _ebyte_open_socket()
    assert sock is not None
    try:
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65535)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 65535)
        except OSError:
            pass
        for page in order:
            ep = pages[page]
            body = bytes((bodies or {}).get(page, ep.body))
            payload = (
                bytes([0xFE, 0x01])
                + mac
                + struct.pack(">H", int(page))
                + ep.checksum_bytes(body)
                + body
            )
            acked = False
            last_rx = b""
            wait = max(0.6, float(timeout))
            for attempt in range(max(1, int(retries))):
                _ebyte_drain(sock)
                # Ein Send wie im Mitschnitt (kein Doppel-Send); Multi-Interface
                # erst ab dem zweiten Versuch (siehe _ebyte_send_cmd)
                extra_socks = _ebyte_send_cmd(
                    sock, payload, unicast_ip=unicast_ip, all_interfaces=attempt > 0
                )
                try:
                    all_socks = [sock] + extra_socks
                    end = time.time() + wait
                    while time.time() < end:
                        entry = _ebyte_recv_from_any(all_socks, end - time.time())
                        if entry is None:
                            continue
                        data, _addr = entry
                        last_rx = data
                        if len(data) < 12 or data[0] != 0xFD or data[1] != 0x01:
                            continue
                        if data[2:8] != mac:
                            continue
                        if struct.unpack(">H", data[8:10])[0] != page:
                            continue
                        if data[10:12] != b"\x00\x01":
                            continue
                        acked = True
                        break
                finally:
                    for es in extra_socks:
                        try:
                            es.close()
                        except OSError:
                            pass
                if acked:
                    break
                time.sleep(0.15 * (attempt + 1))
            if not acked:
                result["ok"] = False
                detail = last_rx[:16].hex() if last_rx else "kein RX"
                result["error"] = f"no ACK for page {page} ({detail})"
                break
            result["acked"].append(page)
            time.sleep(0.02)
    except Exception as exc:
        result["ok"] = False
        result["error"] = str(exc)
    finally:
        if own:
            try:
                sock.close()
            except OSError:
                pass
    return result


def ebyte_detect_vendor(model: str, pages: Dict[int, EbytePage]) -> str:
    m = (model or "").upper()
    if m.startswith("NE2"):
        return VENDOR_NE2
    if m.startswith("NA11") or m.startswith("NA111"):
        return VENDOR_NA11X
    p0 = pages.get(0)
    if p0 and _c_str_at(p0.body, 0).upper().startswith("NE2"):
        return VENDOR_NE2
    p5 = pages.get(5)
    if p5 and _c_str_at(p5.body, 0).upper().startswith("NA"):
        return VENDOR_NA11X
    return VENDOR_GENERIC


def ebyte_apply_pages(dev: EbyteDevice, pages: Dict[int, EbytePage]) -> EbyteDevice:
    """Fuellt Modell/FW/SN/Netzfelder aus gelesenen Seiten."""
    dev.pages = dict(pages)
    p0 = pages.get(0)
    if p0:
        model0 = _c_str_at(p0.body, 0)
        if model0.upper().startswith("NE2"):
            dev.model = model0
            # Mitschnitt: Modell 20 B, FW ab Offset 26, MAC@40, SN@46
            dev.fw = _c_str_at(p0.body, 26)
            if len(p0.body) >= 56:
                sn = _c_str_at(p0.body, 46)
                if sn:
                    dev.sn = sn
        elif len(p0.body) >= 30 and p0.body[14] in (10, 172, 192):
            # NA111: Netz in Seite 0, Ident in spaeterer Seite
            pass
    p5 = pages.get(5)
    if p5:
        m5 = _c_str_at(p5.body, 0)
        if m5.upper().startswith("NA"):
            dev.model = m5 or dev.model
            if len(p5.body) >= 12:
                fw = _c_str_at(p5.body, 12)
                if fw:
                    dev.fw = fw
            if len(p5.body) >= 40:
                sn = _c_str_at(p5.body, 29)
                if sn:
                    dev.sn = sn
    dev.vendor = ebyte_detect_vendor(dev.model, pages)
    ebyte_parse_net_fields(dev)
    return dev


def ebyte_parse_net_fields(dev: EbyteDevice) -> None:
    """Liest IP/Mask/GW/DNS aus NE2-Seite 1 bzw. NA111-Seite 0."""
    if dev.vendor == VENDOR_NE2 or (dev.model or "").upper().startswith("NE2"):
        p1 = dev.pages.get(1)
        if p1 and len(p1.body) >= _NE2_NET_OFF + 20:
            b = p1.body
            off = _NE2_NET_OFF
            dev.ip = _ipv4_at(b, off)
            dev.gateway = _ipv4_at(b, off + 4)
            dev.mask = _ipv4_at(b, off + 8)
            dev.dns = _ipv4_at(b, off + 12)
            dev.dns2 = _ipv4_at(b, off + 16)
            return
    p0 = dev.pages.get(0)
    if p0 and len(p0.body) >= _NA111_NET_OFF + 16:
        b = p0.body
        off = _NA111_NET_OFF
        # Heuristik: plausibles IPv4-Muster
        if b[off] in (10, 172, 192) or b[off + 2] != 0:
            dev.ip = _ipv4_at(b, off)
            dev.gateway = _ipv4_at(b, off + 4)
            dev.mask = _ipv4_at(b, off + 8)
            dev.dns = _ipv4_at(b, off + 12)
            if not (dev.model or "").upper().startswith("NE2"):
                if not dev.vendor or dev.vendor == VENDOR_GENERIC:
                    dev.vendor = VENDOR_NA11X


def ebyte_patch_net_pages(
    pages: Dict[int, EbytePage],
    *,
    ip: str,
    mask: str,
    gateway: str,
    dns: str = "",
    dns2: str = "",
    vendor: str = "",
) -> Dict[int, bytes]:
    """Gibt geaenderte Seiten-Bodies zurueck (CRC wird beim Write neu berechnet)."""
    v = (vendor or "").strip().lower()
    if not v:
        v = ebyte_detect_vendor("", pages)
    bodies: Dict[int, bytes] = {p: bytes(ep.body) for p, ep in pages.items()}

    if v == VENDOR_NE2:
        p1 = pages.get(1)
        if p1 is None:
            raise ValueError("NE2 page 1 missing")
        buf = bytearray(p1.body)
        off = _NE2_NET_OFF
        if len(buf) < off + 20:
            raise ValueError("NE2 page 1 too short for network fields")
        _put_ipv4(buf, off, ip)
        _put_ipv4(buf, off + 4, gateway)
        _put_ipv4(buf, off + 8, mask)
        if dns:
            _put_ipv4(buf, off + 12, dns)
        if dns2:
            _put_ipv4(buf, off + 16, dns2)
        bodies[1] = bytes(buf)
        return bodies

    # NA111 / Default: Seite 0
    p0 = pages.get(0)
    if p0 is None:
        raise ValueError("page 0 missing")
    buf = bytearray(p0.body)
    off = _NA111_NET_OFF
    if len(buf) < off + 16:
        raise ValueError("page 0 too short for network fields")
    _put_ipv4(buf, off, ip)
    _put_ipv4(buf, off + 4, gateway)
    _put_ipv4(buf, off + 8, mask)
    if dns:
        _put_ipv4(buf, off + 12, dns)
    # Speichern-Flag wie im offiziellen Tool (Byte 171 = 0x0A)
    if len(buf) > _NA111_SAVE_FLAG_OFF:
        buf[_NA111_SAVE_FLAG_OFF] = 0x0A
    bodies[0] = bytes(buf)
    p3 = pages.get(3)
    if p3 is not None and len(p3.body) >= 1:
        b3 = bytearray(p3.body)
        b3[0] = _NA111_SAVE_PAGE3_FLAG
        bodies[3] = bytes(b3)
    return bodies


def ebyte_set_network(
    mac: bytes,
    *,
    ip: str,
    mask: str,
    gateway: str,
    dns: str = "",
    dns2: str = "",
    vendor: str = "",
    pages: Optional[Dict[int, EbytePage]] = None,
) -> Dict[str, Any]:
    """Setzt Netzdaten per UDP wie im Mitschnitt: Ping → alle Seiten lesen → alle schreiben."""
    hint_ip = ""
    if pages:
        probe = EbyteDevice(mac=mac)
        ebyte_apply_pages(probe, pages)
        hint_ip = probe.ip or ""
        if not vendor:
            vendor = probe.vendor

    sock = _ebyte_open_socket()
    try:
        # Geraet „wecken“ wie bei Discovery
        for _ in range(2):
            _ebyte_send_broadcast(sock, EBYTE_DISCOVER_PING)
            time.sleep(0.05)
        _ebyte_drain(sock)

        page_map = ebyte_udp_read_pages(
            mac, sock=sock, timeout=0.8, unicast_ip=hint_ip
        )
        if not page_map and pages:
            page_map = pages
        if not page_map:
            return {"ok": False, "error": "no pages read"}

        tmp = EbyteDevice(mac=mac)
        ebyte_apply_pages(tmp, page_map)
        if not vendor:
            vendor = tmp.vendor
        current_ip = tmp.ip or hint_ip

        try:
            bodies = ebyte_patch_net_pages(
                page_map,
                ip=ip,
                mask=mask,
                gateway=gateway,
                dns=dns,
                dns2=dns2,
                vendor=vendor,
            )
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

        # Mitschnitt: ALLE gelesenen Seiten zurueckschreiben (nicht nur Diff)
        return ebyte_udp_write_pages(
            mac,
            page_map,
            bodies=bodies,
            unicast_ip=current_ip,
            timeout=1.2,
            retries=4,
            sock=sock,
        )
    finally:
        try:
            sock.close()
        except OSError:
            pass


def vendor_from_ebyte_model(model: str) -> str:
    return ebyte_detect_vendor(model, {})


def guess_local_subnet_cidr() -> str:
    """Best-effort /24-CIDR der primaeren lokalen IPv4 (Legacy, ungenutzt von UDP-Suche)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
        finally:
            s.close()
        if ip and not ip.startswith("127."):
            net = ipaddress.ip_network(f"{ip}/24", strict=False)
            return str(net)
    except OSError:
        pass
    return "192.168.0.0/24"


def scan_subnet(
    cidr: str,
    ports: Sequence[int],
    *,
    timeout: float = 0.25,
    max_workers: int = 64,
) -> List[Dict[str, Any]]:
    """Legacy TCP-Connect-Scan (durch Ebyte-UDP-Discovery ersetzt)."""
    try:
        net = ipaddress.ip_network(str(cidr or "").strip() or guess_local_subnet_cidr(), strict=False)
    except ValueError:
        net = ipaddress.ip_network(guess_local_subnet_cidr(), strict=False)
    port_list = []
    for p in ports:
        try:
            pi = int(p)
        except (TypeError, ValueError):
            continue
        if 1 <= pi <= 65535:
            port_list.append(pi)
    if not port_list:
        port_list = [8886, 8899, 80]

    hosts = [str(h) for h in net.hosts()]
    if len(hosts) > 1024:
        hosts = hosts[:1024]

    found: List[Dict[str, Any]] = []
    targets = [(h, p) for h in hosts for p in port_list]

    def _one(hp: Tuple[str, int]) -> Optional[Dict[str, Any]]:
        h, p = hp
        if probe_online(h, p, timeout=timeout):
            return {"host": h, "port": p}
        return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        for fut in concurrent.futures.as_completed(
            [ex.submit(_one, t) for t in targets]
        ):
            try:
                r = fut.result()
            except Exception:
                r = None
            if r:
                found.append(r)
    found.sort(key=lambda x: (tuple(int(x) for x in x["host"].split(".")), x["port"]))
    return found


def vendor_for_port(port: int) -> str:
    """Heuristik: bekannte Default-Ports → Vendor."""
    p = int(port)
    if p == 8899:
        return VENDOR_USR
    if p in (8886, 8887):
        return VENDOR_NE2
    return VENDOR_GENERIC
