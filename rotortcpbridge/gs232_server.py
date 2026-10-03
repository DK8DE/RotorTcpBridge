"""Yaesu GS-232B TCP-Server (Zeilenprotokoll)."""

from __future__ import annotations

from typing import Any, Optional

from .gs232_protocol import extract_gs232_commands, process_gs232_line
from .line_protocol_server import LineProtocolTcpServer
from .logutil import LogBuffer

DEFAULT_GS232_PORT = 4003


class Gs232Server(LineProtocolTcpServer):
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
            return process_gs232_line(
                line,
                self.ctrl,
                shortest_path=self._az_shortest_path(),
                report_mod360=self._az_report_mod360(),
            )

        super().__init__(
            host,
            port,
            log,
            name="GS-232B",
            process_line=_process,
            cfg_section="gs232_server",
            extract_commands=extract_gs232_commands,
        )

    def _section(self) -> dict:
        try:
            return (self.cfg or {}).get("gs232_server", {}) or {}
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
