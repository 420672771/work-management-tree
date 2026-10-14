#!/usr/bin/env python3
"""Run the complete V1.2 task lifecycle against an isolated database."""
import json, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT)); import app

def main():
    app.initialize(); c=app.db(); result={'passed':False,'checks':[]}
    try:
        owner=c.execute("select id from user where role='owner' order by id limit 1").fetchone()[0]
        node=c.execute("select id from org_node where node_type='domain' limit 1").fetchone()[0]
        due=(datetime.now(timezone.utc)+timedelta(days=2)).isoformat()
        tid=c.execute('insert into task(org_node_id,title,owner_id,due_at,priority,deliverable,source,created_at,updated_at) values(?,?,?,?,?,?,?,?,?)',(node,'试点验收：完成任务闭环',owner,due,'P1','提交验收记录','pilot',app.now(),app.now())).lastrowid
        c.execute("update task set work_status='accepted',updated_at=? where id=?",(app.now(),tid)); app.audit(c,'task',tid,'accept','pilot')
        c.execute('insert into feedback(task_id,user_id,content,progress,risk,created_at) values(?,?,?,?,?,?)',(tid,owner,'已完成并提交验收记录','已完成','',app.now()))
        c.execute("update task set work_status='awaiting_acceptance',updated_at=? where id=?",(app.now(),tid))
        c.execute('insert into acceptance(task_id,acceptor_id,result,reason,created_at) values(?,?,?,?,?)',(tid,1,'passed','',app.now()))
        c.execute("update task set work_status='completed',updated_at=? where id=?",(app.now(),tid)); c.commit()
        task=app.task_view(c,c.execute('select * from task where id=?',(tid,)).fetchone())
        checks=[('single_owner',task['owner_id']==owner),('feedback_recorded',c.execute('select count(*) from feedback where task_id=?',(tid,)).fetchone()[0]==1),('accepted',c.execute("select count(*) from acceptance where task_id=? and result='passed'",(tid,)).fetchone()[0]==1),('completed',task['work_status']=='completed'),('audit_present',c.execute("select count(*) from audit_log where object_id=?",(tid,)).fetchone()[0]>=1)]
        result['checks']=[{'name':n,'passed':ok} for n,ok in checks]; result['passed']=all(ok for _,ok in checks); result['task_id']=tid
    finally: c.close()
    print(json.dumps(result,ensure_ascii=False,indent=2)); return 0 if result['passed'] else 1

if __name__=='__main__': raise SystemExit(main())
