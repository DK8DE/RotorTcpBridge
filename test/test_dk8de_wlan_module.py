"""Unit-Tests fuer DK8DE WLAN/RS485 Protokoll (ohne Hardware)."""

from __future__ import annotations

import struct
import unittest

from rotortcpbridge.dk8de_wlan_module import (
    config_frame_encode,
    config_frame_try_parse,
    dk8de_crc16,
    dk8de_netmode_to_sock,
    dk8de_sock_to_netmode,
    normalize_uid,
    parse_dk8de_kv_text,
    parse_dk8de_stats_payload,
    uid_is_valid,
    _apply_discover_contact_ip,
    _at_session_ready,
    _at_udp_to_text,
    _device_from_info,
    _parse_discover_payloads,
    _pick_device_ip,
)


class TestDk8deCrc(unittest.TestCase):
    def test_crc_empty(self):
        self.assertEqual(dk8de_crc16(b""), 0xFFFF)

    def test_crc_known_vector(self):
        # "123456789" standard CCITT-FALSE test vector
        self.assertEqual(dk8de_crc16(b"123456789"), 0x29B1)


class TestConfigFrame(unittest.TestCase):
    def test_roundtrip_discover(self):
        raw = config_frame_encode(0x01, seq=42, payload=b"")
        consumed, frame = config_frame_try_parse(raw)
        self.assertEqual(consumed, len(raw))
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame["type"], 0x01)
        self.assertEqual(frame["seq"], 42)
        self.assertEqual(frame["payload"], b"")

    def test_discover_response_payload(self):
        text = b"UID=ABCDEF01\nIP=192.168.1.10\n"
        raw = config_frame_encode(0x02, seq=1, payload=text)
        devices = _parse_discover_payloads(raw)
        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].uid_norm, "ABCDEF01")
        self.assertEqual(devices[0].ip, "192.168.1.10")

    def test_discover_contact_ip_prefers_udp_reply(self):
        text = b"UID=ABCDEF01\nIP=192.168.99.1\n"
        raw = config_frame_encode(0x02, seq=1, payload=text)
        devices = _parse_discover_payloads(raw)
        self.assertEqual(len(devices), 1)
        _apply_discover_contact_ip(devices[0], "192.168.0.148")
        self.assertEqual(devices[0].ip, "192.168.0.148")
        self.assertEqual(devices[0].info.get("REPORTED_IP"), "192.168.99.1")

    def test_pick_device_ip(self):
        self.assertEqual(_pick_device_ip("192.168.99.1", "192.168.0.148"), "192.168.0.148")
        self.assertEqual(_pick_device_ip("0.0.0.0", "192.168.0.148"), "192.168.0.148")
        self.assertEqual(_pick_device_ip("192.168.0.50", ""), "192.168.0.50")


class TestKvParse(unittest.TestCase):
    def test_info_lines(self):
        info = parse_dk8de_kv_text("UID=C5E632AC\nNAME=Test\nFW=1.4.0\n")
        self.assertEqual(info["UID"], "C5E632AC")
        self.assertEqual(info["NAME"], "Test")
        self.assertEqual(info["FW"], "1.4.0")

    def test_device_from_info(self):
        dev = _device_from_info({"UID": "AABBCCDD", "LPORT": "9999", "IP": "10.0.0.5"})
        self.assertEqual(dev.uid_norm, "AABBCCDD")
        self.assertEqual(dev.lport, 9999)
        self.assertEqual(dev.ip, "10.0.0.5")


class TestNetmodeMap(unittest.TestCase):
    def test_numeric(self):
        self.assertEqual(dk8de_netmode_to_sock(0), "TCPS")
        self.assertEqual(dk8de_netmode_to_sock(4), "UDPC")

    def test_string(self):
        self.assertEqual(dk8de_netmode_to_sock("TCP_CLIENT"), "TCPC")
        self.assertEqual(dk8de_sock_to_netmode("UDPS"), "UDP_SERVER")


