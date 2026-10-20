#!/usr/bin/env python3
"""Generate today's routine task instances; safe to run repeatedly."""
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

app.initialize()
c=app.db()
try:
    generated=app.generate_routines(c)
    c.commit()
    print({'generated_task_ids':generated,'count':len(generated)})
finally:
    c.close()
