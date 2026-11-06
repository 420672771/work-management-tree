#!/usr/bin/env python3
"""Generate the week following the last due Sunday 18:00; safe to repeat."""
import argparse
import json
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--week', help='Generate the week containing YYYY-MM-DD instead of the scheduled week')
    args=parser.parse_args()
    app.initialize()
    c=app.db()
    try:
        generated=app.generate_routines(c,args.week)
        missing=[dict(r) for r in c.execute("SELECT id,title FROM task_template WHERE status='active' AND estimated_hours<0.5")]
        c.commit()
        print(json.dumps({'generated_task_ids':generated,'count':len(generated),'templates_needing_hours':missing},ensure_ascii=False))
    finally:
        c.close()
