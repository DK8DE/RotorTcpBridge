"""Flrig-Bridge: XML-RPC über HTTP (WSJT-X / Hamlib-FLRig) + optionaler Textmodus.

WSJT-X spricht mit dem Funkgerät-Typ „FLRig“ über Hamlibs flrig-Backend: POST /RPC2,
XML-RPC (main.get_version, rig.get_vfoA, …). Der frühere reine Zeilenmodus
(„GET FREQ“) bleibt für einfache Skripte erhalten, sofern die erste Anfrage
nicht wie HTTP aussieht.
"""

from __future__ import annotations

import re
import socket
import threading
import xml.sax.saxutils as xml_esc
from typing import Any, Callable, Set
from xml.etree import ElementTree as ET

from .utils import bind_tcp_listen_socket

_MAX_XMLRPC_BODY = 4 * 1024 * 1024

# Hamlib/XML-RPC++ sendet u. a. <?clientid="hamlib(pid)"?> — das ist für ElementTree kein
# gültiges XML (zweites PI) und würde sonst das gesamte methodCall-Parsing scheitern lassen.
_FLRIG_CLIENTID_PI = re.compile(r"<\?clientid[^?]*\?>\s*", re.IGNORECASE)

# rig.get_modes muss RIG_OK liefern (Hamlib flrig_open); pipe-getrennt wie fldigi/flrig.
_FLRIG_MODES_PIPE = (
    "USB|LSB|CW|CWU|CWL|FM|AM|FMN|NFM|RTTY|RTTY-U|RTTY-L|USB-D1|LSB-D1|"
    "DATA|DATA-USB|DATA-LSB|DIG|DIGU|DIGL|PKT|PKT-U|PKT-L|WFM|SPEC|C4FM|DV"
)


def _first_line_is_http(first_line: str) -> bool:
    t = first_line.strip().upper()
    if t.startswith("POST "):
        return True
    return t.startswith("GET ") and "HTTP/" in first_line.upper()


def _read_full_http_request(sock: socket.socket, buf: bytearray) -> bytes | None:
    """Liest einen vollständigen HTTP-Request inkl. Body (Content-Length) in buf."""
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(32768)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > _MAX_XMLRPC_BODY:
            return None
    header_end = buf.index(b"\r\n\r\n") + 4
    headers = buf[:header_end].decode("latin-1", errors="replace")
    content_length = 0
    for line in headers.split("\r\n"):
        if line.lower().startswith("content-length:"):
            try:
                content_length = int(line.split(":", 1)[1].strip())
            except ValueError:
                content_length = 0
            break
    need = header_end + max(0, content_length)
    while len(buf) < need:
        chunk = sock.recv(32768)
        if not chunk:
            return None
        buf += chunk
        if len(buf) > _MAX_XMLRPC_BODY:
            return None
    out = bytes(buf[:need])
    del buf[:need]
    return out


def _http_body(raw: bytes) -> bytes:
    if b"\r\n\r\n" not in raw:
        return b""
    return raw.split(b"\r\n\r\n", 1)[1]


def _xml_escape(s: str) -> str:
    return xml_esc.escape(s, entities={'"': "&quot;", "'": "&apos;"})


def _method_response_value(inner: str) -> str:
    # Eine Zeile ohne eingebettete \n: Hamlib read_transaction liest per \n und sucht
    # ``</methodResponse>`` im zusammengefügten Puffer — weniger Kleinteile-Parsing-Risiko.
    return (
        '<?xml version="1.0"?><methodResponse><params><param><value>'
        f"{inner}"
        "</value></param></params></methodResponse>"
    )


def _method_response_void() -> str:
    return (
        "<?xml version=\"1.0\"?><methodResponse><params><param><value>"
        "<boolean>1</boolean></value></param></params></methodResponse>"
    )


