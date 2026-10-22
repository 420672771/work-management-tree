#!/usr/bin/env python3
"""Receive Feishu events via WebSocket; no public callback URL is required."""
import json, os, sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

try:
    import lark_oapi as lark
except ImportError as exc:
    raise SystemExit('Install dependencies: pip install -r requirements.txt') from exc

def receive(data):
    raw=json.loads(lark.JSON.marshal(data)); event=raw.get('event',raw)
    message=event.get('message',{}); sender=(event.get('sender') or {}).get('sender_id',{}).get('open_id')
    event_id=(raw.get('header') or {}).get('event_id') or message.get('message_id')
    if not event_id: return
    c=app.db()
    try:
        user=c.execute('SELECT id FROM user WHERE feishu_open_id=?',(sender,)).fetchone() if sender else None
        try: c.execute('INSERT INTO im_message_log(channel,external_msg_id,user_id,payload_json,created_at) VALUES(?,?,?,?,?)',('feishu_ws',event_id,user[0] if user else None,json.dumps(raw,ensure_ascii=False),app.now()))
        except Exception: return
        text=''
        if message.get('message_type')=='text':
            try: text=json.loads(message.get('content','{}')).get('text','')
            except Exception: text=message.get('content','')
        if text and sender:
            progress=app.parse_progress_message(text)
            if progress:
                if not user:
                    reply='已识别为任务进度，但当前飞书账号尚未绑定系统成员，请先完成成员绑定。'
                elif progress['percent'] is None:
                    reply=f"已识别任务#{progress['task_id']}。请补充完成比例，例如：任务#{progress['task_id']} 完成 60%。"
                else:
                    try:
                        _,_,task=app.submit_progress(c,progress['task_id'],user[0],progress['percent'],progress['comment'],progress['blocked'],progress['block_reason'],progress['expected_finish'],'feishu')
                        reply=f"已提交《{task['title']}》进度 {progress['percent']}%，等待上级确认后生效。"
                    except PermissionError:
                        reply='该任务的进度只能由任务责任人提交。'
                    except ValueError as exc:
                        reply='进度提交未成功：'+str(exc)
            else:
                slots,missing=app.draft_slots(text,user[0] if user else None)
                expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
                state='collecting' if missing else 'preview'
                c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
                          (f'feishu:{sender}',user[0] if user else None,'feishu',text,json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),state,expire,app.now(),app.now()))
                if user:
                    missing_text=app.friendly_slot_labels(missing)
                    reply='已识别任务草稿：'+slots['title']+('。请补充：'+missing_text if missing else '。已进入待确认草稿，请在工作管理树中确认生成。')
                else:
                    reply='已识别任务草稿：'+slots['title']+'。当前飞书账号尚未绑定系统成员，请管理员在“组织配置 → 飞书成员绑定”中完成绑定；绑定后再次发送即可自动带入责任人。'
            # Persist the event and draft before calling the remote send API. A transient
            # Feishu send failure must not cause the event receiver to fail or retry-create.
            c.commit()
            try:
                app.FeishuAdapter().send_text(sender,reply)
            except Exception as exc:
                print(f'Feishu reply failed: {exc}', file=sys.stderr)
        else:
            c.commit()
    finally: c.close()

if not os.getenv('FEISHU_APP_ID') or not os.getenv('FEISHU_APP_SECRET'):
    raise SystemExit('FEISHU_APP_ID and FEISHU_APP_SECRET are required')
handler=lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(receive).build()
lark.ws.Client(os.environ['FEISHU_APP_ID'],os.environ['FEISHU_APP_SECRET'],event_handler=handler).start()
