#!/usr/bin/env python3
"""Run morning/evening/approval job logic. Delivery remains dry-run until IM is configured."""
import argparse, sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

parser=argparse.ArgumentParser()
parser.add_argument('--date', dest='report_date')
parser.add_argument('--kind', choices=('all','morning','evening','close','approval'), default='all')
args=parser.parse_args()
app.initialize(); c=app.db()
try:
    print(app.run_daily_jobs(c,args.report_date,args.kind)); c.commit()
finally:
    c.close()
