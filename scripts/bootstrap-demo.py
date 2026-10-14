#!/usr/bin/env python3
"""Create an isolated demo dataset. Set WMT_DB to avoid touching the trial database."""
import os, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); import app

def main():
    app.initialize(); c=app.db()
    try:
        users=[('产品负责人（演示）','科技产品中心','owner','demo-product'),('项目负责人（演示）','科技产品中心','owner','demo-project')]
        ids=[]
        for row in users:
            hit=c.execute('select id from user where wecom_userid=?',(row[3],)).fetchone()
            if hit: ids.append(hit[0]); continue
            ids.append(c.execute('insert into user(name,department,role,wecom_userid) values(?,?,?,?)',row).lastrowid)
        root=c.execute("select id from org_node where node_type='department' order by id limit 1").fetchone()[0]
        domain=c.execute("select id from org_node where name='产品与项目管理' limit 1").fetchone()
        domain_id=domain[0] if domain else c.execute('insert into org_node(parent_id,node_type,name,boundary,effective_at,created_at) values(?,?,?,?,?,?)',(root,'domain','产品与项目管理','负责产品规划和项目交付',app.now(),app.now())).lastrowid
        c.execute('insert into task(org_node_id,title,description,owner_id,due_at,priority,deliverable,source,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?)',(domain_id,'演示：完成责任树评审','用于端到端验收',ids[0],'2030-01-01T17:00:00+08:00','P1','评审纪要','demo',app.now(),app.now()))
        c.execute('insert into task(org_node_id,title,description,owner_id,due_at,priority,deliverable,source,created_at,updated_at) values(?,?,?,?,?,?,?,?,?,?)',(domain_id,'演示：验证企业微信反馈','用于回调验收',ids[1],'2030-01-02T17:00:00+08:00','P1','反馈记录','demo',app.now(),app.now()))
        c.commit(); print({'demo_users':ids,'demo_tasks':2,'db':str(app.DB_PATH)})
    finally: c.close()

if __name__=='__main__': main()
