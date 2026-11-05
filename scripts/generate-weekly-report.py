#!/usr/bin/env python3
"""Generate the last due Sunday report once, without sending external messages."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import app
import weekly_reports

if __name__ == '__main__':
    app.initialize()
    c = app.db()
    try:
        print(json.dumps(weekly_reports.run_due(c), ensure_ascii=False))
        c.commit()
    finally:
        c.close()
