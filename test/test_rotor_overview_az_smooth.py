from __future__ import annotations

from rotortcpbridge.rotor_overview_monitor import SiteLiveState
from rotortcpbridge.rotor_overview_node import apply_node_snapshot_to_state


def test_node_az_is_display_degrees_not_d10() -> None:
    st = SiteLiveState(site_id="t")
    apply_node_snapshot_to_state(st, {"online": True, "az_deg": 123.4})
    assert st.az_deg == 123.4
    assert st.az_smooth_deg == 123.4


def test_offline_keeps_last_az_in_state() -> None:
    st = SiteLiveState(site_id="t")
    apply_node_snapshot_to_state(st, {"online": True, "az_deg": 40.0})
    apply_node_snapshot_to_state(st, {"online": False, "az_deg": 40.0})
    assert st.az_deg == 40.0
    assert st.online is False
