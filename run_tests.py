#!/usr/bin/env python3
"""Alle Unit-Tests ausführen (ein Aufruf genügt).

  python run_tests.py
  python run_tests.py -v
  python run_tests.py test/test_angle_utils.py -k wrap

Weitere Argumente werden an pytest durchgereicht.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest


def main() -> int:
    root = Path(__file__).resolve().parent
    test_dir = root / "test"
    if not test_dir.is_dir():
        print("ERROR: Ordner test/ nicht gefunden.", file=sys.stderr)
        return 1
    args = [str(test_dir)]
    if len(sys.argv) > 1:
        args.extend(sys.argv[1:])
    else:
        args.append("-q")
    return pytest.main(args)


if __name__ == "__main__":
    raise SystemExit(main())