def _method_fault_unknown(method: str) -> str:
    # Hamlib flrig_transaction: strstr(xml, "unknown") → RIG_ENAVAIL
    m = _xml_escape(method)
    return (
        "<?xml version=\"1.0\"?><methodResponse><fault><value><struct>"
        "<member><name>faultCode</name><value><i4>-1</i4></value></member>"
        "<member><name>faultString</name><value><string>unknown method "
        f"{m}</string></value></member>"
        "</struct></value></fault></methodResponse>"
    )


def _sanitize_xmlrpc_body_text(text: str) -> str:
    return _FLRIG_CLIENTID_PI.sub("", text)


def _parse_method_name(body: bytes) -> str | None:
    try:
        text = body.decode("utf-8", errors="replace")
    except Exception:
        text = body.decode("latin-1", errors="replace")
    m = re.search(r"<methodName>\s*([^<]+?)\s*</methodName>", text, re.I)
    if m:
        return m.group(1).strip()
    try:
        root = ET.fromstring(_sanitize_xmlrpc_body_text(text))
    except ET.ParseError:
        return None
    el = root.find("methodName")
    if el is not None and el.text:
        return el.text.strip()
    return None


def _param_scalar_values(body: bytes) -> list[str | int | float]:
    """Erste XML-RPC-Parameter als Skalare (string/double/i4/int/boolean)."""
    out: list[str | int | float] = []
    try:
        text = body.decode("utf-8", errors="replace")
        root = ET.fromstring(_sanitize_xmlrpc_body_text(text))
    except Exception:
        return out
    params = root.find("params")
    if params is None:
        return out
    for p in params.findall("param"):
        val = p.find("value")
        if val is None or len(val) == 0:
            continue
        child = val[0]
        tag = child.tag.split("}")[-1].lower()
        tx = (child.text or "").strip()
        if tag in ("string",):
            out.append(tx)
        elif tag in ("double",):
            try:
                out.append(float(tx))
            except ValueError:
                out.append(tx)
        elif tag in ("i4", "int"):
            try:
                out.append(int(tx))
            except ValueError:
                out.append(tx)
        elif tag == "boolean":
            out.append(1 if tx == "1" else 0)
        else:
            out.append(tx)
    return out


def _body_first_frequency_hz(body: bytes) -> int | None:
    """Fallback, wenn ElementTree die Hamlib-/XML-RPC++-Parameter nicht erfasst."""
    if not body:
        return None
    try:
        t = body.decode("utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(
        r"<(?:double|i4|int)>\s*([0-9.eE+-]+)\s*</(?:double|i4|int)>",
        t,
        re.I,
    )
    if not m:
        return None
    try:
        v = float(m.group(1).strip())
        return int(round(v))
    except ValueError:
        return None


