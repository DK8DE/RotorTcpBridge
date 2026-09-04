"""Unit-Tests fuer network_modules Parser/Builder (ohne Sockets)."""

from __future__ import annotations

import struct
import unittest
from pathlib import Path

from rotortcpbridge.network_modules import (
    NetworkModule,
    build_netat_line,
    build_netp_cmd,
    build_sock_cmd,
    build_usr_line,
    build_wan_cmd,
    build_wann_cmd,
    build_wsdns_cmd,
    extract_ok_payload,
    ip_octets_to_str,
    ip_str_to_octets,
    map_ebyte_web_to_status,
    modules_from_cfg,
    modules_to_cfg,
    parse_linksta_response,
    parse_netp_response,
    parse_sock_response,
    parse_wan_response,
    parse_wann_response,
    status_has_data,
    vendor_for_port,
)


class TestOkPayload(unittest.TestCase):
    def test_ok_upper(self):
        self.assertEqual(extract_ok_payload("\r\n+OK=NE2-D11\r\n"), "NE2-D11")

    def test_ok_lower_usr(self):
        self.assertEqual(
            extract_ok_payload("+ok=static,192.168.1.10,255.255.255.0,192.168.1.1\r\n\r\n"),
            "static,192.168.1.10,255.255.255.0,192.168.1.1",
        )

    def test_none(self):
        self.assertIsNone(extract_ok_payload("garbage"))


class TestWan(unittest.TestCase):
    def test_parse_ne2_two_dns(self):
        p = parse_wan_response(
            "STATIC,192.168.3.7,255.255.255.0,192.168.3.1,114.114.114.114,8.8.8.8"
        )
        self.assertEqual(p["mode"], "STATIC")
        self.assertEqual(p["ip"], "192.168.3.7")
        self.assertEqual(p["mask"], "255.255.255.0")
        self.assertEqual(p["gateway"], "192.168.3.1")
        self.assertEqual(p["dns"], "114.114.114.114")
        self.assertEqual(p["dns2"], "8.8.8.8")

    def test_parse_na11x_one_dns(self):
        p = parse_wan_response(
            "STATIC,192.168.3.7,255.255.255.0,192.168.3.1,114.114.114.114"
        )
        self.assertEqual(p["dns"], "114.114.114.114")
        self.assertEqual(p["dns2"], "")

    def test_build_wan_with_dns2(self):
        cmd = build_wan_cmd(
            "STATIC",
            "192.168.0.10",
            "255.255.255.0",
            "192.168.0.1",
            "8.8.8.8",
            "1.1.1.1",
            with_dns2=True,
        )
        self.assertEqual(
            cmd,
            "AT+WAN=STATIC,192.168.0.10,255.255.255.0,192.168.0.1,8.8.8.8,1.1.1.1",
        )

    def test_build_wan_without_dns2(self):
        cmd = build_wan_cmd(
            "DHCP",
            "192.168.0.10",
            "255.255.255.0",
            "192.168.0.1",
            "8.8.8.8",
            with_dns2=False,
        )
        self.assertEqual(
            cmd,
            "AT+WAN=DHCP,192.168.0.10,255.255.255.0,192.168.0.1,8.8.8.8",
        )


class TestWannUsr(unittest.TestCase):
    def test_parse_wann(self):
        p = parse_wann_response("static,192.168.1.50,255.255.255.0,192.168.1.1")
        self.assertEqual(p["mode"], "STATIC")
        self.assertEqual(p["ip"], "192.168.1.50")

    def test_build_wann(self):
        self.assertEqual(
            build_wann_cmd("STATIC", "10.0.0.2", "255.255.255.0", "10.0.0.1"),
            "AT+WANN=static,10.0.0.2,255.255.255.0,10.0.0.1",
        )

    def test_wsdns(self):
        self.assertEqual(build_wsdns_cmd("8.8.8.8"), "AT+WSDNS=8.8.8.8")


