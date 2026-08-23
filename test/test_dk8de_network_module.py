"""NetworkModule serialisation with DK8DE fields."""

from __future__ import annotations

import unittest

from rotortcpbridge.network_modules import (
    VENDOR_DK8DE,
    NetworkModule,
    modules_from_cfg,
    modules_to_cfg,
)


class TestDk8deNetworkModule(unittest.TestCase):
    def test_roundtrip(self):
        m = NetworkModule(
            name="Test",
            vendor=VENDOR_DK8DE,
            host="192.168.1.50",
            uid="AABBCCDD",
            at_port=8886,
            config_port=8880,
        )
        raw = modules_to_cfg([m])[0]
        self.assertEqual(raw["vendor"], VENDOR_DK8DE)
        self.assertEqual(raw["uid"], "AABBCCDD")
        self.assertEqual(raw["config_port"], 8880)
        restored = modules_from_cfg({"network_modules": [raw]})[0]
        self.assertEqual(restored.uid, "AABBCCDD")
        self.assertEqual(restored.config_port, 8880)

    def test_non_dk8de_omits_uid(self):
        m = NetworkModule(vendor="ne2", host="1.2.3.4", uid="SHOULDGO")
        d = m.to_dict()
        self.assertNotIn("uid", d)
        self.assertNotIn("config_port", d)


if __name__ == "__main__":
    unittest.main()
