"""Asynchrone Telegramm-Verarbeitung (Antworten ohne pending Request)."""

from __future__ import annotations

import time

from .rs485_protocol import BROADCAST_DST, Telegram
from .rotor_model import AxisState
from .rotor_parse_utils import parse_float, parse_float_any, parse_int


class RotorControllerAsyncMixin:
    """Dispatch für eingehende ACK/NAK ohne zugeordneten HwRequest."""

    def _tel_dst_allowed(self, tel: Telegram) -> bool:
        """Eigene Antworten (dst = unsere Master-ID) oder Antworten der konfigurierten Slaves an einen fremden Master."""
        try:
            d = int(tel.dst)
            s = int(tel.src)
            mid = int(self.master_id)
            if d == mid:
                return True
            saz = int(self.slave_az)
            sel = int(self.slave_el)
            if (s == saz or s == sel) and d != mid:
                return True
            # SETPOSDG an unseren Rotor (Mitschnitt; auch bei gleicher Master-ID wie wir,
            # z. B. zweiter Rechner / Echo auf dem Bus – Ziel muss trotzdem ins UI)
            cmd_u = str(tel.cmd or "").strip().upper()
            if cmd_u == "SETPOSDG" and (d == saz or d == sel):
                return True
            # Broadcast: gewählte Antenne (alle Teilnehmer)
            if cmd_u == "SETASELECT" and d == int(BROADCAST_DST):
                return True
            return False
        except Exception:
            return True

    # -------------------- Async telegram handler --------------------
    def _on_async_tel(self, tel: Telegram):
        # Asynchrone ACK/NAK aus Polling (wenn Requests ohne pending gesendet werden).
        if not self._tel_dst_allowed(tel):
            return
        cmd_u = str(tel.cmd or "").strip().upper()
        # Broadcast SETASELECT → UI (Kompass/Karte)
        if cmd_u == "SETASELECT" and int(tel.dst) == int(BROADCAST_DST):
            # Checksumme nicht zwingend: Fremdgerät / Sniffer kann leicht abweichen
            try:
                n = parse_int(str(tel.params).strip().split(";")[0])
                if n is not None and 1 <= n <= 3:
                    fn = getattr(self, "on_setaselect_from_bus", None)
                    if callable(fn):
                        fn(int(n))
            except Exception:
                pass
            return
        # SETPOSDG an unsere Slave-ID: Zielwinkel steht in params (nicht in ACK).
        # Kein Filter src!=master_id: Auf dem Bus kann dieselbe Master-ID wie unsere
        # konfigurierte vorkommen (anderes Gerät); sonst würde der Soll nie gesetzt.
        if cmd_u == "SETPOSDG":
            try:
                dst = int(tel.dst)
                saz = int(self.slave_az)
                sel = int(self.slave_el)
                if dst == saz or dst == sel:
                    self._apply_local_state_for_ui_command(dst, "SETPOSDG", tel.params)
                    ax = self.az if dst == saz else self.el
                    ax.online = True
                    ax.last_rx_ts = time.time()
            except Exception:
                pass
            return
        try:
            axis_state: AxisState | None = None
            axis_name = None
            if tel.src == self.slave_az:
                axis_state = self.az
                axis_name = "AZ"
            elif tel.src == self.slave_el:
                axis_state = self.el
                axis_name = "EL"

            if axis_state is not None:
                # Jede gültige Antwort vom Slave zählt als "online"
                axis_state.online = True
                axis_state.last_rx_ts = time.time()

                # Position (GETPOSDG ohne pending)
                if tel.cmd.startswith("ACK_GETPOSDG") or tel.cmd.startswith("ACK_POSDG"):
                    # Inflight freigeben (sonst stauen sich Requests)
                    try:
                        axis_state.pos_poll_inflight = False
                    except Exception:
                        pass
                    axis_state.online = True
                    axis_state.last_rx_ts = time.time()
                    v = parse_float(tel.params.split(";")[-1])
                    if v is not None:
                        d10 = int(round(v * 10))
                        prev_pos = int(axis_state.pos_d10)
                        prev_moving = bool(axis_state.moving)
                        # Beim allerersten Sample nach Programmstart ist prev_pos nur Default (meist 0)
                        # und darf nicht als echte Bewegung interpretiert werden.
                        try:
                            had_prev_sample = (
                                float(getattr(axis_state, "_last_sample_ts", 0.0) or 0.0) > 0.0
                            )
                        except Exception:
                            had_prev_sample = False

                        try:
                            exp = float(
                                getattr(axis_state, "pos_poll_expected_period_s", 0.2) or 0.2
                            )
                        except Exception:
                            exp = 0.2
                        axis_state.update_position_sample(
                            d10, sample_ts=time.time(), expected_period_s=exp
                        )

                        # Während aktiver Referenzfahrt darf "moving" NICHT auf False fallen,
                        # auch wenn temporär keine sauberen Positions-Samples ankommen.
                        # Sonst blinkt die Anzeige "Fährt" zwischen ja/nein.
                        try:
                            if bool(getattr(axis_state, "ref_poll_active", False)):
                                axis_state.moving = True
                                axis_state.stop_confirm_samples = 0
                                return
                        except Exception:
                            pass

                        # Bewegung/Stillstand robust bestimmen:
                        # - Nicht sofort "moving=False" sobald wir nahe am Ziel sind, weil der Rotor
                        #   beim Überschleifen noch weiterläuft. Das führte zu langsamem Polling und
                        #   zu einem kurzen "Stocken" im grünen Zeiger.
                        # - Stattdessen: "steht" erst nach mehreren stabilen Samples.
                        #
                        # WICHTIG (Bugfix):
                        # - Beim Programmstart oder nach einem externen SET (z.B. PST) kann `moving=True`
                        #   gesetzt sein, obwohl der Rotor faktisch steht (Ziel == Position).
                        # - Außerdem kann die Zielabweichung >0,2° sein, obwohl der Motor steht.
                        #   Daher basiert "steht" primär auf *Positionsstabilität*.
                        dpos = abs(int(d10) - int(prev_pos)) if had_prev_sample else 0
                        stable = (not had_prev_sample) or (dpos <= 1)

                        if stable:
                            try:
                                axis_state.stop_confirm_samples = (
                                    int(getattr(axis_state, "stop_confirm_samples", 0)) + 1
                                )
                            except Exception:
                                axis_state.stop_confirm_samples = 1
                        else:
                            axis_state.stop_confirm_samples = 0

                        if axis_state.stop_confirm_samples >= 4:
                            axis_state.moving = False
                        else:
                            # Wenn wir eine nennenswerte Positionsänderung sehen, sind wir sicher in Bewegung.
                            if dpos > 1:
                                axis_state.moving = True
                            else:
                                axis_state.moving = prev_moving
                    return
                # SETPOSDG-Bestätigung (z. B. anderer Master mit separater Controller-ID)
                if tel.cmd.startswith("ACK_SETPOSDG"):
                    try:
                        # ACK-Parameter ist typischerweise "1" (übernommen), nicht der Zielwinkel.
                        # Ziel setzen wir über mitgeschnittenes SETPOSDG; hier nur Bewegung spiegeln.
                        p0 = str(tel.params).strip().split(";")[0]
                        try:
                            ok = int(float(p0.replace(",", "."))) != 0
                        except Exception:
                            ok = True
                        if ok:
                            axis_state.moving = True
                    except Exception:
                        pass
                    axis_state.last_rx_ts = time.time()
                    return
                if tel.cmd.startswith("NAK_GETPOSDG") or tel.cmd.startswith("NAK_POSDG"):
                    try:
                        axis_state.pos_poll_inflight = False
                    except Exception:
                        pass
                    axis_state.online = True
                    axis_state.last_rx_ts = time.time()
                    self.log.write("WARN", f"{axis_name} GETPOSDG NAK: {tel.params}")
                    return

                # Referenz-Status (GETREF Polling ohne pending)
                if tel.cmd.startswith("ACK_GETREF") or tel.cmd.startswith("ACK_REF"):
                    axis_state.last_rx_ts = time.time()
                    v = parse_int(tel.params.strip())
                    if v == 1:
                        axis_state.referenced = True
                        axis_state.ref_poll_active = False
                        # Homing beendet (oder war bereits fertig)
                        axis_state.moving = False
                        self.log.write("INFO", f"{axis_name} referenziert")
                        # Falls Ziel bereits gesetzt ist, erneut anstoßen
                        if abs(axis_state.target_d10) > 0:
                            if axis_name == "AZ":
                                self.set_az_from_spid(axis_state.target_d10)
                            else:
                                self.set_el_from_spid(axis_state.target_d10)
                    else:
                        # Noch nicht referenziert -> Homing läuft weiter
                        axis_state.referenced = False
                        # Nur während aktiver Referenzfahrt "Fährt" setzen
                        if axis_state.ref_poll_active:
                            axis_state.moving = True
                    return
                if tel.cmd.startswith("NAK_GETREF") or tel.cmd.startswith("NAK_REF"):
                    axis_state.last_rx_ts = time.time()
                    self.log.write("WARN", f"{axis_name} GETREF NAK: {tel.params}")
                    return

                # Warnungen
                if tel.cmd.startswith("ACK_GETWARN") or tel.cmd.startswith("ACK_WARN"):
                    p = tel.params.strip()
                    axis_state.warnings.clear()
                    if p and p != "0":
                        for part in p.split(";"):
                            n = parse_int(part)
                            if n is not None:
                                axis_state.warnings.add(n)
                    return
                if tel.cmd.startswith("NAK_GETWARN") or tel.cmd.startswith("NAK_WARN"):
                    self.log.write("WARN", f"{axis_name} GETWARN NAK: {tel.params}")
                    return

                # Fehlercode
                if tel.cmd.startswith("ACK_GETERR") or tel.cmd.startswith("ACK_ERR"):
                    code = parse_int(tel.params.strip())
                    if code is not None:
                        axis_state.error_code = int(code)
                        if axis_state.error_code != 0:
                            axis_state.moving = False
                    return
                if tel.cmd.startswith("NAK_GETERR") or tel.cmd.startswith("NAK_ERR"):
                    self.log.write("WARN", f"{axis_name} GETERR NAK: {tel.params}")
                    return

                # Telemetrie
                if tel.cmd.startswith("ACK_GETTEMPA"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.temp_ambient_c = v
                    return
                if tel.cmd.startswith("ACK_GETTEMPM"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.temp_motor_c = v
                    return

                # Antennen-Versätze (GETANTOFF1–3)
                if tel.cmd.startswith("ACK_GETANTOFF1"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff1 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANTOFF2"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff2 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANTOFF3"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.antoff3 = max(0.0, min(360.0, v))
                    return
                # Antennen-Öffnungswinkel (GETANGLE1–3)
                if tel.cmd.startswith("ACK_GETANGLE1"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.angle1 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANGLE2"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.angle2 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANGLE3"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.angle3 = max(0.0, min(360.0, v))
                    return
                if tel.cmd.startswith("ACK_GETANEMO") or tel.cmd.startswith("ACK_ANEMO"):
                    v = parse_float_any(tel.params)
                    self._wind_speed_inflight = False
                    if v is not None:
                        axis_state.telemetry.wind_kmh = v
                    return
                if tel.cmd.startswith("ACK_WINDDIR") or tel.cmd.startswith("ACK_GETWINDDIR"):
                    v = parse_float_any(tel.params)
                    self._wind_dir_inflight = False
                    if v is not None:
                        axis_state.telemetry.wind_dir_deg = v
                    return
                if tel.cmd.startswith("ACK_GETBEAUFORT") or tel.cmd.startswith("ACK_BEAUFORT"):
                    parts = (tel.params or "").strip().split(";")[0].split(":")
                    v = parse_int(parts[0].strip()) if parts else None
                    self._wind_beaufort_inflight = False
                    if v is not None and 0 <= v <= 12:
                        axis_state.telemetry.wind_beaufort = int(v)
                    return
                if (
                    tel.cmd.startswith("ACK_GETWINDENABLE")
                    or tel.cmd.startswith("ACK_WINDENABLE")
                    or tel.cmd.startswith("ACK_SETWINDENABLE")
                ):
                    self._wind_enable_inflight = False
                    v = parse_int(tel.params.strip())
                    if v is not None:
                        self.wind_enabled = bool(int(v) != 0)
                        self.wind_enabled_known = True
                        if not self.wind_enabled:
                            self._wind_speed_inflight = False
                            self._wind_dir_inflight = False
                            self._wind_beaufort_inflight = False
                            try:
                                self.az.telemetry.wind_kmh = None
                                self.az.telemetry.wind_dir_deg = None
                                self.az.telemetry.wind_beaufort = None
                                self.el.telemetry.wind_kmh = None
                                self.el.telemetry.wind_dir_deg = None
                                self.el.telemetry.wind_beaufort = None
                            except Exception:
                                pass
                    return
                if tel.cmd.startswith("ACK_GETPWM"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.pwm_max_pct = v
                    return
                if tel.cmd.startswith("ACK_SETPWM"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.pwm_max_pct = v
                    axis_state.last_rx_ts = time.time()
                    return
                if tel.cmd.startswith("ACK_GETMINPWM") or tel.cmd.startswith("ACK_MINPWM"):
                    v = parse_float(tel.params.strip())
                    if v is not None:
                        axis_state.telemetry.pwm_min_pct = v
                    return

                # Kalibrier-Status (GETCALSTATE)
                if tel.cmd.startswith("ACK_GETCALSTATE") or tel.cmd.startswith("ACK_CALSTATE"):
                    parts = (tel.params or "").strip().split(";")
                    state = parse_int(parts[0]) if parts else None
                    if state is not None:
                        axis_state.cal_state = int(state)
                        if state != 2 and axis_name == "AZ":
                            axis_state.cal_bins_cw = None
                            axis_state.cal_bins_ccw = None
                            self._cal_bins_fetched_az = False
                        elif state != 2 and axis_name == "EL":
                            axis_state.cal_bins_cw = None
                            axis_state.cal_bins_ccw = None
                            self._cal_bins_fetched_el = False
                        elif state == 2 and axis_name == "AZ" and (
                            self._statistics_window_open or self._settings_window_open
                        ):
                            if not self._cal_bins_inflight_az and (
                                axis_state.cal_bins_cw is None
                                or axis_state.cal_bins_ccw is None
                                or not self._cal_bins_fetched_az
                            ):
                                self._fetch_cal_bins(int(self.slave_az), axis_state, "AZ")
                            elif not self._live_bins_inflight_az and self._cal_bins_fetched_az:
                                self._fetch_live_bins(int(self.slave_az), axis_state, "AZ")
                                self._last_live_bins_az = time.time()
                        elif state == 2 and axis_name == "EL" and (
                            self._statistics_window_open or self._settings_window_open
                        ):
                            dst_el = int(self.slave_el)
                            if not self._cal_bins_inflight_el and (
                                axis_state.cal_bins_cw is None
                                or axis_state.cal_bins_ccw is None
                                or not self._cal_bins_fetched_el
                            ):
                                self._fetch_cal_bins_el(dst_el, axis_state, "EL")
                            elif not self._live_bins_inflight_el and self._cal_bins_fetched_el:
                                self._fetch_live_bins_el(dst_el, axis_state, "EL")
                                self._last_live_bins_el = time.time()
                    return

                # Kalibrier-Bins + Live-Bins (case-insensitiv für Firmware-Varianten)
                cmd_u = (tel.cmd or "").upper()
                if cmd_u.startswith("ACK_GETCALBINS") or cmd_u.startswith("ACK_CALBINS"):
                    parts = (tel.params or "").strip().split(";")
                    if len(parts) >= 4:
                        dir_val = parse_int(parts[0])
                        start_val = parse_int(parts[1])
                        count_val = parse_int(parts[2])
                        if dir_val is not None and start_val is not None and count_val is not None:
                            bins = (
                                axis_state.cal_bins_cw if dir_val == 1 else axis_state.cal_bins_ccw
                            )
                            if bins is not None and 0 <= start_val < 72 and 1 <= count_val <= 12:
                                for i in range(count_val):
                                    v = parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                    if v is not None:
                                        idx = start_val + i
                                        if idx < 72:
                                            bins[idx] = int(v)
                            # Fertig wenn alle 12 Blöcke angekommen
                            if axis_name == "AZ":
                                self._cal_bins_received_az = (
                                    int(getattr(self, "_cal_bins_received_az", 0)) + 1
                                )
                                if self._cal_bins_received_az >= 12:
                                    self._cal_bins_inflight_az = False
                                    self._cal_bins_fetched_az = True
                    return

                # Live-Bins (ACK_GETLIVEBINS: dir;start;count;v0;v1;...;vn)
                if cmd_u.startswith("ACK_GETLIVEBINS") or cmd_u.startswith("ACK_LIVEBINS"):
                    parts = (tel.params or "").strip().split(";")
                    if len(parts) >= 4:
                        dir_val = parse_int(parts[0])
                        start_val = parse_int(parts[1])
                        count_val = parse_int(parts[2])
                        if dir_val is not None and start_val is not None and count_val is not None:
                            bins = (
                                axis_state.live_bins_cw
                                if dir_val == 1
                                else axis_state.live_bins_ccw
                            )
                            if bins is not None and 0 <= start_val < 72 and 1 <= count_val <= 12:
                                for i in range(count_val):
                                    v = parse_int(parts[3 + i]) if (3 + i) < len(parts) else None
                                    if v is not None:
                                        idx = start_val + i
                                        if idx < 72:
                                            bins[idx] = int(v)
                            if axis_name == "AZ":
                                self._live_bins_received_az = (
                                    int(getattr(self, "_live_bins_received_az", 0)) + 1
                                )
                                if self._live_bins_received_az >= 12:
                                    self._live_bins_inflight_az = False
                    return
        except Exception:
            pass

        if tel.cmd == "ERR":
            code = parse_int(tel.params.strip())
            if code is None:
                return
            if tel.src == self.slave_az:
                self.az.error_code = int(code)
                self.az.moving = False
            elif tel.src == self.slave_el:
                self.el.error_code = int(code)
                self.el.moving = False
            self.log.write("ERROR", f"ERR vom Slave {tel.src}: {code}")