class FlrigBridgeServer:
    """Flrig-Bridge: XML-RPC (fldigi/WSJT-X) und kompakter Textmodus."""

    def __init__(
        self,
        get_state: Callable[[], dict],
        enqueue_write: Callable[[str], None],
        on_clients_changed: Callable[[int], None],
        log_write: Callable[[str, str], None],
        log_client_traffic: bool = True,
        on_state_patch: Callable[[dict[str, Any]], None] | None = None,
        on_tcp_activity: Callable[[], None] | None = None,
    ):
        self._get_state = get_state
        self._enqueue_write = enqueue_write
        self._on_clients_changed = on_clients_changed
        self._on_state_patch = on_state_patch
        self._log_write = log_write
        self._log_client_traffic = bool(log_client_traffic)
        self._on_tcp_activity = on_tcp_activity or (lambda: None)
        self._sock = None
        self._running = False
        self._clients: Set[socket.socket] = set()
        self._listen_host: str = ""
        self._listen_port: int = 0
        self._accept_thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()

    def set_log_client_traffic(self, enabled: bool) -> None:
        """TCP (XML-RPC oder Textzeilen) ins Rig-Diagnose-Log."""
        self._log_client_traffic = bool(enabled)

    def start(self, host: str, port: int) -> None:
        with self._lifecycle_lock:
            host = str(host or "127.0.0.1").strip() or "127.0.0.1"
            port = int(port)
            if self._running and self._listen_host == host and self._listen_port == port:
                return
            if self._running:
                self._stop_unlocked()
            s = bind_tcp_listen_socket(host, port)
            s.listen(8)
            self._sock = s
            self._listen_host = host
            self._listen_port = port
            self._running = True
            self._accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
            self._accept_thread.start()
            self._log_write("INFO", f"Flrig-Bridge gestartet auf {host}:{port}")

    def stop(self) -> None:
        with self._lifecycle_lock:
            self._stop_unlocked()

    def _stop_unlocked(self) -> None:
        self._running = False
        for c in list(self._clients):
            try:
                c.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()
        ls = self._sock
        self._sock = None
        self._listen_host = ""
        self._listen_port = 0
        if ls is not None:
            try:
                ls.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            try:
                ls.close()
            except Exception:
                pass
        self._on_clients_changed(0)
        t = self._accept_thread
        self._accept_thread = None
        if t is not None and t.is_alive():
            t.join(timeout=4.0)

    def _accept_loop(self) -> None:
        while self._running and self._sock is not None:
            try:
                c, _ = self._sock.accept()
            except OSError:
                break
            except Exception:
                if not self._running:
                    break
                continue
            if not self._running:
                try:
                    c.close()
                except Exception:
                    pass
                break
            self._clients.add(c)
            self._on_clients_changed(len(self._clients))
            threading.Thread(target=self._client_loop, args=(c,), daemon=True).start()

    def _client_loop(self, client: socket.socket) -> None:
        try:
            with client:
                buf = bytearray()
                while self._running:
                    chunk = client.recv(8192)
                    if not chunk:
                        break
                    buf += chunk
                    nl = buf.find(b"\n")
                    if nl < 0:
                        if len(buf) > 8192:
                            break
                        continue
                    first_line = buf[:nl].decode("latin-1", errors="replace").strip()
                    if _first_line_is_http(first_line):
                        self._xmlrpc_loop(client, buf)
                        return
                    self._legacy_line_loop(client, buf)
                    return
        except Exception:
            pass
        finally:
            self._clients.discard(client)
            self._on_clients_changed(len(self._clients))

    def _xmlrpc_loop(self, client: socket.socket, buf: bytearray) -> None:
        try:
            while self._running:
                raw = _read_full_http_request(client, buf)
                if raw is None:
                    break
                try:
                    self._on_tcp_activity()
                except Exception:
                    pass
                if self._log_client_traffic:
                    head = raw[: min(200, len(raw))].decode("latin-1", errors="replace")
                    self._log_write("INFO", f"Flrig XML-RPC Request (Anfang): {head!r}…")
                body = _http_body(raw)
                method = _parse_method_name(body)
                if method is None:
                    resp_xml = _method_fault_unknown("(parse)")
                else:
                    params = _param_scalar_values(body)
                    resp_xml = self._dispatch_xmlrpc(method, params, body)
                # Hamlib read_string(..., "\n", 1): jede „Zeile“ endet mit \n. Ohne \n nach
                # dem XML-Block blockiert der Client auf weiteren Socket-Bytes bis Timeout
                # (~30 s) — dann nur erneut main.get_version, kein flrig_open-Fortschritt.
                resp_bytes = resp_xml.encode("utf-8") + b"\n"
                http = (
                    b"HTTP/1.1 200 OK\r\n"
                    b"Content-Type: text/xml; charset=utf-8\r\n"
                    b"Content-Length: "
                    + str(len(resp_bytes)).encode("ascii")
                    + b"\r\n\r\n"
                    + resp_bytes
                )
                if self._running:
                    client.sendall(http)
                if self._log_client_traffic and method is not None:
                    self._log_write(
                        "INFO",
                        f"Flrig XML-RPC {method} → HTTP 200, Body {len(resp_bytes)} B (inkl. abschließendes LF)",
                    )
        except Exception as exc:
            self._log_write("WARN", f"Flrig XML-RPC Verbindungsfehler: {exc!r}")

    def _patch_state(self, patch: dict[str, Any]) -> None:
        if self._on_state_patch is not None and patch:
            self._on_state_patch(patch)

    def _dispatch_xmlrpc(
        self, method: str, params: list[str | int | float], body: bytes = b""
    ) -> str:
        if not self._running:
            return _method_fault_unknown(method)
        st = self._get_state()
        m = method.strip()

        def sval(inner: str) -> str:
            return _method_response_value(inner)

        hz = int(st.get("frequency_hz", 0) or 0)
        mode = str(st.get("mode", "USB") or "USB")
        vfo = str(st.get("vfo", "A") or "A")
        ptt = bool(st.get("ptt", False))

        # --- Lesen / Meta (Hamlib flrig_open + Laufzeit) ---
        if m == "main.get_version":
            return sval("<string>1.4.2</string>")
        if m == "rig.get_xcvr":
            return sval("<string>RotorTcpBridge</string>")
        if m == "rig.get_pwrmeter_scale":
            return sval("<string>100</string>")
        if m in ("rig.get_modeA", "rig.get_modeB", "rig.get_mode"):
            return sval(f"<string>{_xml_escape(mode)}</string>")
        if m in ("rig.get_vfoA", "rig.get_vfoB", "rig.get_vfo"):
            return sval(f"<double>{float(hz)}</double>")
        if m == "rig.get_AB":
            return sval(f"<string>{_xml_escape(vfo if vfo in ('A', 'B') else 'A')}</string>")
        if m == "rig.get_modes":
            return sval(f"<string>{_FLRIG_MODES_PIPE}</string>")
        if m in ("rig.get_bwA", "rig.get_bwB", "rig.get_bw", "rig.get_bws"):
            return sval("<string>3000</string>")
        if m == "rig.get_split":
            return sval("<i4>0</i4>")
        if m == "rig.get_ptt":
            return sval(f"<i4>{1 if ptt else 0}</i4>")

        # S-Meter / Pegel (WSJT-X / Hamlib können diese nach dem Öffnen abfragen)
        if m in (
            "rig.get_DBM",
            "rig.get_smeter",
            "rig.get_swrmeter",
            "rig.get_SWR",
            "rig.get_Sunits",
            "rig.get_pwrmeter",
        ):
            return sval("<string>0</string>")
        if m in ("rig.get_volume", "rig.get_rfgain", "rig.get_micgain", "rig.get_power"):
            return sval("<i4>0</i4>")
        if m == "rig.get_agc":
            return sval("<i4>0</i4>")

        # --- Schreiben ---
        if m in (
            "rig.set_vfoA",
            "rig.set_vfoB",
            "rig.set_vfo",
            "rig.set_verify_vfoA",
            "rig.set_verify_vfoB",
            "rig.set_vfoA_fast",
            "rig.set_vfoB_fast",
            "main.set_frequency",
            "rig.set_frequency",
        ):
            fhz: float | None = None
            for p in params:
                if isinstance(p, (int, float)):
                    fhz = float(p)
                    break
            if fhz is None and params:
                try:
                    fhz = float(str(params[0]).strip())
                except ValueError:
                    fhz = None
            if fhz is None:
                fb = _body_first_frequency_hz(body)
                if fb is not None:
                    fhz = float(fb)
            if fhz is not None:
                hz_i = int(fhz)
                # Sofort gleiche Anzeigefrequenz für rig.get_vfo* (wie Hamlib NET / F …).
                self._patch_state({"frequency_hz": hz_i})
                self._enqueue_write(f"SETFREQ {hz_i}")
            return _method_response_void()

        if m in ("rig.set_mode", "rig.set_modeA", "rig.set_modeB", "rig.set_verify_mode", "rig.set_verify_modeA", "rig.set_verify_modeB"):
            name = ""
            for p in params:
                if isinstance(p, str) and p.strip():
                    name = p.strip()
                    break
            if not name and params:
                name = str(params[-1]).strip()
            if name:
                self._patch_state({"mode": name})
                self._enqueue_write(f"SETMODE {name}")
            return _method_response_void()

        if m in ("rig.set_ptt", "rig.set_ptt_fast", "rig.set_verify_ptt"):
            v = 0
            for p in params:
                if isinstance(p, int):
                    v = int(p)
                    break
                if isinstance(p, float):
                    v = int(p)
                    break
                if isinstance(p, str) and p.strip().isdigit():
                    v = int(p.strip())
                    break
            self._patch_state({"ptt": bool(v)})
            self._enqueue_write(f"SETPTT {v}")
            return _method_response_void()

        if m in ("rig.set_AB", "rig.set_verify_AB"):
            ab = ""
            for p in params:
                if isinstance(p, str) and p.strip().upper() in ("A", "B"):
                    ab = p.strip().upper()
                    break
            if not ab and params:
                s = str(params[-1]).strip().upper()
                if s in ("A", "B"):
                    ab = s
            if ab:
                self._patch_state({"vfo": ab})
            return _method_response_void()

        if m in ("rig.set_split", "rig.set_verify_split"):
            return _method_response_void()

        if m.startswith("rig.set_bw") or m == "rig.set_bandwidth":
            return _method_response_void()

        if m in ("rig.shutdown", "rig.tune"):
            return _method_response_void()

        if m == "rig.cat_string":
            return sval("<string></string>")

        return _method_fault_unknown(m)

    def _legacy_line_loop(self, client: socket.socket, buf: bytearray) -> None:
        try:
            while True:
                if not self._running:
                    break
                while b"\n" in buf:
                    if not self._running:
                        buf.clear()
                        break
                    line, rest = buf.split(b"\n", 1)
                    buf[:] = rest
                    text = line.decode("ascii", errors="ignore").strip()
                    if not text:
                        continue
                    out = self._handle_cmd(text)
                    if self._running:
                        try:
                            client.sendall((out + "\n").encode("ascii", errors="ignore"))
                        except Exception:
                            return
                chunk = client.recv(8192)
                if not chunk:
                    break
                buf += chunk
        except Exception:
            pass

    def _handle_cmd(self, cmd: str) -> str:
        if not self._running:
            return "ERR"
        try:
            self._on_tcp_activity()
        except Exception:
            pass
        if self._log_client_traffic:
            self._log_write("INFO", f"Flrig TCP RX: {cmd!r}")
        st = self._get_state()
        up = cmd.upper()
        if up == "GET FREQ":
            out = str(int(st.get("frequency_hz", 0)))
        elif up.startswith("SET FREQ "):
            try:
                hz_i = int(float(cmd[9:].strip()))
                self._patch_state({"frequency_hz": hz_i})
            except ValueError:
                pass
            self._enqueue_write(f"SETFREQ {cmd[9:].strip()}")
            out = "OK"
        elif up == "GET MODE":
            out = str(st.get("mode", "USB"))
        elif up.startswith("SET MODE "):
            name = cmd[9:].strip()
            if name:
                self._patch_state({"mode": name})
                self._enqueue_write(f"SETMODE {name}")
            out = "OK"
        elif up == "GET PTT":
            out = "1" if st.get("ptt", False) else "0"
        elif up.startswith("SET PTT "):
            try:
                v = int(cmd[8:].strip())
            except ValueError:
                v = 0
            self._patch_state({"ptt": bool(v)})
            self._enqueue_write(f"SETPTT {v}")
            out = "OK"
        elif up == "GET VFO":
            out = str(st.get("vfo", "A"))
        else:
            out = "ERR"
        if self._log_client_traffic:
            self._log_write("INFO", f"Flrig TCP TX: {out!r}")
        return out
