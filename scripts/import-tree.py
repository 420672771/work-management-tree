#!/usr/bin/env python3
import csv, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); import app

def main(path):
    app.initialize(); c=app.db(); created={r['name']:r['id'] for r in c.execute('select id,name from org_node')}; count=0
    try:
        for row in csv.DictReader(open(path,encoding='utf-8-sig')):
            name=(row.get('name') or '').strip(); parent=(row.get('parent_name') or '').strip()
            if not name: raise ValueError('name 必填')
            if name in created: continue
            parent_id=created.get(parent)
            owner=None
            if row.get('owner_name'):
                hit=c.execute('select id from user where name=?',(row['owner_name'],)).fetchone(); owner=hit[0] if hit else None
            cur=c.execute('insert into org_node(parent_id,node_type,name,boundary,owner_id,effective_at,created_at) values(?,?,?,?,?,?,?)',(parent_id,row.get('node_type','domain'),name,row.get('boundary',''),owner,row.get('effective_at') or app.now(),app.now())); created[name]=cur.lastrowid; count+=1
        c.commit()
    except Exception:
        c.rollback(); raise
    finally: c.close()
    print({'imported':count})

if __name__=='__main__':
    if len(sys.argv)!=2: raise SystemExit('usage: import-tree.py tree.csv')
    main(sys.argv[1])