class TestSock(unittest.TestCase):
    def test_parse_ne2_with_link(self):
        p = parse_sock_response("0,TCPC,192.168.3.3,8888")
        self.assertEqual(p["link_id"], "0")
        self.assertEqual(p["mode"], "TCPC")
        self.assertEqual(p["remote_ip"], "192.168.3.3")
        self.assertEqual(p["remote_port"], "8888")

    def test_parse_na11x_without_link(self):
        p = parse_sock_response("TCPS,0.0.0.0,8886")
        self.assertEqual(p["link_id"], "")
        self.assertEqual(p["mode"], "TCPS")
        self.assertEqual(p["remote_port"], "8886")

    def test_build_sock_ne2(self):
        self.assertEqual(
            build_sock_cmd("TCPS", "0.0.0.0", 8886, link_id=0),
            "AT+SOCK=0,TCPS,0.0.0.0,8886",
        )

    def test_build_sock_na11x(self):
        self.assertEqual(
            build_sock_cmd("TCPC", "192.168.1.1", 9000, link_id=None),
            "AT+SOCK=TCPC,192.168.1.1,9000",
        )


class TestNetp(unittest.TestCase):
    def test_parse_client(self):
        p = parse_netp_response("TCP,CLIENT,8899,192.168.1.1")
        self.assertEqual(p["mode"], "TCPC")
        self.assertEqual(p["remote_port"], "8899")
        self.assertEqual(p["remote_ip"], "192.168.1.1")

    def test_parse_server(self):
        p = parse_netp_response("TCP,SERVER,8899,0.0.0.0")
        self.assertEqual(p["mode"], "TCPS")

    def test_build_netp(self):
        self.assertEqual(
            build_netp_cmd("TCPC", "192.168.1.1", 8899),
            "AT+NETP=TCP,CLIENT,8899,192.168.1.1",
        )
        self.assertEqual(
            build_netp_cmd("TCPS", "0.0.0.0", 8899),
            "AT+NETP=TCP,SERVER,8899,0.0.0.0",
        )


class TestPrefixes(unittest.TestCase):
    def test_netat_from_at(self):
        self.assertEqual(build_netat_line("NETAT", "AT+WAN"), "NETAT+WAN")
        self.assertEqual(
            build_netat_line("NETAT", "AT+WAN=STATIC,1.2.3.4,255.255.255.0,1.2.3.1,8.8.8.8,8.8.4.4"),
            "NETAT+WAN=STATIC,1.2.3.4,255.255.255.0,1.2.3.1,8.8.8.8,8.8.4.4",
        )

    def test_usr_prefix(self):
        self.assertEqual(build_usr_line("USR", "AT+WANN"), "USRAT+WANN")
        self.assertEqual(build_usr_line("USR", "AT+NETP=TCP,CLIENT,1,2"), "USRAT+NETP=TCP,CLIENT,1,2")


class TestLinksta(unittest.TestCase):
    def test_connect(self):
        self.assertEqual(parse_linksta_response("0,Connect"), "Connect")
        self.assertEqual(parse_linksta_response("Disconnect"), "Disconnect")
        self.assertEqual(parse_linksta_response("on"), "Connect")


class TestModuleSerde(unittest.TestCase):
    def test_roundtrip(self):
        mods = [
            NetworkModule(
                name="Bus",
                vendor="ne2",
                host="192.168.0.246",
                at_port=8886,
                role="bus_gateway",
            )
        ]
        cfg = {"network_modules": modules_to_cfg(mods)}
        back = modules_from_cfg(cfg)
        self.assertEqual(len(back), 1)
        self.assertEqual(back[0].host, "192.168.0.246")
        self.assertEqual(back[0].vendor, "ne2")

    def test_vendor_for_port(self):
        self.assertEqual(vendor_for_port(8899), "usr_dr164")
        self.assertEqual(vendor_for_port(8886), "ne2")

    def test_web_creds_roundtrip(self):
        mods = [
            NetworkModule(
                name="Bus",
                vendor="ne2",
                host="192.168.0.246",
                web_user="admin",
                web_password="secret",
            )
        ]
        back = modules_from_cfg({"network_modules": modules_to_cfg(mods)})
        self.assertEqual(back[0].web_user, "admin")
        self.assertEqual(back[0].web_password, "secret")


