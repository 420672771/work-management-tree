#!/usr/bin/env python3
"""Dispatch due user-configured task progress summaries exactly once per day."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

app.initialize(); c=app.db()
try:
    print(app.run_scheduled_reports(c)); c.commit()
finally:
    c.close()
