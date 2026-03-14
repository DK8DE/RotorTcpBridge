
import time
from pathlib import Path
from rotortcpbridge.backup import make_source_backup

BASE = Path(__file__).resolve().parents[1]
out = make_source_backup(BASE)
print("created", out)