class TestEbyteWebMap(unittest.TestCase):
    def test_ip_octets(self):
        self.assertEqual(ip_octets_to_str([192, 168, 0, 246]), "192.168.0.246")
        self.assertEqual(ip_str_to_octets("10.0.0.1"), [10, 0, 0, 1])

    def test_map_basic_static_socket(self):
        basic = {
            "net_DHCP": 0,
            "net_localIP": [192, 168, 0, 246],
            "net_mask": [255, 255, 255, 0],
            "net_getway": [192, 168, 0, 1],
            "net_dns": [114, 114, 114, 114],
            "net_dns2": [8, 8, 8, 8],
        }
        static = {
            "static_moudle": "NE2-D11",
            "static_MAC": "44-1D-64-92-65-C3",
            "static_FW": "1.0",
        }
        socket_a = {
            "sock_mode": 2,
            "sock_localport": 8886,
            "sock_desname": "0.0.0.0",
            "sock_desport": 0,
        }
        st = map_ebyte_web_to_status(basic, static, socket_a)
        self.assertTrue(status_has_data(st))
        self.assertEqual(st["model"], "NE2-D11")
        self.assertEqual(st["wan"]["ip"], "192.168.0.246")
        self.assertEqual(st["wan"]["gateway"], "192.168.0.1")
        self.assertEqual(st["sock"]["mode"], "TCPS")
        self.assertEqual(st["sock"]["remote_port"], "8886")
        self.assertEqual(st["source"], "web")

    def test_status_has_data_empty(self):
        self.assertFalse(status_has_data({}))
        self.assertFalse(
            status_has_data(
                {
                    "model": "",
                    "mac": "",
                    "wan": {"ip": ""},
                    "sock": {"mode": "DISABLE"},
                }
            )
        )

    def test_parse_script_object(self):
        from rotortcpbridge.network_modules import parse_ebyte_script_object

        d = parse_ebyte_script_object(b'var dat0={"__08":"192.168.0.248","__07":"1"};')
        self.assertEqual(d["__08"], "192.168.0.248")
        d2 = parse_ebyte_script_object(b'dat4={"__28":"01 03|"}\n')
        self.assertEqual(d2["__28"], "01 03|")

    def test_map_na111_para(self):
        from rotortcpbridge.network_modules import map_na111_para_to_status

        para = {
            "__02": "NA111-M",
            "__04": "9013-2-17",
            "__05": "0C-3D-5E-88-FB-83",
            "__06": "0",
            "__07": "1",
            "__08": "192.168.0.248",
            "__09": "8886",
            "__0A": "80",
            "__0B": "255.255.255.0",
            "__0C": "192.168.0.1",
            "__0D": "192.168.0.1",
            "__0E": "192.168.3.3",
            "__0F": "8888",
        }
        st = map_na111_para_to_status(para)
        self.assertTrue(status_has_data(st))
        self.assertEqual(st["model"], "NA111-M")
        self.assertEqual(st["wan"]["ip"], "192.168.0.248")
        self.assertEqual(st["sock"]["mode"], "TCPS")
        self.assertEqual(st["sock"]["remote_port"], "8886")
        self.assertEqual(st["source"], "web_na111")

    def test_map_usr_web(self):
        from rotortcpbridge.network_modules import (
            map_usr_web_to_status,
            parse_html_js_string_vars,
            usr_web_sock_mode,
        )

        html = 'var wan_setting_ip = "192.168.0.249";\nvar wan_setting_dhcp = "STATIC";\n'
        self.assertEqual(parse_html_js_string_vars(html)["wan_setting_ip"], "192.168.0.249")
        self.assertEqual(usr_web_sock_mode("TCP", "SERVER"), "TCPS")
        st = map_usr_web_to_status(
            {"cover_mid": "USR-DR164", "cover_sta_mac": "D4AD20E03DB0", "cover_ver": "V1"},
            {
                "wan_setting_dhcp": "STATIC",
                "wan_setting_ip": "192.168.0.249",
                "wan_setting_msk": "255.255.255.0",
                "wan_setting_gw": "192.168.0.1",
                "wan_setting_dns": "192.168.0.1",
            },
            {"net_pro": "TCP", "net_cs": "SERVER", "net_port": "8899", "net_ip": "0.0.0.0"},
        )
        self.assertTrue(status_has_data(st))
        self.assertEqual(st["wan"]["ip"], "192.168.0.249")
        self.assertEqual(st["sock"]["mode"], "TCPS")
        self.assertEqual(st["mac"], "D4-AD-20-E0-3D-B0")
        self.assertEqual(st["source"], "web_usr")


