#!/usr/bin/env python3
import csv, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); import app

def main(path):
    app.initialize(); c=app.db(); count=0
    try:
        for row in csv.DictReader(open(path,encoding='utf-8-sig')):
            if not row.get('name') or not row.get('wecom_userid'): raise ValueError('name 和 wecom_userid 必填')
            c.execute('INSERT INTO user(name,department,role,wecom_userid) VALUES(?,?,?,?)',(row['name'],row.get('department','科技产品中心'),row.get('role','owner'),row['wecom_userid'])); count+=1
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()
    print({'imported':count})

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('usage: import-users.py users.csv')
    main(sys.argv[1])
