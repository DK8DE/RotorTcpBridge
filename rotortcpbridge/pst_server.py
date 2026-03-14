from __future__ import annotations
import socket, threading, time
from dataclasses import dataclass

from .spid_rot2prog import parse_command_packet, encode_reply, CMD_SET, CMD_STOP, CMD_STATUS
from .logutil import LogBuffer

@dataclass
class _ServerCfg:
    host:str
    port:int
    axis:str  # "az" oder "el"

class PstAxisServer:
    """Ein TCP-Server-Listener für genau eine Achse (AZ oder EL).

    Wichtig:
    - PstRotator erzwingt oft zwei Ports (4001=AZ, 4002=EL).
    - Stop/Restart muss schnell wirken. Deshalb schließen wir den Listen-Socket beim Stop,
      damit ein blockierendes accept() sofort beendet wird.
    """

    def __init__(self, host:str, port:int, axis:str, controller, log:LogBuffer):
        self.host = host
        self.port = port
        self.axis = axis
        self.ctrl = controller
        self.log = log
        self.running = False
        self._thread: threading.Thread|None = None
        self._listen_sock: socket.socket|None = None
        # Timestamp des letzten gültigen RX-Pakets (für UI "PST Connect" LED)
        self.last_rx_ts: float = 0.0

    def start(self):
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self.log.write("INFO", f"PST-{self.axis.upper()} Server gestartet auf {self.host}:{self.port}")

    def stop(self):
        # Stop flag setzen + Listen-Socket schließen, damit accept() abbricht
        self.running = False
        try:
            if self._listen_sock:
                self._listen_sock.close()
        except Exception:
            pass
        self._listen_sock = None

    def _apply_set(self, cmd):
        # Je nach Port nur AZ oder nur EL setzen
        if self.axis == "az":
            if cmd.az_d10 is not None:
                self.ctrl.set_az_from_spid(cmd.az_d10)
        else:
            if cmd.el_d10 is not None:
                self.ctrl.set_el_from_spid(cmd.el_d10)

    def _apply_stop(self):
        if self.axis == "az":
            self.ctrl.stop_az()
        else:
            self.ctrl.stop_el()

    def _loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listen_sock = s
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.settimeout(0.5)  # damit stop() schnell wirkt
        try:
            s.bind((self.host, self.port))
            s.listen(1)
        except Exception as e:
            self.log.write("ERROR", f"PST-{self.axis.upper()} bind/listen fehlgeschlagen: {e}")
            self.running = False
            try:
                s.close()
            except Exception:
                pass
            self._listen_sock = None
            return

        while self.running:
            try:
                c, addr = s.accept()
            except socket.timeout:
                continue
            except Exception:
                # z.B. Socket geschlossen während stop()
                break

            self.log.write("INFO", f"PST-{self.axis.upper()} verbunden: {addr}")
            with c:
                c.settimeout(0.5)
                buf = b""
                while self.running:
                    try:
                        chunk = c.recv(4096)
                        if not chunk:
                            break
                        buf += chunk

                        # ROT2PROG Commands sind 13 Bytes - wir schneiden sauber
                        while len(buf) >= 13:
                            pkt = buf[:13]
                            buf = buf[13:]
                            cmd = parse_command_packet(pkt)
                            if cmd is None:
                                # Unbekannte Pakete loggen, damit sichtbar ist, ob überhaupt Daten kommen
                                self.log.write("PST", f"{self.axis.upper()} RX <unbekannt> len=13 raw={pkt.hex()}")
                                continue
                            # gültiges Packet -> Aktivität merken
                            try:
                                self.last_rx_ts = time.time()
                            except Exception:
                                pass

                            # Log: PstRotator Anfrage
                            self.log.write("PST", f"{self.axis.upper()} RX cmd={cmd.cmd} az_d10={cmd.az_d10} el_d10={cmd.el_d10} raw={pkt.hex()}")

                            if cmd.cmd == CMD_SET:
                                self._apply_set(cmd)
                            elif cmd.cmd == CMD_STOP:
                                self._apply_stop()
                            elif cmd.cmd == CMD_STATUS:
                                pass

                            # Antwort: immer gesamte Position (AZ+EL), PstRotator nimmt je Port was er braucht.
                            reply = encode_reply(self.ctrl.az.pos_d10, self.ctrl.el.pos_d10, ph=10, pv=10)
                            self.log.write("PST", f"{self.axis.upper()} TX reply_len={len(reply)} az={self.ctrl.az.pos_d10} el={self.ctrl.el.pos_d10} hex={reply.hex()}")
                            c.sendall(reply)

                    except socket.timeout:
                        continue
                    except Exception:
                        break

            self.log.write("INFO", f"PST-{self.axis.upper()} getrennt")

        try:
            s.close()
        except Exception:
            pass
        self._listen_sock = None
        self.running = False

class PstDualServer:
    """Kapselt zwei Listener: AZ (port_az) und EL (port_el)."""
    def __init__(self, host:str, port_az:int, port_el:int, controller, log:LogBuffer):
        self.host = host
        self.port_az = port_az
        self.port_el = port_el
        self.log = log
        self._ctrl = controller
        self.az = PstAxisServer(host, port_az, "az", controller, log)
        self.el = PstAxisServer(host, port_el, "el", controller, log)

    @property
    def running(self)->bool:
        return self.az.running or self.el.running

    @property
    def last_rx_ts(self) -> float:
        """Letzte PST-Aktivität (max aus AZ/EL)."""
        try:
            a = float(getattr(self.az, "last_rx_ts", 0.0) or 0.0)
        except Exception:
            a = 0.0
        try:
            e = float(getattr(self.el, "last_rx_ts", 0.0) or 0.0)
        except Exception:
            e = 0.0
        return a if a >= e else e

    def start(self):
        # host/ports evtl. aktualisieren
        self.az.host = self.host; self.az.port = self.port_az
        self.el.host = self.host; self.el.port = self.port_el
        self.az.start()
        self.el.start()

    def stop(self):
        self.az.stop()
        self.el.stop()

    def restart(self, host:str, port_az:int, port_el:int):
        # Stop -> kurze Pause -> neu konfigurieren -> Start
        self.stop()
        time.sleep(0.2)
        self.host = host
        self.port_az = port_az
        self.port_el = port_el
        # Neue Axis-Objekte erstellen (sauberer Neustart)
        self.az = PstAxisServer(host, port_az, "az", self._ctrl, self.log)
        self.el = PstAxisServer(host, port_el, "el", self._ctrl, self.log)
        self.start()
