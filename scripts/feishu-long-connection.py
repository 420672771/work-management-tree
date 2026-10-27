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

def save_draft(c, sender, user, text, source_type, event_id):
    slots,missing=app.draft_slots(text,user[0] if user else None)
    expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
    state='collecting' if missing else 'preview'
    c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
              (f'feishu:{sender}:{event_id}',user[0] if user else None,source_type,text,json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),state,expire,app.now(),app.now()))
    return slots,missing

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
        text=''; material=None; message_type=message.get('message_type')
        if message_type=='text':
            try: text=json.loads(message.get('content','{}')).get('text','')
            except Exception: text=message.get('content','')
        elif message_type in ('file','audio','media'):
            try: content=json.loads(message.get('content','{}'))
            except Exception: content={}
            material={'type':message_type,'file_key':content.get('file_key',''),'name':content.get('file_name') or content.get('name') or ''}
        if (text or material) and sender:
            if material:
                kind='飞书语音' if material['type']=='audio' else '飞书文件'
                source_type='feishu_audio' if material['type']=='audio' else 'feishu_document'
                title=f"{kind}材料：{material['name'] or '未命名材料'}"
                extracted=''
                if material['file_key']:
                    try:
                        blob=app.FeishuAdapter().download_message_resource(message.get('message_id',''),material['file_key'])
                        extracted=app.ai_audio_transcribe(blob,material['name'] or 'feishu-audio.ogg','audio/ogg') if material['type']=='audio' else app.extract_uploaded_text(blob,material['name'])
                    except Exception as exc:
                        print(f'Feishu material fetch failed: {exc}', file=sys.stderr)
                slots,missing=save_draft(c,sender,user,extracted or title,source_type,event_id)
                if not user:
                    reply='已收取'+kind+'，但当前飞书账号尚未绑定系统成员，请先完成成员绑定后再确认任务。'
                elif extracted:
                    reply='已提取文件内容并生成任务草稿，请在工作管理树的确认中心补全并确认。'
                else:
                    reply='已收取'+kind+'并生成待解析草稿。请在工作管理树的确认中心补充任务内容和截止日期后确认。'
            else:
                uid=user[0] if user else None; session_id=f'feishu:{sender}'
                if text.strip() in ('确认','确认执行','好的确认','好的，确认','是'):
                    pending=c.execute("SELECT * FROM bot_pending_action WHERE session_id=? AND user_id IS ? AND status='pending' AND expire_at>? ORDER BY id DESC LIMIT 1",(session_id,uid,app.now())).fetchone()
                    if not pending: reply='没有待确认的操作。请先告诉我需要执行什么。'
                    else:
                        try:
                            reply=app.execute_bot_action(c,json.loads(pending['action_json']),uid); c.execute("UPDATE bot_pending_action SET status='executed',confirmed_at=? WHERE id=?",(app.now(),pending['id']))
                        except (ValueError,PermissionError) as exc:
                            reply='操作未执行：'+str(exc); c.execute("UPDATE bot_pending_action SET status='failed',confirmed_at=? WHERE id=?",(app.now(),pending['id']))
                elif text.strip() in ('取消','取消操作','不确认','否'):
                    c.execute("UPDATE bot_pending_action SET status='cancelled',confirmed_at=? WHERE session_id=? AND user_id IS ? AND status='pending'",(app.now(),session_id,uid)); reply='已取消待执行操作。'
                else:
                    action,reply=app.plan_bot_action(c,text,uid,session_id)
                    progress,error=app.resolve_progress_message(c,text,uid) if not action else (None,None)
                    if action: pass
                    elif progress:
                        try:
                            _,_,task=app.submit_progress(c,progress['task_id'],uid,progress['percent'],progress['comment'],progress['blocked'],progress['block_reason'],progress['expected_finish'],'feishu')
                            reply=f"已记录《{task['title']}》进度 {progress['percent']}%，已提交上级确认。"
                        except (ValueError,PermissionError) as exc: reply='进度提交未成功：'+str(exc)
                    elif error: reply=error
                    elif app.re.search(r'(?:我的|今天|今日).{0,4}(?:任务|待办)|(?:任务|待办).{0,4}(?:清单|查询)',text): reply=app.feishu_task_list(c,uid)
                    elif app.is_task_creation_request(text):
                        slots,missing=save_draft(c,sender,user,text,'feishu',event_id)
                        domain_hint=('。建议责任域：'+slots['domain_name']) if slots.get('org_node_id') else '。责任域暂未自动确定，请在网页确认时选择'
                        reply='已识别新任务草稿：'+slots['title']+domain_hint+('。请补充：'+app.friendly_slot_labels(missing) if missing else '。请在网页确认生成。')
                    else:
                        reply='我可以协助你：查询待办、填报进度、新建/修改/删除任务、验收、变更责任人和设置确认代理。涉及变更操作时，我会先回显，回复“确认”后执行。'
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