class TestUid(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(normalize_uid("rotor-aabbccdd"), "AABBCCDD")

    def test_valid(self):
        self.assertTrue(uid_is_valid("C5E632AC"))
        self.assertFalse(uid_is_valid("SHORT"))


class TestBuildWanCmds(unittest.TestCase):
    def test_factory_sta_join(self):
        from rotortcpbridge.dk8de_wlan_module import build_wan_cmds_dk8de

        cmds = build_wan_cmds_dk8de(
            ip="192.168.0.148",
            mask="255.255.255.0",
            gateway="192.168.0.1",
            dns="192.168.0.1",
            ssid="FritzBox",
            password="secret",
            wifi_band="5G",
            wifi_mode="STA",
            reboot=True,
        )
        self.assertEqual(cmds[0], 'SSID="FritzBox"')
        self.assertEqual(cmds[1], 'PASS="secret"')
        self.assertIn("WIFIBAND=5G", cmds)
        self.assertIn("DHCP=0", cmds)
        self.assertIn("IP=192.168.0.148", cmds)
        save_i = cmds.index("SAVE")
        sta_i = cmds.index("WIFIMODE=STA")
        self.assertLess(save_i, sta_i)
        self.assertEqual(cmds[-1], "REBOOT")

    def test_dhcp_skips_static(self):
        from rotortcpbridge.dk8de_wlan_module import build_wan_cmds_dk8de

        cmds = build_wan_cmds_dk8de(dhcp=True, ssid="x", wifi_band="2G", reboot=False)
        self.assertIn("DHCP=1", cmds)
        self.assertFalse(any(c.startswith("IP=") for c in cmds))
        self.assertNotIn("REBOOT", cmds)
        self.assertNotIn("WIFIMODE=STA", cmds)


class TestAtUdpFilter(unittest.TestCase):
    def test_ignores_discover_binary_frame(self):
        text = b"UID=C5E632AC\nIP=192.168.0.148\n"
        raw = config_frame_encode(0x02, seq=1, payload=text)
        self.assertEqual(_at_udp_to_text(raw), "")

    def test_keeps_info_kv_text(self):
        # AT+INFO? kann als eigenes Datagramm ohne @OK ankommen — nicht verwerfen.
        pkt = b"UID=C5E632AC\nNAME=Rotor\nIP=192.168.0.148\n"
        self.assertIn("UID=C5E632AC", _at_udp_to_text(pkt))
        self.assertIn("NAME=Rotor", _at_udp_to_text(pkt))

    def test_accepts_config_ready(self):
        pkt = b"@C5E632AC:CONFIG,READY\r\n"
        self.assertIn("CONFIG,READY", _at_udp_to_text(pkt))
        self.assertTrue(_at_session_ready(_at_udp_to_text(pkt), "C5E632AC"))

    def test_accepts_at_ok(self):
        pkt = b"+NETMODE:1\r\n@C5E632AC:OK\r\n"
        self.assertIn("@C5E632AC:OK", _at_udp_to_text(pkt))

    def test_save_gets_longer_timeout(self):
        from rotortcpbridge.dk8de_wlan_module import _at_command_timeout

        self.assertGreaterEqual(_at_command_timeout("SAVE", 3.0), 12.0)
        self.assertEqual(_at_command_timeout("NETMODE?", 3.0), 3.0)


class TestStatsPayload(unittest.TestCase):
    def test_parse_at_lines(self):
        info, status = parse_dk8de_stats_payload(
            "UID=C5E632AC\nNETMODE=1\n",
            "LINK=1\nRSSI=-36\nRS485_RX=1000\nRS485_TX_ALLOWED=1\n",
        )
        self.assertEqual(info["UID"], "C5E632AC")
        self.assertEqual(info["NETMODE"], "1")
        self.assertEqual(status["LINK"], "1")
        self.assertEqual(status["RSSI"], "-36")
        self.assertEqual(status["RS485_RX"], "1000")
        self.assertEqual(status["RS485_TX_ALLOWED"], "1")

    def test_parse_plus_kv_via_helper(self):
        from rotortcpbridge.dk8de_wlan_module import _at_response_to_kv_text

        text = _at_response_to_kv_text(
            ["UID=C5E632AC"],
            ["+NAME:Rotor", "+FW:1.5.0"],
        )
        info = parse_dk8de_kv_text(text)
        self.assertEqual(info["UID"], "C5E632AC")
        self.assertEqual(info["NAME"], "Rotor")
        self.assertEqual(info["FW"], "1.5.0")


if __name__ == "__main__":
    unittest.main()
