# RotorTcpBridge

Desktop-Brücke zwischen Hardware (RS485) und PST/UDP-Clients.

## Tests (Entwicklung)

```bash
pip install -r requirements-dev.txt
pytest
```

Die Tests decken u. a. Winkel-Hilfsfunktionen, Geografie (Peilung), PST-UDP-Positionslogik, RS485-Telegramme und Parameter-Parser ab – ohne Qt/Hardware.

## Code-Qualität (vor Commit empfohlen)

Mit **Ruff** Syntax und offensichtliche Probleme prüfen (Konfiguration in `pyproject.toml`):

```bash
ruff check rotortcpbridge tests
```

Optional alles automatisch formatieren:

```bash
ruff format rotortcpbridge tests
```

Hinweis: Ältere Stil-Patterns (z. B. Einzeiler mit `;`) sind in Ruff bewusst erlaubt, damit das Projekt ohne großen Umbau „grün“ bleibt.
