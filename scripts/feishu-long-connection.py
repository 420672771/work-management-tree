#!/usr/bin/env python3
"""Receive Feishu events via WebSocket; no public callback URL is required."""
import json, os, sys
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
            slots,missing=app.draft_slots(text,user[0] if user else None)
            reply='已识别任务草稿：'+slots['title']+('。请补充：'+'、'.join(missing) if missing else '。请在网页确认生成。')
            app.FeishuAdapter().send_text(sender,reply)
        c.commit()
    finally: c.close()

if not os.getenv('FEISHU_APP_ID') or not os.getenv('FEISHU_APP_SECRET'):
    raise SystemExit('FEISHU_APP_ID and FEISHU_APP_SECRET are required')
handler=lark.EventDispatcherHandler.builder('', '').register_p2_im_message_receive_v1(receive).build()
lark.ws.Client(os.environ['FEISHU_APP_ID'],os.environ['FEISHU_APP_SECRET'],event_handler=handler).start()
