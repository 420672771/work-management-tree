#!/usr/bin/env python3
"""Send idempotent due-soon/overdue reminders. Run from cron or a scheduler."""
import json, sys
from datetime import datetime
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

def main():
    app.initialize(); c=app.db(); today=datetime.now().date().isoformat(); adapter=app.WeComAdapter(); sent=0; skipped=0
    try:
        for raw in c.execute("SELECT * FROM task WHERE work_status NOT IN ('completed','cancelled') ORDER BY due_at"):
            task=app.task_view(c,raw)
            kind='overdue' if task['time_status']=='overdue' else ('due_soon' if task['time_status']=='due_soon' else None)
            if not kind: continue
            owner=c.execute('SELECT id,name,wecom_userid FROM user WHERE id=?',(task['owner_id'],)).fetchone()
            if not owner or not owner['wecom_userid']:
                skipped+=1; continue
            try:
                c.execute('INSERT INTO notify_log(task_id,user_id,kind,notify_day,status,created_at) VALUES(?,?,?,?,?,?)',(task['id'],owner['id'],kind,today,'pending',app.now()))
            except Exception:
                skipped+=1; continue
            text=('【超期提醒】' if kind=='overdue' else '【临期提醒】')+f"\n任务：{task['title']}\n截止日期：{task['due_at'][:10]}\n交付物：{task['deliverable']}\n请回复：任务#{task['id']} + 进度/风险。"
            try:
                result=adapter.send(owner['wecom_userid'],text); status='sent' if result.get('sent') else 'dry-run'
                c.execute('UPDATE notify_log SET status=?,response_json=? WHERE task_id=? AND user_id=? AND kind=? AND notify_day=?',(status,json.dumps(result,ensure_ascii=False),task['id'],owner['id'],kind,today)); sent+=1
            except Exception as exc:
                c.execute('UPDATE notify_log SET status=?,response_json=? WHERE task_id=? AND user_id=? AND kind=? AND notify_day=?',('failed',str(exc),task['id'],owner['id'],kind,today)); skipped+=1
        c.commit()
    finally: c.close()
    print(json.dumps({'sent_or_dry_run':sent,'skipped':skipped,'configured':adapter.configured},ensure_ascii=False))

if __name__=='__main__': main()
