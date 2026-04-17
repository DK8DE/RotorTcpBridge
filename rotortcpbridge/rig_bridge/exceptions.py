"""Spezifische Ausnahmen für das Rig-Bridge-Modul."""


class RigBridgeError(Exception):
    """Basisfehler für Rig-Bridge."""


class RigConnectionError(RigBridgeError):
    """Fehler beim Aufbau/Erhalt der Funkgeräteverbindung."""


class RigConfigurationError(RigBridgeError):
    """Fehlerhafte Rig-Bridge-Konfiguration."""


class ProtocolServerError(RigBridgeError):
    """Fehler beim Start/Stop eines Protokollservers."""
