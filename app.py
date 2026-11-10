import base64, gzip, hashlib, io, json, mimetypes, os, re, secrets, sqlite3, struct, subprocess, sys, tempfile, urllib.request, urllib.parse, zipfile
from base64 import b64decode
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET
import weekly_reports
import monthly_reports
import accounts
import routines
import assistant_management
import assistant_dialogue
import responsibility_domains

ROOT = Path(__file__).parent
DB_PATH = Path(os.getenv("WMT_DB", ROOT / "data" / "worktree.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_node (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, node_type TEXT NOT NULL, name TEXT NOT NULL, boundary TEXT DEFAULT '', owner_id INTEGER, domain_code TEXT DEFAULT '', operating_line TEXT DEFAULT '', effective_at TEXT, inactive_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, department TEXT DEFAULT '', role TEXT DEFAULT 'member', wecom_userid TEXT UNIQUE);
CREATE TABLE IF NOT EXISTS task (id INTEGER PRIMARY KEY AUTOINCREMENT, org_node_id INTEGER, title TEXT NOT NULL, description TEXT DEFAULT '', owner_id INTEGER NOT NULL, due_at TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'P1', work_status TEXT NOT NULL DEFAULT 'assigned', risk_status TEXT NOT NULL DEFAULT 'normal', deliverable TEXT DEFAULT '', source TEXT DEFAULT 'manual', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, user_id INTEGER, content TEXT NOT NULL, parsed_due_at TEXT, progress TEXT DEFAULT '', risk TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS acceptance (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, acceptor_id INTEGER, result TEXT NOT NULL, reason TEXT DEFAULT '', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log (id INTEGER PRIMARY KEY AUTOINCREMENT, object_type TEXT NOT NULL, object_id INTEGER NOT NULL, action TEXT NOT NULL, actor TEXT NOT NULL, before_json TEXT, after_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS wecom_message (id INTEGER PRIMARY KEY AUTOINCREMENT, userid TEXT NOT NULL, msg_type TEXT NOT NULL, content TEXT DEFAULT '', task_id INTEGER, received_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS notify_log (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, user_id INTEGER NOT NULL, kind TEXT NOT NULL, notify_day TEXT NOT NULL, status TEXT NOT NULL, response_json TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(task_id,user_id,kind,notify_day));
CREATE TABLE IF NOT EXISTS org_department (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, parent_id INTEGER, owner_user_id INTEGER, status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS org_position (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, dept_id INTEGER, level TEXT DEFAULT '', default_role TEXT DEFAULT 'member', status TEXT NOT NULL DEFAULT 'active');
CREATE TABLE IF NOT EXISTS user_position (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, position_id INTEGER NOT NULL, is_primary INTEGER NOT NULL DEFAULT 1, valid_from TEXT, valid_to TEXT);
CREATE TABLE IF NOT EXISTS reporting_line (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, direct_leader_id INTEGER, approver_id INTEGER, dotted_leader_id INTEGER, effective_from TEXT NOT NULL, effective_to TEXT, UNIQUE(user_id,effective_from));
CREATE TABLE IF NOT EXISTS delegate (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, delegate_user_id INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT 'approval', start_at TEXT NOT NULL, end_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS progress_record (id INTEGER PRIMARY KEY AUTOINCREMENT, task_id INTEGER NOT NULL, reported_percent INTEGER NOT NULL, report_text TEXT NOT NULL, is_blocked INTEGER NOT NULL DEFAULT 0, block_reason TEXT DEFAULT '', expected_finish TEXT, status TEXT NOT NULL DEFAULT 'pending', reported_by INTEGER, reported_at TEXT NOT NULL, confirmed_by INTEGER, confirmed_at TEXT, effective_percent INTEGER, source TEXT NOT NULL DEFAULT 'web');
CREATE TABLE IF NOT EXISTS approval_record (id INTEGER PRIMARY KEY AUTOINCREMENT, biz_type TEXT NOT NULL, biz_id INTEGER NOT NULL, task_id INTEGER, approver_id INTEGER, due_at TEXT, status TEXT NOT NULL DEFAULT 'pending', result_reason TEXT DEFAULT '', operated_by INTEGER, created_at TEXT NOT NULL, operated_at TEXT);
CREATE TABLE IF NOT EXISTS task_template (id INTEGER PRIMARY KEY AUTOINCREMENT, org_node_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', owner_id INTEGER, deliverable TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'P1', recurrence TEXT NOT NULL, due_offset_days INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', review_at TEXT, created_at TEXT NOT NULL, assignment_type TEXT NOT NULL DEFAULT 'person', position_id INTEGER, owner_ids_json TEXT);
CREATE TABLE IF NOT EXISTS task_instance (id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL, instance_date TEXT NOT NULL, owner_id INTEGER NOT NULL, task_id INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(template_id,instance_date,owner_id));
CREATE TABLE IF NOT EXISTS daily_report_status (id INTEGER PRIMARY KEY AUTOINCREMENT, report_date TEXT NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, task_count INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'system', created_at TEXT NOT NULL, UNIQUE(report_date,user_id));
CREATE TABLE IF NOT EXISTS job_run_log (id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT NOT NULL, run_key TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(job_name,run_key));
CREATE TABLE IF NOT EXISTS conversation_draft (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, user_id INTEGER, source_type TEXT NOT NULL DEFAULT 'text', raw_text TEXT NOT NULL, slots_json TEXT NOT NULL DEFAULT '{}', missing_slots TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'collecting', expire_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS im_message_log (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, external_msg_id TEXT NOT NULL, user_id INTEGER, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(channel,external_msg_id));
CREATE TABLE IF NOT EXISTS feishu_binding_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, action TEXT NOT NULL, old_open_id TEXT DEFAULT '', new_open_id TEXT DEFAULT '', operator TEXT DEFAULT 'local', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feishu_profile (open_id TEXT PRIMARY KEY, display_name TEXT DEFAULT '', avatar_url TEXT DEFAULT '', fetched_at TEXT NOT NULL, fetch_error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS bot_pending_action (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, user_id INTEGER, action_json TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending', expire_at TEXT NOT NULL, created_at TEXT NOT NULL, confirmed_at TEXT);
CREATE TABLE IF NOT EXISTS bot_task_context (session_id TEXT PRIMARY KEY, user_id INTEGER, task_id INTEGER NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled_report (id INTEGER PRIMARY KEY AUTOINCREMENT, created_by INTEGER NOT NULL, receiver_id INTEGER NOT NULL, scope TEXT NOT NULL DEFAULT 'self', frequency TEXT NOT NULL DEFAULT 'daily', schedule_time TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scheduled_report_run (id INTEGER PRIMARY KEY AUTOINCREMENT, schedule_id INTEGER NOT NULL, run_date TEXT NOT NULL, sent_at TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}', UNIQUE(schedule_id,run_date));
CREATE TABLE IF NOT EXISTS performance_config (config_key TEXT PRIMARY KEY, config_json TEXT NOT NULL, updated_at TEXT NOT NULL, updated_by INTEGER);
CREATE TABLE IF NOT EXISTS performance_adjustment (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, period_start TEXT NOT NULL, period_end TEXT NOT NULL, score_delta REAL NOT NULL DEFAULT 0, reason TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_state (state_hash TEXT PRIMARY KEY, expire_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_session (session_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expire_at TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS test_issue (id INTEGER PRIMARY KEY AUTOINCREMENT, record_type TEXT NOT NULL DEFAULT 'issue', title TEXT NOT NULL, module TEXT NOT NULL DEFAULT '', severity TEXT NOT NULL DEFAULT 'normal', reporter TEXT NOT NULL DEFAULT '', steps TEXT DEFAULT '', expected_result TEXT DEFAULT '', actual_result TEXT NOT NULL DEFAULT '', task_ref TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created_at TEXT NOT NULL);
"""

def now(): return datetime.now(timezone.utc).isoformat()
def feishu_login_enabled(): return os.getenv('WMT_AUTH_MODE','').lower()=='feishu'
def login_required(): return os.getenv('WMT_AUTH_MODE','demo').lower() in ('account','feishu')
def monthly_demo_enabled():
    return (os.getenv('WMT_AUTH_MODE','demo').lower()=='demo' and
            os.getenv('WMT_MONTHLY_DEMO','').lower() in ('1','true','yes'))
def feishu_redirect_uri():
    configured=os.getenv('WMT_FEISHU_OAUTH_REDIRECT_URI','').strip()
    if configured: return configured
    base=os.getenv('WMT_PUBLIC_BASE_URL','').strip().rstrip('/')
    return base+'/auth/feishu/callback' if base else ''
def db():
    c = sqlite3.connect(DB_PATH, timeout=10); c.row_factory = sqlite3.Row
    c.execute('PRAGMA foreign_keys=ON')
    c.execute('PRAGMA busy_timeout=10000')
    return c

def migrate(c):
    """Schema work must run once at service startup, never on every request."""
    c.executescript(SCHEMA + weekly_reports.SCHEMA + monthly_reports.SCHEMA + accounts.SCHEMA + responsibility_domains.SCHEMA + assistant_dialogue.SCHEMA)
    for statement in ("ALTER TABLE login_state ADD COLUMN flow_type TEXT NOT NULL DEFAULT 'login'", "ALTER TABLE login_state ADD COLUMN user_id INTEGER", "ALTER TABLE login_state ADD COLUMN session_hash TEXT", "ALTER TABLE user ADD COLUMN feishu_open_id TEXT", "ALTER TABLE org_node ADD COLUMN domain_code TEXT DEFAULT ''", "ALTER TABLE org_node ADD COLUMN operating_line TEXT DEFAULT ''", "ALTER TABLE task ADD COLUMN task_type TEXT NOT NULL DEFAULT 'PROJECT'", "ALTER TABLE task ADD COLUMN parent_template_id INTEGER", "ALTER TABLE task ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'approved'", "ALTER TABLE task ADD COLUMN approver_id INTEGER", "ALTER TABLE task ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN effective_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN expected_finish TEXT", "ALTER TABLE task ADD COLUMN confirmed_by INTEGER", "ALTER TABLE task ADD COLUMN confirmed_at TEXT", "ALTER TABLE task ADD COLUMN confirm_source TEXT", "ALTER TABLE task ADD COLUMN estimated_hours REAL NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN actual_hours REAL NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN complexity TEXT NOT NULL DEFAULT 'general'", "ALTER TABLE task ADD COLUMN business_impact TEXT DEFAULT ''", "ALTER TABLE task ADD COLUMN urgent_source TEXT DEFAULT ''", "ALTER TABLE task ADD COLUMN urgent_level TEXT DEFAULT ''", "ALTER TABLE task ADD COLUMN plan_impact TEXT DEFAULT ''", "ALTER TABLE test_issue ADD COLUMN record_type TEXT NOT NULL DEFAULT 'issue'"):
        try: c.execute(statement)
        except sqlite3.OperationalError: pass
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS user_feishu_identity ON user(feishu_open_id) WHERE feishu_open_id IS NOT NULL AND feishu_open_id<>''")
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=NORMAL')
    routines.migrate(c)
    responsibility_domains.install_guards(c)
def rowdict(row): return dict(row) if row else None
def audit(c, typ, oid, action, actor, before=None, after=None):
    c.execute("INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)", (typ, oid, action, actor, json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False) if after else None, now()))
def mask_external_id(value):
    value=str(value or '')
    return ('••••'+value[-4:]) if len(value)>4 else ('••••' if value else '')
def record_feishu_binding(c, user_id, action, old_open_id='', new_open_id='', operator='local'):
    return c.execute('INSERT INTO feishu_binding_history(user_id,action,old_open_id,new_open_id,operator,created_at) VALUES(?,?,?,?,?,?)',(user_id,action,old_open_id or '',new_open_id or '',operator,now())).lastrowid
def feishu_profile_for(c, open_id, refresh=False):
    cached=c.execute('SELECT display_name,avatar_url FROM feishu_profile WHERE open_id=?',(open_id,)).fetchone()
    if cached and not refresh: return rowdict(cached)
    try:
        profile=FeishuAdapter().get_user_profile(open_id)
        value={'display_name':profile.get('name',''),'avatar_url':profile.get('avatar_url','')}
        c.execute("INSERT INTO feishu_profile(open_id,display_name,avatar_url,fetched_at,fetch_error) VALUES(?,?,?,?,?) ON CONFLICT(open_id) DO UPDATE SET display_name=excluded.display_name,avatar_url=excluded.avatar_url,fetched_at=excluded.fetched_at,fetch_error=excluded.fetch_error",(open_id,value['display_name'],value['avatar_url'],now(),''))
        return value
    except Exception as exc:
        c.execute("INSERT INTO feishu_profile(open_id,display_name,avatar_url,fetched_at,fetch_error) VALUES(?,?,?,?,?) ON CONFLICT(open_id) DO UPDATE SET fetched_at=excluded.fetched_at,fetch_error=excluded.fetch_error",(open_id,'','',now(),str(exc)[:200]))
        return rowdict(cached) if cached else {'display_name':'','avatar_url':''}
def ensure_feishu_binding_history(c):
    """Backfill one auditable baseline record for bindings that predate the ledger."""
    for user in c.execute("SELECT id,feishu_open_id FROM user WHERE feishu_open_id IS NOT NULL AND feishu_open_id<>''"):
        exists=c.execute('SELECT 1 FROM feishu_binding_history WHERE user_id=? LIMIT 1',(user['id'],)).fetchone()
        if not exists: record_feishu_binding(c,user['id'],'baseline','',user['feishu_open_id'],'system')
def approver_for(c, user_id):
    line=c.execute("SELECT approver_id,direct_leader_id FROM reporting_line WHERE user_id=? AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC LIMIT 1",(user_id,now())).fetchone()
    approver=(line['approver_id'] or line['direct_leader_id']) if line and (line['approver_id'] or line['direct_leader_id']) else None
    if not approver:
        manager=c.execute("SELECT id FROM user WHERE role='manager' ORDER BY id LIMIT 1").fetchone()
        approver=manager[0] if manager else None
    # The top-level manager must not be made their own approver.
    if approver and int(approver)==int(user_id):
        return None
    if approver:
        delegate=c.execute("SELECT delegate_user_id FROM delegate WHERE user_id=? AND scope IN ('approval','all') AND start_at<=? AND end_at>=? ORDER BY id DESC LIMIT 1",(approver,now(),now())).fetchone()
        if delegate: return delegate[0]
    return approver
def create_approval(c, biz_type, biz_id, task_id, approver_id):
    if not approver_id:
        return None
    return c.execute("INSERT INTO approval_record(biz_type,biz_id,task_id,approver_id,due_at,created_at) VALUES(?,?,?,?,?,?)",(biz_type,biz_id,task_id,approver_id,now(),now())).lastrowid

TASK_PRIORITIES={'P0','P1','P2'}
TASK_TYPES={'ROUTINE','PROJECT','ADHOC'}

def valid_due_at(value):
    if not isinstance(value,str) or not value.strip():
        return None
    try:
        parsed=datetime.fromisoformat(value.strip().replace('Z','+00:00'))
        return parsed.isoformat()
    except (ValueError, TypeError):
        return None

def validate_task_fields(c, payload, *, require_all=False):
    """Validate data before it can enter the task table.

    Returns a compact normalized dict or raises ValueError with an API-safe message.
    """
    title=str(payload.get('title','')).strip()
    if require_all and not title: raise ValueError('任务标题不能为空')
    if title and len(title)>200: raise ValueError('任务标题不能超过 200 个字符')
    description=str(payload.get('description','') or '').strip()
    if len(description)>20000: raise ValueError('任务详情不能超过 20000 个字符')
    deliverable=str(payload.get('deliverable','') or '').strip()
    if len(deliverable)>5000: raise ValueError('交付物/验收标准不能超过 5000 个字符')
    priority=payload.get('priority','P1')
    if priority not in TASK_PRIORITIES: raise ValueError('优先级仅支持 P0、P1、P2')
    task_type=payload.get('task_type','PROJECT')
    if task_type not in TASK_TYPES: raise ValueError('任务类型无效')
    due=valid_due_at(payload.get('due_at')) if ('due_at' in payload or require_all) else None
    if require_all and not due: raise ValueError('截止日期格式无效，请使用 YYYY-MM-DD')
    owner_id=payload.get('owner_id')
    if require_all and not owner_id: raise ValueError('任务责任人不能为空')
    if owner_id and not c.execute('SELECT 1 FROM user WHERE id=?',(owner_id,)).fetchone():
        raise ValueError('任务责任人不存在')
    org_node_id=payload.get('org_node_id')
    if org_node_id not in (None,'') and not c.execute('SELECT 1 FROM org_node WHERE id=? AND inactive_at IS NULL',(org_node_id,)).fetchone():
        raise ValueError('挂载责任域不存在或已停用')
    return {'title':title,'description':description,'deliverable':deliverable,'priority':priority,'task_type':task_type,'due_at':due,'owner_id':owner_id,'org_node_id':org_node_id}
def generate_routines(c, instance_date=None, current_time=None, template_ids=None):
    created=routines.generate(c,instance_date,current_time,approver_for,template_ids)
    for tid in created:
        task=c.execute('SELECT parent_template_id,routine_week_start,owner_id,routine_position_id FROM task WHERE id=?',(tid,)).fetchone()
        audit(c,'task',tid,'routine_generated','system',after={'template_id':task['parent_template_id'],'instance_date':task['routine_week_start'],'owner_id':task['owner_id'],'position_id':task['routine_position_id']})
    return created
def daily_targets(c, report_date=None):
    day=report_date or datetime.now(timezone.utc).date().isoformat(); rows=[]
    for user in c.execute("SELECT id,name FROM user WHERE role<>'manager' ORDER BY id"):
        tasks=[task_view(c,x) for x in c.execute("SELECT * FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled')",(user['id'],))]
        focus=[x for x in tasks if x['work_status']=='in_progress' or x['time_status'] in ('overdue','due_soon') or x['due_at'][:10]==day]
        if focus: rows.append({'user_id':user['id'],'user_name':user['name'],'tasks':focus})
    return rows

def send_feishu_once(c, task_id, user_id, kind, day, text):
    """Send one user-facing notification at most once per task/kind/day."""
    if c.execute('SELECT 1 FROM notify_log WHERE task_id=? AND user_id=? AND kind=? AND notify_day=?',(task_id,user_id,kind,day)).fetchone():
        return 'skipped_duplicate'
    user=c.execute('SELECT feishu_open_id FROM user WHERE id=?',(user_id,)).fetchone()
    if not user or not user['feishu_open_id']:
        status,response='skipped_no_binding',{'reason':'member is not bound to Feishu'}
    else:
        try:
            response=FeishuAdapter().send_text(user['feishu_open_id'],text)
            status='sent' if response.get('sent') else response.get('mode','failed')
        except Exception as exc:
            status,response='failed',{'reason':str(exc)}
    c.execute("INSERT INTO notify_log(task_id,user_id,kind,notify_day,status,response_json,created_at) VALUES(?,?,?,?,?,?,?)",(task_id,user_id,kind,day,status,json.dumps(response,ensure_ascii=False),now()))
    return status

def run_daily_jobs(c, report_date=None, kind='all'):
    day=report_date or datetime.now(timezone.utc).date().isoformat(); out={'date':day,'morning':0,'evening':0,'redboard':0,'approval_reminders':0,'sent':0,'skipped':0,'failed':0}
    def count_delivery(status):
        if status=='sent': out['sent']+=1
        elif status=='failed': out['failed']+=1
        else: out['skipped']+=1
    if kind in ('all','morning'):
        targets=daily_targets(c,day); out['morning']=len(targets)
        for target in targets:
            lines='\n'.join(f"{i+1}. #{task['id']} {task['title']}（截止 {task['due_at'][:10]}）" for i,task in enumerate(target['tasks'][:5]))
            status=send_feishu_once(c,target['tasks'][0]['id'],target['user_id'],'morning_digest',day,'【今日任务清单】\n'+lines+'\n请按计划推进，并在有进展时填报进度。'); count_delivery(status)
    if kind in ('all','evening','close'):
        for target in daily_targets(c,day):
            reported=c.execute("SELECT 1 FROM progress_record WHERE reported_by=? AND substr(reported_at,1,10)=? LIMIT 1",(target['user_id'],day)).fetchone()
            status='reported' if reported else 'unreported'; c.execute("INSERT OR REPLACE INTO daily_report_status(report_date,user_id,status,task_count,created_at) VALUES(?,?,?,?,?)",(day,target['user_id'],status,len(target['tasks']),now()))
            if kind!='close': out['evening']+=1
            if status=='unreported':
                out['redboard']+=1
                lines='、'.join(f"#{task['id']} {task['title']}" for task in target['tasks'][:3])
                delivery=send_feishu_once(c,target['tasks'][0]['id'],target['user_id'],'evening_progress',day,'【进度填报提醒】\n请更新今日重点任务进度：'+lines+'\n示例：任务#编号 完成 60%。'); count_delivery(delivery)
    if kind in ('all','approval'):
        from datetime import timedelta
        hours=int(os.getenv('WMT_PROGRESS_AUTO_CONFIRM_HOURS','24')); cutoff=(datetime.now(timezone.utc)-timedelta(hours=hours)).isoformat(); auto=0
        for approval in c.execute("SELECT * FROM approval_record WHERE status='pending' AND biz_type='progress_confirm' AND created_at<=?",(cutoff,)).fetchall():
            record=c.execute('SELECT * FROM progress_record WHERE id=?',(approval['biz_id'],)).fetchone()
            if not record: continue
            c.execute("UPDATE approval_record SET status='approved',result_reason=?,operated_at=? WHERE id=?",('超时自动生效',now(),approval['id']))
            c.execute("UPDATE progress_record SET status='auto_approved',effective_percent=?,confirmed_at=? WHERE id=?",(record['reported_percent'],now(),record['id']))
            c.execute("UPDATE task SET effective_percent=?,progress_percent=?,risk_status=?,expected_finish=?,work_status=?,confirm_source='timeout_auto',updated_at=? WHERE id=?",(record['reported_percent'],record['reported_percent'],'blocked' if record['is_blocked'] else 'normal',record['expected_finish'],progress_work_status(c,approval['task_id'],record['reported_percent']),now(),approval['task_id']))
            audit(c,'approval',approval['id'],'timeout_auto','system',after={'hours':hours}); auto+=1
        approvals=c.execute("SELECT a.*,t.title FROM approval_record a LEFT JOIN task t ON t.id=a.task_id WHERE a.status='pending'").fetchall()
        out['approval_reminders']=len(approvals); out['auto_confirmed_progress']=auto
        for approval in approvals:
            if not approval['approver_id'] or not approval['task_id']: continue
            title=approval['title'] or '一项工作事项'
            delivery=send_feishu_once(c,approval['task_id'],approval['approver_id'],'approval_reminder',day,'【待确认提醒】\n《'+title+'》等待您的'+('任务确认' if approval['biz_type']=='task_confirm' else '进度确认')+'，请在工作管理树确认中心处理。'); count_delivery(delivery)
    c.execute("INSERT OR REPLACE INTO job_run_log(job_name,run_key,result_json,created_at) VALUES(?,?,?,?)",('daily_'+kind,day,json.dumps(out,ensure_ascii=False),now()))
    return out
def explicit_assignment_draft(text, owner_id=None, c=None):
    """Extract the last explicit assignment from a long meeting/chat record.

    Long materials commonly contain a background or a report first and a
    manager's directive at the end.  In that situation the directive is more
    reliable than a whole-document summary: only its assignee, deadline,
    deliverable and priority are allowed to enter the task draft.
    """
    source=strip_decorative_emoji(text).strip()
    if not source:
        return None
    own_connection=c is None; c=c or db()
    try:
        candidates=[]
        for user in c.execute('SELECT id,name FROM user ORDER BY length(name) DESC,id'):
            name=re.escape(user['name'])
            # Covers both “请钱缘……” and “现需要汪文娟在……完成……”.
            pattern=rf'(?:现(?:在)?需要|需要|请|安排|交由|由|委托)\s*(?:@|＠)?\s*{name}(?=\s|[，,。；;]|在|于|基于|负责|牵头|完成|推进|落实|组织|开展|协同)'
            for match in re.finditer(pattern,source):
                candidates.append((match.start(),match,user))
        if not candidates:
            return None
        _, match, user=max(candidates,key=lambda item:item[0])
        # Use the sentence that starts with the assignment; preceding text is
        # context only and must never pollute the generated title/detail.
        end=re.search(r'[。；;\n]',source[match.start():])
        sentence=source[match.start():match.start()+end.start()] if end else source[match.start():]
        if not re.search(r'(?:完成|负责|牵头|推进|落实|组织|开展|协同|提交|发出|发送|发布|编写|整理|评审|测试|部署|巡检|检查|跟进|处理)',sentence):
            return None
        work=re.search(r'(?:完成|负责|牵头|推进|落实|组织|开展|协同|提交|发出|发送|发布|编写|整理|评审|测试|部署|巡检|检查|跟进|处理)\s*(.+?)(?=(?:[，,、]\s*(?:输出物|交付物|产出|提交物|优先级))|$)',sentence)
        if not work:
            return None
        title=concise_task_title(work.group(1))
        title=re.sub(r'^(?:基于(?:以上|上述|前述)?背景[，,、]?\s*)','',title)
        title=re.sub(r'^(?:相关|以上)\s*','',title).strip(' ，,。；;：:')[:40]
        if not title:
            return None
        delivery=re.search(r'(?:输出物|交付物|产出|提交物)\s*(?:为|是|：|:)?\s*([^，。；;]+)',sentence)
        deliverable=delivery.group(1).strip() if delivery else None
        priority_match=re.search(r'优先级\s*[:：]?\s*(P[012])',sentence,re.I)
        domain=detect_responsibility_domain(c,title) or detect_responsibility_domain(c,sentence)
        task_type='PROJECT' if re.search(r'(?:项目|一期|二期|三期|roadmap|路线图)',sentence,re.I) else 'ADHOC'
        description=title + (f'。交付物：{deliverable}。' if deliverable else '')
        slots={'title':title,'description':description,'due_at':parse_due(sentence),
               'priority':priority_match.group(1).upper() if priority_match else None,
               'owner_id':user['id'],'org_node_id':domain['id'] if domain else None,
               'domain_name':domain['name'] if domain else '', 'task_type':task_type,
               'confidence':0.99,'extraction_mode':'explicit_assignment'}
        return slots,[key for key in ('owner_id','due_at') if not slots.get(key)]
    finally:
        if own_connection: c.close()

def draft_slots(text, owner_id=None, c=None):
    text=strip_decorative_emoji(text)
    own_connection=c is None; c=c or db()
    try:
        explicit=explicit_assignment_draft(text,owner_id,c)
        if explicit:
            return explicit
        draft=ai_draft(text,c)
        instruction=task_instruction_text(text)
        domain=detect_responsibility_domain(c,instruction)
        if not domain and draft.get('suggested_domain'):
            domain=c.execute("SELECT id,name FROM org_node WHERE node_type='domain' AND inactive_at IS NULL AND name=? ORDER BY id LIMIT 1",(draft['suggested_domain'],)).fetchone()
        # An explicitly named owner in the meeting item always takes precedence
        # over the sender/current web user used only as a fallback.
        model_owner=None
        if draft.get('owner_name'):
            model_owner=c.execute('SELECT id FROM user WHERE name=? ORDER BY id LIMIT 1',(draft['owner_name'],)).fetchone()
            model_owner=model_owner['id'] if model_owner else None
        detected_owner=detect_task_owner(c,instruction) or model_owner or owner_id
        # Rule parsing is the stable fallback for an explicit date in meeting notes.
        # It also covers dates that a model may omit because of formatting whitespace.
        priority=draft.get('priority') if draft.get('priority') in ('P0','P1','P2') else None
        task_type=draft.get('task_type') if draft.get('task_type') in ('ROUTINE','PROJECT','ADHOC') else None
        # An explicit date in the source (including “今天下班前”) is factual
        # input and must always win over a model's inferred date.
        source_due=parse_due(instruction) or parse_due(text)
        slots={'title':draft['title'],'description':draft.get('description'),'due_at':source_due or draft.get('due_at'),'priority':priority,'owner_id':detected_owner,'org_node_id':domain['id'] if domain else None,'domain_name':domain['name'] if domain else '','task_type':task_type}
        missing=[k for k in ('owner_id','due_at') if not slots.get(k)]
        return slots,missing
    finally:
        if own_connection: c.close()

def fast_direct_draft(text, owner_id=None, c=None):
    """Parse a short, explicit assignment without waiting for the LLM.

    This deliberately narrow path is for one-line instructions that begin with
    a configured person's name and contain both an actionable verb and a
    concrete deadline. Meeting notes, pasted chats and ambiguous material keep
    using the model-first extraction path below.
    """
    source=strip_decorative_emoji(text).strip()
    if not source or len(source)>160 or '\n' in source or len(re.findall(r'[。；;！？!?]',source))>1:
        return None
    own_connection=c is None; c=c or db()
    try:
        due=parse_due(source)
        owner=detect_task_owner(c,source)
        if not due or not owner:
            return None
        user=c.execute('SELECT name FROM user WHERE id=?',(owner,)).fetchone()
        if not user:
            return None
        # A mention buried in a discussion is not enough: the assignee must be
        # named at the start of this compact instruction.
        if not re.match(r'^\s*(?:请|由)?(?:@|＠)?'+re.escape(user['name']),source):
            return None
        if not re.search(r'(?:完成|发出|发送|提交|发布|编写|整理|评审|测试|部署|巡检|检查|跟进|推进|落实|处理|组织|开展|协同)',source):
            return None
        title=re.sub(r'^\s*(?:请|由)?(?:@|＠)?'+re.escape(user['name'])+r'\s*','',source)
        # Dates already live in a structured field; retaining them in titles
        # makes cards and list rows unnecessarily noisy.
        title=re.sub(r'(?:今天|今日|明天|后天)(?:下班前|内|之前|完成|截止)?','',title)
        title=re.sub(r'(?:(?:本|这|下)周|(?:星期|周))[一二三四五六日天](?:\s*\d{1,2}(?::|：)\d{2})?(?:前|之前|截止|完成)?','',title)
        title=re.sub(r'(?:在|于)?\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日(?:前|之前|截止|完成)?','',title)
        title=re.sub(r'(?:在|于)?\s*\d{1,2}月\s*\d{1,2}(?:日|号)?(?:前|之前|截止|完成)?','',title)
        title=title.strip(' ，,。；;：:')[:40]
        if not title:
            return None
        domain=detect_responsibility_domain(c,source)
        slots={'title':title,'description':None,'due_at':due,'priority':None,
               'owner_id':owner,'org_node_id':domain['id'] if domain else None,
               'domain_name':domain['name'] if domain else '', 'task_type':None,
               'confidence':0.98,'extraction_mode':'fast_rule'}
        return slots,[]
    finally:
        if own_connection: c.close()

def ai_drafts(text, owner_id=None, c=None):
    """Extract one or more independently editable task drafts from a meeting note."""
    own_connection=c is None; c=c or db(); text=strip_decorative_emoji(text)
    try:
        explicit=explicit_assignment_draft(text,owner_id,c)
        if explicit:
            return [explicit]
        # Most day-to-day instructions are short and complete already. Parse
        # those locally so users do not wait on a 10–30 second model call; keep
        # the model for the meeting notes and materials where it adds value.
        fast=fast_direct_draft(text,owner_id,c)
        if fast:
            return [fast]
        if not ai_configured():
            slots,missing=draft_slots(text,owner_id,c); return [(slots,missing)]
        members='、'.join(row['name'] for row in c.execute('SELECT name FROM user ORDER BY name,id'))
        domains='、'.join(row['name'] for row in c.execute("SELECT name FROM org_node WHERE node_type='domain' AND inactive_at IS NULL ORDER BY id"))
        prompt=('将材料拆分为可独立创建的任务草稿。只输出 JSON 对象 {"tasks":[...]}，禁止 Markdown。每个 task 字段为 '
                'title,owner_name,due_at,suggested_domain,priority,task_type,description,confidence。'
                '一条 task 只能有一名最终责任人；按不同责任人、独立交付物、不同截止日期或可独立验收结果拆分。协同人不要成为责任人。'
                '如果材料是多人聊天或会议记录，按每个发言人的语义分别判断：汇报结果、风险说明、背景和已完成事项不是新任务；只提取明确安排、请人推进或尚未完成的待办。'
                '后续管理者的指令优先于前文汇报；责任人只能取该指令中被明确指派或 @ 的系统成员，不能把汇报人、被汇报人或不在成员表中的别名当作责任人。'
                '未明确的字段返回 null，不得编造。owner_name 必须精确从成员中选择：'+members+'。'
                'suggested_domain 必须精确从责任域中选择：'+domains+'。priority 仅 P0/P1/P2；task_type 仅 ROUTINE/PROJECT/ADHOC。'
                '忽略已完成事项、背景和讨论过程。材料：'+text)
        try:
            raw=ai_chat_completion([
                {'role':'system','content':'你是严谨的企业待办提炼器。仅输出合法 JSON，不输出 Markdown 或解释。'},
                {'role':'user','content':prompt}
            ], temperature=0.05, max_tokens=1800, timeout=30)
            value=parse_model_json(raw); items=value.get('tasks',[]) if isinstance(value,dict) else value
            if not isinstance(items,list) or not items: raise ValueError('no drafts')
        except Exception:
            slots,missing=draft_slots(text,owner_id,c); return [(slots,missing)]
        out=[]
        for item in items[:20]:
            if not isinstance(item,dict) or not str(item.get('title') or '').strip(): continue
            domain=c.execute("SELECT id,name FROM org_node WHERE node_type='domain' AND inactive_at IS NULL AND name=? LIMIT 1",(item.get('suggested_domain'),)).fetchone()
            owner=c.execute('SELECT id FROM user WHERE name=? LIMIT 1',(item.get('owner_name'),)).fetchone()
            item_source=str(item.get('source_text') or item.get('description') or item.get('title') or '')
            source_due=parse_due(item_source) or parse_due(text)
            slots={'title':str(item['title']).strip()[:40],'description':item.get('description'),'due_at':source_due or item.get('due_at'),'priority':item.get('priority') if item.get('priority') in ('P0','P1','P2') else None,'owner_id':owner['id'] if owner else owner_id,'org_node_id':domain['id'] if domain else None,'domain_name':domain['name'] if domain else '','task_type':item.get('task_type') if item.get('task_type') in ('ROUTINE','PROJECT','ADHOC') else None,'confidence':item.get('confidence',0.5)}
            out.append((slots,[key for key in ('owner_id','due_at') if not slots.get(key)]))
        if out: return out
        slots,missing=draft_slots(text,owner_id,c); return [(slots,missing)]
    finally:
        if own_connection: c.close()

# Internal storage keys must never be exposed in conversational or UI prompts.
SLOT_LABELS={
    'title':'任务标题', 'description':'任务详情', 'owner_id':'任务责任人', 'due_at':'截止日期',
    'org_node_id':'责任域',
    'priority':'优先级', 'task_type':'任务类型',
}
def friendly_slot_labels(keys):
    return '、'.join(SLOT_LABELS.get(key,'必要信息') for key in keys)
def discard_draft(c, draft_id, actor='local'):
    draft=c.execute('SELECT * FROM conversation_draft WHERE id=?',(draft_id,)).fetchone()
    if not draft or draft['status'] not in ('collecting','preview'):
        return False
    c.execute("UPDATE conversation_draft SET status='discarded',updated_at=? WHERE id=?",(now(),draft_id))
    audit(c,'conversation_draft',draft_id,'discard',actor,after={'status':'discarded'})
    return True
def seed(c):
    if c.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0:
        c.execute("INSERT INTO user(name,department,role) VALUES('科技产品中心负责人','科技产品中心','manager')")
        c.execute("INSERT INTO user(name,department,role) VALUES('部门管理PM','科技产品中心','pm')")
        c.execute("INSERT INTO user(name,department,role) VALUES('待分配成员','科技产品中心','member')")
    if c.execute("SELECT COUNT(*) FROM org_node").fetchone()[0] == 0:
        t=now(); c.execute("INSERT INTO org_node(node_type,name,boundary,effective_at,created_at) VALUES('department','科技产品中心','科技产品中心全部责任域',?,?)",(t,t))
        root=c.execute("INSERT INTO org_node(parent_id,node_type,name,boundary,effective_at,created_at) VALUES(?,?,?,?,?,?)",(c.execute("SELECT id FROM org_node ORDER BY id DESC LIMIT 1").fetchone()[0],'domain','产品与项目管理','负责产品规划、项目交付和跨部门推进',t,t)).lastrowid
    manager=c.execute("SELECT id FROM user WHERE role='manager' ORDER BY id LIMIT 1").fetchone()
    if not c.execute("SELECT 1 FROM org_department LIMIT 1").fetchone():
        c.execute("INSERT INTO org_department(name,owner_user_id,status,created_at) VALUES(?,?,?,?)",('科技产品中心',manager[0] if manager else None,'active',now()))
    if manager:
        for row in c.execute("SELECT id FROM user WHERE id<>?",(manager[0],)):
            if not c.execute("SELECT 1 FROM reporting_line WHERE user_id=? AND effective_to IS NULL",(row[0],)).fetchone(): c.execute("INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)",(row[0],manager[0],manager[0],now()))

RESPONSIBILITY_DOMAINS = (
    ('A1','AI创新线','Agent 应用交付域','钱缘','负责业务场景 Agent 的方案、开发部署、应用支持与效果优化；不负责通用平台、传统系统功能和推广运营。','产品与项目管理'),
    ('A2','AI创新线','AI 平台与技术能力域','闫兴赫','负责 AI 平台、通用组件、技术评测、AI 基础架构逻辑层及技术兜底；不负责具体场景交付和推广运营。','产品规划与需求管理'),
    ('A3','AI创新线','AI 推广与赋能运营域','覃俞淦','负责 AI 工具推广、培训认证、覆盖率与活跃度运营；不负责应用开发和平台建设。','项目交付与质量管理'),
    ('B1','运营保障线','核心业务系统运营保障域','钱缘','负责 ERP、供应链作业、财务结算、风控与供应链金融等传统系统的需求交付和应用运维；不负责基础设施与 AI 平台。','研发与技术平台'),
    ('B2','运营保障线','IT 基础设施与终端运维域','封滘生','负责网络、桌面、设备、机房、账号资产及 AI 算力物理层；不负责业务应用与 AI 平台逻辑层。','数据运营与客户反馈'),
)

DOMAIN_KEYWORDS={
    'A1':('agent','智能体','应用交付','应用开发','场景交付','应用部署','应用支持'),
    'A2':('ai平台','人工智能平台','大模型','模型评测','技术评测','rag','知识库','通用组件','技术架构','基础架构'),
    'A3':('培训','认证','推广','赋能','覆盖率','活跃度','宣导'),
    'B1':('erp','供应链','财务','结算','风控','订单','业务系统','传统系统'),
    'B2':('网络','终端','桌面','设备','机房','账号资产','服务器','基础设施','算力'),
}

def detect_responsibility_domain(c, text):
    """Return a domain only when task wording has a clear, auditable match."""
    content=str(text or '').lower()
    rows=[rowdict(row) for row in c.execute("SELECT id,name,domain_code,boundary FROM org_node WHERE node_type='domain' AND inactive_at IS NULL ORDER BY id")]
    scored=[]
    for row in rows:
        code=(row.get('domain_code') or '').upper(); score=0
        if code and re.search(r'(?<![A-Z0-9])'+re.escape(code.lower())+r'(?![A-Z0-9])',content): score+=10
        if row['name'].lower() in content: score+=10
        for keyword in DOMAIN_KEYWORDS.get(code,()):
            if keyword.lower() in content: score+=3
        if score: scored.append((score,row))
    if not scored: return None
    scored.sort(key=lambda item:(-item[0],item[1]['id']))
    if len(scored)>1 and scored[0][0]-scored[1][0]<2: return None
    return scored[0][1]

def initialize_responsibility_domains(c):
    """Bootstrap the approved five-domain structure without deleting task history."""
    if c.execute("SELECT 1 FROM system_setting WHERE setting_key='responsibility_domains_initialized'").fetchone():return
    # Existing definitions are authoritative; upgrades must not reset edits or
    # recreate deleted seed domains. Only an uninitialized database is seeded.
    if c.execute("SELECT 1 FROM org_node WHERE domain_code<>'' LIMIT 1").fetchone():
        c.execute("INSERT INTO system_setting VALUES('responsibility_domains_initialized','1')");return
    # A department may explicitly maintain its own team-based responsibility tree.
    if c.execute("SELECT 1 FROM org_node WHERE domain_code IN ('BLD','TRN','OPS') LIMIT 1").fetchone(): return
    root=c.execute("SELECT id FROM org_node WHERE node_type='department' ORDER BY id LIMIT 1").fetchone()
    manager=c.execute("SELECT id FROM user WHERE role='manager' ORDER BY id LIMIT 1").fetchone()
    if not root: return
    if manager: c.execute('UPDATE org_node SET owner_id=? WHERE id=?',(manager['id'],root['id']))
    for code,line,name,owner_name,boundary,legacy_name in RESPONSIBILITY_DOMAINS:
        owner=c.execute('SELECT id FROM user WHERE name=? ORDER BY id LIMIT 1',(owner_name,)).fetchone()
        if not owner:
            owner_id=c.execute('INSERT INTO user(name,department,role) VALUES(?,?,?)',(owner_name,'科技产品中心','member')).lastrowid
        else: owner_id=owner['id']
        active_line=c.execute("SELECT id,direct_leader_id,approver_id FROM reporting_line WHERE user_id=? AND effective_to IS NULL ORDER BY effective_from DESC LIMIT 1",(owner_id,)).fetchone()
        if manager and not active_line:
            c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)',(owner_id,manager['id'],manager['id'],now()))
        node=c.execute('SELECT id FROM org_node WHERE domain_code=? ORDER BY id LIMIT 1',(code,)).fetchone()
        if not node: node=c.execute('SELECT id FROM org_node WHERE parent_id=? AND name=? ORDER BY id LIMIT 1',(root['id'],legacy_name)).fetchone()
        if node:
            c.execute('UPDATE org_node SET parent_id=?,node_type=?,name=?,boundary=?,owner_id=?,domain_code=?,operating_line=?,inactive_at=NULL WHERE id=?',(root['id'],'domain',name,boundary,owner_id,code,line,node['id']))
        else:
            c.execute('INSERT INTO org_node(parent_id,node_type,name,boundary,owner_id,domain_code,operating_line,effective_at,created_at) VALUES(?,?,?,?,?,?,?,?,?)',(root['id'],'domain',name,boundary,owner_id,code,line,now(),now()))
    c.execute("INSERT OR IGNORE INTO system_setting VALUES('responsibility_domains_initialized','1')")

def initialize():
    c=db()
    try:
        migrate(c)
        seed(c); c.execute("UPDATE user SET role='member' WHERE role='owner'"); initialize_responsibility_domains(c); ensure_feishu_binding_history(c); normalize_legacy_conversation_tasks(c); c.commit()
    finally:
        c.close()

def task_view(c, r):
    d=rowdict(r); owner=c.execute('SELECT name FROM user WHERE id=?',(d['owner_id'],)).fetchone(); d['owner_name']=owner[0] if owner else '未知责任人'
    approver=c.execute('SELECT name FROM user WHERE id=?',(d.get('approver_id'),)).fetchone() if d.get('approver_id') else None; d['approver_name']=approver[0] if approver else ''
    try:
        due=datetime.fromisoformat(str(d['due_at']).replace('Z','+00:00')); current=datetime.now(timezone.utc)
        d['time_status']='overdue' if due < current and d['work_status'] not in ('completed','cancelled') else ('due_soon' if (due-current).total_seconds()<86400 and d['work_status'] not in ('completed','cancelled') else 'normal')
    except (ValueError, TypeError):
        # Legacy bad data must not make a task-list/detail response crash.
        d['time_status']='invalid_due_date'
    progress=c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at DESC,id DESC LIMIT 1',(d['id'],)).fetchone()
    d['latest_progress']=rowdict(progress)
    acceptance=c.execute('SELECT a.*,u.name AS acceptor_name FROM acceptance a LEFT JOIN user u ON u.id=a.acceptor_id WHERE a.task_id=? ORDER BY a.id DESC LIMIT 1',(d['id'],)).fetchone()
    d['latest_acceptance']=rowdict(acceptance)
    return d

def approval_view(c, r):
    """Return the evidence needed to make a confirmation decision."""
    d=rowdict(r)
    task=c.execute('SELECT * FROM task WHERE id=?',(d.get('task_id'),)).fetchone() if d.get('task_id') else None
    d['task']=task_view(c,task) if task else None
    approver=c.execute('SELECT name FROM user WHERE id=?',(d.get('approver_id'),)).fetchone() if d.get('approver_id') else None
    d['approver_name']=approver['name'] if approver else '未配置'
    if d.get('biz_type')=='progress_confirm' and d.get('biz_id'):
        record=c.execute('SELECT * FROM progress_record WHERE id=?',(d['biz_id'],)).fetchone()
        d['progress']=rowdict(record)
        d['progress_history']=[rowdict(x) for x in c.execute('SELECT * FROM progress_record WHERE task_id=? AND status=? ORDER BY reported_at DESC,id DESC LIMIT 10',(d['task_id'],'approved'))]
    else:
        d['progress']=None; d['progress_history']=[]
    return d

# 绩效只对已确认生效的任务计算。标准工时用于衡量工作量，实际工时只用于负荷与估算偏差分析。
PERFORMANCE_DEFAULTS={
    'priority':{'P0':1.5,'P1':1.2,'P2':1.0},
    'complexity':{'simple':1.0,'general':1.5,'complex':2.0,'major':3.0},
    'type':{'ROUTINE':1.0,'PROJECT':1.2,'ADHOC':1.1},
    'role_weights':{
        'member':{'delivery':40,'quality':25,'timeliness':20,'process':15,'management':0},
        'leader':{'delivery':25,'quality':20,'timeliness':15,'process':10,'management':30},
        'pm':{'delivery':20,'quality':15,'timeliness':20,'process':15,'management':30},
        'manager':{'delivery':0,'quality':0,'timeliness':0,'process':0,'management':100},
    }
}

def performance_config(c):
    row=c.execute("SELECT config_json FROM performance_config WHERE config_key='default'").fetchone()
    if not row: return json.loads(json.dumps(PERFORMANCE_DEFAULTS))
    try:
        value=json.loads(row['config_json'])
        return {**json.loads(json.dumps(PERFORMANCE_DEFAULTS)),**value}
    except Exception: return json.loads(json.dumps(PERFORMANCE_DEFAULTS))

def performance_period(period=None, start=None, end=None):
    if start and end: return str(start)[:10],str(end)[:10]
    raw=str(period or datetime.now(timezone.utc).strftime('%Y-%m'))
    try:
        from calendar import monthrange
        year,month=map(int,raw[:7].split('-')); return f'{year:04d}-{month:02d}-01',f'{year:04d}-{month:02d}-{monthrange(year,month)[1]:02d}'
    except Exception: raise ValueError('period must be YYYY-MM or provide start/end dates')

def position_name_for(c,user_id):
    row=c.execute("SELECT p.name FROM user_position up JOIN org_position p ON p.id=up.position_id WHERE up.user_id=? AND (up.valid_to IS NULL OR up.valid_to>?) ORDER BY up.is_primary DESC,up.id DESC LIMIT 1",(user_id,now())).fetchone()
    return row['name'] if row else ''

def performance_role(c,user):
    if user['role']=='manager': return 'manager'
    if user['role']=='pm': return 'pm'
    position=position_name_for(c,user['id']).lower()
    return 'leader' if 'leader' in position else 'member'

def task_performance(c,task,config=None):
    config=config or performance_config(c); priority=config['priority'].get(task['priority'],1.0)
    complexity=config['complexity'].get(task.get('complexity') or 'general',1.5)
    task_type=config['type'].get(task.get('task_type') or 'PROJECT',1.2)
    fallback={'ROUTINE':0,'PROJECT':8,'ADHOC':4}.get(task.get('task_type') or 'PROJECT',8)
    hours=float(task.get('estimated_hours') or fallback)
    base=round(hours*complexity*priority*task_type,2)
    accepts=[rowdict(x) for x in c.execute('SELECT * FROM acceptance WHERE task_id=? ORDER BY id',(task['id'],))]
    passed=[x for x in accepts if x['result']=='passed']; rejected=[x for x in accepts if x['result']=='rejected']
    completed=task['work_status']=='completed' and bool(passed or task.get('effective_percent')==100)
    actual_at=(passed[-1]['created_at'] if passed else task.get('updated_at','')) if completed else ''
    on_time=bool(completed and actual_at and str(actual_at)[:10]<=str(task['due_at'])[:10])
    quality=1.0 if completed and not rejected else (.8 if completed and len(rejected)==1 else (.6 if completed and rejected else 0))
    days_late=0
    if completed and actual_at:
        try: days_late=max(0,(datetime.fromisoformat(str(actual_at).replace('Z','+00:00')).date()-datetime.fromisoformat(str(task['due_at']).replace('Z','+00:00')).date()).days)
        except Exception: days_late=0
    timeliness=1.0 if on_time else (.9 if days_late<=3 else (.75 if days_late<=7 else (.5 if completed else 0)))
    progress_count=c.execute('SELECT count(*) FROM progress_record WHERE task_id=?',(task['id'],)).fetchone()[0]
    risk_count=c.execute("SELECT count(*) FROM progress_record WHERE task_id=? AND is_blocked=1",(task['id'],)).fetchone()[0]
    process_recorded=bool(task.get('routine_hours_updated_at') or task.get('actual_hours')) if task.get('task_type')=='ROUTINE' else bool(progress_count)
    return {'task_id':task['id'],'base_points':base,'estimated_hours':hours,'actual_hours':float(task.get('actual_hours') or 0),'complexity_factor':complexity,'priority_factor':priority,'type_factor':task_type,'completed':completed,'on_time':on_time,'quality_factor':quality,'timeliness_factor':timeliness,'earned_points':round(base*quality*timeliness,2) if completed else 0,'days_late':days_late,'progress_count':progress_count,'risk_count':risk_count,'process_recorded':process_recorded}

def performance_summary(c,user_id,period=None,start=None,end=None,include_management=True):
    start,end=performance_period(period,start,end); config=performance_config(c)
    user=c.execute('SELECT * FROM user WHERE id=?',(user_id,)).fetchone()
    if not user: raise ValueError('user not found')
    tasks=[rowdict(x) for x in c.execute("SELECT * FROM task WHERE approval_status='approved' AND owner_id=? AND substr(due_at,1,10)>=? AND substr(due_at,1,10)<=? ORDER BY due_at,id",(user_id,start,end))]
    scored=[task_performance(c,x,config) for x in tasks]
    base=sum(x['base_points'] for x in scored); earned=sum(x['earned_points'] for x in scored); completed=[x for x in scored if x['completed']]
    delivery=round(100*earned/base,1) if base else 0
    timeliness=round(100*sum(x['base_points'] for x in completed if x['on_time'])/sum(x['base_points'] for x in completed),1) if completed else 0
    quality=round(100*sum(x['base_points']*x['quality_factor'] for x in completed)/sum(x['base_points'] for x in completed),1) if completed else 0
    process=round(100*sum(1 for x in scored if x['process_recorded'])/len(scored),1) if scored else 0
    role=performance_role(c,user); weights=config['role_weights'][role]
    management=0
    if include_management and role in ('leader','pm','manager'):
        managed=managed_user_ids(c,user_id) if role=='leader' else [x['id'] for x in c.execute("SELECT id FROM user WHERE role<>'manager'")]
        child=[performance_summary(c,uid,start=start,end=end,include_management=False) for uid in managed if uid!=user_id]
        management=round(sum(x['delivery'] for x in child)/len(child),1) if child else 0
    total=round(delivery*weights['delivery']/100+quality*weights['quality']/100+timeliness*weights['timeliness']/100+process*weights['process']/100+management*weights['management']/100,1)
    adjustments=sum(x[0] for x in c.execute('SELECT score_delta FROM performance_adjustment WHERE user_id=? AND period_start=? AND period_end=?',(user_id,start,end)))
    return {'period_start':start,'period_end':end,'user':rowdict(user),'position':position_name_for(c,user_id),'performance_role':role,'weights':weights,'task_count':len(scored),'completed_count':len(completed),'base_points':round(base,2),'earned_points':round(earned,2),'estimated_hours':round(sum(x['estimated_hours'] for x in scored),1),'actual_hours':round(sum(x['actual_hours'] for x in scored),1),'delivery':delivery,'timeliness':timeliness,'quality':quality,'process':process,'management':management,'raw_score':total,'adjustment':round(adjustments,1),'score':round(total+adjustments,1),'evidence':[{**task_performance(c,t,config),**{'title':t['title'],'task_type':t.get('task_type'),'priority':t['priority'],'complexity':t.get('complexity'),'due_at':t['due_at'],'work_status':t['work_status'],'deliverable':t['deliverable'],'routine_week_start':t.get('routine_week_start'),'routine_week_end':t.get('routine_week_end'),'actual_hours_reported':bool(t.get('routine_hours_updated_at') or t.get('actual_hours'))}} for t in tasks]}

def parse_due(text):
    # Meeting minutes commonly write dates as “2026 年 10 月 30 日前”.
    # Remove formatting whitespace first, while keeping the original text intact
    # for title and delivery extraction elsewhere.
    normalized=re.sub(r'\s+','',str(text or ''))
    # “今天下班前/今日完成” is a deadline on the current China business day.
    # Handle it before model inference and before generic weekday parsing.
    if re.search(r'(?:今天|今日)(?:下班前|内|之前|完成|截止)?',normalized):
        china_now=datetime.now(timezone(timedelta(hours=8)))
        return china_now.date().isoformat()+'T17:00:00+08:00'
    m=re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})',normalized)
    if m: return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T17:00:00+08:00'
    # Conversational task changes commonly omit the year: “改到10月28号”。
    month_day=re.search(r'(?<!\d)(\d{1,2})月(\d{1,2})(?:日|号)?',normalized)
    # Chat users often write dates as “10.28” or “10/28”.  Keep this below the
    # full-year pattern so e.g. 2026-10-28 never loses its year.
    if not month_day:
        month_day=re.search(r'(?<!\d)(\d{1,2})[./](\d{1,2})(?!\d)',normalized)
    if month_day:
        today=datetime.now(timezone(timedelta(hours=8))).date(); month,day=map(int,month_day.groups()); year=today.year
        try:
            target=datetime(year,month,day).date()
            if target<today: target=datetime(year+1,month,day).date()
            return target.isoformat()+'T17:00:00+08:00'
        except ValueError:
            return None
    # Both “本周五” and “本周周五” occur frequently in natural-language
    # instructions.  Support the compact form as well as 星期/周五.
    weekday=re.search(r'(?:(本周|这周|下周)([一二三四五六日天])|(?:星期|周)([一二三四五六日天]))',normalized)
    if weekday:
        prefix=weekday.group(1) or ''
        weekday_char=weekday.group(2) or weekday.group(3)
        target_index={'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6}[weekday_char]
        today=datetime.now(timezone(timedelta(hours=8))).date(); target=today-timedelta(days=today.weekday())+timedelta(days=target_index)
        if prefix=='下周' or (not prefix and target<today): target+=timedelta(days=7)
        return target.isoformat()+'T17:00:00+08:00'
    china_today=datetime.now(timezone(timedelta(hours=8))).date()
    if '后天' in normalized:
        return (china_today+timedelta(days=2)).isoformat()+'T17:00:00+08:00'
    if '明天' in normalized:
        return (china_today+timedelta(days=1)).isoformat()+'T17:00:00+08:00'
    days=re.search(r'(\d{1,3})\s*(?:天|日)后',normalized)
    if days:
        return (china_today+timedelta(days=int(days.group(1)))).isoformat()+'T17:00:00+08:00'
    if re.search(r'(?:本月|这个月)底',normalized):
        next_month=(china_today.replace(day=28)+timedelta(days=4)).replace(day=1)
        return (next_month-timedelta(days=1)).isoformat()+'T17:00:00+08:00'
    return None

def normalize_assistant_text(text):
    """Normalise conversational punctuation without losing Chinese task titles."""
    source=strip_decorative_emoji(str(text or ''))
    source=source.replace('＃','#').replace('％','%').replace('：',':')
    return re.sub(r'\s+',' ',source).strip()

def task_title_tokens(title):
    """Small, explainable token set for title references in short chat messages."""
    source=re.sub(r'[\s，,。；;：:、（）()【】\[\]·—_\-]','',str(title or '')).lower()
    return {source[i:i+2] for i in range(max(0,len(source)-1))} or ({source} if source else set())

def task_reference_by_title(c, user_id, text, include_managed=True):
    """Resolve a task named in ordinary language, returning None on ambiguity.

    It deliberately requires a strong title overlap.  A fuzzy match is useful for
    asking a clarification, but must never choose a target for a write operation.
    """
    source=normalize_assistant_text(text)
    if not source or not user_id: return None
    actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
    can_manage=bool(actor and actor['role'] in ('manager','pm')) and include_managed
    rows=c.execute("""SELECT t.id,t.title,t.owner_id,t.approver_id FROM task t
                      WHERE t.approval_status='approved' AND t.work_status NOT IN ('completed','cancelled')
                        AND (t.owner_id=? OR t.approver_id=? OR ?)
                      ORDER BY t.due_at,t.id""",(user_id,user_id,1 if can_manage else 0)).fetchall()
    source_compact=re.sub(r'[\s，,。；;：:、（）()【】\[\]·—_\-]','',source).lower()
    scored=[]
    for row in rows:
        title_compact=re.sub(r'[\s，,。；;：:、（）()【】\[\]·—_\-]','',row['title']).lower()
        if len(title_compact)>=3 and title_compact in source_compact:
            scored.append((1.0,row)); continue
        title_tokens=task_title_tokens(row['title']); source_tokens=task_title_tokens(source)
        score=len(title_tokens & source_tokens)/max(1,len(title_tokens))
        if score>=0.72 and len(title_tokens & source_tokens)>=2: scored.append((score,row))
    if not scored: return None
    scored.sort(key=lambda item:item[0],reverse=True)
    if len(scored)>1 and abs(scored[0][0]-scored[1][0])<0.15: return None
    return int(scored[0][1]['id'])

def parse_progress_message(text):
    """Parse an explicit task-number progress message; never guesses a task."""
    task_match=re.search(r'任务\s*#\s*(\d+)',text)
    if not task_match: return None
    percent_match=re.search(r'(\d{1,3})\s*[%％]',text)
    percent=int(percent_match.group(1)) if percent_match else (100 if re.search(r'已完成|完成了|完成$',text.strip()) else None)
    blocked_match=re.search(r'(?:阻塞|风险)(?:原因)?\s*[：:]?\s*(.+)',text)
    return {'task_id':int(task_match.group(1)),'percent':percent,'comment':text.strip(),'blocked':bool(blocked_match),'block_reason':blocked_match.group(1).strip() if blocked_match else '','expected_finish':parse_due(text)}

def resolve_progress_message(c, text, user_id, session_id=None):
    """Resolve a progress update safely, including a single-task conversational context.

    A vague phrase such as “此任务完成 80%” is only mapped automatically when the
    sender has exactly one active task.  We prefer one clarification over updating
    the wrong task.
    """
    parsed=parse_progress_message(text)
    if parsed is None:
        percent_match=re.search(r'(\d{1,3})\s*[%％]',text or '')
        parsed={'task_id':None,'percent':int(percent_match.group(1)) if percent_match else (100 if re.search(r'已完成|完成了|完成$',str(text or '').strip()) else None),'comment':str(text or '').strip(),'blocked':False,'block_reason':'','expected_finish':parse_due(text)}
    progress_words=bool(re.search(r'进度|进展|完成\s*\d{1,3}\s*[%％]|(?:当前|已经|已)?(?:完成|做到|达到)\s*\d{1,3}\s*[%％]|已完成|完成了|阻塞|风险',text or ''))
    if not progress_words:
        return None, None
    if not user_id:
        return None, '你的飞书账号尚未绑定系统成员，暂不能提交进度。'
    if parsed['percent'] is None:
        return None, '我理解你是在反馈进度，请补充完成比例，例如：任务#15 完成 80%。'
    task_id=parsed['task_id'] or referenced_task_id(c,session_id,user_id,text) or task_reference_by_title(c,user_id,text,include_managed=False)
    if not task_id and re.search(r'(此|这|当前|本)任务',text or ''):
        rows=c.execute("SELECT id,title FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled') ORDER BY due_at,id",(user_id,)).fetchall()
        if len(rows)==1:
            task_id=rows[0]['id']
        elif not rows:
            return None, '未找到你当前可填报进度的任务。'
        else:
            choices='、'.join(f"#{row['id']} {row['title']}" for row in rows[:5])
            return None, '你有多条进行中的任务，请注明任务编号，例如：任务#15 完成 80%。可选：'+choices
    if not task_id:
        return None, '我理解你是在反馈进度，但无法确定是哪一项。可直接说“本周需求池梳理完成 80%”，或回复任务编号。'
    task=c.execute('SELECT id,title,owner_id,approval_status FROM task WHERE id=?',(task_id,)).fetchone()
    if not task:
        return None, f'未找到任务#{task_id}，请确认编号。'
    if int(task['owner_id'])!=int(user_id):
        return None, f'任务#{task_id}《{task["title"]}》不是你的责任任务，不能代为填报。'
    if task['approval_status']!='approved':
        return None, f'任务#{task_id}尚未确认生效，暂不能填报进度。'
    parsed['task_id']=task_id
    return parsed, None

def is_task_creation_request(text):
    """Only create a draft for an explicit assignment, never for ordinary dialogue."""
    source=str(text or '').strip()
    if not source or '不是在新建' in source:return False
    if re.search(r'^(?:新建|创建|新增|安排)\s*(?:一个|一项|一条)?\s*(?:任务|事项)(?!模板)',source):return True
    if re.search(r'进度|完成\s*\d{1,3}\s*[%％]|已完成|完成了|阻塞|风险',source):
        return False
    if re.search(r'^(?:新建|创建|新增|安排).{0,8}(?:任务|事项)',source):
        return True
    return bool(re.search(r'请.{1,24}(?:负责|完成|推进|落实|开展|组织)',source))

def structured_progress_report(text):
    """Recognise labelled status reports without treating their prose as commands."""
    source=str(text or '').strip()
    if re.match(r'^(?:(?:请|帮我|帮忙|麻烦)\s*)*(?:修改|更改|调整|变更|删除|移除|设置|验收)',source):return None
    labels=r'任务标题|任务名称|任务情况|任务描述|优先级|计划完成时间|计划完成日期|预计完成时间|进度百分比|完成比例|进度'
    matches=list(re.finditer(r'(?:\d+[、.．)）]\s*)?('+labels+r')\s*[:：]\s*',source))
    fields={match[1]:source[match.end():matches[index+1].start() if index+1<len(matches) else len(source)].strip()
            for index,match in enumerate(matches)}
    progress=next((fields[key] for key in ('进度百分比','完成比例','进度') if key in fields),None)
    if progress is None or len(fields)<2:return None
    percent=re.fullmatch(r'(-?\d+)\s*[%％][。；;]?',progress)
    if not percent:return None
    title=fields.get('任务标题') or fields.get('任务名称') or source[:matches[0].start()].strip(' \t\r\n:：')
    planned=next((fields[key] for key in ('计划完成时间','计划完成日期','预计完成时间') if key in fields),'')
    return {'title':title,'percent':int(percent[1]),'task_id':task_number(title),'comment':source,
            'expected_finish':parse_due(planned) if planned else None}

def process_structured_progress_report(c,text,user_id,session_id):
    report=structured_progress_report(text);row=None
    if not report and user_id and re.fullmatch(r'(?:#\s*\d+|(?:任务|task)\s*(?:编号\s*[:#]?\s*|[#:：\-]\s*)?\d+)[。]?',normalize_assistant_text(text),re.I):
        row=c.execute("SELECT * FROM bot_pending_action WHERE session_id=? AND user_id=? AND status='collecting' AND expire_at>? ORDER BY id DESC LIMIT 1",(session_id,user_id,now())).fetchone()
        action=json.loads(row['action_json']) if row else {}
        if action.get('type')=='collect_progress_report':report={**action['report'],'task_id':task_number(text)}
    if not report:return None
    if not user_id:return '你的飞书账号尚未绑定系统成员，暂不能提交进度。'
    if not 0<=report['percent']<=100:
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id=? AND status IN ('pending','collecting')",(session_id,user_id))
        return '进度百分比须在0%到100%之间，请核对后重新发送。'
    if row is None:
        action_id=save_bot_action(c,session_id,user_id,{'type':'collect_progress_report','report':report})
        c.execute("UPDATE bot_pending_action SET status='collecting' WHERE id=?",(action_id,))
    else:action_id=row['id']
    tid=report['task_id'];title=report['title']
    if not tid and title:
        rows=c.execute("SELECT id,title FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled') ORDER BY id",(user_id,)).fetchall()
        matches=[task for task in rows if re.sub(r'\s+','',task['title']).casefold()==re.sub(r'\s+','',title).casefold()]
        if len(matches)==1:tid=matches[0]['id']
        elif len(matches)>1:return f'已识别《{title}》的进度汇报（{report["percent"]}%），但有多条同名任务。请回复任务编号：'+ '、'.join('#'+str(task['id']) for task in matches[:5])+'。'
    if not tid:
        label=f'《{title}》' if title else '这项工作'
        return f'已识别{label}的进度汇报（{report["percent"]}%），但尚未定位到对应任务。若更新已有任务，请回复任务编号，例如“#15”；若要新建，请明确说“新建任务”并提供任务内容，系统会先生成草稿供确认。'
    task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
    if not task:return f'未找到任务#{tid}，请核对编号。'
    if task['owner_id']!=user_id:return f'任务#{tid}《{task["title"]}》不是你的责任任务，不能代为填报。'
    if task['approval_status']!='approved' or task['work_status'] in ('completed','cancelled'):return f'任务#{tid}当前不支持填报进度，请核对任务状态。'
    try:
        submit_progress(c,tid,user_id,report['percent'],report['comment'],expected_finish=report['expected_finish'],source='feishu')
    except (ValueError,PermissionError) as exc:return str(exc)
    c.execute("UPDATE bot_pending_action SET status='executed',confirmed_at=? WHERE id=?",(now(),action_id))
    save_bot_task_context(c,session_id,user_id,tid)
    return f'已记录《{task["title"]}》进度 {report["percent"]}%，已提交上级确认。'

def is_deadline_change_request(text):
    source=normalize_assistant_text(text)
    field=r'(?:截止(?:日期|时间)?|计划完成(?:日期|时间)|完成时间|交付日期|到期(?:日期|时间)?)'
    change=r'(?:修改|更改|调整|变更|更正|改为|改成|改到|改至|设为|设置|定为|推迟|延后|延迟|延期|延长|提前|顺延)'
    # Complete verbs and a bounded clause prevent “需求调研 … 计划完成时间” from becoming a mutation.
    return bool(re.search(field+r'[^，,。；;!?！？]{0,30}'+change+'|'+change+r'[^，,。；;!?！？]{0,30}'+field,source)
                or re.search(r'(?:该|这|此)(?:任务|事项)?.{0,12}(?:到|为)\s*(?:\d{1,2}[./月]|今天|明天|后天|下周)',source))

def feishu_task_list(c, user_id, session_id=None):
    if not user_id:
        return '你的飞书账号尚未绑定系统成员，暂不能查询任务。'
    rows=c.execute("SELECT id,title,due_at,work_status FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled') ORDER BY due_at,id LIMIT 8",(user_id,)).fetchall()
    if not rows: return '你当前没有待完成任务。'
    # The first item is the most urgent task and becomes the safe referent for
    # immediate follow-ups such as “该任务截止日期改为 10.28”.
    save_bot_task_context(c,session_id,user_id,rows[0]['id'])
    return '【我的待办】\n'+'\n'.join(f"{idx}. #{row['id']} {row['title']}（截止 {str(row['due_at'])[:10]}，{ {'assigned':'待开始','in_progress':'进行中','awaiting_acceptance':'待验收'}.get(row['work_status'],row['work_status'])}）" for idx,row in enumerate(rows,1))

def is_department_progress_query(text):
    """Recognise management questions such as “汇总一下部门所有任务进展”。"""
    source=re.sub(r'\s+','',str(text or ''))
    scope=bool(re.search(r'(?:部门|全体|所有|全部|整体|全局)',source))
    topic=bool(re.search(r'(?:任务|事项|工作)',source))
    progress=bool(re.search(r'(?:进展|进度|汇总|概况|情况|报表|总结)',source))
    return scope and topic and progress

def is_team_progress_query(text):
    source=re.sub(r'\s+','',str(text or ''))
    return bool(re.search(r'(?:团队|下属|组员|直属).{0,10}(?:任务|事项|工作).{0,10}(?:进展|进度|汇总|概况|情况|报表|总结)|(?:任务|事项|工作).{0,10}(?:进展|进度|汇总|概况|情况|报表|总结).{0,10}(?:团队|下属|组员|直属)',source))

def managed_user_ids(c, user_id):
    """Return the full active reporting subtree below a user, never peers."""
    found=[]; frontier=[int(user_id)]
    while frontier:
        marks=','.join('?' for _ in frontier)
        rows=c.execute(f"SELECT user_id FROM reporting_line WHERE direct_leader_id IN ({marks}) AND (effective_to IS NULL OR effective_to='')",frontier).fetchall()
        next_ids=[int(row['user_id']) for row in rows if int(row['user_id']) not in found and int(row['user_id'])!=int(user_id)]
        found.extend(next_ids); frontier=next_ids
    return found

def feishu_team_progress(c, user_id):
    """Give Team leaders a safe view of their reporting subtree only."""
    if not user_id:
        return '你的飞书账号尚未绑定系统成员，暂不能查询团队任务。'
    people=managed_user_ids(c,user_id)
    if not people:
        return '当前没有配置在你名下的直属成员，因此暂无可汇总的团队任务。'
    marks=','.join('?' for _ in people)
    rows=c.execute(f"""SELECT t.id,t.title,t.due_at,t.work_status,t.effective_percent,t.progress_percent,u.name AS owner_name
                       FROM task t JOIN user u ON u.id=t.owner_id
                       WHERE t.approval_status='approved' AND t.owner_id IN ({marks})
                       ORDER BY CASE WHEN t.work_status IN ('completed','cancelled') THEN 1 ELSE 0 END,t.due_at,t.id""",people).fetchall()
    if not rows: return '你的团队当前没有已生效任务。'
    labels={'assigned':'待开始','in_progress':'进行中','awaiting_acceptance':'待验收','completed':'已完成','cancelled':'已取消'}
    counts={key:0 for key in labels}
    for row in rows: counts[row['work_status']]=counts.get(row['work_status'],0)+1
    lines=[f"#{row['id']}《{row['title']}》｜{row['owner_name']}｜{labels.get(row['work_status'],row['work_status'])} {int(row['effective_percent'] or row['progress_percent'] or 0)}%｜截止 {str(row['due_at'])[:10]}" for row in rows[:15]]
    headline=f"共 {len(rows)} 项：待开始 {counts['assigned']}、进行中 {counts['in_progress']}、待验收 {counts['awaiting_acceptance']}、已完成 {counts['completed']}"
    return '【团队任务进展】\n'+headline+'\n'+'\n'.join(lines)+('\n其余任务请在网页任务列表查看。' if len(rows)>15 else '')

def parse_report_schedule(c, text, user_id):
    """Extract a safe daily task-progress report schedule from natural language."""
    source=re.sub(r'\s+','',str(text or ''))
    if not (re.search(r'(?:每天|每日|每日|每个工作日|定时)',source) and re.search(r'(?:任务|工作|事项).{0,10}(?:汇总|汇报|进展|进度)|(?:汇总|汇报|进展|进度).{0,10}(?:任务|工作|事项)',source)):
        return None
    clock=re.search(r'(?<!\d)([01]?\d|2[0-3])(?:点|:|：)([0-5]?\d)?(?:分)?',source)
    if not clock:
        return {'missing':'发送时间'}
    hour=int(clock.group(1)); minute=int(clock.group(2) or 0)
    actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
    if re.search(r'(?:部门|全体|所有|全部|整体)',source): scope='department'
    elif re.search(r'(?:团队|下属|组员|直属)',source): scope='team'
    elif re.search(r'(?:我的|本人|个人)',source): scope='self'
    elif actor and actor['role'] in ('manager','pm'): scope='department'
    elif managed_user_ids(c,user_id): scope='team'
    else: scope='self'
    return {'scope':scope,'frequency':'daily','schedule_time':f'{hour:02d}:{minute:02d}','receiver_id':user_id}

def scheduled_report_text(c, schedule):
    if schedule['scope']=='department': return feishu_department_progress(c,schedule['created_by'])
    if schedule['scope']=='team': return feishu_team_progress(c,schedule['created_by'])
    return feishu_task_list(c,schedule['created_by'])

def run_scheduled_reports(c, local_now=None):
    """Dispatch due user-configured reports once per local calendar day."""
    local_now=local_now or datetime.now(timezone(timedelta(hours=8)))
    day=local_now.date().isoformat(); clock=local_now.strftime('%H:%M'); out={'due':0,'sent':0,'skipped':0,'failed':0}
    rows=c.execute("SELECT * FROM scheduled_report WHERE enabled=1 AND frequency='daily' AND schedule_time<=?",(clock,)).fetchall()
    for schedule in rows:
        if c.execute('SELECT 1 FROM scheduled_report_run WHERE schedule_id=? AND run_date=?',(schedule['id'],day)).fetchone(): continue
        out['due']+=1
        receiver=c.execute('SELECT feishu_open_id FROM user WHERE id=?',(schedule['receiver_id'],)).fetchone()
        try:
            if not receiver or not receiver['feishu_open_id']:
                result={'status':'skipped_no_binding'}; out['skipped']+=1
            else:
                response=FeishuAdapter().send_text(receiver['feishu_open_id'],scheduled_report_text(c,schedule))
                result=response; out['sent' if response.get('sent') else 'skipped']+=1
        except Exception as exc:
            result={'status':'failed','reason':str(exc)}; out['failed']+=1
        c.execute('INSERT INTO scheduled_report_run(schedule_id,run_date,sent_at,result_json) VALUES(?,?,?,?)',(schedule['id'],day,now(),json.dumps(result,ensure_ascii=False)))
    return out

def feishu_department_progress(c, user_id):
    """Return an auditable management summary; never expose drafts."""
    if not user_id:
        return '你的飞书账号尚未绑定系统成员，暂不能查询部门任务。'
    actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
    if not actor or actor['role'] not in ('manager','pm'):
        return '部门任务汇总仅对管理PM和部门负责人开放；你可以查询“我有哪些任务”。'
    rows=c.execute("""SELECT t.id,t.title,t.due_at,t.work_status,t.effective_percent,t.progress_percent,
                    t.risk_status,u.name AS owner_name,COALESCE(o.name,'未挂载责任域') AS domain_name
                    FROM task t JOIN user u ON u.id=t.owner_id LEFT JOIN org_node o ON o.id=t.org_node_id
                    WHERE t.approval_status='approved' ORDER BY CASE WHEN t.work_status IN ('completed','cancelled') THEN 1 ELSE 0 END,t.due_at,t.id""").fetchall()
    if not rows: return '当前没有已生效的部门任务。'
    status_names={'assigned':'待开始','in_progress':'进行中','awaiting_acceptance':'待验收','completed':'已完成','cancelled':'已取消'}
    status_counts={key:0 for key in status_names}
    today=datetime.now(timezone.utc)
    overdue=0
    for row in rows:
        status_counts[row['work_status']]=status_counts.get(row['work_status'],0)+1
        try:
            if row['work_status'] not in ('completed','cancelled') and datetime.fromisoformat(row['due_at'].replace('Z','+00:00'))<today: overdue+=1
        except (TypeError,ValueError): pass
    headline=f"共 {len(rows)} 项：待开始 {status_counts['assigned']}、进行中 {status_counts['in_progress']}、待验收 {status_counts['awaiting_acceptance']}、已完成 {status_counts['completed']}"
    if overdue: headline+=f'；逾期 {overdue} 项'
    items=[]
    for row in rows[:15]:
        progress=int(row['effective_percent'] or row['progress_percent'] or 0)
        items.append(f"#{row['id']}《{row['title']}》｜{row['domain_name']}｜{row['owner_name']}｜{status_names.get(row['work_status'],row['work_status'])} {progress}%｜截止 {str(row['due_at'])[:10]}")
    tail='\n其余任务请在网页任务列表查看。' if len(rows)>15 else ''
    return '【部门任务进展】\n'+headline+'\n'+'\n'.join(items)+tail

def save_bot_task_context(c, session_id, user_id, task_id):
    """Remember the task just shown/referred to for short follow-up phrases.

    This is deliberately a per-session, short-lived conversational convenience;
    the subsequent action still runs through normal ownership and confirmation
    checks, so an ambiguous reference cannot bypass permission controls.
    """
    if session_id and user_id and task_id:
        c.execute('INSERT INTO bot_task_context(session_id,user_id,task_id,updated_at) VALUES(?,?,?,?) ON CONFLICT(session_id) DO UPDATE SET user_id=excluded.user_id,task_id=excluded.task_id,updated_at=excluded.updated_at',(session_id,user_id,task_id,now()))

def referenced_task_id(c, session_id, user_id, text):
    # Follow-up messages rarely repeat the noun exactly.  Treat “这条”“该项”
    # and “上一个” as references only when a short-lived per-user context exists
    # (or when the user has one and only one active task), never as a global guess.
    reference_pattern=(r'(?:该|这|此)(?:个|条|项)?(?:任务|事项|工作)'
                       r'|(?:上面|刚才|上一条|上一个)(?:个|条|项)?(?:任务|事项|工作)?'
                       r'|(?:这|该|此)(?:条|项|个)(?=(?:的)?(?:截止|日期|责任人|负责人|进度|状态|标题|详情|验收|删除|开始|完成))')
    if not re.search(reference_pattern,str(text or '')):
        return task_reference_by_title(c,user_id,text)
    row=c.execute("SELECT task_id,updated_at FROM bot_task_context WHERE session_id=? AND user_id=?",(session_id,user_id)).fetchone()
    if row:
        try:
            if datetime.fromisoformat(row['updated_at']) >= datetime.now(timezone.utc)-timedelta(minutes=30):
                return int(row['task_id'])
        except (TypeError,ValueError):
            pass
    # If there is exactly one active task, pronouns are still unambiguous even
    # without a prior saved context (e.g. after a service restart).
    rows=c.execute("SELECT id FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled') ORDER BY due_at,id LIMIT 2",(user_id,)).fetchall()
    return int(rows[0]['id']) if len(rows)==1 else None

def is_task_query_request(text):
    """Recognise task-list questions in common conversational Chinese.

    This deliberately uses intent signals rather than a single fixed sentence: people
    naturally say “我今天要做什么”, “查下我手上的事” or “有哪些未完成”.
    Explicit progress reports and task mutations are excluded so they still flow to
    their dedicated, permission-checked handlers.
    """
    source=re.sub(r'\s+','',str(text or '')).lower()
    if not source or re.search(r'(?:任务|事项)?#?\d+.*(?:完成|开始|删除|验收|改为|变更)|(?:完成|开始|删除|验收|改为|变更).*(?:任务|事项)?#?\d+',source):
        return False
    # Direct list expressions are always a query, with or without an explicit “我”.
    if re.search(r'(?:任务|待办|事项|工作).{0,12}(?:清单|列表|查询|有哪些|有什么|多少|几条|全部|未完成|待处理|安排)|(?:有哪些|有什么|多少|几条|全部|未完成|待处理).{0,12}(?:任务|待办|事项|工作)',source):
        return True
    # First-person phrasing often omits “任务”, e.g. “我今天要做什么”.
    if re.search(r'(?:我|本人|手上|当前).{0,12}(?:任务|待办|事项|要做什么|该做什么|需要做什么|负责什么|忙什么|有什么安排|有哪些安排)',source):
        return True
    if re.search(r'(?:查|看|列|告诉).{0,8}(?:我|本人|手上|当前).{0,8}(?:任务|待办|事项|工作)',source):
        return True
    return bool(re.search(r'^(?:待办|任务|事项|工作)(?:清单|列表|查询)?$',source))

def assistant_clarification(text):
    """A useful fallback that reflects likely intent instead of a canned rejection."""
    source=normalize_assistant_text(text)
    if re.search(r'(?:截止|日期|延期|提前|推迟|改到|改为)',source):
        return '我理解你想变更任务日期。请补充任务标题、任务编号，或先说“我的待办”后再说“该任务截止日期改到 10 月 28 日”。'
    if re.search(r'(?:进度|进展|完成|做到|达到).*(?:%|％)|(?:%|％).*(?:进度|进展|完成)',source):
        return '我理解你在反馈进度。请说明对应任务，例如“客户投诉工单闭环处理完成 80%”；如果刚查询过待办，也可以说“该任务完成 80%”。'
    if re.search(r'(?:汇总|总结|概况|报表|情况)',source):
        return '我可以汇总你的待办、团队或部门任务。可以直接说“我有哪些任务”“汇总团队任务进展”或“汇总部门所有任务进度”。'
    if re.search(r'(?:删除|作废|移除|验收|责任人|代理|开始|提交)',source):
        return '我理解你希望执行任务操作。请带上任务标题或编号；涉及修改、删除、验收和代理时，我会先回显操作内容，得到“确认”后才会执行。'
    return '我可以查询任务、反馈进度、新建/修改/删除任务、提交或验收任务、变更责任人、设置确认代理，以及创建定时汇总。请直接说出目标和对象，例如“我有哪些任务”或“把客户投诉工单截止日期改到 10 月 28 日”。'

def rule_bot_intent(text):
    """Fast, explainable intent recognition for the common assistant commands."""
    source=re.sub(r'\s+','',str(text or ''))
    if is_task_query_request(source): return 'task_query'
    if is_task_creation_request(source): return 'task_creation'
    if re.search(r'^(?:帮助|帮忙|你能做什么|你会什么|怎么用|功能说明)[？?！!。]*$',source): return 'help'
    return 'unknown'

def model_bot_intent(text):
    """Use a configured OpenAI-compatible model only for otherwise unknown wording.

    The model selects an intent; it never executes an operation or bypasses the
    downstream identity, permission and explicit-confirmation checks.
    """
    if not ai_configured(): return 'unknown'
    prompt=(
        '你是企业工作管理助手的意图分类器。只返回 JSON：{"intent":"..."}。'
        'intent 只能是 task_query、task_creation、progress_update、task_operation、help、unknown。'
        'task_query 表示查询本人待办、任务清单、当前安排或未完成工作；'
        'task_creation 表示明确新建/安排待办；progress_update 表示汇报任务进展；'
        'task_operation 表示开始、修改、删除、验收、变更责任人或设置代理；'
        '不确定返回 unknown。不要执行操作，不要解释。用户输入：'+str(text or '')
    )
    try:
        content=ai_chat_completion([
            {'role':'system','content':'只输出合法 JSON，不输出解释。'},
            {'role':'user','content':prompt}
        ], temperature=0, max_tokens=256, timeout=12)
        parsed=parse_model_json(content)
        return parsed.get('intent') if parsed.get('intent') in {'task_query','task_creation','progress_update','task_operation','help','unknown'} else 'unknown'
    except Exception:
        return 'unknown'

def classify_bot_intent(text):
    """Rule-first classifier with model fallback when an AI provider is configured."""
    intent=rule_bot_intent(text)
    return intent if intent!='unknown' else model_bot_intent(text)

def model_action_to_command(data):
    """Convert a model proposal to a restricted command understood by the planner."""
    if not isinstance(data,dict): return None
    operation=str(data.get('operation') or '')
    try: task_id=int(data.get('task_id')) if data.get('task_id') is not None else None
    except (TypeError,ValueError): task_id=None
    if operation=='change_due' and task_id and re.fullmatch(r'\d{4}-\d{2}-\d{2}',str(data.get('due_date') or '')):
        return f'任务#{task_id} 截止日期改为{data["due_date"]}'
    if operation=='change_owner' and task_id and str(data.get('owner_name') or '').strip():
        return f'任务#{task_id} 责任人改为{str(data["owner_name"]).strip()}'
    if operation=='delete_task' and task_id: return f'删除任务#{task_id}'
    if operation=='start_task' and task_id: return f'开始任务#{task_id}'
    if operation=='submit_acceptance' and task_id: return f'任务#{task_id} 完成并提交'
    if operation=='accept_task' and task_id:
        return f'验收任务#{task_id} {"驳回" if data.get("result")=="rejected" else "通过"}'
    if operation=='edit_task' and task_id:
        field={'title':'任务标题','description':'任务详情','deliverable':'交付物','priority':'优先级',
               'risk_status':'风险状态','task_type':'任务类型','estimated_hours':'标准工时',
               'actual_hours':'实际工时','complexity':'复杂度'}.get(str(data.get('field') or ''))
        value=str(data.get('value') or '').strip()
        if field and value: return f'任务#{task_id} {field}改为{value}'
    if operation=='set_delegate' and str(data.get('principal_name') or '').strip() and str(data.get('delegate_name') or '').strip():
        return f'{str(data["principal_name"]).strip()}代理{str(data["delegate_name"]).strip()}'
    if operation=='progress_update' and task_id and isinstance(data.get('percent'),int) and 0<=data['percent']<=100:
        return f'任务#{task_id} 完成{data["percent"]}%'
    return None

def model_bot_operation_command(c, text, user_id, session_id=None):
    """Ask the model to extract an operation, constrained to known tasks and people."""
    if not user_id or not ai_configured(): return None
    actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone(); can_manage=bool(actor and actor['role'] in ('manager','pm'))
    # A Team leader may not edit a subordinate's task, but must be able to find
    # it by title when they are its designated approver.  The planner still
    # verifies the exact operation's permission before any action is saved.
    rows=c.execute("SELECT task.id,task.title,task.owner_id,task.approver_id,user.name AS owner_name,substr(task.due_at,1,10) AS due_date,task.work_status FROM task JOIN user ON user.id=task.owner_id WHERE task.approval_status='approved' AND task.work_status NOT IN ('completed','cancelled') AND (task.owner_id=? OR task.approver_id=? OR ?) ORDER BY task.due_at,task.id LIMIT 30",(user_id,user_id,1 if can_manage else 0)).fetchall()
    tasks=[rowdict(row) for row in rows]
    people=[rowdict(row) for row in c.execute('SELECT id,name FROM user ORDER BY id LIMIT 60')]
    referenced_id=referenced_task_id(c,session_id,user_id,text) if session_id else None
    referenced=next((task for task in tasks if task['id']==referenced_id),None)
    prompt=(
        '你是工作管理助手的安全参数提取器。只返回 JSON，不要解释。今天是'+datetime.now(timezone.utc).date().isoformat()+
        '。从用户输入识别 operation，允许：change_due、change_owner、delete_task、start_task、submit_acceptance、accept_task、edit_task、set_delegate、progress_update、unknown。'
        '仅能使用给定任务中的 id 和给定成员姓名；无法唯一确定时 operation=unknown。'
        '若用户说“该任务/这项任务/此任务”，优先使用“当前会话任务”；若没有当前会话任务且可操作任务只有一条，使用那一条。'
        'change_due 需 due_date 为 YYYY-MM-DD；change_owner 需 owner_name；accept_task 的 result 为 passed/rejected；'
        'edit_task 的 field 仅为 title/description/deliverable/priority/risk_status/task_type/estimated_hours/actual_hours/complexity 且带 value；progress_update 带整数 percent；'
        'set_delegate 带 principal_name 和 delegate_name。用户输入：'+str(text or '')+
        '。可操作任务：'+json.dumps(tasks,ensure_ascii=False,separators=(',',':'))+
        '。当前会话任务：'+json.dumps(referenced,ensure_ascii=False,separators=(',',':'))+
        '。系统成员：'+json.dumps(people,ensure_ascii=False,separators=(',',':'))
    )
    try:
        content=ai_chat_completion([
            {'role':'system','content':'只输出合法 JSON，不执行操作，不解释。'},
            {'role':'user','content':prompt}
        ], temperature=0, max_tokens=400, timeout=15)
        return model_action_to_command(parse_model_json(content))
    except Exception:
        return None

def task_number(text):
    source=normalize_assistant_text(text)
    match=re.search(r'(?:任务|task)\s*(?:编号\s*[:#]?\s*|[#:：\-]\s*)?(\d+)',source,re.I)
    if not match:match=re.match(r'#\s*(\d+)',source)
    return int(match.group(1)) if match else None

def user_by_name(c, name):
    return c.execute('SELECT * FROM user WHERE name=? ORDER BY id LIMIT 1',(name,)).fetchone()

def bot_can_manage_task(c, user_id, task):
    if not user_id or not task: return False
    role=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
    return int(user_id)==int(task['owner_id']) or bool(role and role['role'] in ('manager','pm'))

def save_bot_action(c, session_id, user_id, action):
    expiry=(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()
    if action.get('task_id'):
        action['task_before']=rowdict(c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone())
    c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id IS ? AND status IN ('pending','collecting')",(session_id,user_id))
    return c.execute('INSERT INTO bot_pending_action(session_id,user_id,action_json,expire_at,created_at) VALUES(?,?,?,?,?)',(session_id,user_id,json.dumps(action,ensure_ascii=False),expiry,now())).lastrowid

BOT_HELP = ('我是科技产品中心智能助手，可以查询任务、反馈进度、新建任务草稿、修改/删除任务、提交/验收任务、设置确认代理和定时汇总。\n'
            '部门负责人和管理 PM 还可维护基础资料和成员信息，例如：\n'
            '查看成员 / 查看部门 / 查看岗位 / 查看责任域 / 查看例行任务模板\n'
            '新建部门 数据产品部\n'
            '新建岗位 数据分析师，部门：数据产品部\n'
            '新建成员 张三，部门：数据产品部，账号：zhangsan\n'
            '修改成员 张三，岗位：数据分析师，直属上级：部门管理PM\n'
            '把张三的部门改为科技产品中心\n'
            '新建责任域 数据治理，负责人：张三，边界：数据质量管理\n'
            '新建例行任务模板 每周数据检查，责任域：数据治理，责任人：张三，标准工时：2，交付物：检查记录\n'
            '新建例行任务模板 每周例行检查，责任域：数据治理，责任人员：张三、李四，标准工时：2，交付物：检查记录\n'
            '任务#15 优先级改为P0，交付物改为评审纪要\n'
            '删除任务#15\n'
            '涉及资料新建、修改、删除的操作会先回显，回复“确认”执行、“取消”放弃，10 分钟内有效。重名对象请使用 #编号。')


def bot_task_changes(c, task, changes):
    allowed={'title','description','deliverable','priority','task_type','due_at','owner_id','org_node_id','risk_status','estimated_hours','actual_hours','complexity'}
    if not changes or set(changes)-allowed: raise ValueError('不支持的任务修改字段')
    validated=validate_task_fields(c,{**dict(task),**changes},require_all=True)
    result={key:validated.get(key,value) for key,value in changes.items()}
    if 'risk_status' in changes and changes['risk_status'] not in ('normal','at_risk','blocked'): raise ValueError('风险状态须为正常、有风险或已阻塞')
    if 'complexity' in changes and changes['complexity'] not in PERFORMANCE_DEFAULTS['complexity']: raise ValueError('复杂度无效')
    for field in ('estimated_hours','actual_hours'):
        if field in changes: result[field]=routines.hours(changes[field],actual=field=='actual_hours')
    if task['routine_week_start']:
        if ('estimated_hours' in result and result['estimated_hours']!=task['estimated_hours']) or ('task_type' in result and result['task_type']!='ROUTINE'):
            raise ValueError('已生成例行任务的类型和标准工时快照不能修改，请调整模板用于未来任务')
    return result


def plan_bot_task_fields(c, text, user_id, session_id, task_id):
    labels={'任务标题':'title','标题':'title','任务详情':'description','详情':'description','说明':'description',
            '交付物':'deliverable','验收标准':'deliverable','优先级':'priority','风险状态':'risk_status',
            '责任域':'org_node_id','任务类型':'task_type','标准工时':'estimated_hours','实际工时':'actual_hours',
            '复杂度':'complexity','责任人':'owner_id','负责人':'owner_id','截止日期':'due_at','截止时间':'due_at'}
    pattern='|'.join(re.escape(k) for k in sorted(labels,key=len,reverse=True))
    changes={}; display=[]
    for part in re.split(r'[，,；;]',text):
        match=re.search('('+pattern+r')\s*(?:修改为|变更为|调整为|设置为|改为|改成|[:：])\s*(.+)$',part)
        if not match: continue
        key=labels[match[1]]; value=match[2].strip(' 。')
        if key in changes: return None,'同一任务字段重复填写，请每项只提供一个值。'
        display.append(match[1]+'：'+value);changes[key]=value
    # Preserve the established single-field due/owner planners and their
    # conversational date/title handling. This adds fields and combined edits.
    if not changes:return None,None
    if len(changes)==1:
        if 'due_at' in changes and re.search(r'改|调|变|延|推',text):return None,None
        if 'owner_id' in changes and not changes['owner_id'].startswith('#') and re.search(r'改为|变更为|调整为|改成|换成|为',text):return None,None
    task=c.execute('SELECT * FROM task WHERE id=?',(task_id,)).fetchone()
    if not task: return None,'未找到该任务。'
    if not bot_can_manage_task(c,user_id,task): return None,'只有任务责任人、管理 PM 或部门负责人可以修改任务。'
    try:
        for key,table in (('owner_id','user'),('org_node_id','org_node')):
            if key in changes:changes[key]=assistant_management.lookup(c,table,changes[key],nodes=key=='org_node_id')['id']
        if 'due_at' in changes:changes['due_at']=valid_due_at(changes['due_at']) if re.match(r'^\d{4}-\d{2}-\d{2}',changes['due_at']) else parse_due(changes['due_at'])
        if 'priority' in changes:changes['priority']=changes['priority'].upper()
        if 'task_type' in changes:changes['task_type']={'专项':'PROJECT','专项任务':'PROJECT','临时':'ADHOC','临时任务':'ADHOC','例行':'ROUTINE','例行任务':'ROUTINE'}.get(changes['task_type'],changes['task_type'])
        if 'risk_status' in changes:changes['risk_status']={'正常':'normal','有风险':'at_risk','已阻塞':'blocked','阻塞':'blocked'}.get(changes['risk_status'],changes['risk_status'])
        for key in ('estimated_hours','actual_hours'):
            if key in changes:changes[key]=re.sub(r'\s*(?:小时|h)$','',changes[key],flags=re.I)
        changes=bot_task_changes(c,task,changes)
    except (ValueError,LookupError) as exc:return None,str(exc)
    action={'type':'update_task','task_id':task_id,'changes':changes};save_bot_action(c,session_id,user_id,action)
    return action,f'拟修改任务#{task_id}《{task["title"]}》：\n'+ '\n'.join(display)+'\n回复“确认”执行，回复“取消”放弃。'

BOT_EDIT_LABELS={'责任人':'owner_id','负责人':'owner_id','截止日期':'due_at','截止时间':'due_at','到期时间':'due_at',
                 '任务标题':'title','标题':'title','任务详情':'description','详情':'description',
                 '交付物':'deliverable','验收标准':'deliverable','优先级':'priority'}
BOT_EDIT_NAMES={'owner_id':'责任人','due_at':'截止日期','title':'标题','description':'详情','deliverable':'交付物','priority':'优先级'}


def plan_bot_edit_dialogue(c, source, user_id, session_id):
    """Collect one task edit across messages without guessing missing parameters."""
    if not user_id:return False,None,None
    row=c.execute("SELECT * FROM bot_pending_action WHERE session_id=? AND user_id=? AND status='collecting' AND expire_at>? ORDER BY id DESC LIMIT 1",(session_id,user_id,now())).fetchone()
    prior=json.loads(row['action_json']) if row else None
    if not prior or prior.get('type')!='collect_task_edit':prior=None
    pattern='|'.join(re.escape(label) for label in sorted(BOT_EDIT_LABELS,key=len,reverse=True))
    labels=list(re.finditer(pattern,source));fields={BOT_EDIT_LABELS[match[0]] for match in labels}
    change=bool(re.search(r'修改|更改|更换|调整|变更|改为|改成|改到|改下|改一下|设置|换成|转交|转给|交给|指派给|改由',source))
    transfer=re.search(r'(?:转交给?|转给|交给|指派给|改由)\s*(.+?)(?:负责|处理|跟进|完成)?[。！!]*$',source)
    other=bool(re.search(r'^(?:(?:请|帮我|帮忙|麻烦)\s*)*(?:新建|新增|创建|添加|删除|作废|移除|开始|启动|提交|验收)\s*(?:任务|事项|#|部门|岗位|成员|员工|用户|责任域|例行)',source)
               or re.search(r'^(?:任务|#)\s*#?\d+\s*(?:开始|启动|完成并提交|提交验收|删除|验收)',source)
               or re.search(r'设置.*代理|(?:每天|每日|定时).*(?:汇总|进展|报告)',source))
    reference=bool(re.match(r'(?:(?:请|帮我|帮忙|麻烦)\s*)*(?:修改|更新|调整|变更|设置|更改)\s*(?:部门|岗位|成员|员工|用户|责任域|例行.*模板)',source))
    query=is_task_query_request(source) or bool(re.match(r'(?:查看|查询|看看|汇总|总结|列出)',source))
    fresh=not reference and not other and not query and len(fields)<=1 and bool(
        (fields and change) or transfer or (fields and task_number(source) and re.search(r'[:=]',source)))
    bare=bool(re.fullmatch(r'(?:#\s*\d+|(?:任务|task)\s*(?:编号\s*[:#]?\s*|[#:：\-]\s*)?\d+)[。]?',source,re.I))
    if prior and (other or reference or (change and not fresh and not re.match(r'^(?:改为|改成|改到|修改为|调整为|变更为|换成)',source))):
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE id=?",(row['id'],));prior=None
    if query:return False,None,None
    if not fresh and not prior:
        if not bare:return False,None,None
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id=? AND status='pending'",(session_id,user_id))
        tid=task_number(source);task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task:return True,None,f'未找到任务#{tid}。'
        if not bot_can_manage_task(c,user_id,task) and task['approver_id']!=user_id:return True,None,'无权查看该任务。'
        save_bot_task_context(c,session_id,user_id,tid)
        owner=c.execute('SELECT name FROM user WHERE id=?',(task['owner_id'],)).fetchone()
        return True,None,f'任务#{tid}《{task["title"]}》，当前责任人：{owner[0] if owner else "未配置"}，截止日期：{str(task["due_at"])[:10]}。请说明要查询或修改的内容。'
    if fresh:
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id=? AND status IN ('pending','collecting')",(session_id,user_id))
        scope=source[:labels[0].start()] if labels else source[:transfer.start()]
        numbers=re.findall(r'(?:任务|task)\s*(?:编号\s*[:#]?\s*|[#:：\-]\s*)?(\d+)',scope,re.I)
        if len(set(numbers))>1 or re.search(r'(?:任务|task)\s*#?\d+\s*[、,，和及]\s*(?:任务|#)?\s*\d+',scope,re.I):
            if row:c.execute("UPDATE bot_pending_action SET status='superseded' WHERE id=?",(row['id'],))
            return True,None,'请一次明确一条要修改的任务，例如“修改任务15的责任人”。'
        field=next(iter(fields)) if fields else 'owner_id'
        tid=task_number(source) or (prior.get('task_id') if prior else None) or referenced_task_id(c,session_id,user_id,source)
        proposal={'type':'collect_task_edit','field':field,'task_id':tid,'value':None}
        if labels:
            suffix=source[labels[0].end():]
            value=re.fullmatch(r'\s*[，,]?\s*(?:修改为|修改到|更改为|调整为|调整到|变更为|设置为|修改成|改为|改成|改到|换成|推迟到|提前到|延后到|为|是|到|[:=])\s*(.+)',suffix)
            if value:proposal['value']=value[1]
        elif transfer:proposal['value']=transfer[1]
    else:
        proposal=dict(prior);field=proposal['field']
        if bare:proposal['task_id']=task_number(source)
        elif labels:
            if fields!={field}:return True,None,'当前正在补充任务的'+BOT_EDIT_NAMES[field]+'；如需修改其他内容，请重新说明完整要求，或回复“取消”。'
            value=source[labels[0].end():]
            proposal['value']=re.sub(r'^\s*(?:修改为|修改到|更改为|调整为|调整到|变更为|设置为|改为|改成|改到|换成|为|是|到|[:=])?\s*','',value)
        else:proposal['value']=re.sub(r'^\s*(?:修改为|修改到|更改为|调整为|调整到|变更为|设置为|改为|改成|改到|换成|转给|交给)\s*','',source)
    tid=proposal['task_id'];task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone() if tid else None
    if tid and not task:
        if row:c.execute("UPDATE bot_pending_action SET status='failed' WHERE id=?",(row['id'],))
        return True,None,f'未找到任务#{tid}，请核对编号后重新说明修改要求。'
    if task and not bot_can_manage_task(c,user_id,task):
        if row:c.execute("UPDATE bot_pending_action SET status='failed' WHERE id=?",(row['id'],))
        return True,None,'只有任务责任人、管理 PM 或部门负责人可以修改任务。'
    if task:save_bot_task_context(c,session_id,user_id,tid)
    value=str(proposal.get('value') or '').strip(' \t。！!，,；;')
    example={'owner_id':'完整成员姓名，或“责任人：#成员编号”','due_at':'2030-01-15','priority':'P0、P1 或 P2'}.get(field,'修改后的完整内容')
    def collect(reply):
        assistant_management.store(c,session_id,user_id,proposal,'collecting')
        return True,None,reply
    if not task:return collect('要修改哪条任务的'+BOT_EDIT_NAMES[field]+'？请回复任务编号（如“#15”）或“任务15”。')
    if not value:
        current=''
        if field=='owner_id':
            owner=c.execute('SELECT name FROM user WHERE id=?',(task['owner_id'],)).fetchone();current='当前责任人是'+(owner[0] if owner else '未配置')+'。'
        return collect(f'已定位任务#{tid}《{task["title"]}》。'+current+'请提供新的'+BOT_EDIT_NAMES[field]+'（'+example+'）；补齐后我会回显供你确认。')
    try:
        if field=='owner_id':
            target=assistant_management.lookup(c,'user',value)
            changes={'owner_id':target['id']};action={'type':'change_owner','task_id':tid,'owner_id':target['id']};display=target['name']
        elif field=='due_at':
            due=valid_due_at(value) if re.match(r'^\d{4}-\d{2}-\d{2}T',value) else parse_due(value)
            if not due:raise ValueError('未识别新的截止日期，请提供明确日期，例如 2030-01-15。')
            changes={'due_at':due};action={'type':'change_due','task_id':tid,'due_at':due};display=due[:10]
        else:
            changes={field:value.upper() if field=='priority' else value};action={'type':'update_task','task_id':tid,'changes':changes};display=value
        bot_task_changes(c,task,changes)
    except (ValueError,LookupError) as exc:
        proposal['value']=None
        return collect(str(exc)+' 请继续补充新的'+BOT_EDIT_NAMES[field]+'（'+example+'）。')
    save_bot_action(c,session_id,user_id,action)
    return True,action,f'拟将任务#{tid}《{task["title"]}》的{BOT_EDIT_NAMES[field]}改为：{display}。\n回复“确认”执行，回复“取消”放弃。'


def plan_bot_action(c, text, user_id, session_id, allow_model=True):
    """Create an explicit, reviewable plan for non-creation work actions."""
    source=normalize_assistant_text(text)
    if re.fullmatch(r'(?:助手帮助|基础资料帮助|帮助|你能做什么|你会什么|怎么用|功能说明)[？?！!。]*',source): return None,BOT_HELP
    handled,action,reply=plan_bot_edit_dialogue(c,source,user_id,session_id)
    if handled:return action,reply
    handled,action,reply=assistant_management.plan(c,str(text),user_id,session_id,extract=model_management_proposal if allow_model and ai_configured() else None)
    if handled:return action,reply
    tid=task_number(source) or referenced_task_id(c,session_id,user_id,source); fallback_reply=None
    if not user_id: return None,'你的飞书账号尚未绑定系统成员，暂不能执行管理操作。'
    if tid:
        action,reply=plan_bot_task_fields(c,source,user_id,session_id,tid)
        if action or reply:return action,reply
    report_schedule=parse_report_schedule(c,source,user_id)
    if report_schedule:
        if report_schedule.get('missing'):
            return None,'可以配置定时任务进度汇总。请补充'+report_schedule['missing']+'，例如“每天18点汇总部门任务进展”。'
        actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
        if report_schedule['scope']=='department' and (not actor or actor['role'] not in ('manager','pm')):
            return None,'部门任务汇总仅可由管理PM或部门负责人配置；你可以配置个人或团队任务汇总。'
        if report_schedule['scope']=='team' and not (managed_user_ids(c,user_id) or (actor and actor['role'] in ('manager','pm'))):
            return None,'当前没有配置在你名下的直属成员，不能创建团队任务汇总。'
        action={'type':'create_scheduled_report',**report_schedule}; save_bot_action(c,session_id,user_id,action)
        labels={'department':'部门','team':'团队','self':'个人'}
        return action,f'拟设置每日 {report_schedule["schedule_time"]} 向你发送{labels[report_schedule["scope"]]}任务进展汇总。回复“确认”生效，回复“取消”放弃。'
    if tid and re.search(r'(?:开始|启动)(?:处理|执行)?|(?:任务|#).*?(?:开始|启动)',source):
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task: return None,f'未找到任务#{tid}。'
        if int(task['owner_id'])!=int(user_id): return None,'只有任务责任人可以开始任务。'
        if task['work_status']!='assigned': return None,f'任务#{tid}当前不是待开始状态。'
        action={'type':'start_task','task_id':tid}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟开始任务#{tid}《{task["title"]}》。回复“确认”执行，回复“取消”放弃。'
    if tid and re.search(r'(?:提交验收|完成并提交|完成任务|提请验收)',source):
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task: return None,f'未找到任务#{tid}。'
        if int(task['owner_id'])!=int(user_id): return None,'只有任务责任人可以提交验收。'
        if task['work_status']!='in_progress': return None,f'任务#{tid}当前不是进行中状态。'
        action={'type':'submit_acceptance','task_id':tid}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟提交任务#{tid}《{task["title"]}》等待上级验收。回复“确认”执行，回复“取消”放弃。'
    if re.search(r'(?:删除|移除|作废)',source):
        if not tid: return None,'请注明要删除的任务编号，例如：删除任务#15。'
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task: return None,f'未找到任务#{tid}。'
        if not bot_can_manage_task(c,user_id,task): return None,'只有任务责任人、管理PM或部门负责人可以删除任务。'
        action={'type':'delete_task','task_id':tid}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟删除任务#{tid}《{task["title"]}》及其确认、进度、验收记录。回复“确认”执行，回复“取消”放弃。'
    if '验收' in source and re.search(r'(?:验收|通过|驳回)',source):
        if not tid: return None,'请注明要验收的任务编号，例如：验收任务#15 通过。'
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task: return None,f'未找到任务#{tid}。'
        if task['work_status']!='awaiting_acceptance': return None,f'任务#{tid}当前不是待验收状态。'
        approver=task['approver_id'] or approver_for(c,task['owner_id'])
        if int(approver or 0)!=int(user_id): return None,'只有该任务责任人的上级确认人可以验收。'
        result='rejected' if '驳回' in source else 'passed'
        action={'type':'accept_task','task_id':tid,'result':result}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟{("驳回" if result=="rejected" else "验收通过")}任务#{tid}《{task["title"]}》。回复“确认”执行，回复“取消”放弃。'
    if tid and re.search(r'(?:确认任务|任务确认|确认进度|进度确认).*(?:通过|驳回)|(?:通过|驳回).*(?:确认任务|任务确认|确认进度|进度确认)',source):
        kind='progress_confirm' if '进度' in source else 'task_confirm'; result='rejected' if '驳回' in source else 'approved'
        approval=c.execute("SELECT * FROM approval_record WHERE task_id=? AND biz_type=? AND status='pending' ORDER BY id DESC LIMIT 1",(tid,kind)).fetchone()
        if not approval: return None,f'任务#{tid}没有待处理的{("进度" if kind=="progress_confirm" else "任务")}确认。'
        if int(approval['approver_id'] or 0)!=int(user_id): return None,'只有指定确认人可以执行该确认。'
        action={'type':'confirm_approval','approval_id':approval['id'],'result':result}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟{("驳回" if result=="rejected" else "通过")}任务#{tid}的{("进度" if kind=="progress_confirm" else "任务")}确认。回复“确认”执行，回复“取消”放弃。'
    owner_match=re.search(r'(?:责任人|负责人)(?:改为|变更为|调整为|改成|换成|为)\s*([\u4e00-\u9fffA-Za-z·]{2,20})',source)
    if not owner_match and tid:
        owner_match=re.search(r'(?:交给|转给|由)\s*([\u4e00-\u9fffA-Za-z·]{2,20})\s*(?:负责|处理|跟进|完成)',source)
    if owner_match and tid:
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone(); target=user_by_name(c,owner_match.group(1))
        if not task: return None,f'未找到任务#{tid}。'
        if not target: return None,f'未找到成员“{owner_match.group(1)}”。'
        if not bot_can_manage_task(c,user_id,task): return None,'只有任务责任人、管理PM或部门负责人可以变更责任人。'
        action={'type':'change_owner','task_id':tid,'owner_id':target['id']}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟将任务#{tid}《{task["title"]}》的责任人变更为{target["name"]}。回复“确认”执行，回复“取消”放弃。'
    field_match=re.search(r'(?:任务标题|标题|任务详情|详情|验收标准|交付物)\s*(?:改为|变更为|调整为)\s*[：:]?\s*(.+)$',source)
    if tid and field_match:
        task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
        if not task: return None,f'未找到任务#{tid}。'
        if not bot_can_manage_task(c,user_id,task): return None,'只有任务责任人、管理PM或部门负责人可以修改任务。'
        field='title' if ('标题' in field_match.group(0) and '详情' not in field_match.group(0)) else ('deliverable' if ('验收' in field_match.group(0) or '交付物' in field_match.group(0)) else 'description')
        value=field_match.group(1).strip('。；; ')
        if not value: return None,'请补充修改后的内容。'
        try:bot_task_changes(c,task,{field:value})
        except ValueError as exc:return None,str(exc)
        action={'type':'edit_field','task_id':tid,'field':field,'value':value}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟将任务#{tid}《{task["title"]}》的{ {"title":"标题","description":"详情","deliverable":"交付物/验收标准"}[field] }改为：{value}。回复“确认”执行，回复“取消”放弃。'
    due=parse_due(source)
    if due and is_deadline_change_request(source):
        if not tid:
            active=c.execute("SELECT * FROM task WHERE owner_id=? AND approval_status='approved' AND work_status NOT IN ('completed','cancelled') ORDER BY due_at,id",(user_id,)).fetchall()
            if len(active)==1:
                tid=active[0]['id']
            elif not active:
                return None,'你当前没有可修改截止日期的未完成任务。'
            else:
                options='、'.join(f"#{row['id']} {row['title']}" for row in active[:5])
                fallback_reply='你有多条未完成任务，请说明任务编号，例如：任务#15 截止日期改到10月28号。可选：'+options
        if tid:
            task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
            if not task: return None,f'未找到任务#{tid}。'
            if not bot_can_manage_task(c,user_id,task): return None,'只有任务责任人、管理PM或部门负责人可以变更截止日期。'
            action={'type':'change_due','task_id':tid,'due_at':due}; save_bot_action(c,session_id,user_id,action)
            return action,f'拟将任务#{tid}《{task["title"]}》截止日期调整为{due[:10]}。回复“确认”执行，回复“取消”放弃。'
    delegate=re.search(r'([\u4e00-\u9fffA-Za-z·]{2,20})\s*(?:代理|代为确认)\s*([\u4e00-\u9fffA-Za-z·]{2,20})',source)
    if delegate:
        principal=user_by_name(c,delegate.group(1)); proxy=user_by_name(c,delegate.group(2))
        if not principal or not proxy: return None,'未能识别被代理确认人或代理人，请使用系统内成员姓名。'
        actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
        if int(user_id)!=int(principal['id']) and not (actor and actor['role'] in ('manager','pm')): return None,'只能为自己设置确认代理；管理PM或部门负责人可代为配置。'
        dates=re.findall(r'\d{4}[年\-/]\d{1,2}[月\-/]\d{1,2}日?',source)
        start=parse_due(dates[0]) if dates else datetime.now(timezone.utc).date().isoformat()+'T00:00:00+08:00'
        end=parse_due(dates[1]) if len(dates)>1 else (datetime.now(timezone.utc)+timedelta(days=7)).date().isoformat()+'T23:59:59+08:00'
        action={'type':'set_delegate','user_id':principal['id'],'delegate_user_id':proxy['id'],'start_at':start,'end_at':end}; save_bot_action(c,session_id,user_id,action)
        return action,f'拟设置{principal["name"]}的确认代理为{proxy["name"]}（{start[:10]} 至 {end[:10]}）。回复“确认”执行，回复“取消”放弃。'
    if allow_model:
        command=model_bot_operation_command(c,source,user_id,session_id)
        if command and command!=source:
            return plan_bot_action(c,command,user_id,session_id,allow_model=False)
    return None,fallback_reply

def execute_bot_action(c, action, user_id):
    typ=action['type']; t=now()
    if typ=='manage_reference':return assistant_management.execute(c,action,user_id)
    if 'task_before' in action:
        current=rowdict(c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone())
        if current!=action['task_before']:raise ValueError('任务已发生变化，请重新提出操作并确认最新内容')
    if typ=='update_task':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone()
        if not task or not bot_can_manage_task(c,user_id,task):raise PermissionError('无权修改该任务')
        changes=bot_task_changes(c,task,action['changes'])
        if 'actual_hours' in changes and task['task_type']=='ROUTINE':changes['routine_hours_updated_at']=t
        if 'owner_id' in changes:changes['approver_id']=approver_for(c,changes['owner_id'])
        changes['updated_at']=t
        c.execute('UPDATE task SET '+','.join(key+'=?' for key in changes)+' WHERE id=?',(*changes.values(),task['id']))
        if 'owner_id' in changes:sync_bot_task_approver(c,task['id'],changes['approver_id'],user_id,t)
        audit(c,'task',task['id'],'assistant_update',str(user_id),before=dict(task),after=changes)
        return '任务内容已修改，已记录操作审计。'
    if typ in ('start_task','submit_acceptance'):
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone(); expected='assigned' if typ=='start_task' else 'in_progress'
        if not task or int(task['owner_id'])!=int(user_id) or task['work_status']!=expected: raise PermissionError('当前无法执行该任务状态变更')
        status='in_progress' if typ=='start_task' else 'awaiting_acceptance'; c.execute('UPDATE task SET work_status=?,updated_at=? WHERE id=?',(status,t,task['id'])); audit(c,'task',task['id'],typ,str(user_id)); return '任务已开始。' if typ=='start_task' else '已提交验收，等待上级确认。'
    if typ=='delete_task':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone()
        if not task or not bot_can_manage_task(c,user_id,task): raise PermissionError('无权删除该任务')
        c.execute('DELETE FROM approval_record WHERE task_id=?',(task['id'],)); c.execute('DELETE FROM progress_record WHERE task_id=?',(task['id'],)); c.execute('DELETE FROM feedback WHERE task_id=?',(task['id'],)); c.execute('DELETE FROM acceptance WHERE task_id=?',(task['id'],)); c.execute('DELETE FROM task WHERE id=?',(task['id'],)); audit(c,'task',action['task_id'],'delete',str(user_id),before=rowdict(task)); return '任务已删除。'
    if typ=='accept_task':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone(); approver=task['approver_id'] or approver_for(c,task['owner_id']) if task else None
        if not task or task['work_status']!='awaiting_acceptance' or int(approver or 0)!=int(user_id): raise PermissionError('当前无权验收该任务')
        c.execute('INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,?,?,?)',(task['id'],user_id,action['result'],'飞书机器人操作',t)); c.execute('UPDATE task SET work_status=?,updated_at=? WHERE id=?',('completed' if action['result']=='passed' else 'in_progress',t,task['id'])); audit(c,'task',task['id'],'accept' if action['result']=='passed' else 'reject',str(user_id)); return '已'+('验收通过。' if action['result']=='passed' else '驳回任务，已退回责任人继续处理。')
    if typ=='change_owner':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone()
        if not task or not bot_can_manage_task(c,user_id,task): raise PermissionError('无权变更该任务责任人')
        bot_task_changes(c,task,{'owner_id':action['owner_id']})
        approver=approver_for(c,action['owner_id'])
        c.execute('UPDATE task SET owner_id=?,approver_id=?,updated_at=? WHERE id=?',(action['owner_id'],approver,t,task['id']))
        sync_bot_task_approver(c,task['id'],approver,user_id,t)
        audit(c,'task',task['id'],'change_owner',str(user_id),before=rowdict(task),after=action); return '任务责任人已变更。'
    if typ=='change_due':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone()
        if not task or not bot_can_manage_task(c,user_id,task): raise PermissionError('无权变更截止日期')
        bot_task_changes(c,task,{'due_at':action['due_at']})
        c.execute('UPDATE task SET due_at=?,updated_at=? WHERE id=?',(action['due_at'],t,task['id'])); audit(c,'task',task['id'],'change_due',str(user_id),before=rowdict(task),after=action); return '任务截止日期已调整。'
    if typ=='edit_field':
        task=c.execute('SELECT * FROM task WHERE id=?',(action['task_id'],)).fetchone()
        if not task or not bot_can_manage_task(c,user_id,task): raise PermissionError('无权修改该任务')
        if action['field'] not in ('title','description','deliverable'): raise ValueError('不支持的修改字段')
        bot_task_changes(c,task,{action['field']:action['value']})
        c.execute(f"UPDATE task SET {action['field']}=?,updated_at=? WHERE id=?",(action['value'],t,task['id'])); audit(c,'task',task['id'],'edit_'+action['field'],str(user_id),before=rowdict(task),after=action); return '任务内容已修改。'
    if typ=='confirm_approval':
        approval=c.execute("SELECT * FROM approval_record WHERE id=? AND status='pending'",(action['approval_id'],)).fetchone()
        if not approval or int(approval['approver_id'] or 0)!=int(user_id): raise PermissionError('当前无权执行该确认')
        c.execute('UPDATE approval_record SET status=?,operated_by=?,operated_at=? WHERE id=?',(action['result'],user_id,t,approval['id']))
        if approval['biz_type']=='task_confirm': c.execute("UPDATE task SET approval_status=?,work_status=?,confirmed_by=?,confirmed_at=?,confirm_source='feishu',updated_at=? WHERE id=?",('approved' if action['result']=='approved' else 'rejected','assigned' if action['result']=='approved' else 'draft',user_id,t,t,approval['task_id']))
        elif approval['biz_type']=='progress_confirm' and action['result']=='approved':
            record=c.execute('SELECT * FROM progress_record WHERE id=?',(approval['biz_id'],)).fetchone(); c.execute('UPDATE progress_record SET status=?,effective_percent=?,confirmed_by=?,confirmed_at=? WHERE id=?',('approved',record['reported_percent'],user_id,t,record['id'])); c.execute('UPDATE task SET effective_percent=?,progress_percent=?,risk_status=?,expected_finish=?,work_status=?,updated_at=? WHERE id=?',(record['reported_percent'],record['reported_percent'],'blocked' if record['is_blocked'] else 'normal',record['expected_finish'],progress_work_status(c,approval['task_id'],record['reported_percent']),t,approval['task_id']))
        audit(c,'approval',approval['id'],action['result'],str(user_id),after=action); return '确认已通过。' if action['result']=='approved' else '已驳回。'
    if typ=='set_delegate':
        actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
        if not actor or (action['user_id']!=user_id and actor['role'] not in ('manager','pm')):raise PermissionError('当前无权设置该确认代理')
        if any(not c.execute('SELECT 1 FROM user WHERE id=?',(action[key],)).fetchone() for key in ('user_id','delegate_user_id')):raise ValueError('被代理人或代理成员不存在')
        c.execute('INSERT INTO delegate(user_id,delegate_user_id,scope,start_at,end_at) VALUES(?,?,?,?,?)',(action['user_id'],action['delegate_user_id'],'approval',action['start_at'],action['end_at'])); audit(c,'delegate',action['user_id'],'create',str(user_id),after=action); return '确认代理已设置。'
    if typ=='create_scheduled_report':
        if int(action.get('receiver_id') or 0)!=int(user_id): raise PermissionError('当前只支持为自己配置定时汇总')
        actor=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone(); scope=action.get('scope')
        if scope not in ('self','team','department') or not re.fullmatch(r'[0-2]\d:[0-5]\d',str(action.get('schedule_time') or '')): raise ValueError('定时汇总配置无效')
        if scope=='department' and (not actor or actor['role'] not in ('manager','pm')): raise PermissionError('无权配置部门任务汇总')
        if scope=='team' and not (managed_user_ids(c,user_id) or (actor and actor['role'] in ('manager','pm'))): raise PermissionError('当前没有可汇总的团队')
        existing=c.execute("SELECT id FROM scheduled_report WHERE created_by=? AND receiver_id=? AND scope=? AND frequency='daily' AND schedule_time=? AND enabled=1",(user_id,user_id,scope,action['schedule_time'])).fetchone()
        if existing: return '相同的定时汇总已存在，无需重复创建。'
        sid=c.execute("INSERT INTO scheduled_report(created_by,receiver_id,scope,frequency,schedule_time,enabled,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",(user_id,user_id,scope,'daily',action['schedule_time'],1,t,t)).lastrowid
        audit(c,'scheduled_report',sid,'create',str(user_id),after=action); return '定时任务已生效。'
    raise ValueError('不支持的机器人操作')


def sync_bot_task_approver(c, task_id, approver, user_id, stamp):
    task=c.execute('SELECT approval_status FROM task WHERE id=?',(task_id,)).fetchone()
    if task and task['approval_status']=='pending':
        if approver:
            c.execute("UPDATE approval_record SET approver_id=? WHERE task_id=? AND biz_type='task_confirm' AND status='pending'",(approver,task_id))
        else:
            c.execute("UPDATE approval_record SET status='approved',operated_at=?,operated_by=? WHERE task_id=? AND biz_type='task_confirm' AND status='pending'",(stamp,user_id,task_id))
            c.execute("UPDATE task SET approval_status='approved',confirmed_by=?,confirmed_at=?,confirm_source='feishu' WHERE id=?",(user_id,stamp,task_id))


def confirm_bot_action(c, user_id, session_id):
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')
    pending=c.execute("SELECT * FROM bot_pending_action WHERE session_id=? AND user_id IS ? AND status='pending' AND expire_at>? ORDER BY id DESC LIMIT 1",(session_id,user_id,now())).fetchone()
    if not pending:return '没有待确认的操作；请先补齐资料，或重新说明需要执行什么。'
    c.execute('SAVEPOINT bot_confirmation')
    try:
        reply=execute_bot_action(c,json.loads(pending['action_json']),user_id)
    except (ValueError,PermissionError,LookupError,sqlite3.IntegrityError) as exc:
        c.execute('ROLLBACK TO bot_confirmation')
        c.execute('RELEASE bot_confirmation')
        c.execute("UPDATE bot_pending_action SET status='failed',confirmed_at=? WHERE id=?",(now(),pending['id']))
        return '操作未执行：'+('资料发生冲突，请重新核对后提出操作。' if isinstance(exc,sqlite3.IntegrityError) else str(exc))
    c.execute('RELEASE bot_confirmation')
    c.execute("UPDATE bot_pending_action SET status='executed',confirmed_at=? WHERE id=?",(now(),pending['id']))
    return reply


def create_bot_task_draft(c,text,user_id,session_id):
    return assistant_dialogue.TaskDialogue(c,user_id,session_id,sys.modules[__name__]).update(text,start=True)

def process_bot_message(c, text, user_id, session_id):
    """Shared text workflow for the Feishu callback and WebSocket receiver."""
    reply=_process_bot_message(c,text,user_id,session_id)
    assistant_dialogue.record(c,session_id,user_id,text,reply,now())
    return reply

def _process_bot_message(c, text, user_id, session_id):
    source=normalize_assistant_text(text)
    dialogue=assistant_dialogue.TaskDialogue(c,user_id,session_id,sys.modules[__name__])
    dialogue_reply=dialogue.handle(text)
    if dialogue_reply:return dialogue_reply
    if re.fullmatch(r'(?:确认|确认执行|好的，?确认|是)[。！!]?',source):return confirm_bot_action(c,user_id,session_id)
    if re.fullmatch(r'(?:取消|取消操作|不确认|否)[。！!]?',source):
        c.execute("UPDATE bot_pending_action SET status='cancelled',confirmed_at=? WHERE session_id=? AND user_id IS ? AND status IN ('pending','collecting')",(now(),session_id,user_id))
        return '已取消待执行操作。'
    if re.match(r'^(?:新建|创建|新增|安排)\s*(?:一个|一项|一条)?\s*(?:任务|事项)(?!模板)',source):
        return create_bot_task_draft(c,text,user_id,session_id)
    report_reply=process_structured_progress_report(c,text,user_id,session_id)
    if report_reply:return report_reply
    if re.search(r'删除|移除|作废|修改|改为|改成|调整|变更|新建|新增|创建|添加|开始|启动|提交|验收|设置|配置|代理',source):
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id IS ? AND status='pending'",(session_id,user_id))
        row=c.execute("SELECT id,action_json FROM bot_pending_action WHERE session_id=? AND user_id IS ? AND status='collecting' ORDER BY id DESC LIMIT 1",(session_id,user_id)).fetchone()
        if row and json.loads(row['action_json']).get('type')=='collect_progress_report':
            c.execute("UPDATE bot_pending_action SET status='superseded' WHERE id=?",(row['id'],))
    action,reply=plan_bot_action(c,text,user_id,session_id,allow_model=not dialogue.model_failed)
    if action or reply:return reply
    progress,error=resolve_progress_message(c,source,user_id,session_id)
    if progress:
        try:
            _,_,task=submit_progress(c,progress['task_id'],user_id,progress['percent'],progress['comment'],progress['blocked'],progress['block_reason'],progress['expected_finish'],'feishu')
            return f'已记录《{task["title"]}》进度 {progress["percent"]}%，已提交上级确认。'
        except (ValueError,PermissionError) as exc:return str(exc)
    if error:return error
    intent=rule_bot_intent(source) if dialogue.model_failed else classify_bot_intent(source)
    if is_department_progress_query(source):return feishu_department_progress(c,user_id)
    if is_team_progress_query(source):return feishu_team_progress(c,user_id)
    if intent=='task_query':return feishu_task_list(c,user_id,session_id)
    if intent=='help':return BOT_HELP
    if intent=='task_creation':return create_bot_task_draft(c,source,user_id,session_id)
    return dialogue.clarify(text)


def model_management_proposal(text):
    """Model proposals remain inert until field/reference validation and confirmation."""
    try:
        content=ai_chat_completion([
            {'role':'system','content':'你是科技产品中心助手的基础资料参数提取器，只输出 JSON，不执行操作。'
             '格式：{"operation":"create或update","entity":"department/position/user/domain/subdomain/template","target":"修改成员时的完整姓名或#编号","fields":{...}}。'
             '只提取用户明确提供的信息，不猜测姓名、权限、账号、归属；不确定返回{}。仅 user 支持 update。'
             '字段白名单：'+json.dumps({key:sorted(value) for key,value in assistant_management.ALLOWED.items()},ensure_ascii=False)+
             '。name为新资料名称/修改后姓名，department为所属部门完整名称，owner为负责人完整姓名，position为岗位名称，leader为直属上级姓名，approver为确认人姓名。'
             'role取member/pm/manager，username为登录账号；template的owner支持用顿号分隔的多个完整姓名，不能按岗位推断人员；所有字段值必须是字符串；禁止提取密码、飞书绑定或用户声明的操作者身份。'},
            {'role':'user','content':text}],temperature=0,max_tokens=600,timeout=12)
        return parse_model_json(content)
    except Exception:return None

def extract_document_text(data, filename=''):
    """Best-effort local extraction for common text documents; never invents content."""
    name=(filename or '').lower()
    if name.endswith(('.txt','.md','.csv','.log')):
        return data.decode('utf-8',errors='replace').strip()
    if name.endswith('.docx'):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as doc:
                root=ET.fromstring(doc.read('word/document.xml'))
            return ''.join(root.itertext()).strip()
        except Exception:
            return ''
    return ''

def ai_configured():
    return all(os.getenv(key) for key in ('AI_API_BASE','AI_API_KEY','AI_MODEL'))

def ai_chat_completion(messages, temperature=0, max_tokens=800, timeout=20):
    """Call the configured OpenAI-compatible model through one guarded path.

    Every AI feature shares the same transport, response normalisation and
    timeout behaviour.  A model answer is always a suggestion: downstream
    identity, field and permission checks remain deterministic.
    """
    if not ai_configured():
        raise RuntimeError('AI model is not configured')
    payload={'model':os.getenv('AI_MODEL'),'messages':messages,'temperature':temperature,'max_tokens':max_tokens}
    req=urllib.request.Request(
        os.getenv('AI_API_BASE').rstrip('/')+'/chat/completions',
        json.dumps(payload,ensure_ascii=False).encode(),
        {'Authorization':'Bearer '+os.getenv('AI_API_KEY'),'Content-Type':'application/json'}
    )
    with urllib.request.urlopen(req,timeout=timeout) as response:
        result=json.load(response)
    choices=result.get('choices') or []
    if not choices: raise ValueError('AI response has no choices')
    content=(choices[0].get('message') or {}).get('content','')
    if isinstance(content,list):
        content=''.join(str(item.get('text','')) if isinstance(item,dict) else str(item) for item in content)
    content=str(content or '').strip()
    if not content: raise ValueError('AI response is empty')
    return content

def parse_model_json(content):
    """Tolerate harmless code fences but reject narrative model responses."""
    source=str(content or '').strip()
    source=re.sub(r'^```(?:json)?\s*|\s*```$','',source,flags=re.I).strip()
    try: return json.loads(source)
    except json.JSONDecodeError:
        match=re.search(r'(\{.*\}|\[.*\])',source,re.S)
        if not match: raise
        return json.loads(match.group(1))

def strip_decorative_emoji(text):
    """Remove decorative emoji from extracted materials without touching @mentions."""
    cleaned=re.sub(r'[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F\u200D]','',str(text or ''))
    return re.sub(r'[ \t]{2,}',' ',cleaned).strip()

def ai_vision_extract(data, mime_type):
    """Transcribe image text faithfully before the task extractor summarizes it."""
    if not ai_configured():
        raise ValueError('图片识别需要先配置 AI 视觉模型')
    encoded=base64.b64encode(data).decode()
    prompt=('请完整、逐行转写图片中所有可辨识的中文、英文、数字、@提及和标点，保持原有顺序。'
            '不要概括、不要筛选、不要纠错、不要只保留待办；即使是背景、风险结论或上下文也必须保留。'
            '只输出转写结果，不要说明。')
    payload={'model':os.getenv('AI_MODEL'),'messages':[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':f'data:{mime_type};base64,{encoded}'}}]}],'temperature':0}
    req=urllib.request.Request(os.getenv('AI_API_BASE').rstrip('/')+'/chat/completions',json.dumps(payload).encode(),{'Authorization':'Bearer '+os.getenv('AI_API_KEY'),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=45) as response:
        return strip_decorative_emoji(json.load(response)['choices'][0]['message']['content'])

def ai_audio_transcribe(data, filename, mime_type):
    """Transcribe an uploaded recording via an OpenAI-compatible transcription endpoint."""
    if not ai_configured():
        raise ValueError('录音识别需要先配置 AI 语音转写模型')
    boundary='----wmt'+secrets.token_hex(12)
    def part(name,value,extra=''):
        return f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{extra}\r\n\r\n'.encode()+value+b'\r\n'
    model=os.getenv('AI_TRANSCRIBE_MODEL') or os.getenv('AI_MODEL')
    body=part('model',model.encode())+part('file',data,f'; filename="{filename}"\r\nContent-Type: {mime_type}')+f'--{boundary}--\r\n'.encode()
    req=urllib.request.Request(os.getenv('AI_API_BASE').rstrip('/')+'/audio/transcriptions',body,{'Authorization':'Bearer '+os.getenv('AI_API_KEY'),'Content-Type':f'multipart/form-data; boundary={boundary}'})
    with urllib.request.urlopen(req,timeout=90) as response:
        return str(json.load(response).get('text','')).strip()

def extract_uploaded_text(data, filename, mime_type=''):
    """Return text from an uploaded meeting artifact; never create a task from raw binary."""
    if not data: raise ValueError('文件内容为空')
    if len(data)>20*1024*1024: raise ValueError('单个文件不能超过 20MB')
    suffix=Path(filename or '').suffix.lower()
    mime_type=mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
    if suffix in {'.txt','.md','.csv','.log','.docx'}:
        text=extract_document_text(data,filename)
    elif suffix=='.pdf':
        try:
            from pypdf import PdfReader
            text='\n'.join((page.extract_text() or '') for page in PdfReader(io.BytesIO(data)).pages).strip()
        except Exception:
            with tempfile.NamedTemporaryFile(suffix='.pdf') as file:
                file.write(data); file.flush()
                try: text=subprocess.check_output(['pdftotext',file.name,'-'],stderr=subprocess.DEVNULL,timeout=20).decode('utf-8',errors='replace').strip()
                except (FileNotFoundError,subprocess.CalledProcessError,subprocess.TimeoutExpired): text=''
        if not text: raise ValueError('未能从 PDF 提取文字；扫描件请作为图片上传后识别')
    elif suffix in {'.png','.jpg','.jpeg','.webp','.bmp'} or mime_type.startswith('image/'):
        text=ai_vision_extract(data,mime_type)
    elif suffix in {'.mp3','.wav','.m4a','.aac','.ogg','.mp4'} or mime_type.startswith('audio/'):
        text=ai_audio_transcribe(data,filename,mime_type)
    else:
        raise ValueError('仅支持图片、Word、PDF、文本和常见录音文件')
    text=strip_decorative_emoji(text)
    if not text: raise ValueError('未从文件中识别到可用于建单的文字')
    return text

def progress_work_status(c, task_id, percent):
    if percent!=100:return 'in_progress'
    task=c.execute('SELECT routine_week_start FROM task WHERE id=?',(task_id,)).fetchone()
    return 'awaiting_acceptance' if task and task['routine_week_start'] else 'completed'

def submit_progress(c, task_id, reporter_id, percent, comment, blocked=False, block_reason='', expected_finish=None, source='web'):
    task=c.execute('SELECT * FROM task WHERE id=?',(task_id,)).fetchone()
    if not task: raise ValueError('任务不存在')
    if task['approval_status']!='approved': raise ValueError('任务尚未确认生效')
    if int(reporter_id)!=int(task['owner_id']): raise PermissionError('只有任务责任人可以提交进度')
    if not isinstance(percent,int) or percent<0 or percent>100: raise ValueError('完成比例应为 0 至 100')
    if not comment: raise ValueError('请补充进度说明')
    t=now(); rid=c.execute("INSERT INTO progress_record(task_id,reported_percent,report_text,is_blocked,block_reason,expected_finish,reported_by,reported_at,source) VALUES(?,?,?,?,?,?,?,?,?)",(task_id,percent,comment,1 if blocked else 0,block_reason,expected_finish,reporter_id,t,source)).lastrowid
    aid=create_approval(c,'progress_confirm',rid,task_id,task['approver_id']); c.execute("UPDATE task SET work_status='in_progress',updated_at=? WHERE id=?",(t,task_id)); audit(c,'progress',rid,'report_pending',str(reporter_id),after={'percent':percent,'comment':comment,'blocked':blocked,'source':source})
    return rid,aid,task

def concise_task_title(text):
    """Extract a manageable task title from a meeting note without losing the source note."""
    source=task_instruction_text(text)
    source=re.sub(r'\s+',' ',source)
    # Screenshot/OCR material often puts the speaker before a space rather than
    # a colon.  It is context, not part of the task title.
    source=re.sub(r'^[\u4e00-\u9fff]{2,8}\s+(?=(?:请|由|今天|明天|后天|本周|下周|完成|推进|落实|组织|协调))','',source)
    source=re.sub(r'[@＠][\w\-\u4e00-\u9fff]+','',source).strip()
    # A meeting is a concrete, independently checkable next action.  Prefer it
    # over the policy/background wording that often appears before it.
    meeting=re.search(r'(?:我们)?(?:先)?(?:和|与)\s*([^，。；；]+?)\s*(?:开个会|开会|召开(?:沟通)?会)',source)
    if meeting:
        counterpart=meeting.group(1).strip(' ，,。；;')
        return ('与'+counterpart+'召开沟通会')[:40]
    coordinated=re.search(r'由\s*(?:公司)?\s*协调\s*([^，。；\n]+)',source)
    if coordinated:
        candidate='协调'+coordinated.group(1).strip()
        return candidate[:40]
    # The action after “请某人负责完成” is normally the task itself.
    action=re.search(r'(?:请[^，。；]{1,20}?负责)?(?:完成|推进|落实|组织|开展|协同)([^。；]+)',source)
    candidate=action.group(1).strip() if action else source
    candidate=re.split(r'[，,](?:并)?(?:于|在)\s*\d{4}\s*年|[，,](?:请)?(?:于|在)\s*\d{4}[-/]',candidate)[0].strip(' ，,。；;')
    candidate=re.sub(r'(?:并)?(?:于|在)\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*$','',candidate).strip(' ，,。；;')
    if not candidate: candidate='待明确工作事项'
    return candidate[:40]

def task_instruction_text(text):
    """Prefer the final actionable speaker turn over earlier status updates."""
    source=re.sub(r'【[^】]*】','',str(text or '')).strip()
    # Chat screenshots usually put a speaker name on a separate line.  Preserve
    # turns instead of treating the complete screenshot as one sentence, then
    # use the last actionable turn so an earlier report cannot become a task.
    turns=re.split(r'(?:^|\n)\s*[\u4e00-\u9fff]{2,8}\s*(?=\n)',source)
    if len(turns)>1:
        actionable=[turn.strip() for turn in turns if re.search(r'(?:[@＠]|请|由|完成|推进|推动|落实|组织|开展|协同|本周|下周|截止|安排)',turn)]
        if actionable:
            return actionable[-1]
    # A pasted chat may use normal punctuation rather than separate speaker
    # rows.  Select the latest explicit instruction instead of summarising the
    # complete thread (which would otherwise turn an earlier report into a task).
    # A chat OCR export often omits punctuation before an @mention. Treat that
    # mention as the beginning of a new semantic clause, otherwise an earlier
    # background sentence can hide the actual assignment that follows it.
    sentences=[part.strip() for part in re.split(r'(?<=[。！？；;])|\n|(?=[@＠])',source) if part.strip()]
    actionable=[part for part in sentences if re.search(r'(?:请|由|安排|需要).{0,30}(?:负责|牵头|完成|推进|推动|落实|组织|开展|协同|协调)|(?:今天|明天|后天|本周|下周).{0,28}(?:完成|推进|落实|处理)|[@＠].{0,40}(?:开个会|开会|召开|沟通|完成|推进|落实|处理)',part)]
    if actionable:
        return actionable[-1]
    return source

def task_detail(text):
    """Keep the work scope, but omit meeting headers and fields already structured."""
    source=task_instruction_text(text)
    source=re.sub(r'\s+',' ',source)
    source=re.sub(r'[@＠][\w\-\u4e00-\u9fff]+','',source).strip()
    meeting=re.search(r'(?:我们)?(?:先)?(?:和|与)\s*([^，。；；]+?)\s*(?:开个会|开会|召开(?:沟通)?会)',source)
    if meeting:
        # Chat OCR frequently drops punctuation between the business objective,
        # @mentions and "我们先…开会".  Stop at a mention or a run of spaces,
        # rather than letting the objective swallow the actual next action.
        before=source[:meeting.start()]
        objectives=re.findall(r'(?:协调|推动)[^，。；；@＠]*?(?=\s{2,}|$)',before)
        objective=(objectives[-1].strip(' ，,。；;') if objectives else '')
        counterpart=meeting.group(1).strip(' ，,。；;')
        if objective:
            return f'{objective}；与{counterpart}召开沟通会'[:240]
    # In multi-speaker records preserve the final instruction as the scope,
    # while date/owner stay in their structured fields.
    raw_without_header=re.sub(r'【[^】]*】','',str(text or '')).strip()
    is_multi_turn=len(re.findall(r'(?:^|\n)\s*[\u4e00-\u9fff]{2,8}\s*(?=\n)',raw_without_header))>=2
    if is_multi_turn:
        source=re.sub(r'(?:本|下)周[一二三四五六日天][之前前]?\s*(?:完成)?','',source)
        return source.strip(' ，,。；;')[:240] or '待补充任务范围'
    action=re.search(r'(?:请[^，。；]{1,20}?负责)?(?:完成|推进|落实|组织|开展|协同)([^。；]+)',source)
    detail=(action.group(1).strip() if action else concise_task_title(source))
    detail=re.split(r'[，,](?:并)?(?:于|在)\s*\d{4}\s*年|[，,](?:请)?(?:于|在)\s*\d{4}[-/]',detail)[0]
    detail=re.sub(r'(?:并)?(?:于|在)\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*$','',detail).strip(' ，,。；;')
    return detail or '待补充任务范围'

def similar_tasks(c, title, limit=5):
    """Find likely duplicate tasks without treating a fuzzy match as a hard block."""
    source=re.sub(r'\s|[，,。；;：:、（）()【】\[\]·—-]','',str(title or '')).lower()
    if len(source)<2: return []
    source_chars=set(source)
    candidates=[]
    for task in c.execute("SELECT id,title,owner_id,due_at,approval_status,work_status FROM task WHERE approval_status<>'rejected' ORDER BY updated_at DESC"):
        target=re.sub(r'\s|[，,。；;：:、（）()【】\[\]·—-]','',task['title']).lower()
        if not target: continue
        score=1.0 if source in target or target in source else len(source_chars & set(target))/max(1,len(source_chars | set(target)))
        if score>=0.42: candidates.append((score,task))
    return [dict(rowdict(task),similarity=round(score,2)) for score,task in sorted(candidates,key=lambda item:item[0],reverse=True)[:limit]]

def normalized_task_title(value):
    """Canonical form used only for duplicate detection, never for display."""
    return re.sub(r'\s|[，,。；;：:、（）()【】\[\]·—-]', '', str(value or '')).lower()

def task_duplicate_candidates(c, slots, exclude_draft_id=None):
    """Return exact blocks and softer same-owner/same-date similarity warnings.

    A draft is not a task until confirmed, so duplicate detection must include
    both stored tasks and still-live drafts.  This prevents two clicks (or two
    pasted copies of the same meeting note) from silently creating duplicates.
    """
    title = normalized_task_title(slots.get('title'))
    owner_id = slots.get('owner_id')
    due_date = str(slots.get('due_at') or '')[:10]
    if not title or not owner_id or not due_date:
        return {'exact': [], 'similar': []}

    exact, similar = [], []
    source_chars = set(title)

    def consider(item, source, item_id):
        candidate_title = normalized_task_title(item.get('title'))
        if not candidate_title or str(item.get('owner_id')) != str(owner_id) or str(item.get('due_at') or '')[:10] != due_date:
            return
        payload = {
            'source': source, 'id': item_id, 'title': item.get('title') or '未命名任务',
            'owner_id': item.get('owner_id'), 'due_at': item.get('due_at'),
        }
        if candidate_title == title:
            exact.append(payload)
            return
        score = 1.0 if title in candidate_title or candidate_title in title else len(source_chars & set(candidate_title)) / max(1, len(source_chars | set(candidate_title)))
        # Same owner and due date substantially narrow the candidate set.  A
        # 0.55 threshold catches common action-word variations ("完成…" vs
        # "…处理") while keeping this as a warning rather than a hard block.
        if score >= 0.55:
            payload['similarity'] = round(score, 2)
            similar.append(payload)

    for row in c.execute("SELECT id,title,owner_id,due_at FROM task WHERE approval_status<>'rejected' ORDER BY updated_at DESC"):
        consider(rowdict(row), 'task', row['id'])
    for row in c.execute("SELECT id,slots_json FROM conversation_draft WHERE status IN ('collecting','preview') AND expire_at>?", (now(),)):
        if exclude_draft_id is not None and int(row['id']) == int(exclude_draft_id):
            continue
        try:
            draft_slots = json.loads(row['slots_json'] or '{}')
        except (TypeError, json.JSONDecodeError):
            continue
        consider(draft_slots, 'draft', row['id'])
    return {'exact': exact, 'similar': similar}

def detect_task_owner(c, text):
    """Recognize an explicit assignee, including a system-member @mention."""
    source=str(text or '')
    for user in c.execute('SELECT id,name FROM user ORDER BY length(name) DESC'):
        name=re.escape(user['name'])
        if re.search(rf'(?:请|由)\s*{name}\s*(?:负责|牵头|完成|推进|落实|组织|开展|协同)',source) or re.search(rf'(?:任务)?责任人\s*[:：]\s*{name}(?![\u4e00-\u9fffA-Za-z0-9_])',source):
            return user['id']
    # Materials pasted from chat/screenshots commonly use @姓名 instead of a
    # full "请某某负责" sentence.  An exact mention of a configured member is
    # still an explicit assignment signal; unknown mentions (such as @LM) are
    # ignored rather than guessed.
    for user in c.execute('SELECT id,name FROM user ORDER BY length(name) DESC'):
        name=re.escape(user['name'])
        if re.search(rf'[@＠]\s*{name}(?![\u4e00-\u9fffA-Za-z0-9_])',source):
            return user['id']
    # Short operational messages often omit “请/由”，e.g. “刘明今天下班前
    # 完成客户投诉工单闭环处理”。 Treat it as an assignment only when the name
    # starts the instruction and no other explicit assignee exists.
    for user in c.execute('SELECT id,name FROM user ORDER BY length(name) DESC'):
        name=re.escape(user['name'])
        if re.search(rf'^\s*{name}\s*(?:今天|明天|后天|本周|下周|在|于)?[^。；\n]{{0,24}}(?:完成|发出|发送|提交|发布|编写|整理|评审|测试|部署|巡检|检查|跟进|推进|落实|处理|组织|开展|协同)',source):
            return user['id']
    return None

def normalize_legacy_conversation_tasks(c):
    """One-time-safe cleanup for drafts created before title/detail separation."""
    rows=c.execute("SELECT id,title,description,owner_id,approver_id FROM task WHERE source='conversation'").fetchall()
    for row in rows:
        source=row['description'] or row['title']
        # Older records may already have had their detail trimmed. Recover the
        # original note from the generated draft when available for owner repair.
        if not re.search(r'(?:请|由)\s*.+?(?:负责|牵头|完成|推进|落实|组织|开展|协同)',source):
            raw=c.execute("SELECT raw_text FROM conversation_draft WHERE status='generated' AND raw_text LIKE ? ORDER BY updated_at DESC LIMIT 1",('%'+row['title']+'%',)).fetchone()
            if raw: source=raw['raw_text']
        title=concise_task_title(source) if len(row['title'])>40 else row['title']
        owner=detect_task_owner(c,source)
        changes={'title':title,'description':task_detail(source)}
        if owner:changes.update(owner_id=owner,approver_id=approver_for(c,owner))
        changes={key:value for key,value in changes.items() if row[key]!=value}
        if changes:
            changes['updated_at']=now()
            c.execute('UPDATE task SET '+','.join(key+'=?' for key in changes)+' WHERE id=?',(*changes.values(),row['id']))

def ai_draft(text, c=None):
    """Creates a non-binding task draft. Uses an OpenAI-compatible endpoint when configured."""
    text=strip_decorative_emoji(text)
    api_base, api_key, model = os.getenv('AI_API_BASE'), os.getenv('AI_API_KEY'), os.getenv('AI_MODEL')
    raw_without_header=re.sub(r'【[^】]*】','',str(text or '')).strip()
    instruction=task_instruction_text(text)
    if api_base and api_key and model:
        own_connection=c is None; c=c or db()
        try:
            domains=[rowdict(row) for row in c.execute("SELECT name,boundary FROM org_node WHERE node_type='domain' AND inactive_at IS NULL ORDER BY id")]
            members=[row['name'] for row in c.execute('SELECT name FROM user ORDER BY name,id')]
        finally:
            if own_connection: c.close()
        domain_text='；'.join(f"{row['name']}：{row['boundary']}" for row in domains) or '无可选责任域'
        member_text='、'.join(members) or '无可选成员'
        prompt = ('你是工作管理树的任务草稿提炼助手。只提炼材料中明确提出、尚未完成、需要后续推进的工作事项；忽略会议标题、称呼、参会人员、背景说明、讨论过程、已完成事项和无关信息。'
                  '若材料包含多人对话或多段发言，必须区分“前文汇报/结论”和“后文安排”：仅后者的明确待办可生成草稿，后续指令优先于前文汇报。'
                  '责任人只能从该待办指令中明确指定或 @ 的成员识别，绝不可把汇报人、被汇报对象、未知简称或上下文出现的人名当作责任人。'
                  '只输出一个 JSON 对象（禁止使用数组或 Markdown 代码块），不要输出解释。字段为 title,owner_name,due_at,suggested_domain,priority,task_type,description,confidence。'
                  'title 为不超过40字的简洁工作标题，不包含责任人、日期、优先级或背景；无明确待办时为 null。'
                  'owner_name 仅在材料明确指定责任人时填写，且必须从以下系统成员中精确选择：'+member_text+'；否则为 null，不得猜测。'
                  'due_at 使用 YYYY-MM-DD；只能填写明确或可准确换算的截止日期，否则为 null。'
                  'suggested_domain 只能从以下已有责任域中精确选择：'+domain_text+'；无法判断或有歧义时为 null。'
                  'priority 只能为 P0、P1、P2；task_type 只能为 ROUTINE（例行）、PROJECT（专项）、ADHOC（临时）；无法判断时均为 null。'
                  'description 仅提炼任务范围、关键动作、约束和必要上下文，不重复标题、责任人或日期；信息不足时为 null。'
                  '上述字段均为可编辑草稿建议，允许为空；confidence 为 0 到 1 的数字。材料：' + text)
        try:
            content=ai_chat_completion([
                {'role':'system','content':'你是企业任务提炼助手。必须只输出合法 JSON；宁可留空，也不得把汇报、背景或已完成事项当作新任务。'},
                {'role':'user','content':prompt}
            ], temperature=0.05, max_tokens=900, timeout=20)
            draft=parse_model_json(content)
            # Some otherwise compatible providers return a one-item JSON array
            # despite being asked for an object. Accept that shape explicitly,
            # but reject every other non-object response so the safe fallback
            # remains in effect for malformed model output.
            if isinstance(draft,list):
                draft=draft[0] if len(draft)==1 else None
            if not isinstance(draft,dict):
                raise ValueError('model draft must be a JSON object')
            title=(draft.get('title') or '').strip()
            # A later speaker's instruction takes precedence over an earlier
            # report, even when a model tries to summarize the whole thread.
            multi_turn=len(re.findall(r'(?:^|\n)\s*[\u4e00-\u9fff]{2,8}\s*(?=\n)',raw_without_header))>=2
            if multi_turn or not title or len(title)>40 or title in text:
                title=concise_task_title(text)
            description=task_detail(text) if multi_turn else (task_detail(draft['description']) if draft.get('description') else None)
            return {'title':title, 'owner_name':draft.get('owner_name'), 'description':description, 'due_at':draft.get('due_at') or parse_due(instruction), 'suggested_domain':draft.get('suggested_domain'), 'priority':draft.get('priority'), 'task_type':draft.get('task_type'), 'confidence':draft.get('confidence',0.5), 'requires_human_confirmation':True, 'mode':'model'}
        except Exception:
            # Safe fallback: a model outage must never block a human-created task.
            pass
    due=parse_due(instruction) or parse_due(text)
    return {'title':concise_task_title(text), 'owner_name':None, 'description':task_detail(text), 'due_at': due, 'suggested_domain':None, 'priority':None, 'task_type':None, 'confidence':0.35 if not due else 0.65, 'requires_human_confirmation':True, 'mode':'rule'}

class WeComAdapter:
    def __init__(self): self.configured=all(os.getenv(k) for k in ('WECOM_CORP_ID','WECOM_AGENT_ID','WECOM_SECRET'))
    def send(self, userid, text):
        if not self.configured: return {'sent':False,'mode':'dry-run','userid':userid,'text':text}
        # Token/message calls are intentionally isolated here; credentials never enter the database.
        q=urllib.parse.urlencode({'corpid':os.environ['WECOM_CORP_ID'],'corpsecret':os.environ['WECOM_SECRET']})
        with urllib.request.urlopen('https://qyapi.weixin.qq.com/cgi-bin/gettoken?'+q, timeout=10) as f: token=json.load(f)['access_token']
        payload=json.dumps({'touser':userid,'msgtype':'text','agentid':int(os.environ['WECOM_AGENT_ID']),'text':{'content':text}}).encode()
        req=urllib.request.Request('https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token='+token,payload,{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as f: return {'sent':True,'response':json.load(f)}

class FeishuAdapter:
    """Minimal Feishu tenant-token sender and HTTP event verifier; secrets stay in env."""
    def __init__(self): self.app_id=os.getenv('FEISHU_APP_ID'); self.secret=os.getenv('FEISHU_APP_SECRET'); self.verify_token=os.getenv('FEISHU_VERIFICATION_TOKEN')
    def configured(self): return bool(self.app_id and self.secret and self.verify_token)
    def login_configured(self): return bool(self.app_id and self.secret and feishu_redirect_uri())
    def authorization_url(self, state):
        if not self.login_configured(): raise RuntimeError('Feishu login is not configured')
        query=urllib.parse.urlencode({'client_id':self.app_id,'redirect_uri':feishu_redirect_uri(),'state':state,'response_type':'code'})
        return 'https://accounts.feishu.cn/open-apis/authen/v1/authorize?'+query
    def qr_authorization_url(self, state):
        # QR SDK 1.0.3 explicitly supports the earlier OAuth login process only.
        query=urllib.parse.urlencode({'client_id':self.app_id,'redirect_uri':feishu_redirect_uri(),'state':state,'response_type':'code'})
        return 'https://passport.feishu.cn/suite/passport/oauth/authorize?'+query
    def exchange_qr_login_code(self, code):
        payload={'grant_type':'authorization_code','client_id':self.app_id,'client_secret':self.secret,
                 'code':code,'redirect_uri':feishu_redirect_uri()}
        req=urllib.request.Request('https://passport.feishu.cn/suite/passport/oauth/token',
            urllib.parse.urlencode(payload).encode(),{'Content-Type':'application/x-www-form-urlencoded'})
        with urllib.request.urlopen(req,timeout=10) as response:out=json.load(response)
        if out.get('error') or not out.get('access_token'):raise RuntimeError('Feishu QR authorization failed')
        return out
    def qr_login_open_id(self, access_token):
        req=urllib.request.Request('https://passport.feishu.cn/suite/passport/oauth/userinfo',
            headers={'Authorization':'Bearer '+access_token})
        with urllib.request.urlopen(req,timeout=10) as response:out=json.load(response)
        if out.get('error') or not out.get('open_id'):raise RuntimeError('Feishu QR identity unavailable')
        return out['open_id']
    def exchange_login_code(self, code):
        if not self.login_configured(): raise RuntimeError('Feishu login is not configured')
        payload={'grant_type':'authorization_code','code':code,'client_id':self.app_id,'client_secret':self.secret,'redirect_uri':feishu_redirect_uri()}
        req=urllib.request.Request('https://open.feishu.cn/open-apis/authen/v2/oauth/token',json.dumps(payload).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as f: out=json.load(f)
        data=out.get('data') or out
        if out.get('code',0)!=0 or not data.get('access_token'): raise RuntimeError(out.get('msg','Feishu authorization failed'))
        return data
    def login_open_id(self, access_token):
        req=urllib.request.Request('https://open.feishu.cn/open-apis/authen/v1/user_info',headers={'Authorization':'Bearer '+access_token})
        with urllib.request.urlopen(req,timeout=10) as f: out=json.load(f)
        data=out.get('data') or out
        if out.get('code',0)!=0 or not data.get('open_id'): raise RuntimeError(out.get('msg','Feishu user identity unavailable'))
        return data['open_id']
    def token(self):
        req=urllib.request.Request('https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal',json.dumps({'app_id':self.app_id,'app_secret':self.secret}).encode(),{'Content-Type':'application/json'})
        with urllib.request.urlopen(req,timeout=10) as f:
            out=json.load(f)
        if out.get('code',0)!=0: raise RuntimeError('feishu token request failed')
        return out['tenant_access_token']
    def send_text(self, open_id, text):
        if not self.configured(): return {'sent':False,'mode':'dry-run','open_id':open_id,'text':text}
        req=urllib.request.Request('https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type=open_id',json.dumps({'receive_id':open_id,'msg_type':'text','content':json.dumps({'text':text},ensure_ascii=False)}).encode(),{'Authorization':'Bearer '+self.token(),'Content-Type':'application/json; charset=utf-8'})
        with urllib.request.urlopen(req,timeout=10) as f: return {'sent':True,'response':json.load(f)}
    def download_message_resource(self, message_id, file_key, resource_type='file'):
        if not self.configured(): raise RuntimeError('Feishu integration is not configured')
        query=urllib.parse.urlencode({'type':resource_type})
        url=f'https://open.feishu.cn/open-apis/im/v1/messages/{urllib.parse.quote(message_id)}/resources/{urllib.parse.quote(file_key)}?{query}'
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token()})
        with urllib.request.urlopen(req,timeout=30) as f: return f.read()
    def get_user_profile(self, open_id):
        if not self.configured(): raise RuntimeError('Feishu integration is not configured')
        url='https://open.feishu.cn/open-apis/contact/v3/users/'+urllib.parse.quote(open_id)+'?user_id_type=open_id'
        req=urllib.request.Request(url,headers={'Authorization':'Bearer '+self.token()})
        with urllib.request.urlopen(req,timeout=10) as f: out=json.load(f)
        if out.get('code',0)!=0: raise RuntimeError(out.get('msg','Feishu contact lookup failed'))
        user=(out.get('data') or {}).get('user') or {}; avatar=user.get('avatar') or {}
        return {'name':user.get('name',''),'avatar_url':avatar.get('avatar_240') or avatar.get('avatar_72') or avatar.get('avatar_origin') or ''}

class WeComCallback:
    """Enterprise WeChat secure callback codec and feedback mapper."""
    def __init__(self):
        self.token=os.getenv('WECOM_TOKEN'); self.aes_key=os.getenv('WECOM_AES_KEY'); self.receive_id=os.getenv('WECOM_CORP_ID','')
    def configured(self): return bool(self.token and self.aes_key and len(self.aes_key)==43)
    def signature(self, timestamp, nonce, encrypted):
        return hashlib.sha1(''.join(sorted([self.token,timestamp,nonce,encrypted])).encode()).hexdigest()
    def decrypt(self, encrypted):
        from Crypto.Cipher import AES
        key=base64.b64decode(self.aes_key+'=')
        raw=AES.new(key,AES.MODE_CBC,key[:16]).decrypt(base64.b64decode(encrypted)); pad=raw[-1]
        if pad<1 or pad>32: raise ValueError('invalid PKCS7 padding')
        raw=raw[:-pad]; length=struct.unpack('>I',raw[16:20])[0]
        message=raw[20:20+length].decode('utf-8'); receive_id=raw[20+length:].decode('utf-8')
        if self.receive_id and receive_id and receive_id != self.receive_id: raise ValueError('receive id mismatch')
        return message
    def task_id_from_text(self, text):
        m=re.search(r'(?:任务|task)\s*[#编号：:\-]?\s*(\d+)',text,re.I)
        return int(m.group(1)) if m else None
    def handle_message(self, c, xml_text):
        root=ET.fromstring(xml_text); userid=root.findtext('FromUserName') or ''; msg_type=root.findtext('MsgType') or 'unknown'; content=root.findtext('Content') or ''
        task_id=self.task_id_from_text(content)
        c.execute('INSERT INTO wecom_message(userid,msg_type,content,task_id,received_at) VALUES(?,?,?,?,?)',(userid,msg_type,content,task_id,now()))
        user_row=c.execute('SELECT id FROM user WHERE wecom_userid=?',(userid,)).fetchone(); internal_user_id=user_row[0] if user_row else None
        if task_id and c.execute('SELECT 1 FROM task WHERE id=?',(task_id,)).fetchone():
            c.execute('INSERT INTO feedback(task_id,user_id,content,parsed_due_at,progress,risk,created_at) VALUES(?,?,?,?,?,?,?)',(task_id,internal_user_id,content,parse_due(content),content,'',now()))
            c.execute('UPDATE task SET updated_at=? WHERE id=?',(now(),task_id)); audit(c,'task',task_id,'wecom_feedback',userid,after={'content':content})
        return {'userid':userid,'msg_type':msg_type,'content':content,'task_id':task_id}

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def cookie(self, name):
        for value in self.headers.get('Cookie','').split(';'):
            key,sep,item=value.strip().partition('=')
            if sep and key==name: return item
        return ''
    def session_user(self):
        session_id=self.cookie('wmt_session')
        if not session_id: return None
        c=db()
        try:
            row=c.execute("SELECT u.* FROM login_session s JOIN user u ON u.id=s.user_id WHERE s.session_id=? AND s.expire_at>?",(session_id,now())).fetchone()
            if row: c.execute('UPDATE login_session SET last_seen_at=? WHERE session_id=?',(now(),session_id)); c.commit()
            return accounts.public_user(c,row) if row else None
        finally: c.close()
    def actor_id(self, submitted=None): return getattr(self,'authenticated_user_id',None) or submitted
    def handle_monthly_reports(self, path, query=None):
        # Test-only identity switching is opt-in; real login sessions always win.
        user=self.session_user()
        demo=not user and monthly_demo_enabled()
        if not user and not demo:
            return self.json(401,{'error':'monthly_login_required','message':'个人月报需要员工本人登录，演示身份不能访问私人月报。',
                                  'login_url':'/auth/feishu/start' if feishu_login_enabled() else None})
        c=db()
        try:
            query=query or {}; body=None
            if self.command!='GET':
                body=self.body()
                if not isinstance(body,dict):raise ValueError('请求须为 JSON 对象')
                submitted=body.pop('actor_id',None)
            else:submitted=query.get('actor_id',[None])[0]
            if user:uid=int(user['id'])
            else:
                if type(submitted) is bool or submitted is None:raise ValueError('请选择演示员工身份')
                try:uid=int(submitted)
                except (TypeError,ValueError):raise ValueError('演示员工身份无效')
                monthly_reports.actor(c,uid)
            if self.command=='GET':
                if path=='/api/monthly-reports':
                    out=monthly_reports.list_reports(c,uid,query.get('month',[None])[0],query.get('scope',['mine'])[0],query.get('user_id',[None])[0])
                else:
                    match=re.fullmatch(r'/api/monthly-reports/(\d+)(/text|/export)?',path)
                    if not match: return self.json(404,{'error':'月报接口不存在'})
                    out=monthly_reports.get_report(c,int(match[1]),uid)
                    if match[2]=='/text':out={'text':monthly_reports.text_export(out)}
                    elif match[2]=='/export':out={'month':out['month'],'owner':out['snapshot']['owner'],'outline':out['editorial']['outline']}
            else:
                if path=='/api/monthly-reports/generate':
                    if set(body)-{'month'}:raise ValueError('只能生成登录员工本人的月报')
                    out=monthly_reports.generate(c,uid,body.get('month'))
                else:
                    match=re.fullmatch(r'/api/monthly-reports/(\d+)(/revise)?',path)
                    if not match:return self.json(404,{'error':'月报接口不存在'})
                    if match[2]:
                        if body:raise ValueError('修订请求不接受额外参数')
                        out=monthly_reports.revise(c,int(match[1]),uid)
                    else:out=monthly_reports.update(c,int(match[1]),uid,body)
                c.commit()
            return self.json(200,out)
        except PermissionError as exc:return self.json(403,{'error':str(exc)})
        except LookupError as exc:return self.json(404,{'error':str(exc)})
        except monthly_reports.Conflict as exc:return self.json(409,{'error':str(exc)})
        except (ValueError,TypeError,RecursionError) as exc:return self.json(400,{'error':str(exc) or '导图内容无效'})
        finally:c.close()
    def handle_weekly_reports(self, path, query=None):
        c=db()
        try:
            query=query or {}
            if self.command=='GET':
                user_id=self.actor_id(query.get('actor_id',[None])[0])
                if path=='/api/weekly-reports':
                    out=weekly_reports.list_reports(c,user_id,query.get('year',[None])[0],query.get('month',[None])[0],query.get('week',[None])[0])
                else:
                    match=re.fullmatch(r'/api/weekly-reports/(\d+)(/text|/changes)?',path)
                    if not match: return self.json(404,{'error':'周报接口不存在'})
                    out=weekly_reports.get_report(c,int(match[1]),user_id)
                    if match[2]=='/text': out={'text':weekly_reports.meeting_text(out)}
                    elif match[2]=='/changes': out=weekly_reports.report_changes(c,int(match[1]),user_id)
            else:
                body=self.body()
                if not isinstance(body,dict): raise ValueError('请求须为 JSON 对象')
                user_id=self.actor_id(body.pop('actor_id',None))
                if path=='/api/weekly-reports/current':
                    if body: raise ValueError('本周周报请求不接受额外参数')
                    out=weekly_reports.generate_report(c,user_id,create_if_missing=True)
                elif path=='/api/weekly-reports/generate':
                    if set(body)-{'date'}: raise ValueError('生成请求只接受报告日期')
                    out=weekly_reports.generate_report(c,user_id,body.get('date'))
                else:
                    match=re.fullmatch(r'/api/weekly-reports/(\d+)(/refresh|/revise)?',path)
                    if not match: return self.json(404,{'error':'周报接口不存在'})
                    if match[2]=='/refresh':
                        if set(body)-{'edit_revision'}: raise ValueError('任务数据更新请求只接受版本标识')
                        out=weekly_reports.refresh_report(c,int(match[1]),user_id,body.get('edit_revision'))
                    elif match[2]=='/revise':
                        if body: raise ValueError('修订请求不接受额外参数')
                        out=weekly_reports.revise_report(c,int(match[1]),user_id)
                    else: out=weekly_reports.update_report(c,int(match[1]),user_id,body)
                c.commit()
            return self.json(200,out)
        except PermissionError as exc: return self.json(403,{'error':str(exc)})
        except LookupError as exc: return self.json(404,{'error':str(exc)})
        except weekly_reports.ReportConflict as exc: return self.json(409,{'error':str(exc)})
        except (ValueError,TypeError) as exc: return self.json(400,{'error':str(exc)})
        finally: c.close()
    def authorized(self):
        if login_required(): return bool(self.session_user())
        # Local-only mode remains frictionless; public/tunnel mode must set both values.
        username=os.getenv('WMT_PUBLIC_USER'); password=os.getenv('WMT_PUBLIC_PASSWORD')
        if not username and not password: return True
        if not username or not password: return False
        supplied=self.headers.get('Authorization','')
        if not supplied.startswith('Basic '): return False
        try: got=b64decode(supplied[6:]).decode('utf-8'); user,sep,pwd=got.partition(':')
        except Exception: return False
        return bool(sep and user==username and pwd==password)
    def require_auth(self):
        self.authenticated_user_id=None
        user=self.session_user()
        if user:
            self.authenticated_user_id=user['id']; return True
        if self.authorized(): return True
        if getattr(self,'command','GET')=='POST':self.close_connection=True
        if login_required(): return self.json(401,{'error':'login_required','login_url':'/auth/feishu/start'}) or False
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Work Management Tree"'); self.send_header('Content-Length','0'); self.end_headers(); return False
    def redirect(self, location, cookie=None):
        self.send_response(302); self.send_header('Location',location)
        for item in ([cookie] if isinstance(cookie,str) else cookie or []):self.send_header('Set-Cookie',item)
        self.send_header('Content-Length','0'); self.end_headers()
    def session_payload(self):
        user=self.session_user()
        return {'authenticated':bool(user),'mode':os.getenv('WMT_AUTH_MODE','demo').lower(),'user':user,
                'monthly_demo':monthly_demo_enabled(),'feishu_ready':FeishuAdapter().login_configured()}
    def management_user(self,c,body=None,message='仅部门负责人和管理 PM 可配置账号或重置密码'):
        uid=getattr(self,'authenticated_user_id',None)
        if not uid and monthly_demo_enabled():uid=(body or {}).get('actor_id')
        row=c.execute('SELECT id,role FROM user WHERE id=?',(uid,)).fetchone() if uid else None
        if not row or row['role'] not in ('manager','pm'):raise PermissionError(message)
        return row['id']
    def account_cookie(self,token,max_age=28800,name='wmt_session',path='/'):
        secure='; Secure' if feishu_redirect_uri().startswith('https://') else ''
        return f'{name}={token}; Path={path}; Max-Age={max_age}; HttpOnly; SameSite=Lax{secure}'
    def handle_account_api(self,path):
        user=self.session_user();self.authenticated_user_id=user['id'] if user else None;c=db()
        try:
            if self.command=='GET':
                if path=='/api/auth/options':return self.json(200,{'demo_enabled':not login_required(),'feishu_ready':FeishuAdapter().login_configured()})
                if path=='/api/me':
                    if not user:return self.json(401,{'error':'请先登录个人账号','login_url':'/login'})
                    return self.json(200,{'user':user,'feishu_bound':bool(user.get('feishu_open_id')),
                                          'feishu_masked':mask_external_id(user.get('feishu_open_id')),
                                          'feishu_ready':FeishuAdapter().login_configured()})
                return self.json(404,{'error':'接口不存在'})
            origin=self.headers.get('Origin')
            if origin and urllib.parse.urlparse(origin).netloc!=self.headers.get('Host'):
                self.close_connection=True
                return self.json(403,{'error':'请从本站页面发起操作'})
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':
                self.close_connection=True
                return self.json(400,{'error':'请求须使用 JSON 格式'})
            body=self.body()
            if not isinstance(body,dict):raise ValueError('请求须为 JSON 对象')
            if path=='/api/auth/login':
                previous=self.cookie('wmt_session')
                uid=accounts.authenticate(c,body.get('username'),body.get('password'),getattr(self,'client_address',('local',))[0])
                if previous:c.execute('DELETE FROM login_session WHERE session_id=?',(previous,))
                token=accounts.new_session(c,uid);c.commit()
                return self.json(200,{'status':'ok','redirect':'/'},self.account_cookie(token))
            if path=='/api/auth/logout':
                c.execute('DELETE FROM login_session WHERE session_id=?',(self.cookie('wmt_session'),));c.commit()
                return self.json(200,{'status':'ok'},self.account_cookie('',0))
            if path=='/api/me/password':
                if not user:return self.json(401,{'error':'请先登录个人账号'})
                accounts.change_password(c,user['id'],body.get('current_password'),body.get('new_password'))
                token=accounts.new_session(c,user['id']);c.commit()
                return self.json(200,{'status':'ok','message':'密码已更新，其他登录会话已失效'},self.account_cookie(token))
            match=re.fullmatch(r'/api/users/(\d+)/password-reset',path)
            if match:
                if user:self.authenticated_user_id=user['id']
                actor=self.management_user(c,body)
                accounts.reset_password(c,int(match[1]),body.get('new_password'),actor);c.commit()
                return self.json(200,{'status':'ok','message':'密码已重置，该员工原登录会话已失效'})
            return self.json(404,{'error':'接口不存在'})
        except accounts.Throttled as exc:return self.json(429,{'error':str(exc)})
        except accounts.Conflict as exc:return self.json(409,{'error':str(exc)})
        except sqlite3.IntegrityError as exc:return self.json(409,{'error':str(exc)})
        except PermissionError as exc:return self.json(403 if path!='/api/auth/login' else 401,{'error':str(exc)})
        except LookupError as exc:return self.json(404,{'error':str(exc)})
        except (ValueError,TypeError) as exc:return self.json(400,{'error':str(exc)})
        finally:c.close()
    def show_feishu_entry(self,bind=False):
        if bind and not self.session_user():return self.redirect('/login')
        if not FeishuAdapter().login_configured():return self.redirect('/?page=profile&binding=not_configured' if bind else '/login?login_error=not_configured')
        return self.send_bytes(200,'text/html; charset=utf-8',(ROOT/'static'/'feishu-entry.html').read_bytes(),'no-store')
    def start_feishu_login(self,bind=False,qr=False,popup_channel=None):
        user=self.session_user()
        if bind and not user:
            return self.json(401,{'error':'请先登录个人账号，再扫码绑定。'}) if qr else self.redirect('/login')
        if not FeishuAdapter().login_configured():
            return self.json(503,{'error':'飞书网页登录尚未完成配置，请先使用账号密码。'}) if qr else self.redirect('/?page=profile&binding=not_configured' if bind else '/login?login_error=not_configured')
        state=secrets.token_urlsafe(32);c=db()
        flow_type=('qr_bind' if bind else 'qr_login') if qr else ('bind' if bind else 'login')
        if popup_channel:flow_type=(('popup_qr_bind:' if bind else 'popup_qr_login:') if qr else ('popup_bind:' if bind else 'popup_login:'))+popup_channel
        try:
            c.execute('DELETE FROM login_state WHERE expire_at<=?',(now(),))
            # Refreshing a QR code invalidates this browser's previous attempt.
            c.execute('DELETE FROM login_state WHERE state_hash=?',(hashlib.sha256(self.cookie('wmt_oauth_state').encode()).hexdigest(),))
            c.execute('INSERT INTO login_state(state_hash,expire_at,created_at,flow_type,user_id,session_hash) VALUES(?,?,?,?,?,?)',
                (hashlib.sha256(state.encode()).hexdigest(),(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),now(),
                 flow_type,user['id'] if bind else None,hashlib.sha256(self.cookie('wmt_session').encode()).hexdigest() if bind else None));c.commit()
        finally:c.close()
        adapter=FeishuAdapter();url=adapter.qr_authorization_url(state) if qr else adapter.authorization_url(state)
        cookie=self.account_cookie(state,600,'wmt_oauth_state','/auth/feishu')
        if qr:return self.json(200,{'authorization_url':url,'expires_in':600},cookie)
        self.redirect(url,cookie)
    def feishu_login_result(self,location,cookie,channel=None):
        if not channel:return self.redirect(location,cookie)
        data=json.dumps({'channel':channel,'target':location},ensure_ascii=False).replace('<','\\u003c')
        html=(ROOT/'static'/'feishu-result.html').read_text().replace('__WMT_FEISHU_RESULT__',data).encode()
        self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
        self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(html)))
        for value in ([cookie] if isinstance(cookie,str) else cookie or []):self.send_header('Set-Cookie',value)
        self.end_headers();self.wfile.write(html)
    def finish_feishu_login(self,query):
        state=query.get('state',[''])[0];code=query.get('code',[''])[0]
        if not state or not secrets.compare_digest(state,self.cookie('wmt_oauth_state')):return self.redirect('/login?login_error=expired')
        current=self.session_user();c=db()
        try:
            c.execute('BEGIN IMMEDIATE')
            valid=c.execute('SELECT * FROM login_state WHERE state_hash=? AND expire_at>?',(hashlib.sha256(state.encode()).hexdigest(),now())).fetchone()
            c.execute('DELETE FROM login_state WHERE state_hash=?',(hashlib.sha256(state.encode()).hexdigest(),));c.commit()
        finally:c.close()
        clear=self.account_cookie('',0,'wmt_oauth_state','/auth/feishu')
        if not valid:return self.redirect('/login?login_error=expired',clear)
        flow_type,_,channel=valid['flow_type'].partition(':')
        channel=channel if flow_type in ('popup_login','popup_bind','popup_qr_login','popup_qr_bind') and re.fullmatch(r'[a-f0-9]{32}',channel) else None
        finish=lambda location,cookie=clear:self.feishu_login_result(location,cookie,channel)
        binding=flow_type in ('bind','qr_bind','popup_bind','popup_qr_bind')
        target='/?page=profile&binding=' if binding else '/login?login_error='
        if binding and (not current or current['id']!=valid['user_id'] or
            not secrets.compare_digest(valid['session_hash'] or '',hashlib.sha256(self.cookie('wmt_session').encode()).hexdigest())):
            return finish('/login?login_error=expired')
        if not code:return finish(target+'cancelled')
        try:
            adapter=FeishuAdapter()
            if flow_type in ('qr_login','qr_bind','popup_qr_login','popup_qr_bind'):
                token=adapter.exchange_qr_login_code(code);open_id=adapter.qr_login_open_id(token['access_token'])
            else:
                token=adapter.exchange_login_code(code);open_id=adapter.login_open_id(token['access_token'])
        except Exception:return finish(target+'authorization_failed')
        c=db()
        try:
            c.execute('BEGIN IMMEDIATE')
            if binding:
                # A reset/logout during OAuth must invalidate the pending binding, too.
                live=c.execute('SELECT 1 FROM login_session WHERE session_id=? AND user_id=? AND expire_at>?',
                               (self.cookie('wmt_session'),current['id'],now())).fetchone()
                if not live:return finish('/login?login_error=expired')
                occupied=c.execute('SELECT id FROM user WHERE feishu_open_id=? AND id<>?',(open_id,current['id'])).fetchone()
                if occupied:return finish(target+'already_bound')
                old=c.execute('SELECT feishu_open_id FROM user WHERE id=?',(current['id'],)).fetchone()[0]
                if old and old!=open_id:return finish(target+'different_account')
                c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',(open_id,current['id']))
                if old!=open_id:record_feishu_binding(c,current['id'],'bind',old,open_id,str(current['id']))
                audit(c,'user',current['id'],'self_bind_feishu',str(current['id']),after={'bound':True});c.commit()
                return finish(target+'success')
            user=c.execute('SELECT id FROM user WHERE feishu_open_id=?',(open_id,)).fetchone()
            if not user:return finish(target+'unbound')
            c.execute('DELETE FROM login_session WHERE session_id=?',(self.cookie('wmt_session'),))
            session_id=accounts.new_session(c,user['id']);audit(c,'login',user['id'],'feishu_login',str(user['id']),after={'channel':'feishu'});c.commit()
            return finish('/',[clear,self.account_cookie(session_id)])
        except sqlite3.IntegrityError:return finish(target+'already_bound')
        finally:c.close()
    def logout(self):
        c=db()
        try:c.execute('DELETE FROM login_session WHERE session_id=?',(self.cookie('wmt_session'),));c.commit()
        finally:c.close()
        self.redirect('/login',self.account_cookie('',0))
    def json(self, code, payload, cookie=None):
        data=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Cache-Control','no-store, no-cache, must-revalidate'); self.send_header('Content-Length',str(len(data)));
        if cookie:self.send_header('Set-Cookie',cookie)
        self.end_headers(); self.wfile.write(data)
    def body(self):
        try: n=int(self.headers.get('Content-Length','0'))
        except ValueError: raise ValueError('Content-Length 无效')
        if n<0 or n>1024*1024: raise ValueError('请求内容不能超过 1MB')
        return json.loads(self.rfile.read(n) or '{}')
    def send_bytes(self, code, content_type, data, cache_control=None):
        """Serve static content with HTTP/1.1 compression when the client supports it."""
        use_gzip=('gzip' in self.headers.get('Accept-Encoding','').lower() and len(data)>1024)
        payload=gzip.compress(data,compresslevel=6) if use_gzip else data
        self.send_response(code); self.send_header('Content-Type',content_type)
        if cache_control: self.send_header('Cache-Control',cache_control)
        if use_gzip:
            self.send_header('Content-Encoding','gzip'); self.send_header('Vary','Accept-Encoding')
        self.send_header('Content-Length',str(len(payload))); self.end_headers()
        if self.command!='HEAD': self.wfile.write(payload)
    def do_HEAD(self):
        p=urllib.parse.urlparse(self.path)
        if p.path.startswith('/wecom/callback'): return self.send_bytes(200,'text/plain; charset=utf-8',b'')
        if p.path not in ('/api/session','/auth/feishu/start','/auth/feishu/callback','/auth/logout') and not self.require_auth(): return
        # A lightweight health response also supports common load-balancer probes.
        return self.send_bytes(200,'text/plain; charset=utf-8',b'')
    def multipart(self):
        """Small multipart reader for task-source uploads; other APIs stay JSON-only."""
        content_type=self.headers.get('Content-Type','')
        match=re.search(r'boundary=(?:"([^"]+)"|([^;\s]+))',content_type)
        if not match: raise ValueError('缺少上传边界')
        boundary=(match.group(1) or match.group(2)).encode()
        size=int(self.headers.get('Content-Length','0'))
        if size<=0: raise ValueError('未收到上传文件')
        if size>21*1024*1024: raise ValueError('上传内容不能超过 20MB')
        raw=self.rfile.read(size); files=[]; fields={}
        for item in raw.split(b'--'+boundary):
            item=item.lstrip(b'\r\n')
            if not item or item.startswith(b'--'): continue
            headers, marker, payload=item.partition(b'\r\n\r\n')
            if not marker: continue
            if payload.endswith(b'\r\n'): payload=payload[:-2]
            header_text=headers.decode('utf-8',errors='replace')
            if not re.search(r'Content-Disposition:\s*form-data',header_text,re.I): continue
            name_match=re.search(r'(?:^|;)\s*name="([^"]+)"',header_text,re.I)
            filename_match=re.search(r'(?:^|;)\s*filename="([^"]*)"',header_text,re.I)
            if not name_match: continue
            name,filename=name_match.group(1),filename_match.group(1) if filename_match else None
            type_match=re.search(r'Content-Type:\s*([^\r\n;]+)',header_text,re.I)
            if filename is None:
                fields[name]=payload.decode('utf-8',errors='replace')
            else:
                files.append({'name':name,'filename':Path(filename).name or '未命名文件','content_type':type_match.group(1) if type_match else '', 'data':payload})
        return fields,files
    def do_GET(self):
        p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query)
        if p.path in ('/login','/login.html'):
            return self.send_bytes(200,'text/html; charset=utf-8',(ROOT/'static'/'login.html').read_bytes(),'no-store')
        if p.path=='/static/brand/unifortune-logo.png':
            return self.send_bytes(200,'image/png',(ROOT/'static'/'brand'/'unifortune-logo.png').read_bytes(),'public, max-age=86400')
        if p.path=='/static/accounts.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'accounts.js').read_bytes(),'no-cache')
        if p.path=='/static/feishu-login.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'feishu-login.js').read_bytes(),'no-cache')
        if p.path in ('/api/auth/options','/api/me'):return self.handle_account_api(p.path)
        if p.path=='/auth/feishu/window':
            flow=q.get('flow',['login'])[0];channel=q.get('channel',[''])[0]
            if flow not in ('login','bind') or not re.fullmatch(r'[a-f0-9]{32}',channel):return self.json(400,{'error':'登录窗口参数无效，请重新点击飞书扫码登录'})
            if flow=='bind' and not self.session_user():return self.redirect('/login')
            if not FeishuAdapter().login_configured():return self.redirect('/login?login_error=not_configured')
            return self.send_bytes(200,'text/html; charset=utf-8',(ROOT/'static'/'feishu-scan.html').read_bytes(),'no-store')
        if p.path=='/auth/feishu/qr':
            flow=q.get('flow',['login'])[0];channel=q.get('channel',[''])[0]
            if flow not in ('login','bind') or (channel and not re.fullmatch(r'[a-f0-9]{32}',channel)):return self.json(400,{'error':'不支持的扫码操作'})
            return self.start_feishu_login(bind=flow=='bind',qr=True,popup_channel=channel or None)
        if p.path=='/auth/feishu/bind':return self.show_feishu_entry(bind=True)
        if p.path=='/auth/feishu/authorize':
            flow=q.get('flow',['login'])[0]
            if flow not in ('login','bind'):return self.json(400,{'error':'不支持的授权操作'})
            return self.start_feishu_login(bind=flow=='bind')
        if p.path=='/static/monthly-reports.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'monthly-reports.js').read_bytes(),'no-cache')
        if p.path=='/routine-test-report':
            report=ROOT/'artifacts'/'routine-flow-test'/'report.html'
            if not report.exists():return self.json(404,{'error':'演练报告尚未生成'})
            return self.send_bytes(200,'text/html; charset=utf-8',report.read_bytes(),'no-cache')
        if p.path=='/static/responsibility-domains.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'responsibility-domains.js').read_bytes(),'no-cache')
        if p.path=='/static/routine-tasks.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'routine-tasks.js').read_bytes(),'no-cache')
        if p.path=='/static/weekly-reports.js':
            return self.send_bytes(200,'application/javascript; charset=utf-8',(ROOT/'static'/'weekly-reports.js').read_bytes(),'no-cache')
        if p.path=='/auth/feishu/start': return self.show_feishu_entry()
        if p.path=='/auth/feishu/callback': return self.finish_feishu_login(q)
        if p.path=='/auth/logout': return self.logout()
        if p.path=='/api/session': return self.json(200,self.session_payload())
        if p.path=='/static/task-create-feedback.js':
            data=(ROOT/'static'/'task-create-feedback.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/static/multi-draft.js':
            data=(ROOT/'static'/'multi-draft.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/static/phase1-refinement.js':
            data=(ROOT/'static'/'phase1-refinement.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/static/stale-approval-guard.js':
            data=(ROOT/'static'/'stale-approval-guard.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/static/pending-approval-manage.js':
            data=(ROOT/'static'/'pending-approval-manage.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/static/layout-guard.js':
            data=(ROOT/'static'/'layout-guard.js').read_bytes(); self.send_response(200); self.send_header('Content-Type','application/javascript; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if re.fullmatch(r'/static/manual-assets/[a-z0-9][a-z0-9-]*\.(?:svg|png)',p.path):
            asset=ROOT/'static'/'manual-assets'/p.path.rsplit('/',1)[1]
            if not asset.is_file():return self.json(404,{'error':'手册图片不存在'})
            content_type='image/png' if asset.suffix=='.png' else 'image/svg+xml'
            return self.send_bytes(200,content_type,asset.read_bytes(),'no-cache')
        if p.path=='/' or p.path=='/index.html':
            if not login_required() and not self.require_auth(): return
            data=(ROOT/'static'/'index.html').read_bytes().replace(b'</body>',b'<script defer src="/static/task-create-feedback.js?v=20261104-layout2"></script><script>window.getWmtDraftData=()=>({users:typeof currentUsers!=="undefined"?currentUsers:[],nodes:typeof nodeNames!=="undefined"?nodeNames:{}});</script><script defer src="/static/multi-draft.js?v=20261104-layout5"></script><script defer src="/static/phase1-refinement.js?v=20261106-weekly1"></script><script defer src="/static/routine-tasks.js?v=20261106-people"></script><script defer src="/static/responsibility-domains.js?v=20261106-1"></script><script defer src="/static/stale-approval-guard.js?v=20261104-layout1"></script><script defer src="/static/pending-approval-manage.js?v=20261106-domains"></script><script defer src="/static/layout-guard.js?v=20261106-domains"></script></body>'); return self.send_bytes(200,'text/html; charset=utf-8',data,'no-cache')
        if p.path=='/manual' or p.path=='/manual.html':
            if not self.require_auth(): return
            data=(ROOT/'static'/'manual.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/manual.md':
            if not self.require_auth(): return
            data=(ROOT/'docs'/'工作管理树用户操作手册.md').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/markdown; charset=utf-8'); self.send_header('Content-Disposition','inline; filename="work-management-tree-user-manual.md"'); self.send_header('Cache-Control','no-cache'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path=='/test-issues' or p.path=='/test-issues.html':
            if not self.require_auth(): return
            data=(ROOT/'static'/'test-issues.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path.startswith('/wecom/callback'):
            self.handle_wecom_get(); return
        if not self.require_auth(): return
        if p.path.startswith('/api/monthly-reports'):
            return self.handle_monthly_reports(p.path,q)
        if p.path=='/monthly-report-demo':
            # Only the prebuilt fictional fixture is served; never read live reports.
            preview=ROOT/'artifacts'/'monthly-report-demo'/'index.html'
            if not preview.is_file(): return self.json(404,{'error':'演示月报尚未生成'})
            return self.send_bytes(200,'text/html; charset=utf-8',preview.read_bytes(),'no-cache')
        if p.path=='/weekly-report-demo':
            c=db()
            try:
                weekly_reports.actor(c,self.actor_id(q.get('actor_id',[None])[0]))
                preview=ROOT/'artifacts'/'weekly-report-demo'/'index.html'
                if not preview.is_file(): return self.json(404,{'error':'演示周报尚未生成'})
                return self.send_bytes(200,'text/html; charset=utf-8',preview.read_bytes(),'no-cache')
            except PermissionError as exc: return self.json(403,{'error':str(exc)})
            finally: c.close()
        if p.path.startswith('/api/weekly-reports'):
            return self.handle_weekly_reports(p.path,q)
        c=db()
        if p.path=='/api/bootstrap':
            task_rows=[task_view(c,x) for x in c.execute("SELECT * FROM task WHERE approval_status='approved' ORDER BY due_at")]
            today=datetime.now(timezone.utc).date().isoformat()
            out={'dashboard':{'total':len(task_rows),'active':sum(x['work_status']!='completed' for x in task_rows),'completed':sum(x['work_status']=='completed' for x in task_rows),'overdue':sum(x['time_status']=='overdue' for x in task_rows),'risk':sum(x['risk_status']!='normal' for x in task_rows),'pending_approval':c.execute("SELECT count(*) FROM approval_record WHERE status='pending'").fetchone()[0],'unreported':c.execute("SELECT count(*) FROM daily_report_status WHERE report_date=? AND status='unreported'",(today,)).fetchone()[0]},'users':[accounts.public_user(c,x) for x in c.execute('SELECT * FROM user ORDER BY id')],'tree':[rowdict(x) for x in c.execute('SELECT * FROM org_node ORDER BY id')],'tasks':task_rows}
        elif p.path=='/api/users': out=[accounts.public_user(c,x) for x in c.execute('SELECT * FROM user ORDER BY id')]
        elif p.path=='/api/tree': out=[rowdict(x) for x in c.execute('SELECT * FROM org_node ORDER BY id')]
        elif p.path=='/api/domains':
            try:
                self.management_user(c,{'actor_id':q.get('actor_id',[None])[0]},message='仅部门负责人和管理 PM 可维护责任域')
                out=responsibility_domains.listing(c)
            except PermissionError as exc:c.close();return self.json(403,{'error':str(exc)})
        elif p.path=='/api/tasks': out=[task_view(c,x) for x in c.execute("SELECT * FROM task WHERE approval_status='approved' ORDER BY due_at")]
        elif p.path=='/api/tasks/similar':
            out=similar_tasks(c,q.get('title',[''])[0])
        elif re.fullmatch(r'/api/tasks/\d+',p.path):
            task=c.execute('SELECT * FROM task WHERE id=?',(int(p.path.rsplit('/',1)[1]),)).fetchone()
            if not task: return self.json(404,{'error':'task not found'})
            out=task_view(c,task)
        elif p.path=='/api/approvals':
            rows=c.execute("SELECT * FROM approval_record WHERE status=? ORDER BY created_at",(q.get('status',['pending'])[0],)).fetchall()
            out=[approval_view(c,x) for x in rows]
        elif re.fullmatch(r'/api/approvals/\d+',p.path):
            approval=c.execute('SELECT * FROM approval_record WHERE id=?',(int(p.path.rsplit('/',1)[1]),)).fetchone()
            if not approval: return self.json(404,{'error':'approval not found'})
            out=approval_view(c,approval)
        elif p.path=='/api/reporting-lines': out=[rowdict(x) for x in c.execute('SELECT * FROM reporting_line ORDER BY user_id,effective_from DESC')]
        elif p.path=='/api/org/departments': out=[rowdict(x) for x in c.execute('SELECT * FROM org_department WHERE status=? ORDER BY name',('active',))]
        elif p.path=='/api/org/positions': out=[rowdict(x) for x in c.execute("SELECT p.*,d.name AS department_name FROM org_position p LEFT JOIN org_department d ON d.id=p.dept_id WHERE p.status='active' ORDER BY d.name,p.name")]
        elif p.path=='/api/org/user-positions': out=[rowdict(x) for x in c.execute("SELECT up.*,u.name AS user_name,p.name AS position_name FROM user_position up JOIN user u ON u.id=up.user_id JOIN org_position p ON p.id=up.position_id WHERE up.valid_to IS NULL OR up.valid_to>? ORDER BY up.is_primary DESC,u.name",(now(),))]
        elif p.path=='/api/delegates':
            out=[]
            for row in c.execute("SELECT d.*,u.name AS user_name,du.name AS delegate_name FROM delegate d JOIN user u ON u.id=d.user_id JOIN user du ON du.id=d.delegate_user_id ORDER BY d.end_at DESC"):
                item=rowdict(row); item['is_active']=item['start_at']<=now()<=item['end_at']; out.append(item)
        elif p.path=='/api/progress': out=[rowdict(x) for x in c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at DESC,id DESC',(q.get('task_id',[0])[0],))]
        elif p.path=='/api/routine/templates':
            try:out=routines.list_templates(c,q.get('week',[None])[0])
            except ValueError as exc:c.close();return self.json(400,{'error':str(exc)})
        elif p.path=='/api/feishu/unbound-identities':
            rows=[]
            for log in c.execute("SELECT id,payload_json,created_at FROM im_message_log WHERE channel='feishu_ws' ORDER BY id DESC"):
                try:
                    payload=json.loads(log['payload_json']); event=payload.get('event',payload); sender=((event.get('sender') or {}).get('sender_id') or {}).get('open_id'); content=json.loads((event.get('message') or {}).get('content','{}')).get('text','')
                    bound=c.execute('SELECT 1 FROM user WHERE feishu_open_id=?',(sender,)).fetchone() if sender else None
                    if sender and not bound and not any(x['open_id']==sender for x in rows):
                        profile=feishu_profile_for(c,sender,q.get('refresh',['0'])[0]=='1')
                        rows.append({'open_id':sender,'open_id_masked':mask_external_id(sender),'display_name':profile['display_name'],'avatar_url':profile['avatar_url'],'last_text':content[:80],'last_seen_at':log['created_at']})
                except Exception: pass
            out=rows
        elif p.path=='/api/feishu/bindings':
            out=[]
            for row in c.execute("SELECT id,name,feishu_open_id FROM user WHERE feishu_open_id IS NOT NULL AND feishu_open_id<>'' ORDER BY name,id"):
                out.append({'user_id':row['id'],'user_name':row['name'],'open_id_masked':mask_external_id(row['feishu_open_id'])})
        elif p.path=='/api/feishu/binding-history':
            out=[]
            for row in c.execute("SELECT h.*,u.name AS user_name FROM feishu_binding_history h JOIN user u ON u.id=h.user_id ORDER BY h.created_at DESC,h.id DESC"):
                item=rowdict(row)
                item['old_open_id_masked']=mask_external_id(item.pop('old_open_id',''))
                item['new_open_id_masked']=mask_external_id(item.pop('new_open_id',''))
                out.append(item)
        elif p.path=='/api/task/drafts':
            status=q.get('status',['preview,collecting'])[0].split(',')
            marks=','.join('?' for _ in status)
            out=[]
            for draft in c.execute(f"SELECT d.*,u.name AS user_name FROM conversation_draft d LEFT JOIN user u ON u.id=d.user_id WHERE d.status IN ({marks}) AND d.expire_at>? ORDER BY d.updated_at DESC",(*status,now())):
                item=rowdict(draft); item['slots']=json.loads(item.pop('slots_json')); item['missing_slots']=json.loads(item['missing_slots']); out.append(item)
        elif p.path=='/api/ops/daily-preview': out={'date':q.get('date',[None])[0] or datetime.now(timezone.utc).date().isoformat(),'targets':daily_targets(c,q.get('date',[None])[0])}
        elif p.path=='/api/dashboard':
            tasks=[task_view(c,x) for x in c.execute("SELECT * FROM task WHERE approval_status='approved'")]; today=datetime.now(timezone.utc).date().isoformat(); out={'total':len(tasks),'active':sum(x['work_status']!='completed' for x in tasks),'completed':sum(x['work_status']=='completed' for x in tasks),'overdue':sum(x['time_status']=='overdue' for x in tasks),'risk':sum(x['risk_status']!='normal' for x in tasks),'pending_approval':c.execute("SELECT count(*) FROM approval_record WHERE status='pending'").fetchone()[0],'unreported':c.execute("SELECT count(*) FROM daily_report_status WHERE report_date=? AND status='unreported'",(today,)).fetchone()[0]}
        elif p.path=='/api/test-issues': out=[rowdict(x) for x in c.execute("SELECT * FROM test_issue ORDER BY CASE status WHEN 'open' THEN 0 WHEN 'processing' THEN 1 ELSE 2 END,id DESC")]
        elif p.path=='/api/performance/config': out=performance_config(c)
        elif p.path=='/api/performance/personal':
            actor=self.actor_id(q.get('actor_id',[None])[0]); target=int(q.get('user_id',[actor])[0] or actor or 0)
            actor_row=c.execute('SELECT * FROM user WHERE id=?',(actor,)).fetchone() if actor else None
            allowed=actor==target or (actor_row and actor_row['role'] in ('manager','pm')) or (actor and target in managed_user_ids(c,actor))
            if not allowed: return self.json(403,{'error':'无权查看该成员绩效'})
            out=performance_summary(c,target,q.get('period',[None])[0],q.get('start',[None])[0],q.get('end',[None])[0])
        elif p.path=='/api/performance/team':
            actor=self.actor_id(q.get('actor_id',[None])[0]); actor_row=c.execute('SELECT * FROM user WHERE id=?',(actor,)).fetchone() if actor else None
            if not actor_row: return self.json(400,{'error':'请选择查看身份'})
            user_ids=[x['id'] for x in c.execute("SELECT id FROM user WHERE role<>'manager'")] if actor_row['role'] in ('manager','pm') else managed_user_ids(c,actor)
            reports=[performance_summary(c,uid,q.get('period',[None])[0],q.get('start',[None])[0],q.get('end',[None])[0]) for uid in user_ids if uid!=actor]
            out={'period_start':reports[0]['period_start'] if reports else performance_period(q.get('period',[None])[0])[0],'period_end':reports[0]['period_end'] if reports else performance_period(q.get('period',[None])[0])[1],'members':reports,'summary':{'member_count':len(reports),'base_points':round(sum(x['base_points'] for x in reports),2),'earned_points':round(sum(x['earned_points'] for x in reports),2),'estimated_hours':round(sum(x['estimated_hours'] for x in reports),1),'actual_hours':round(sum(x['actual_hours'] for x in reports),1),'average_score':round(sum(x['score'] for x in reports)/len(reports),1) if reports else 0}}
        elif p.path=='/api/performance/department':
            actor=self.actor_id(q.get('actor_id',[None])[0]); actor_row=c.execute('SELECT * FROM user WHERE id=?',(actor,)).fetchone() if actor else None
            if not actor_row or actor_row['role'] not in ('manager','pm'): return self.json(403,{'error':'仅管理PM和部门负责人可查看部门绩效'})
            users=[rowdict(x) for x in c.execute("SELECT * FROM user WHERE role<>'manager' ORDER BY department,name")]
            reports=[performance_summary(c,x['id'],q.get('period',[None])[0],q.get('start',[None])[0],q.get('end',[None])[0]) for x in users]
            groups={}
            for report in reports:
                key=report['user'].get('department') or '未分组'; groups.setdefault(key,[]).append(report)
            out={'teams':[{'name':name,'member_count':len(items),'score':round(sum(x['score'] for x in items)/len(items),1) if items else 0,'base_points':round(sum(x['base_points'] for x in items),2),'earned_points':round(sum(x['earned_points'] for x in items),2),'on_time':round(sum(x['timeliness'] for x in items)/len(items),1) if items else 0,'quality':round(sum(x['quality'] for x in items)/len(items),1) if items else 0} for name,items in groups.items()],'members':reports}
        else: self.json(404,{'error':'not found'}); c.close(); return
        c.commit(); self.json(200,out); c.close()
    def handle_wecom_get(self):
        p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query); codec=WeComCallback()
        if not codec.configured(): return self.json(503,{'error':'WECOM_TOKEN and WECOM_AES_KEY are required'})
        try:
            signature=q.get('msg_signature',[''])[0]; timestamp=q.get('timestamp',[''])[0]; nonce=q.get('nonce',[''])[0]; encrypted=q.get('echostr',[''])[0]
            if signature != codec.signature(timestamp,nonce,encrypted): return self.json(403,{'error':'invalid signature'})
            plain=codec.decrypt(encrypted); data=plain.encode(); self.send_response(200); self.send_header('Content-Type','text/plain'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as exc: self.json(400,{'error':'callback verification failed','detail':str(exc)})
    def do_POST(self):
        if self.path.startswith('/wecom/callback'):
            self.handle_wecom_post(); return
        if self.path.startswith('/im/callback/feishu'):
            self.handle_feishu_post(); return
        p=urllib.parse.urlparse(self.path)
        if p.path in ('/api/auth/login','/api/auth/logout','/api/me/password') or re.fullmatch(r'/api/users/\d+/password-reset',p.path):
            return self.handle_account_api(p.path)
        if not self.require_auth(): return
        if p.path.startswith('/api/monthly-reports'):
            return self.handle_monthly_reports(p.path)
        if p.path.startswith('/api/weekly-reports'):
            return self.handle_weekly_reports(p.path)
        c=db()
        try:
            fields,files=self.multipart() if p.path=='/api/task/input/extract' else ({},[])
            b=fields if p.path=='/api/task/input/extract' else self.body()
        except (ValueError,json.JSONDecodeError) as exc:
            c.close(); return self.json(400,{'error':str(exc) or '请求格式不正确'})
        t=now(); response_code=200
        try:
            roster_actor=None
            if (p.path=='/api/users' or p.path.startswith('/api/users/') or
                p.path in ('/api/org/positions','/api/org/user-positions','/api/reporting-lines') or
                p.path.startswith(('/api/org/positions/','/api/reporting-lines/'))):
                roster_actor=self.management_user(c,b,message='仅部门负责人和管理 PM 可新增、修改或删除岗位和成员资料')
            if p.path=='/api/task/input/extract':
                if not files: return self.json(400,{'error':'请选择要识别的文件'})
                upload=files[0]
                try:
                    extracted=extract_uploaded_text(upload['data'],upload['filename'],upload['content_type'])
                except ValueError as exc:
                    return self.json(400,{'error':str(exc)})
                suffix=Path(upload['filename']).suffix.lower()
                kind='图片' if upload['content_type'].startswith('image/') else ('录音' if upload['content_type'].startswith('audio/') or suffix in {'.mp3','.wav','.m4a','.aac','.ogg','.mp4'} else '文档')
                out={'filename':upload['filename'],'kind':kind,'text':extracted}
            elif p.path=='/api/domains' or re.fullmatch(r'/api/domains/\d+',p.path):
                domain_actor=self.management_user(c,b,message='仅部门负责人和管理 PM 可维护责任域')
                changes={key:value for key,value in b.items() if key not in ('actor_id','operator_id','action')}
                if p.path=='/api/domains':
                    out=responsibility_domains.create(c,changes,domain_actor);response_code=201
                else:
                    domain_id=int(p.path.rsplit('/',1)[1])
                    if b.get('action')=='delete':out=responsibility_domains.delete(c,domain_id,domain_actor)
                    elif b.get('action','update')=='update':out=responsibility_domains.update(c,domain_id,changes,domain_actor)
                    else:raise ValueError('不支持的责任域操作')
                c.commit()
            elif p.path=='/api/tree':
                if getattr(self,'authenticated_user_id',None):self.management_user(c,b)
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_node(parent_id,node_type,name,boundary,owner_id,effective_at,created_at) VALUES(?,?,?,?,?,?,?)',(b.get('parent_id'),b.get('node_type','domain'),b['name'],b.get('boundary',''),b.get('owner_id'),t,t)).lastrowid; audit(c,'org_node',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/users':
                account_actor=roster_actor
                required=['name']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if b.get('role','member') not in ('manager','pm','member'): return self.json(400,{'error':'invalid role'})
                try:
                    oid=c.execute('INSERT INTO user(name,department,role,wecom_userid) VALUES(?,?,?,?)',(b['name'],b.get('department','科技产品中心'),b.get('role','member'),b.get('wecom_userid') or None)).lastrowid
                except sqlite3.IntegrityError: return self.json(409,{'error':'wecom_userid already exists'})
                if b.get('username') or b.get('password'):accounts.configure(c,oid,b.get('username'),b.get('password'),account_actor)
                audit(c,'user',oid,'create',str(account_actor or 'local'),after={k:v for k,v in b.items() if 'password' not in k.lower()}); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/users/') and p.path.endswith('/manage'):
                account_actor=roster_actor
                uid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM user WHERE id=?',(uid,)).fetchone()
                if not old: return self.json(404,{'error':'user not found'})
                old_account=accounts.metadata(c,uid)
                action=b.get('action')
                if action=='delete':
                    if c.execute('SELECT 1 FROM task WHERE owner_id=? LIMIT 1',(uid,)).fetchone(): return self.json(409,{'error':'该成员仍有关联任务，暂不能删除'})
                    c.execute('DELETE FROM reporting_line WHERE user_id=? OR direct_leader_id=? OR approver_id=?',(uid,uid,uid)); c.execute('DELETE FROM user_position WHERE user_id=?',(uid,)); c.execute('DELETE FROM delegate WHERE user_id=? OR delegate_user_id=?',(uid,uid)); c.execute('DELETE FROM login_session WHERE user_id=?',(uid,)); c.execute('DELETE FROM user WHERE id=?',(uid,)); audit(c,'user',uid,'delete','local',before=rowdict(old)); c.commit(); out={'id':uid,'status':'deleted'}
                elif action=='update':
                    if not b.get('name'): return self.json(400,{'error':'name is required'})
                    if b.get('role','member') not in ('manager','pm','member'): return self.json(400,{'error':'invalid role'})
                    try: c.execute('UPDATE user SET name=?,department=?,role=?,wecom_userid=? WHERE id=?',(b['name'].strip(),b.get('department','').strip(),b.get('role','member'),b.get('wecom_userid') or None,uid))
                    except sqlite3.IntegrityError: return self.json(409,{'error':'wecom_userid already exists'})
                    if b.get('username') or b.get('password'):accounts.configure(c,uid,b.get('username'),b.get('password'),account_actor)
                    audit(c,'user',uid,'update',str(account_actor or 'local'),before=rowdict(old),after={k:v for k,v in b.items() if 'password' not in k.lower()}); c.commit(); out={'id':uid,'status':'updated','reauthenticate':bool(getattr(self,'authenticated_user_id',None)==uid and (b.get('password') or (b.get('username') and b['username'].strip().lower()!=old_account['username'])))}
                else: return self.json(400,{'error':'unsupported manage action'})
            elif p.path.startswith('/api/users/') and p.path.endswith('/reporting-line'):
                uid=int(p.path.split('/')[3]); user=c.execute('SELECT id FROM user WHERE id=?',(uid,)).fetchone()
                if not user: return self.json(404,{'error':'user not found'})
                leader_id=b.get('direct_leader_id')
                old=c.execute('SELECT * FROM reporting_line WHERE user_id=? AND effective_to IS NULL ORDER BY effective_from DESC LIMIT 1',(uid,)).fetchone()
                if leader_id in (None,''):
                    if old: c.execute('UPDATE reporting_line SET effective_to=? WHERE id=?',(t,old['id']))
                    audit(c,'reporting_line',old['id'] if old else uid,'clear_from_member','local',before=rowdict(old) if old else None,after={'user_id':uid,'direct_leader_id':None}); c.commit(); out={'id':uid,'status':'cleared'}
                else:
                    leader_id=int(leader_id)
                    if leader_id==uid: return self.json(400,{'error':'a user cannot report to self'})
                    if not c.execute('SELECT id FROM user WHERE id=?',(leader_id,)).fetchone(): return self.json(404,{'error':'direct leader not found'})
                    if old: c.execute('UPDATE reporting_line SET effective_to=? WHERE id=?',(t,old['id']))
                    oid=c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,dotted_leader_id,effective_from) VALUES(?,?,?,?,?)',(uid,leader_id,leader_id,None,t)).lastrowid
                    audit(c,'reporting_line',oid,'set_from_member','local',before=rowdict(old) if old else None,after={'user_id':uid,'direct_leader_id':leader_id}); c.commit(); out={'id':oid,'status':'updated'}
            elif p.path.startswith('/api/users/') and p.path.endswith('/feishu-open-id/unbind'):
                uid=int(p.path.split('/')[3])
                user=c.execute('SELECT feishu_open_id FROM user WHERE id=?',(uid,)).fetchone()
                if not user: return self.json(404,{'error':'user not found'})
                old_open_id=user['feishu_open_id'] or ''
                if not old_open_id: return self.json(400,{'error':'user has no feishu binding'})
                c.execute('UPDATE user SET feishu_open_id=NULL WHERE id=?',(uid,))
                record_feishu_binding(c,uid,'unbind',old_open_id,'')
                audit(c,'user',uid,'unbind_feishu','local',before={'bound':True},after={'bound':False})
                c.commit(); out={'id':uid,'status':'unbound'}
            elif p.path.startswith('/api/users/') and p.path.endswith('/feishu-open-id'):
                uid=int(p.path.split('/')[3])
                if not b.get('feishu_open_id'): return self.json(400,{'error':'feishu_open_id is required'})
                open_id=b['feishu_open_id'].strip()
                user=c.execute('SELECT feishu_open_id FROM user WHERE id=?',(uid,)).fetchone()
                if not user: return self.json(404,{'error':'user not found'})
                old_open_id=user['feishu_open_id'] or ''
                existing=c.execute('SELECT id FROM user WHERE feishu_open_id=? AND id<>?',(open_id,uid)).fetchone()
                if existing: return self.json(409,{'error':'feishu_open_id already bound to another user'})
                if old_open_id==open_id: return self.json(200,{'id':uid,'status':'unchanged'})
                c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',(open_id,uid))
                c.execute("UPDATE conversation_draft SET user_id=?,updated_at=? WHERE source_type='feishu' AND session_id=? AND user_id IS NULL",(uid,t,f'feishu:{open_id}'))
                c.execute("UPDATE im_message_log SET user_id=? WHERE channel='feishu_ws' AND user_id IS NULL AND payload_json LIKE ?",(uid,f'%{open_id}%'))
                action='modify' if old_open_id else 'bind'
                record_feishu_binding(c,uid,action,old_open_id,open_id)
                audit(c,'user',uid,action+'_feishu','local',before={'bound':bool(old_open_id)},after={'bound':True}); c.commit(); out={'id':uid,'status':'modified' if old_open_id else 'bound'}
            elif p.path=='/api/reporting-lines':
                required=['user_id','direct_leader_id']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if int(b['user_id'])==int(b['direct_leader_id']): return self.json(400,{'error':'a user cannot report to self'})
                c.execute('UPDATE reporting_line SET effective_to=? WHERE user_id=? AND effective_to IS NULL',(t,b['user_id']))
                oid=c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,dotted_leader_id,effective_from) VALUES(?,?,?,?,?)',(b['user_id'],b['direct_leader_id'],b.get('approver_id') or b['direct_leader_id'],b.get('dotted_leader_id'),t)).lastrowid; audit(c,'reporting_line',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/reporting-lines/'):
                lid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM reporting_line WHERE id=?',(lid,)).fetchone()
                if not old: return self.json(404,{'error':'reporting line not found'})
                if old['effective_to'] is not None: return self.json(409,{'error':'reporting line is no longer active'})
                action=b.get('action')
                if action=='delete':
                    c.execute('UPDATE reporting_line SET effective_to=? WHERE id=?',(t,lid)); audit(c,'reporting_line',lid,'delete','local',before=rowdict(old)); c.commit(); out={'id':lid,'status':'deleted'}
                elif action=='update':
                    required=['user_id','direct_leader_id']; missing=[x for x in required if not b.get(x)]
                    if missing: return self.json(400,{'error':'missing fields','fields':missing})
                    if int(b['user_id'])==int(b['direct_leader_id']): return self.json(400,{'error':'a user cannot report to self'})
                    c.execute('UPDATE reporting_line SET effective_to=? WHERE id=?',(t,lid))
                    oid=c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,dotted_leader_id,effective_from) VALUES(?,?,?,?,?)',(b['user_id'],b['direct_leader_id'],b.get('approver_id') or b['direct_leader_id'],b.get('dotted_leader_id'),t)).lastrowid
                    audit(c,'reporting_line',lid,'update','local',before=rowdict(old),after=b); c.commit(); out={'id':oid,'status':'updated'}
                else: return self.json(400,{'error':'unsupported manage action'})
            elif p.path=='/api/org/departments':
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_department(name,parent_id,owner_user_id,status,created_at) VALUES(?,?,?,?,?)',(b['name'],b.get('parent_id'),b.get('owner_user_id'),'active',t)).lastrowid; audit(c,'org_department',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/org/positions':
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_position(name,dept_id,level,default_role,status) VALUES(?,?,?,?,?)',(b['name'],b.get('dept_id'),b.get('level',''),b.get('default_role','member'),'active')).lastrowid; audit(c,'org_position',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/org/positions/'):
                pid=int(p.path.split('/')[4]); old=c.execute('SELECT * FROM org_position WHERE id=? AND status=?',(pid,'active')).fetchone()
                if not old: return self.json(404,{'error':'position not found'})
                action=b.get('action')
                if action=='delete':
                    c.execute("UPDATE user_position SET valid_to=? WHERE position_id=? AND valid_to IS NULL",(t,pid)); c.execute("UPDATE org_position SET status='inactive' WHERE id=?",(pid,)); audit(c,'org_position',pid,'delete','local',before=rowdict(old)); c.commit(); out={'id':pid,'status':'deleted'}
                elif action=='update':
                    if not b.get('name'): return self.json(400,{'error':'name is required'})
                    c.execute('UPDATE org_position SET name=?,dept_id=?,level=? WHERE id=?',(b['name'].strip(),b.get('dept_id'),b.get('level','').strip(),pid)); audit(c,'org_position',pid,'update','local',before=rowdict(old),after=b); c.commit(); out={'id':pid,'status':'updated'}
                else: return self.json(400,{'error':'unsupported manage action'})
            elif p.path=='/api/org/user-positions':
                if not b.get('user_id') or not b.get('position_id'): return self.json(400,{'error':'user and position are required'})
                if not c.execute('SELECT 1 FROM user WHERE id=?',(b['user_id'],)).fetchone() or not c.execute('SELECT 1 FROM org_position WHERE id=? AND status=?',(b['position_id'],'active')).fetchone(): return self.json(400,{'error':'user or position not found'})
                primary=1 if b.get('is_primary',True) else 0
                if primary: c.execute('UPDATE user_position SET is_primary=0 WHERE user_id=? AND valid_to IS NULL',(b['user_id'],))
                oid=c.execute('INSERT INTO user_position(user_id,position_id,is_primary,valid_from) VALUES(?,?,?,?)',(b['user_id'],b['position_id'],primary,t)).lastrowid; audit(c,'user_position',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/delegates':
                required=['user_id','delegate_user_id','start_at','end_at']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'delegate details are required'})
                if int(b['user_id'])==int(b['delegate_user_id']): return self.json(400,{'error':'delegate must be another user'})
                if b['start_at']>=b['end_at']: return self.json(400,{'error':'delegate period is invalid'})
                oid=c.execute('INSERT INTO delegate(user_id,delegate_user_id,scope,start_at,end_at) VALUES(?,?,?,?,?)',(b['user_id'],b['delegate_user_id'],b.get('scope','approval'),b['start_at'],b['end_at'])).lastrowid; audit(c,'delegate',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/routine/templates' or re.fullmatch(r'/api/routine/templates/\d+',p.path):
                template_actor=self.management_user(c,b)
                if b.get('action')=='deactivate' and p.path!='/api/routine/templates':
                    oid=int(p.path.rsplit('/',1)[1]);old=c.execute('SELECT * FROM task_template WHERE id=?',(oid,)).fetchone()
                    if not old:return self.json(404,{'error':'模板不存在'})
                    c.execute("UPDATE task_template SET status='inactive' WHERE id=?",(oid,))
                    audit(c,'task_template',oid,'deactivate',str(template_actor),before=rowdict(old),after={'status':'inactive'})
                    c.commit();return self.json(200,{'id':oid,'status':'inactive'})
                fields=routines.template_fields(c,b)
                if p.path=='/api/routine/templates':
                    fields['created_at']=t
                    oid=c.execute('INSERT INTO task_template('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
                    audit(c,'task_template',oid,'create',str(template_actor),after=fields)
                else:
                    oid=int(p.path.rsplit('/',1)[1]); old=c.execute('SELECT * FROM task_template WHERE id=?',(oid,)).fetchone()
                    if not old:return self.json(404,{'error':'模板不存在'})
                    c.execute('UPDATE task_template SET '+','.join(key+'=?' for key in fields)+' WHERE id=?',(*fields.values(),oid))
                    audit(c,'task_template',oid,'update',str(template_actor),before=rowdict(old),after=fields)
                c.commit(); out={'id':oid,'status':'ok'}
            elif p.path=='/api/routine/generate':
                self.management_user(c,b)
                generated=generate_routines(c,b.get('instance_date'),template_ids=b.get('template_ids')); c.commit(); out={'generated_task_ids':generated,'count':len(generated)}
            elif p.path=='/api/ops/daily-run':
                out=run_daily_jobs(c,b.get('report_date'),b.get('kind','all')); c.commit()
            elif p.path=='/api/task/drafts/parse':
                if not b.get('text'): return self.json(400,{'error':'text is required'})
                from datetime import timedelta
                batch_id=b.get('session_id') or f"web:{b.get('user_id','anonymous')}:{secrets.token_hex(6)}"; expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(); drafts=[]; duplicates=[]
                for slots,missing in ai_drafts(b['text'],b.get('user_id'),c):
                    matches=task_duplicate_candidates(c,slots)
                    if matches['exact']:
                        duplicates.extend([{**item,'kind':'exact'} for item in matches['exact']])
                        continue
                    oid=c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(batch_id,b.get('user_id'),b.get('source_type','text'),b['text'],json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',expire,t,t)).lastrowid
                    drafts.append({'id':oid,'slots':slots,'missing_slots':missing,'status':'collecting' if missing else 'preview','similar_tasks':matches['similar']})
                    duplicates.extend([{**item,'kind':'similar'} for item in matches['similar']])
                c.commit(); out={'session_id':batch_id,'drafts':drafts,'duplicates':duplicates,'expire_at':expire}
            elif p.path=='/api/task/draft/parse':
                if not b.get('text'): return self.json(400,{'error':'text is required'})
                from datetime import timedelta
                slots,missing=draft_slots(b['text'],b.get('user_id'),c); expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
                matches=task_duplicate_candidates(c,slots)
                if matches['exact']:
                    return self.json(409,{'error':'已存在相同任务，未生成重复草稿','duplicates':matches['exact']})
                oid=c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(b.get('session_id') or f"web:{b.get('user_id','anonymous')}",b.get('user_id'),b.get('source_type','text'),b['text'],json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',expire,t,t)).lastrowid; c.commit(); out={'id':oid,'slots':slots,'missing_slots':missing,'status':'collecting' if missing else 'preview','expire_at':expire}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/complete'):
                did=int(p.path.split('/')[4]); draft=c.execute('SELECT * FROM conversation_draft WHERE id=?',(did,)).fetchone()
                if not draft or draft['expire_at']<t: return self.json(410,{'error':'draft expired or not found'})
                slots=json.loads(draft['slots_json'])
                optional_fields=('description','org_node_id','priority','task_type')
                for key,value in b.items():
                    if key in optional_fields and value in (None,''):
                        slots[key]=None
                    elif value not in (None,''):
                        slots[key]=value
                if slots.get('priority') not in ('P0','P1','P2'): slots['priority']=None
                if slots.get('task_type') not in ('ROUTINE','PROJECT','ADHOC'): slots['task_type']=None
                missing=[k for k in ('title','owner_id','due_at') if not slots.get(k)]
                matches=task_duplicate_candidates(c,slots,exclude_draft_id=did)
                if matches['exact']:
                    return self.json(409,{'error':'已存在相同任务，请修改草稿后再保存','duplicates':matches['exact']})
                c.execute('UPDATE conversation_draft SET slots_json=?,missing_slots=?,status=?,updated_at=? WHERE id=?',(json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',t,did)); c.commit(); out={'id':did,'slots':slots,'missing_slots':missing,'status':'collecting' if missing else 'preview','similar_tasks':matches['similar']}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/confirm'):
                did=int(p.path.split('/')[4]); draft=c.execute('SELECT * FROM conversation_draft WHERE id=?',(did,)).fetchone()
                if not draft or draft['expire_at']<t: return self.json(410,{'error':'draft expired or not found'})
                slots=json.loads(draft['slots_json']); missing=[k for k in ('title','owner_id','due_at') if not slots.get(k)]
                if missing: return self.json(400,{'error':'missing slots','fields':missing})
                try: fields=validate_task_fields(c,{**slots,'priority':slots.get('priority') or 'P1','task_type':slots.get('task_type') or 'PROJECT'},require_all=True)
                except ValueError as exc: return self.json(400,{'error':str(exc)})
                matches=task_duplicate_candidates(c,fields,exclude_draft_id=did)
                if matches['exact']:
                    return self.json(409,{'error':'已存在相同任务，未重复创建','duplicates':matches['exact']})
                approver_id=approver_for(c,fields['owner_id']); approval_status='pending' if approver_id else 'approved'
                oid=c.execute("INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,risk_status,deliverable,source,task_type,approval_status,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(fields['org_node_id'],fields['title'],fields['description'],fields['owner_id'],fields['due_at'],fields['priority'],'normal',fields['deliverable'],'conversation',fields['task_type'],approval_status,approver_id,t,t)).lastrowid
                aid=create_approval(c,'task_confirm',oid,oid,approver_id)
                c.execute("UPDATE conversation_draft SET status='generated',updated_at=? WHERE id=?",(t,did)); audit(c,'task',oid,'create_from_draft',str(draft['user_id'] or 'local'),after=slots); c.commit(); out={'task_id':oid,'approval_id':aid,'status':approval_status}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/discard'):
                did=int(p.path.split('/')[4])
                if not discard_draft(c,did,'local'): return self.json(404,{'error':'draft is no longer available'})
                c.commit(); out={'id':did,'status':'discarded'}
            elif p.path=='/api/tasks':
                required=['title','owner_id','due_at']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                try: fields=validate_task_fields(c,b,require_all=True)
                except ValueError as exc: return self.json(400,{'error':str(exc)})
                matches=task_duplicate_candidates(c,fields)
                if matches['exact']:
                    return self.json(409,{'error':'已存在相同任务，未重复创建','duplicates':matches['exact']})
                approver_id=approver_for(c,fields['owner_id'])
                task_type=fields['task_type']
                complexity=b.get('complexity','general')
                if complexity not in PERFORMANCE_DEFAULTS['complexity']: return self.json(400,{'error':'invalid complexity'})
                # 兼容历史接口：未填写时按类型带出待校准的默认标准工时；网页端会要求专项、临时任务补充口径。
                estimated_hours=float(b.get('estimated_hours') or {'ROUTINE':2,'PROJECT':8,'ADHOC':4}[task_type])
                approval_status='pending' if approver_id else 'approved'
                oid=c.execute("INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,risk_status,deliverable,source,task_type,approval_status,approver_id,estimated_hours,actual_hours,complexity,business_impact,urgent_source,urgent_level,plan_impact,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(fields['org_node_id'],fields['title'],fields['description'],fields['owner_id'],fields['due_at'],fields['priority'],b.get('risk_status','normal'),fields['deliverable'],b.get('source','manual'),task_type,approval_status,approver_id,estimated_hours,float(b.get('actual_hours') or 0),complexity,b.get('business_impact',''),b.get('urgent_source',''),b.get('urgent_level',''),b.get('plan_impact',''),t,t)).lastrowid
                aid=create_approval(c,'task_confirm',oid,oid,approver_id); audit(c,'task',oid,'create_pending','local',after=b); c.commit(); out={'id':oid,'approval_id':aid,'approval_status':approval_status}
                response_code=201
            elif p.path=='/api/test-issues':
                if not str(b.get('title','')).strip(): return self.json(400,{'error':'问题描述必填'})
                record_type=str(b.get('record_type','issue')).strip()
                if record_type not in ('issue','requirement'): return self.json(400,{'error':'记录类型无效'})
                oid=c.execute('INSERT INTO test_issue(record_type,title,module,severity,reporter,steps,expected_result,actual_result,task_ref,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(record_type,str(b['title']).strip(),str(b.get('module','')).strip(),str(b.get('severity','normal')).strip(),str(b.get('reporter','')).strip(),str(b.get('steps','')).strip(),str(b.get('expected_result','')).strip(),str(b.get('actual_result') or b['title']).strip(),str(b.get('task_ref','')).strip(),t)).lastrowid; audit(c,'test_issue',oid,'create','manual',after=b); c.commit(); out={'id':oid,'status':'open'}
            elif p.path.startswith('/api/test-issues/'):
                iid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM test_issue WHERE id=?',(iid,)).fetchone()
                if not old: return self.json(404,{'error':'问题记录不存在'})
                action=b.get('action')
                if action=='delete':
                    c.execute('DELETE FROM test_issue WHERE id=?',(iid,)); audit(c,'test_issue',iid,'delete','manual',before=rowdict(old)); c.commit(); out={'id':iid,'status':'deleted'}
                elif action=='close':
                    c.execute("UPDATE test_issue SET status='closed' WHERE id=?",(iid,)); audit(c,'test_issue',iid,'close','manual',before=rowdict(old)); c.commit(); out={'id':iid,'status':'closed'}
                elif action=='update':
                    if not str(b.get('title','')).strip(): return self.json(400,{'error':'问题描述必填'})
                    record_type=str(b.get('record_type',old['record_type'] or 'issue')).strip()
                    if record_type not in ('issue','requirement'): return self.json(400,{'error':'记录类型无效'})
                    c.execute('UPDATE test_issue SET record_type=?,title=?,module=?,severity=?,reporter=?,task_ref=? WHERE id=?',(record_type,str(b['title']).strip(),str(b.get('module','')).strip(),str(b.get('severity','normal')).strip(),str(b.get('reporter','')).strip(),str(b.get('task_ref',old['task_ref'] or '')).strip(),iid)); audit(c,'test_issue',iid,'update','manual',before=rowdict(old),after=b); c.commit(); out={'id':iid,'status':'updated'}
                else: return self.json(400,{'error':'不支持的操作'})
            elif p.path=='/api/ai/draft': out=ai_draft(b.get('text',''))
            elif p.path=='/api/performance/config':
                operator=self.actor_id(b.get('operator_id')); user=c.execute('SELECT role FROM user WHERE id=?',(operator,)).fetchone() if operator else None
                if not user or user['role'] not in ('manager','pm'): return self.json(403,{'error':'仅管理PM和部门负责人可配置绩效口径'})
                config=b.get('config') or PERFORMANCE_DEFAULTS
                c.execute("INSERT INTO performance_config(config_key,config_json,updated_at,updated_by) VALUES('default',?,?,?) ON CONFLICT(config_key) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at,updated_by=excluded.updated_by",(json.dumps(config,ensure_ascii=False),t,operator)); audit(c,'performance_config',1,'update',str(operator),after=config); c.commit(); out={'status':'updated'}
            elif p.path=='/api/performance/adjustments':
                operator=self.actor_id(b.get('operator_id')); user=c.execute('SELECT role FROM user WHERE id=?',(operator,)).fetchone() if operator else None
                if not user or user['role'] not in ('manager','pm'): return self.json(403,{'error':'仅管理PM和部门负责人可调整绩效'})
                start,end=performance_period(b.get('period'),b.get('period_start'),b.get('period_end'))
                if not b.get('user_id') or not b.get('reason'): return self.json(400,{'error':'成员和调整原因必填'})
                oid=c.execute('INSERT INTO performance_adjustment(user_id,period_start,period_end,score_delta,reason,created_by,created_at) VALUES(?,?,?,?,?,?,?)',(b['user_id'],start,end,float(b.get('score_delta') or 0),b['reason'],operator,t)).lastrowid; audit(c,'performance_adjustment',oid,'create',str(operator),after=b); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/approvals/') and p.path.endswith('/confirm'):
                aid=int(p.path.split('/')[3]); approval=c.execute('SELECT * FROM approval_record WHERE id=?',(aid,)).fetchone()
                if not approval or approval['status']!='pending': return self.json(404,{'error':'pending approval not found'})
                result=b.get('result'); operator=self.actor_id(b.get('operator_id'))
                if result not in ('approved','rejected'): return self.json(400,{'error':'result must be approved or rejected'})
                if not operator: return self.json(403,{'error':'operator_id is required'})
                if approval['approver_id'] and int(operator)!=approval['approver_id']: return self.json(403,{'error':'only assigned approver may confirm'})
                c.execute('UPDATE approval_record SET status=?,result_reason=?,operated_by=?,operated_at=? WHERE id=?',(result,b.get('reason',''),operator,t,aid))
                if approval['biz_type']=='task_confirm': c.execute("UPDATE task SET approval_status=?,work_status=?,confirmed_by=?,confirmed_at=?,confirm_source='manual',updated_at=? WHERE id=?",('approved' if result=='approved' else 'rejected','assigned' if result=='approved' else 'draft',operator,t,t,approval['task_id']))
                elif approval['biz_type']=='progress_confirm' and result=='approved':
                    record=c.execute('SELECT * FROM progress_record WHERE id=?',(approval['biz_id'],)).fetchone(); c.execute("UPDATE progress_record SET status='approved',effective_percent=?,confirmed_by=?,confirmed_at=? WHERE id=?",(record['reported_percent'],operator,t,record['id'])); c.execute('UPDATE task SET effective_percent=?,progress_percent=?,risk_status=?,expected_finish=?,work_status=?,updated_at=? WHERE id=?',(record['reported_percent'],record['reported_percent'],'blocked' if record['is_blocked'] else 'normal',record['expected_finish'],progress_work_status(c,approval['task_id'],record['reported_percent']),t,approval['task_id']))
                audit(c,'approval',aid,result,str(operator or 'local'),after=b); c.commit(); out={'id':aid,'status':result}
            elif p.path.startswith('/api/tasks/') and p.path.endswith('/progress'):
                tid=int(p.path.split('/')[3]); task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
                if not task or task['approval_status']!='approved': return self.json(400,{'error':'only approved tasks may receive progress'})
                percent=b.get('percent');
                if not isinstance(percent,int) or percent<0 or percent>100 or not b.get('comment'): return self.json(400,{'error':'percent (0-100 integer) and comment are required'})
                reporter=self.actor_id(b.get('reporter_id',task['owner_id']))
                if not reporter: return self.json(403,{'error':'reporter_id is required'})
                if int(reporter)!=int(task['owner_id']): return self.json(403,{'error':'only task owner may report progress'})
                rid,aid,_=submit_progress(c,tid,reporter,percent,b['comment'],b.get('blocked',False),b.get('block_reason',''),b.get('expected_finish'),b.get('source','web')); c.commit(); out={'id':rid,'approval_id':aid,'status':'pending'}
            elif p.path.startswith('/api/tasks/'):
                tid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone();
                if not old: return self.json(404,{'error':'task not found'})
                # Each action carries its accountable actor under its own field.
                # Never fall back to an anonymous caller: destructive edits still
                # require operator_id, while acceptance/feedback use their named actor.
                action_actor=(b.get('operator_id')
                              or (b.get('acceptor_id') if p.path.endswith('/accept') else None)
                              or (b.get('user_id') if p.path.endswith('/feedback') else None))
                actor=self.actor_id(action_actor)
                actor_row=c.execute('SELECT role FROM user WHERE id=?',(actor,)).fetchone() if actor else None
                if not actor: return self.json(403,{'error':'operator_id is required'})
                can_manage=int(actor)==int(old['owner_id']) or (actor_row and actor_row['role'] in ('manager','pm'))
                if b.get('action')=='delete':
                    if not can_manage: return self.json(403,{'error':'only the task owner or manager may delete a task'})
                    c.execute('DELETE FROM approval_record WHERE task_id=?',(tid,)); c.execute('DELETE FROM progress_record WHERE task_id=?',(tid,)); c.execute('DELETE FROM feedback WHERE task_id=?',(tid,)); c.execute('DELETE FROM acceptance WHERE task_id=?',(tid,)); c.execute('DELETE FROM task WHERE id=?',(tid,)); action='delete'
                elif p.path.endswith('/hours'):
                    if not can_manage:return self.json(403,{'error':'仅任务责任人、部门负责人或管理 PM 可填写工时'})
                    if old['task_type']!='ROUTINE':return self.json(400,{'error':'此入口仅用于例行任务周工时'})
                    actual=routines.hours(b.get('actual_hours'),actual=True)
                    c.execute('UPDATE task SET actual_hours=?,routine_hours_updated_at=?,updated_at=? WHERE id=?',(actual,t,t,tid)); action='routine_hours'
                elif p.path.endswith('/feedback'):
                    if not b.get('content'): return self.json(400,{'error':'content is required'})
                    feedback_user=self.actor_id(b.get('user_id'))
                    if not feedback_user: return self.json(403,{'error':'user_id is required'})
                    if int(feedback_user)!=int(old['owner_id']): return self.json(403,{'error':'only the task owner may submit feedback'})
                    c.execute('INSERT INTO feedback(task_id,user_id,content,parsed_due_at,progress,risk,created_at) VALUES(?,?,?,?,?,?,?)',(tid,feedback_user,b.get('content',''),parse_due(b.get('content','')),b.get('progress',''),b.get('risk',''),t)); c.execute("UPDATE task SET risk_status=?,updated_at=? WHERE id=?",('blocked' if b.get('risk') else old['risk_status'],t,tid)); action='feedback'
                elif p.path.endswith('/accept'):
                    result=b.get('result');
                    if result not in ('passed','rejected'): return self.json(400,{'error':'result must be passed or rejected'})
                    if old['work_status']!='awaiting_acceptance': return self.json(400,{'error':'task is not awaiting acceptance'})
                    acceptor_id=self.actor_id(b.get('acceptor_id'))
                    approver_id=old['approver_id'] or approver_for(c,old['owner_id'])
                    if not acceptor_id or int(acceptor_id)!=int(approver_id): return self.json(403,{'error':'only the task owner\'s approver may accept'})
                    c.execute('INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,?,?,?)',(tid,acceptor_id,result,b.get('reason',''),t)); c.execute('UPDATE task SET work_status=?,updated_at=? WHERE id=?',('completed' if result=='passed' else 'in_progress',t,tid)); action='accept' if result=='passed' else 'reject'
                else:
                    allowed={'title','description','deliverable','org_node_id','task_type','work_status','risk_status','due_at','priority','owner_id','estimated_hours','actual_hours','complexity','business_impact','urgent_source','urgent_level','plan_impact'}; changes={k:v for k,v in b.items() if k in allowed};
                    if not changes: return self.json(400,{'error':'no editable fields'})
                    if 'work_status' in changes:
                        operator_id=actor
                        if not operator_id or int(operator_id)!=int(old['owner_id']): return self.json(403,{'error':'only the task owner may update task status'})
                        transition=(old['work_status'],changes['work_status'])
                        if transition not in {('assigned','in_progress'),('in_progress','awaiting_acceptance')}:
                            return self.json(400,{'error':'invalid task status transition'})
                    elif not can_manage: return self.json(403,{'error':'only the task owner or manager may edit a task'})
                    try:
                        validated=validate_task_fields(c,{**rowdict(old),**changes},require_all=True)
                    except ValueError as exc: return self.json(400,{'error':str(exc)})
                    for key in ('title','description','deliverable','priority','task_type','due_at','owner_id','org_node_id'):
                        if key in changes: changes[key]=validated[key]
                    if old['task_type']=='ROUTINE':
                        if 'estimated_hours' in changes:
                            changes['estimated_hours']=routines.hours(changes['estimated_hours'])
                            if old['routine_week_start'] and changes['estimated_hours']!=old['estimated_hours']:
                                return self.json(400,{'error':'标准工时为生成时的模板快照；请修改模板以调整后续任务'})
                        if 'actual_hours' in changes:
                            changes['actual_hours']=routines.hours(changes['actual_hours'],actual=True)
                            changes['routine_hours_updated_at']=t
                    if 'owner_id' in changes:
                        # A task that is still waiting for confirmation must keep its
                        # confirmation chain in sync with the responsible person.
                        # Otherwise changing the owner in a draft leaves the approval
                        # assigned to the previous owner's manager.
                        changes['approver_id']=approver_for(c,changes['owner_id'])
                    changes['updated_at']=t; c.execute('UPDATE task SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),tid)); action='update'
                    if 'owner_id' in changes and old['approval_status']=='pending':
                        next_approver=changes.get('approver_id')
                        if next_approver:
                            c.execute("UPDATE approval_record SET approver_id=? WHERE task_id=? AND biz_type='task_confirm' AND status='pending'",(next_approver,tid))
                        else:
                            # The department top owner has no higher-level approver.
                            # Do not leave an unprocessable confirmation item behind.
                            c.execute("UPDATE approval_record SET status='approved',operated_at=?,operated_by=? WHERE task_id=? AND biz_type='task_confirm' AND status='pending'",(t,actor,tid))
                            c.execute("UPDATE task SET approval_status='approved',updated_at=? WHERE id=?",(t,tid))
                audit(c,'task',tid,action,'local',before=rowdict(old),after=b); c.commit(); out={'id':tid,'status':'ok'}
            elif p.path=='/api/notify': out=WeComAdapter().send(b.get('userid',''),b.get('text',''))
            else: return self.json(404,{'error':'not found'})
            self.json(response_code,out)
        except responsibility_domains.Referenced as exc:return self.json(409,{'error':str(exc)})
        except LookupError as exc:return self.json(404,{'error':str(exc)})
        except accounts.Conflict as exc:return self.json(409,{'error':str(exc)})
        except PermissionError as exc:return self.json(403,{'error':str(exc)})
        except ValueError as exc:return self.json(400,{'error':str(exc)})
        finally: c.close()
    def handle_feishu_post(self):
        try:
            payload=self.body(); adapter=FeishuAdapter()
            if not adapter.configured(): return self.json(503,{'error':'Feishu credentials are required'})
            if payload.get('type')=='url_verification':
                if payload.get('token')!=adapter.verify_token: return self.json(403,{'error':'invalid verification token'})
                return self.json(200,{'challenge':payload.get('challenge','')})
            header=payload.get('header',{})
            if not secrets.compare_digest(str(header.get('token') or payload.get('token') or ''),str(adapter.verify_token or '')):
                return self.json(403,{'error':'invalid verification token'})
            event=payload.get('event',{}); event_id=header.get('event_id') or event.get('message',{}).get('message_id')
            if not event_id: return self.json(400,{'error':'event id is required'})
            sender=(event.get('sender') or {}).get('sender_id',{}).get('open_id'); message=event.get('message') or {}; content=message.get('content','')
            c=db(); user=c.execute('SELECT id FROM user WHERE feishu_open_id=?',(sender,)).fetchone() if sender else None
            try: c.execute('INSERT INTO im_message_log(channel,external_msg_id,user_id,payload_json,created_at) VALUES(?,?,?,?,?)',('feishu',event_id,user[0] if user else None,json.dumps(payload,ensure_ascii=False),now()))
            except sqlite3.IntegrityError: c.close(); return self.json(200,{'code':0})
            text=''; reply=''; message_type=message.get('message_type')
            if message_type=='text':
                try: text=json.loads(content).get('text','')
                except Exception: text=content
            elif message_type=='audio':
                try:
                    audio=json.loads(content); raw=adapter.download_message_resource(message.get('message_id'),audio.get('file_key'),'file')
                    text=ai_audio_transcribe(raw,audio.get('file_name') or 'feishu-audio.ogg','audio/ogg')
                except Exception as exc: reply='暂未能识别这段语音：'+str(exc)+'。请改发文字，或联系管理员配置语音转写能力。'
            if text:
                uid=user[0] if user else None; session_id=f'feishu:{sender or "anonymous"}'
                reply=process_bot_message(c,text,uid,session_id)
            c.commit(); c.close()
            if sender and reply:
                try: adapter.send_text(sender,reply)
                except Exception: pass  # A send failure must never roll back or repeat a confirmed mutation.
            self.json(200,{'code':0})
        except Exception as exc: self.json(400,{'error':'feishu callback failed','detail':str(exc)})
    def handle_wecom_post(self):
        p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query); codec=WeComCallback()
        if not codec.configured(): return self.json(503,{'error':'WECOM_TOKEN and WECOM_AES_KEY are required'})
        try:
            raw=self.rfile.read(int(self.headers.get('Content-Length','0'))); root=ET.fromstring(raw); encrypted=root.findtext('Encrypt') or ''
            if q.get('msg_signature',[''])[0] != codec.signature(q.get('timestamp',[''])[0],q.get('nonce',[''])[0],encrypted): return self.json(403,{'error':'invalid signature'})
            plain=codec.decrypt(encrypted); c=db(); codec.handle_message(c,plain); c.commit(); c.close()
            data=b'success'; self.send_response(200); self.send_header('Content-Type','text/plain'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
        except Exception as exc: self.json(400,{'error':'callback processing failed','detail':str(exc)})

if __name__=='__main__':
    if (ROOT/'data'/'.auto-reload-enabled').is_file() and os.getenv('WMT_RELOAD_CHILD')!='1':
        from dev_server import run
        raise SystemExit(run(ROOT))
    initialize(); port=int(os.getenv('PORT','8080')); print(f'Work Management Tree listening on http://127.0.0.1:{port}'); ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
