#!/usr/bin/env python3
"""Run or validate the optional pilot backend reloader."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dev_server

if __name__ == '__main__':
    raise SystemExit(dev_server.main())