# Fixtures aus mitschnitt/*.pcapng (UDP 1901/1902)
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "ebyte"


def _fx(name: str) -> bytes:
    return (_FIXTURES / name).read_bytes()


class TestEbyteUdpProtocol(unittest.TestCase):
    def test_crc16_ne2_pages(self):
        from rotortcpbridge.network_modules import ebyte_crc16, ebyte_page_from_payload

        for name in ("ne2_page0.bin", "ne2_page1.bin", "ne2_page1_write.bin"):
            raw = _fx(name)
            page = ebyte_page_from_payload(raw)
            self.assertIsNotNone(page)
            assert page is not None
            stored_lo = int.from_bytes(raw[10:12], "little")
            self.assertEqual((ebyte_crc16(page.body) ^ page.crc_k) & 0xFFFF, stored_lo)

    def test_crc16_na111_k_model(self):
        from rotortcpbridge.network_modules import ebyte_page_from_payload

        r = ebyte_page_from_payload(_fx("na111_page0.bin"))
        w = ebyte_page_from_payload(_fx("na111_page0_write.bin"))
        self.assertIsNotNone(r)
        self.assertIsNotNone(w)
        assert r is not None and w is not None
        self.assertEqual(r.crc_k, w.crc_k)
        self.assertEqual(r.crc_hi, w.crc_hi)
        self.assertEqual(w.checksum_bytes(w.body), _fx("na111_page0_write.bin")[10:14])

    def test_parse_ne2_identity_and_net(self):
        from rotortcpbridge.network_modules import (
            EbyteDevice,
            VENDOR_NE2,
            ebyte_apply_pages,
            ebyte_page_from_payload,
        )

        p0 = ebyte_page_from_payload(_fx("ne2_page0.bin"))
        p1 = ebyte_page_from_payload(_fx("ne2_page1.bin"))
        assert p0 is not None and p1 is not None
        dev = EbyteDevice(mac=_fx("ne2_page0.bin")[2:8])
        ebyte_apply_pages(dev, {0: p0, 1: p1})
        self.assertEqual(dev.vendor, VENDOR_NE2)
        self.assertEqual(dev.model, "NE2-D11")
        self.assertTrue(dev.fw.startswith("FW-9167"))
        self.assertEqual(dev.ip, "192.168.0.245")
        self.assertEqual(dev.gateway, "192.168.0.1")
        self.assertEqual(dev.mask, "255.255.255.0")
        self.assertEqual(dev.dns, "114.114.114.114")
        self.assertEqual(dev.dns2, "8.8.8.8")

    def test_map_ebyte_device_to_status(self):
        from rotortcpbridge.network_modules import (
            EbyteDevice,
            ebyte_apply_pages,
            ebyte_page_from_payload,
            map_ebyte_device_to_status,
            status_has_data,
        )

        p0 = ebyte_page_from_payload(_fx("ne2_page0.bin"))
        p1 = ebyte_page_from_payload(_fx("ne2_page1.bin"))
        assert p0 is not None and p1 is not None
        dev = EbyteDevice(mac=_fx("ne2_page0.bin")[2:8])
        ebyte_apply_pages(dev, {0: p0, 1: p1})
        st = map_ebyte_device_to_status(dev)
        self.assertTrue(status_has_data(st))
        self.assertEqual(st["model"], "NE2-D11")
        self.assertEqual(st["wan"]["ip"], "192.168.0.245")
        self.assertEqual(st["sock"]["mode"], "TCPS")
        self.assertEqual(st["sock"]["remote_port"], "8886")
        self.assertEqual(st["source"], "udp")
        from rotortcpbridge.network_modules import ebyte_device_data_port

        self.assertEqual(ebyte_device_data_port(dev), 8886)

    def test_parse_na111_identity_and_net(self):
        from rotortcpbridge.network_modules import (
            EbyteDevice,
            VENDOR_NA11X,
            ebyte_apply_pages,
            ebyte_page_from_payload,
        )

        p0 = ebyte_page_from_payload(_fx("na111_page0.bin"))
        p5 = ebyte_page_from_payload(_fx("na111_page5.bin"))
        assert p0 is not None and p5 is not None
        dev = EbyteDevice(mac=_fx("na111_page0.bin")[2:8])
        ebyte_apply_pages(dev, {0: p0, 5: p5})
        self.assertEqual(dev.vendor, VENDOR_NA11X)
        self.assertEqual(dev.model, "NA111-M")
        self.assertEqual(dev.ip, "192.168.0.248")
        self.assertEqual(dev.gateway, "192.168.0.1")
        self.assertEqual(dev.mask, "255.255.255.0")
        self.assertEqual(dev.dns, "192.168.0.1")
        from rotortcpbridge.network_modules import ebyte_device_data_port, map_ebyte_device_to_status

        st = map_ebyte_device_to_status(dev)
        self.assertEqual(st["sock"]["mode"], "TCPS")
        self.assertEqual(ebyte_device_data_port(dev), 8886)

    def test_ne2_patch_ip_matches_capture_checksum(self):
        from rotortcpbridge.network_modules import (
            VENDOR_NE2,
            ebyte_page_from_payload,
            ebyte_patch_net_pages,
        )

        p1 = ebyte_page_from_payload(_fx("ne2_page1.bin"))
        assert p1 is not None
        bodies = ebyte_patch_net_pages(
            {1: p1},
            ip="192.168.0.244",
            mask="255.255.255.0",
            gateway="192.168.0.1",
            dns="114.114.114.114",
            dns2="8.8.8.8",
            vendor=VENDOR_NE2,
        )
        write = _fx("ne2_page1_write.bin")
        self.assertEqual(bodies[1], write[14:])
        self.assertEqual(p1.checksum_bytes(bodies[1]), write[10:14])

    def test_ne2_patch_sock_mode_and_port(self):
        import struct

        from rotortcpbridge.network_modules import (
            VENDOR_NE2,
            _NE2_SOCK0_HOST_LEN,
            _NE2_SOCK0_HOST_OFF,
            _NE2_SOCK0_IP_OFF,
            _c_str_at,
            _ne2_sock_from_page1,
            ebyte_at_to_sock_mode,
            ebyte_page_from_payload,
            ebyte_patch_net_pages,
        )

        p1 = ebyte_page_from_payload(_fx("ne2_page1.bin"))
        assert p1 is not None
        bodies = ebyte_patch_net_pages(
            {1: p1},
            ip="192.168.0.245",
            mask="255.255.255.0",
            gateway="192.168.0.1",
            vendor=VENDOR_NE2,
            sock_cfg={
                "mode": "TCPC",
                "remote_ip": "192.168.0.50",
                "remote_port": 9999,
            },
        )
        b = bodies[1]
        tcpc = ebyte_at_to_sock_mode("TCPC")
        self.assertEqual(b[10], tcpc)
        self.assertEqual(b[779], tcpc)
        self.assertEqual(struct.unpack("<H", b[782:784])[0], 9999)
        self.assertEqual(struct.unpack("<H", b[786:788])[0], 9999)
        # Wirksame Ziel-IP: ASCII (Web sock_desname); Binaer @768 wird geleert.
        self.assertEqual(_c_str_at(b, _NE2_SOCK0_HOST_OFF, _NE2_SOCK0_HOST_LEN), "192.168.0.50")
        self.assertEqual(b[_NE2_SOCK0_IP_OFF : _NE2_SOCK0_IP_OFF + 4], bytes(4))
        after = _ne2_sock_from_page1(b)
        self.assertEqual(after["mode"], "TCPC")
        self.assertEqual(after["remote_ip"], "192.168.0.50")
        self.assertEqual(after["remote_port"], "9999")

    def test_ne2_sock_reads_ascii_dest_over_binary(self):
        """NE2-T1M/D11: ASCII-Ziel hat Vorrang vor Rest-Binaer-IP @768."""
        from rotortcpbridge.network_modules import (
            _NE2_SOCK0_HOST_LEN,
            _NE2_SOCK0_HOST_OFF,
            _NE2_SOCK0_IP_OFF,
            _ne2_sock_from_page1,
            _put_c_str,
            ebyte_at_to_sock_mode,
            ebyte_page_from_payload,
        )

        p1 = ebyte_page_from_payload(_fx("ne2_page1.bin"))
        assert p1 is not None
        b = bytearray(p1.body)
        b[10] = ebyte_at_to_sock_mode("TCPC")
        b[779] = ebyte_at_to_sock_mode("TCPC")
        _put_c_str(b, _NE2_SOCK0_HOST_OFF, _NE2_SOCK0_HOST_LEN, "192.168.0.148")
        # Veraltete/andere Binaer-IP darf ASCII nicht ueberstimmen.
        b[_NE2_SOCK0_IP_OFF : _NE2_SOCK0_IP_OFF + 4] = bytes([192, 168, 0, 99])
        sock = _ne2_sock_from_page1(bytes(b))
        self.assertEqual(sock["remote_ip"], "192.168.0.148")

    def test_na111_patch_sock_mode_client(self):
        from rotortcpbridge.network_modules import (
            VENDOR_NA11X,
            _NA111_SOCK_MODE_OFF,
            _NA111_SOCK_MODE_OFF2,
            _NA111_SOCK_REMOTE_HOST_OFF,
            _NA111_SOCK_REMOTE_PORT_OFF,
            _c_str_at,
            ebyte_page_from_payload,
            ebyte_patch_net_pages,
            na111_at_to_sock_mode,
            _na111_sock_from_page0,
        )

        p0 = ebyte_page_from_payload(_fx("na111_page0.bin"))
        p3 = ebyte_page_from_payload(_fx("na111_page3.bin"))
        assert p0 is not None and p3 is not None
        before = _na111_sock_from_page0(p0.body)
        self.assertEqual(before["mode"], "TCPS")
        bodies = ebyte_patch_net_pages(
            {0: p0, 3: p3},
            ip="192.168.0.248",
            mask="255.255.255.0",
            gateway="192.168.0.1",
            dns="192.168.0.1",
            vendor=VENDOR_NA11X,
            sock_cfg={
                "mode": "TCPC",
                "remote_ip": "192.168.0.50",
                "remote_port": 9999,
            },
        )
        b = bodies[0]
        self.assertEqual(b[_NA111_SOCK_MODE_OFF], na111_at_to_sock_mode("TCPC"))
        self.assertEqual(b[_NA111_SOCK_MODE_OFF2], na111_at_to_sock_mode("TCPC"))
        self.assertEqual(
            struct.unpack("<H", b[_NA111_SOCK_REMOTE_PORT_OFF : _NA111_SOCK_REMOTE_PORT_OFF + 2])[0],
            9999,
        )
        self.assertEqual(_c_str_at(b, _NA111_SOCK_REMOTE_HOST_OFF, 32), "192.168.0.50")
        after = _na111_sock_from_page0(b)
        self.assertEqual(after["mode"], "TCPC")
        self.assertEqual(after["remote_ip"], "192.168.0.50")
        self.assertEqual(after["remote_port"], "9999")

    def test_na111_patch_ip_matches_capture_checksum(self):
        from rotortcpbridge.network_modules import (
            VENDOR_NA11X,
            ebyte_page_from_payload,
            ebyte_patch_net_pages,
        )

        p0 = ebyte_page_from_payload(_fx("na111_page0.bin"))
        p3 = ebyte_page_from_payload(_fx("na111_page3.bin"))
        assert p0 is not None and p3 is not None
        bodies = ebyte_patch_net_pages(
            {0: p0, 3: p3},
            ip="192.168.0.247",
            mask="255.255.255.0",
            gateway="192.168.0.1",
            dns="192.168.0.1",
            vendor=VENDOR_NA11X,
        )
        write = _fx("na111_page0_write.bin")
        self.assertEqual(bodies[0], write[14:])
        self.assertEqual(p0.checksum_bytes(bodies[0]), write[10:14])
        self.assertEqual(bodies[3][0], 0x1E)

    def test_ebyte_directed_broadcast(self):
        from rotortcpbridge.network_modules import _ebyte_directed_broadcast

        self.assertEqual(
            _ebyte_directed_broadcast("10.10.100.50", "255.255.255.0"),
            "10.10.100.255",
        )
        self.assertEqual(
            _ebyte_directed_broadcast("192.168.0.245", "255.255.255.0"),
            "192.168.0.255",
        )

    def test_ebyte_merge_page_maps_keeps_full_cache(self):
        from rotortcpbridge.network_modules import EbytePage, _ebyte_merge_page_maps

        p0 = EbytePage(page=0, body=b"a" * 80)
        p1 = EbytePage(page=1, body=b"b" * 200)
        cached = {0: p0, 1: p1}
        fresh = {0: EbytePage(page=0, body=b"x" * 80)}
        merged = _ebyte_merge_page_maps(cached, fresh)
        self.assertIn(1, merged)
        self.assertEqual(merged[0].body, b"x" * 80)
        self.assertEqual(merged[1].body, b"b" * 200)

    def test_ebyte_pages_complete_for_ne2(self):
        from rotortcpbridge.network_modules import (
            EbytePage,
            VENDOR_NE2,
            _ebyte_pages_complete_for_write,
        )

        p0 = EbytePage(page=0, body=b"x" * 80)
        p1 = EbytePage(page=1, body=b"y" * 400)
        self.assertFalse(_ebyte_pages_complete_for_write({0: p0}, VENDOR_NE2))
        self.assertFalse(_ebyte_pages_complete_for_write({0: p0, 1: p1}, VENDOR_NE2))
        p6 = EbytePage(page=6, body=b"z" * 180)
        self.assertTrue(
            _ebyte_pages_complete_for_write({0: p0, 1: p1, 6: p6}, VENDOR_NE2)
        )

    def test_ebyte_reboot_payload(self):
        from rotortcpbridge.network_modules import (
            EBYTE_UDP_REBOOT_TAIL,
            EBYTE_UDP_REBOOT_TAIL_NA111,
            EBYTE_UDP_REBOOT_TAIL_NE2,
            VENDOR_NA11X,
            VENDOR_NE2,
            ebyte_udp_reboot_tail,
        )

        mac = bytes.fromhex("b0cbd84ec5b7")
        payload = bytes([0xFE, 0x03]) + mac + EBYTE_UDP_REBOOT_TAIL
        self.assertEqual(payload.hex(), "fe03b0cbd84ec5b71101")
        self.assertEqual(EBYTE_UDP_REBOOT_TAIL_NE2, b"\x11\x01")
        self.assertEqual(EBYTE_UDP_REBOOT_TAIL_NA111, b"\x03\x01")
        # NA111 reboot.pcapng: fe 03 + MAC + 03 01
        na_mac = bytes.fromhex("0c3d5e88ff86")
        na_payload = bytes([0xFE, 0x03]) + na_mac + ebyte_udp_reboot_tail(VENDOR_NA11X)
        self.assertEqual(na_payload.hex(), "fe030c3d5e88ff860301")
        self.assertEqual(ebyte_udp_reboot_tail(VENDOR_NE2), b"\x11\x01")
        self.assertEqual(ebyte_udp_reboot_tail(model="NA111-M"), b"\x03\x01")

    def test_ebyte_write_session_payload(self):
        from rotortcpbridge.network_modules import _ebyte_udp_write_session_payload

        mac1 = bytes.fromhex("b0cbd84ec5b7")
        mac2 = bytes.fromhex("b0cbd84ec017")
        for mac in (mac1, mac2):
            self.assertEqual(
                _ebyte_udp_write_session_payload(mac, 0x30).hex(),
                f"fe0b{mac.hex()}0000bf5430",
            )
            self.assertEqual(
                _ebyte_udp_write_session_payload(mac, 0x31).hex(),
                f"fe0b{mac.hex()}00007e9431",
            )

    def test_discover_ping_constant(self):
        from rotortcpbridge.network_modules import EBYTE_DISCOVER_PING

        self.assertEqual(EBYTE_DISCOVER_PING, b"www.cdebyte.comwww.cdebyte.com")
        self.assertEqual(len(EBYTE_DISCOVER_PING), 30)


if __name__ == "__main__":
    unittest.main()
