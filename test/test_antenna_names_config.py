"""Antennen-Namen: Config / Defaults (ohne AZ kein GETANTNAME)."""

from __future__ import annotations

from rotortcpbridge.app_config import normalized_antenna_names


def test_normalized_antenna_names_from_config() -> None:
    cfg = {"ui": {"antenna_names": ["A", "B", "C"]}}
    assert normalized_antenna_names(cfg) == ["A", "B", "C"]


def test_normalized_antenna_names_fill_missing_and_empty() -> None:
    cfg = {"ui": {"antenna_names": ["X", "", None]}}
    names = normalized_antenna_names(
        cfg, defaults=["D1", "D2", "D3"]
    )
    assert names == ["X", "D2", "D3"]


def test_normalized_antenna_names_defaults_when_missing() -> None:
    assert normalized_antenna_names({}) == [
        "Antenne 1",
        "Antenne 2",
        "Antenne 3",
    ]
    assert normalized_antenna_names(None, defaults=["A1", "A2", "A3"]) == [
        "A1",
        "A2",
        "A3",
    ]
