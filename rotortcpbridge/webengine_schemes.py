"""Custom URL-Schemes für Qt WebEngine.
Muss als erstes Modul importiert werden, VOR QApplication und allen WebEngine-Nutzungen."""
from __future__ import annotations

from PySide6.QtCore import QByteArray
from PySide6.QtWebEngineCore import QWebEngineUrlScheme

_scheme = QWebEngineUrlScheme(QByteArray(b"rotortiles"))
_scheme.setSyntax(QWebEngineUrlScheme.Syntax.Path)
_scheme.setFlags(
    QWebEngineUrlScheme.Flag.LocalScheme
    | QWebEngineUrlScheme.Flag.SecureScheme
    | QWebEngineUrlScheme.Flag.CorsEnabled
)
QWebEngineUrlScheme.registerScheme(_scheme)
