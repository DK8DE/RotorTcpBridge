"""EasyComm II TCP-Server."""

from __future__ import annotations

from typing import Optional

from .easycomm_protocol import process_easycomm_line
from .line_protocol_server import LineProtocolTcpServer
from .logutil import LogBuffer

DEFAULT_EASYCOMM_PORT = 4535


class EasycommServer(LineProtocolTcpServer):
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

        def _process(line: str) -> tuple[Optional[str], bool]:
            return process_easycomm_line(
                line,
                self.ctrl,
                shortest_path=self._az_shortest_path(),
                report_mod360=self._az_report_mod360(),
            )

        super().__init__(
            host,
            port,
            log,
            name="EasyComm II",
            process_line=_process,
            cfg_section="easycomm_server",
        )

    def _section(self) -> dict:
        try:
            return (self.cfg or {}).get("easycomm_server", {}) or {}
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
