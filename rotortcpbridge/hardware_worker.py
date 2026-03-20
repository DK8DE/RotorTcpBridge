import threading
import queue
import time
import socket

try:
    import serial
except Exception:
    serial = None


class HardwareWorker:
    def __init__(self, cfg):
        self.cfg = cfg
        self.q = queue.Queue()
        self.running = False
        self.sock = None
        self.ser = None

    def start(self):
        self.running = True
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        self.running = False

    def send(self, line: str):
        self.q.put(line)

    def _connect(self):
        if self.cfg["mode"] == "tcp":
            try:
                s = socket.create_connection((self.cfg["tcp_ip"], self.cfg["tcp_port"]), timeout=1)
                s.settimeout(0.2)
                self.sock = s
            except Exception:
                self.sock = None
        else:
            if serial is None:
                return
            try:
                self.ser = serial.Serial(self.cfg["com_port"], self.cfg["baudrate"], timeout=0.2)
            except Exception:
                self.ser = None

    def _loop(self):
        while self.running:
            if not self.sock and not self.ser:
                self._connect()
            try:
                line = self.q.get(timeout=0.2)
            except queue.Empty:
                continue
            try:
                data = (line + "\n").encode("ascii")
                if self.sock:
                    self.sock.sendall(data)
                elif self.ser:
                    self.ser.write(data)
            except Exception:
                pass
            time.sleep(0.01)
