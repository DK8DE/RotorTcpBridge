"""Hy-Gain DCU-1 TCP-Server (Semikolon-Frames)."""

from __future__ import annotations

from typing import Optional

from .dcu1_protocol import process_dcu1_line
from .line_protocol_server import LineProtocolTcpServer
from .logutil import LogBuffer

DEFAULT_DCU1_PORT = 4004


class Dcu1Server(LineProtocolTcpServer):
    def __init__(
        self,
        host: str,
        port: int,
        controller,
        log: LogBuffer,
        cfg: Optional[dict] = None,
    ) -> None:
        self.ctrl = controller
        self.cfg = cfg
        self._pending_target: list = [None]

        def _process(line: str) -> tuple[Optional[str], bool]:
            return process_dcu1_line(
                line,
                self.ctrl,
                shortest_path=self._az_shortest_path(),
                report_mod360=self._az_report_mod360(),
                pending_target=self._pending_target,
            )

        super().__init__(
            host,
            port,
            log,
            name="DCU-1",
            process_line=_process,
            cfg_section="dcu1_server",
            terminators=b";\r\n",
        )

    def _section(self) -> dict:
        try:
            return (self.cfg or {}).get("dcu1_server", {}) or {}
        except Exception:
            return {}

    def _az_shortest_path(self) -> bool:
        try:
            return bool(self._section().get("az_shortest_path", False))
        except Exception:
            return False

    def _az_report_mod360(self) -> bool:
        try:
            return bool(self._section().get("az_report_mod360", False))
        except Exception:
            return False
