import base64, hashlib, io, json, mimetypes, os, re, secrets, sqlite3, struct, subprocess, tempfile, urllib.request, urllib.parse, zipfile
from base64 import b64decode
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

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
CREATE TABLE IF NOT EXISTS task_template (id INTEGER PRIMARY KEY AUTOINCREMENT, org_node_id INTEGER NOT NULL, title TEXT NOT NULL, description TEXT DEFAULT '', owner_id INTEGER NOT NULL, deliverable TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'P1', recurrence TEXT NOT NULL, due_offset_days INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active', review_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_instance (id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL, instance_date TEXT NOT NULL, task_id INTEGER NOT NULL, created_at TEXT NOT NULL, UNIQUE(template_id,instance_date));
CREATE TABLE IF NOT EXISTS daily_report_status (id INTEGER PRIMARY KEY AUTOINCREMENT, report_date TEXT NOT NULL, user_id INTEGER NOT NULL, status TEXT NOT NULL, task_count INTEGER NOT NULL DEFAULT 0, source TEXT NOT NULL DEFAULT 'system', created_at TEXT NOT NULL, UNIQUE(report_date,user_id));
CREATE TABLE IF NOT EXISTS job_run_log (id INTEGER PRIMARY KEY AUTOINCREMENT, job_name TEXT NOT NULL, run_key TEXT NOT NULL, result_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(job_name,run_key));
CREATE TABLE IF NOT EXISTS conversation_draft (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT NOT NULL, user_id INTEGER, source_type TEXT NOT NULL DEFAULT 'text', raw_text TEXT NOT NULL, slots_json TEXT NOT NULL DEFAULT '{}', missing_slots TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL DEFAULT 'collecting', expire_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS im_message_log (id INTEGER PRIMARY KEY AUTOINCREMENT, channel TEXT NOT NULL, external_msg_id TEXT NOT NULL, user_id INTEGER, payload_json TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(channel,external_msg_id));
CREATE TABLE IF NOT EXISTS feishu_binding_history (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, action TEXT NOT NULL, old_open_id TEXT DEFAULT '', new_open_id TEXT DEFAULT '', operator TEXT DEFAULT 'local', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS feishu_profile (open_id TEXT PRIMARY KEY, display_name TEXT DEFAULT '', avatar_url TEXT DEFAULT '', fetched_at TEXT NOT NULL, fetch_error TEXT DEFAULT '');
CREATE TABLE IF NOT EXISTS login_state (state_hash TEXT PRIMARY KEY, expire_at TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS login_session (session_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, expire_at TEXT NOT NULL, created_at TEXT NOT NULL, last_seen_at TEXT NOT NULL);
"""

def now(): return datetime.now(timezone.utc).isoformat()
def feishu_login_enabled(): return os.getenv('WMT_AUTH_MODE','').lower()=='feishu'
def feishu_redirect_uri():
    configured=os.getenv('WMT_FEISHU_OAUTH_REDIRECT_URI','').strip()
    if configured: return configured
    base=os.getenv('WMT_PUBLIC_BASE_URL','').strip().rstrip('/')
    return base+'/auth/feishu/callback' if base else ''
def db():
    c = sqlite3.connect(DB_PATH, timeout=5); c.row_factory = sqlite3.Row; c.executescript(SCHEMA)
    for statement in ("ALTER TABLE user ADD COLUMN feishu_open_id TEXT", "ALTER TABLE org_node ADD COLUMN domain_code TEXT DEFAULT ''", "ALTER TABLE org_node ADD COLUMN operating_line TEXT DEFAULT ''", "ALTER TABLE task ADD COLUMN task_type TEXT NOT NULL DEFAULT 'PROJECT'", "ALTER TABLE task ADD COLUMN parent_template_id INTEGER", "ALTER TABLE task ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'approved'", "ALTER TABLE task ADD COLUMN approver_id INTEGER", "ALTER TABLE task ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN effective_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN expected_finish TEXT", "ALTER TABLE task ADD COLUMN confirmed_by INTEGER", "ALTER TABLE task ADD COLUMN confirmed_at TEXT", "ALTER TABLE task ADD COLUMN confirm_source TEXT"):
        try: c.execute(statement)
        except sqlite3.OperationalError: pass
    return c
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
    if approver:
        delegate=c.execute("SELECT delegate_user_id FROM delegate WHERE user_id=? AND scope IN ('approval','all') AND start_at<=? AND end_at>=? ORDER BY id DESC LIMIT 1",(approver,now(),now())).fetchone()
        if delegate: return delegate[0]
    return approver
def create_approval(c, biz_type, biz_id, task_id, approver_id):
    return c.execute("INSERT INTO approval_record(biz_type,biz_id,task_id,approver_id,due_at,created_at) VALUES(?,?,?,?,?,?)",(biz_type,biz_id,task_id,approver_id,now(),now())).lastrowid
def generate_routines(c, instance_date=None):
    from datetime import date, timedelta
    day=date.fromisoformat(instance_date) if instance_date else datetime.now(timezone.utc).date(); created=[]
    weekday=day.strftime('%a').upper()
    for template in c.execute("SELECT * FROM task_template WHERE status='active'"):
        recurrence=template['recurrence'].upper()
        if recurrence=='WEEKLY' and weekday!='MON': continue
        if recurrence not in ('DAILY','WEEKLY'): continue
        if c.execute('SELECT 1 FROM task_instance WHERE template_id=? AND instance_date=?',(template['id'],day.isoformat())).fetchone(): continue
        due=(day+timedelta(days=template['due_offset_days'])).isoformat()+'T17:00:00+08:00'; t=now(); approver=approver_for(c,template['owner_id'])
        tid=c.execute("INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,deliverable,source,task_type,parent_template_id,approval_status,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(template['org_node_id'],template['title'],template['description'],template['owner_id'],due,template['priority'],template['deliverable'],'routine_generator','ROUTINE',template['id'],'approved',approver,t,t)).lastrowid
        c.execute('INSERT INTO task_instance(template_id,instance_date,task_id,created_at) VALUES(?,?,?,?)',(template['id'],day.isoformat(),tid,t)); audit(c,'task',tid,'routine_generated','system',after={'template_id':template['id'],'instance_date':day.isoformat()}); created.append(tid)
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
            c.execute("UPDATE task SET effective_percent=?,progress_percent=?,risk_status=?,expected_finish=?,work_status=?,confirm_source='timeout_auto',updated_at=? WHERE id=?",(record['reported_percent'],record['reported_percent'],'blocked' if record['is_blocked'] else 'normal',record['expected_finish'],'completed' if record['reported_percent']==100 else 'in_progress',now(),approval['task_id']))
            audit(c,'approval',approval['id'],'timeout_auto','system',after={'hours':hours}); auto+=1
        approvals=c.execute("SELECT a.*,t.title FROM approval_record a LEFT JOIN task t ON t.id=a.task_id WHERE a.status='pending'").fetchall()
        out['approval_reminders']=len(approvals); out['auto_confirmed_progress']=auto
        for approval in approvals:
            if not approval['approver_id'] or not approval['task_id']: continue
            title=approval['title'] or '一项工作事项'
            delivery=send_feishu_once(c,approval['task_id'],approval['approver_id'],'approval_reminder',day,'【待确认提醒】\n《'+title+'》等待您的'+('任务确认' if approval['biz_type']=='task_confirm' else '进度确认')+'，请在工作管理树确认中心处理。'); count_delivery(delivery)
    c.execute("INSERT OR REPLACE INTO job_run_log(job_name,run_key,result_json,created_at) VALUES(?,?,?,?)",('daily_'+kind,day,json.dumps(out,ensure_ascii=False),now()))
    return out
def draft_slots(text, owner_id=None, c=None):
    draft=ai_draft(text); own_connection=c is None; c=c or db()
    try:
        domain=detect_responsibility_domain(c,text)
        # An explicitly named owner in the meeting item always takes precedence
        # over the sender/current web user used only as a fallback.
        detected_owner=detect_task_owner(c,text) or owner_id
        # Rule parsing is the stable fallback for an explicit date in meeting notes.
        # It also covers dates that a model may omit because of formatting whitespace.
        slots={'title':draft['title'],'description':draft.get('description') or task_detail(text),'due_at':draft['due_at'] or parse_due(text),'priority':draft['priority'],'deliverable':draft['deliverable'],'owner_id':detected_owner,'org_node_id':domain['id'] if domain else None,'domain_name':domain['name'] if domain else '','task_type':'PROJECT'}
        missing=[k for k in ('owner_id','due_at') if not slots.get(k)]
        return slots,missing
    finally:
        if own_connection: c.close()

# Internal storage keys must never be exposed in conversational or UI prompts.
SLOT_LABELS={
    'title':'任务标题', 'description':'任务详情', 'owner_id':'任务责任人', 'due_at':'截止日期',
    'deliverable':'交付物或验收标准', 'org_node_id':'责任域',
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

def initialize():
    c=db()
    try:
        seed(c); c.execute("UPDATE user SET role='member' WHERE role='owner'"); initialize_responsibility_domains(c); ensure_feishu_binding_history(c); normalize_legacy_conversation_tasks(c); c.commit()
    finally:
        c.close()

def task_view(c, r):
    d=rowdict(r); owner=c.execute('SELECT name FROM user WHERE id=?',(d['owner_id'],)).fetchone(); d['owner_name']=owner[0] if owner else '未知责任人'
    approver=c.execute('SELECT name FROM user WHERE id=?',(d.get('approver_id'),)).fetchone() if d.get('approver_id') else None; d['approver_name']=approver[0] if approver else ''
    due=datetime.fromisoformat(d['due_at'].replace('Z','+00:00')); current=datetime.now(timezone.utc)
    d['time_status']='overdue' if due < current and d['work_status'] not in ('completed','cancelled') else ('due_soon' if (due-current).total_seconds()<86400 and d['work_status'] not in ('completed','cancelled') else 'normal')
    progress=c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at DESC,id DESC LIMIT 1',(d['id'],)).fetchone()
    d['latest_progress']=rowdict(progress)
    return d

def parse_due(text):
    # Meeting minutes commonly write dates as “2026 年 10 月 30 日前”.
    # Remove formatting whitespace first, while keeping the original text intact
    # for title and delivery extraction elsewhere.
    normalized=re.sub(r'\s+','',str(text or ''))
    m=re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})',normalized)
    if m: return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T17:00:00+08:00'
    weekday=re.search(r'(本周|这周|下周)?(?:星期|周)([一二三四五六日天])',normalized)
    if weekday:
        from datetime import timedelta
        target_index={'一':0,'二':1,'三':2,'四':3,'五':4,'六':5,'日':6,'天':6}[weekday.group(2)]
        today=datetime.now(timezone.utc).date(); target=today-timedelta(days=today.weekday())+timedelta(days=target_index)
        if weekday.group(1)=='下周' or (not weekday.group(1) and target<today): target+=timedelta(days=7)
        return target.isoformat()+'T17:00:00+08:00'
    if '明天' in normalized:
        from datetime import timedelta
        return (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    return None

def parse_progress_message(text):
    """Parse an explicit task-number progress message; never guesses a task."""
    task_match=re.search(r'任务\s*#\s*(\d+)',text)
    if not task_match: return None
    percent_match=re.search(r'(\d{1,3})\s*[%％]',text)
    percent=int(percent_match.group(1)) if percent_match else (100 if re.search(r'已完成|完成了|完成$',text.strip()) else None)
    blocked_match=re.search(r'(?:阻塞|风险)(?:原因)?\s*[：:]?\s*(.+)',text)
    return {'task_id':int(task_match.group(1)),'percent':percent,'comment':text.strip(),'blocked':bool(blocked_match),'block_reason':blocked_match.group(1).strip() if blocked_match else '','expected_finish':parse_due(text)}

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

def ai_vision_extract(data, mime_type):
    """Extract task-bearing text from an image through the configured vision model."""
    if not ai_configured():
        raise ValueError('图片识别需要先配置 AI 视觉模型')
    encoded=base64.b64encode(data).decode()
    prompt='请识别图片中的中文文字，只输出与待办任务、责任人、截止日期、交付物有关的原文，不要解释。若没有任务信息则输出空字符串。'
    payload={'model':os.getenv('AI_MODEL'),'messages':[{'role':'user','content':[{'type':'text','text':prompt},{'type':'image_url','image_url':{'url':f'data:{mime_type};base64,{encoded}'}}]}],'temperature':0}
    req=urllib.request.Request(os.getenv('AI_API_BASE').rstrip('/')+'/chat/completions',json.dumps(payload).encode(),{'Authorization':'Bearer '+os.getenv('AI_API_KEY'),'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=45) as response:
        return json.load(response)['choices'][0]['message']['content'].strip()

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
        with tempfile.NamedTemporaryFile(suffix='.pdf') as file:
            file.write(data); file.flush()
            try: text=subprocess.check_output(['pdftotext',file.name,'-'],stderr=subprocess.DEVNULL,timeout=20).decode('utf-8',errors='replace').strip()
            except (FileNotFoundError,subprocess.CalledProcessError,subprocess.TimeoutExpired): text=''
        if not text: raise ValueError('当前服务器未配置 PDF 文本解析能力，请上传 Word、图片或粘贴文字')
    elif suffix in {'.png','.jpg','.jpeg','.webp','.bmp'} or mime_type.startswith('image/'):
        text=ai_vision_extract(data,mime_type)
    elif suffix in {'.mp3','.wav','.m4a','.aac','.ogg','.mp4'} or mime_type.startswith('audio/'):
        text=ai_audio_transcribe(data,filename,mime_type)
    else:
        raise ValueError('仅支持图片、Word、PDF、文本和常见录音文件')
    if not text: raise ValueError('未从文件中识别到可用于建单的文字')
    return text

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
    source=re.sub(r'【[^】]*】','',str(text or '')).strip()
    source=re.sub(r'\s+',' ',source)
    # The action after “请某人负责完成” is normally the task itself.
    action=re.search(r'(?:请[^，。；]{1,20}?负责)?(?:完成|推进|落实|组织|开展|协同)([^。；]+)',source)
    candidate=action.group(1).strip() if action else source
    candidate=re.split(r'[，,](?:并)?(?:于|在)\s*\d{4}\s*年|[，,](?:请)?(?:于|在)\s*\d{4}[-/]',candidate)[0].strip(' ，,。；;')
    candidate=re.sub(r'(?:并)?(?:于|在)\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*$','',candidate).strip(' ，,。；;')
    if not candidate: candidate='待明确工作事项'
    return candidate[:40]

def task_detail(text):
    """Keep the work scope, but omit meeting headers and fields already structured."""
    source=re.sub(r'【[^】]*】','',str(text or '')).strip()
    source=re.sub(r'\s+',' ',source)
    action=re.search(r'(?:请[^，。；]{1,20}?负责)?(?:完成|推进|落实|组织|开展|协同)([^。；]+)',source)
    detail=(action.group(1).strip() if action else concise_task_title(source))
    detail=re.split(r'[，,](?:并)?(?:于|在)\s*\d{4}\s*年|[，,](?:请)?(?:于|在)\s*\d{4}[-/]',detail)[0]
    detail=re.sub(r'(?:并)?(?:于|在)\s*\d{4}\s*年\s*\d{1,2}\s*月\s*\d{1,2}\s*日.*$','',detail).strip(' ，,。；;')
    return detail or '待补充任务范围'

def detect_task_owner(c, text):
    """Recognize only explicit task assignments, never mere mentions in meeting notes."""
    source=str(text or '')
    for user in c.execute('SELECT id,name FROM user ORDER BY length(name) DESC'):
        name=re.escape(user['name'])
        if re.search(rf'(?:请|由)\s*{name}\s*(?:负责|牵头|完成|推进|落实|组织|开展|协同)',source):
            return user['id']
    return None

def normalize_legacy_conversation_tasks(c):
    """One-time-safe cleanup for drafts created before title/detail separation."""
    rows=c.execute("SELECT id,title,description FROM task WHERE source='conversation'").fetchall()
    for row in rows:
        source=row['description'] or row['title']
        # Older records may already have had their detail trimmed. Recover the
        # original note from the generated draft when available for owner repair.
        if not re.search(r'(?:请|由)\s*.+?(?:负责|牵头|完成|推进|落实|组织|开展|协同)',source):
            raw=c.execute("SELECT raw_text FROM conversation_draft WHERE status='generated' AND raw_text LIKE ? ORDER BY updated_at DESC LIMIT 1",('%'+row['title']+'%',)).fetchone()
            if raw: source=raw['raw_text']
        title=concise_task_title(source) if len(row['title'])>40 else row['title']
        owner=detect_task_owner(c,source)
        if owner:
            c.execute('UPDATE task SET title=?,description=?,owner_id=?,approver_id=?,updated_at=? WHERE id=?',(title,task_detail(source),owner,approver_for(c,owner),now(),row['id']))
        else:
            c.execute('UPDATE task SET title=?,description=?,updated_at=? WHERE id=?',(title,task_detail(source),now(),row['id']))

def ai_draft(text):
    """Creates a non-binding task draft. Uses an OpenAI-compatible endpoint when configured."""
    api_base, api_key, model = os.getenv('AI_API_BASE'), os.getenv('AI_API_KEY'), os.getenv('AI_MODEL')
    if api_base and api_key and model:
        prompt = ('从以下材料中仅提炼明确待办，输出 JSON，不要输出解释。字段为 title,description,due_at,priority,deliverable,owner_id,confidence。'
                  '忽略会议标题、参会信息、背景数据、讨论过程、已完成事项和与待办无关的内容。title 为简洁工作标题（不超过40字）；'
                  'description 只保留工作范围、关键动作和交付要求，不能重复标题，不能重复责任人和截止日期；'
                  'owner_id 必须为 null；无法确定的截止时间为 null；不得编造验收标准。材料：' + text)
        payload=json.dumps({'model':model,'messages':[{'role':'user','content':prompt}],'temperature':0.1}).encode()
        req=urllib.request.Request(api_base.rstrip('/')+'/chat/completions',payload,{'Authorization':'Bearer '+api_key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as f:
                content=json.load(f)['choices'][0]['message']['content']
            draft=json.loads(content.removeprefix('```json').removesuffix('```').strip())
            title=(draft.get('title') or '').strip()
            if not title or len(title)>40 or title in text:
                title=concise_task_title(text)
            return {'title':title, 'description':task_detail(draft.get('description') or text), 'due_at':draft.get('due_at'), 'priority':draft.get('priority','P1'), 'deliverable':draft.get('deliverable') or '待补充验收标准', 'owner_id':None, 'confidence':draft.get('confidence',0.5), 'requires_human_confirmation':True, 'mode':'model'}
        except Exception:
            # Safe fallback: a model outage must never block a human-created task.
            pass
    due=parse_due(text)
    return {'title':concise_task_title(text), 'description':task_detail(text), 'due_at': due, 'priority':'P1', 'deliverable':'待补充验收标准', 'owner_id':None, 'confidence':0.35 if not due else 0.65, 'requires_human_confirmation':True, 'mode':'rule'}

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
        query=urllib.parse.urlencode({'app_id':self.app_id,'redirect_uri':feishu_redirect_uri(),'state':state,'response_type':'code'})
        return 'https://accounts.feishu.cn/open-apis/authen/v1/authorize?'+query
    def exchange_login_code(self, code):
        if not self.login_configured(): raise RuntimeError('Feishu login is not configured')
        payload={'grant_type':'authorization_code','code':code,'client_id':self.app_id,'client_secret':self.secret,'redirect_uri':feishu_redirect_uri()}
        req=urllib.request.Request('https://open.feishu.cn/open-apis/authen/v1/access_token',json.dumps(payload).encode(),{'Content-Type':'application/json'})
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
            return rowdict(row)
        finally: c.close()
    def actor_id(self, submitted=None): return getattr(self,'authenticated_user_id',None) or submitted
    def authorized(self):
        if feishu_login_enabled(): return bool(self.session_user())
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
        user=self.session_user() if feishu_login_enabled() else None
        if user:
            self.authenticated_user_id=user['id']; return True
        if self.authorized(): return True
        if feishu_login_enabled(): return self.json(401,{'error':'login_required','login_url':'/auth/feishu/start'}) or False
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Work Management Tree"'); self.send_header('Content-Length','0'); self.end_headers(); return False
    def redirect(self, location, cookie=None):
        self.send_response(302); self.send_header('Location',location)
        if cookie: self.send_header('Set-Cookie',cookie)
        self.send_header('Content-Length','0'); self.end_headers()
    def session_payload(self):
        user=self.session_user()
        return {'authenticated':bool(user),'mode':'feishu' if feishu_login_enabled() else 'demo','user':user}
    def start_feishu_login(self):
        adapter=FeishuAdapter()
        if not feishu_login_enabled(): return self.redirect('/?login_error=disabled')
        if not adapter.login_configured(): return self.redirect('/?login_error=not_configured')
        state=secrets.token_urlsafe(32); c=db()
        try:
            c.execute('DELETE FROM login_state WHERE expire_at<=?',(now(),))
            c.execute('INSERT INTO login_state(state_hash,expire_at,created_at) VALUES(?,?,?)',(hashlib.sha256(state.encode()).hexdigest(),(datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),now())); c.commit()
        finally: c.close()
        self.redirect(adapter.authorization_url(state))
    def finish_feishu_login(self, query):
        state=query.get('state',[''])[0]; code=query.get('code',[''])[0]
        if not state or not code: return self.redirect('/?login_error=invalid_callback')
        c=db()
        try:
            valid=c.execute('SELECT 1 FROM login_state WHERE state_hash=? AND expire_at>?',(hashlib.sha256(state.encode()).hexdigest(),now())).fetchone()
            c.execute('DELETE FROM login_state WHERE state_hash=?',(hashlib.sha256(state.encode()).hexdigest(),)); c.commit()
        finally: c.close()
        if not valid: return self.redirect('/?login_error=expired')
        try:
            token=FeishuAdapter().exchange_login_code(code); open_id=FeishuAdapter().login_open_id(token['access_token'])
        except Exception: return self.redirect('/?login_error=authorization_failed')
        c=db()
        try:
            user=c.execute("SELECT id FROM user WHERE feishu_open_id=?",(open_id,)).fetchone()
            if not user: return self.redirect('/?login_error=unbound')
            session_id=secrets.token_urlsafe(48); expires=(datetime.now(timezone.utc)+timedelta(hours=8)).isoformat()
            c.execute('DELETE FROM login_session WHERE expire_at<=?',(now(),)); c.execute('INSERT INTO login_session(session_id,user_id,expire_at,created_at,last_seen_at) VALUES(?,?,?,?,?)',(session_id,user['id'],expires,now(),now())); audit(c,'login',user['id'],'feishu_login',str(user['id']),after={'channel':'feishu'}); c.commit()
        finally: c.close()
        secure='; Secure' if feishu_redirect_uri().startswith('https://') else ''
        self.redirect('/',f'wmt_session={session_id}; Path=/; Max-Age=28800; HttpOnly; SameSite=Lax{secure}')
    def logout(self):
        session_id=self.cookie('wmt_session'); c=db()
        try:
            if session_id: c.execute('DELETE FROM login_session WHERE session_id=?',(session_id,)); c.commit()
        finally: c.close()
        self.redirect('/', 'wmt_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax')
    def json(self, code, payload):
        data=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or '{}')
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
            disposition=re.search(r'Content-Disposition:\s*form-data;[^\r\n]*name="([^"]+)"(?:;\s*filename="([^"]*)")?',header_text,re.I)
            if not disposition: continue
            name,filename=disposition.group(1),disposition.group(2)
            type_match=re.search(r'Content-Type:\s*([^\r\n;]+)',header_text,re.I)
            if filename is None:
                fields[name]=payload.decode('utf-8',errors='replace')
            else:
                files.append({'name':name,'filename':Path(filename).name or '未命名文件','content_type':type_match.group(1) if type_match else '', 'data':payload})
        return fields,files
    def do_GET(self):
        p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query)
        if p.path=='/auth/feishu/start': return self.start_feishu_login()
        if p.path=='/auth/feishu/callback': return self.finish_feishu_login(q)
        if p.path=='/auth/logout': return self.logout()
        if p.path=='/api/session': return self.json(200,self.session_payload())
        if p.path=='/' or p.path=='/index.html':
            if not feishu_login_enabled() and not self.require_auth(): return
            data=(ROOT/'static'/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if p.path.startswith('/wecom/callback'):
            self.handle_wecom_get(); return
        if not self.require_auth(): return
        c=db()
        if p.path=='/api/bootstrap':
            task_rows=[task_view(c,x) for x in c.execute('SELECT * FROM task ORDER BY due_at')]
            approved=[x for x in task_rows if x['approval_status']=='approved']; today=datetime.now(timezone.utc).date().isoformat()
            out={'dashboard':{'total':len(approved),'active':sum(x['work_status']!='completed' for x in approved),'completed':sum(x['work_status']=='completed' for x in approved),'overdue':sum(x['time_status']=='overdue' for x in approved),'risk':sum(x['risk_status']!='normal' for x in approved),'pending_approval':c.execute("SELECT count(*) FROM approval_record WHERE status='pending'").fetchone()[0],'unreported':c.execute("SELECT count(*) FROM daily_report_status WHERE report_date=? AND status='unreported'",(today,)).fetchone()[0]},'users':[rowdict(x) for x in c.execute('SELECT * FROM user ORDER BY id')],'tree':[rowdict(x) for x in c.execute('SELECT * FROM org_node ORDER BY id')],'tasks':task_rows}
        elif p.path=='/api/users': out=[rowdict(x) for x in c.execute('SELECT * FROM user ORDER BY id')]
        elif p.path=='/api/tree': out=[rowdict(x) for x in c.execute('SELECT * FROM org_node ORDER BY id')]
        elif p.path=='/api/tasks': out=[task_view(c,x) for x in c.execute('SELECT * FROM task ORDER BY due_at')]
        elif p.path=='/api/approvals':
            out=[rowdict(x) for x in c.execute("SELECT a.*,t.title,u.name AS approver_name,p.reported_percent,p.report_text,p.is_blocked,p.block_reason,p.expected_finish FROM approval_record a LEFT JOIN task t ON t.id=a.task_id LEFT JOIN user u ON u.id=a.approver_id LEFT JOIN progress_record p ON a.biz_type='progress_confirm' AND p.id=a.biz_id WHERE a.status=? ORDER BY a.created_at",(q.get('status',['pending'])[0],))]
        elif p.path=='/api/reporting-lines': out=[rowdict(x) for x in c.execute('SELECT * FROM reporting_line ORDER BY user_id,effective_from DESC')]
        elif p.path=='/api/org/departments': out=[rowdict(x) for x in c.execute('SELECT * FROM org_department WHERE status=? ORDER BY name',('active',))]
        elif p.path=='/api/org/positions': out=[rowdict(x) for x in c.execute("SELECT p.*,d.name AS department_name FROM org_position p LEFT JOIN org_department d ON d.id=p.dept_id WHERE p.status='active' ORDER BY d.name,p.name")]
        elif p.path=='/api/org/user-positions': out=[rowdict(x) for x in c.execute("SELECT up.*,u.name AS user_name,p.name AS position_name FROM user_position up JOIN user u ON u.id=up.user_id JOIN org_position p ON p.id=up.position_id WHERE up.valid_to IS NULL OR up.valid_to>? ORDER BY up.is_primary DESC,u.name",(now(),))]
        elif p.path=='/api/delegates':
            out=[]
            for row in c.execute("SELECT d.*,u.name AS user_name,du.name AS delegate_name FROM delegate d JOIN user u ON u.id=d.user_id JOIN user du ON du.id=d.delegate_user_id ORDER BY d.end_at DESC"):
                item=rowdict(row); item['is_active']=item['start_at']<=now()<=item['end_at']; out.append(item)
        elif p.path=='/api/progress': out=[rowdict(x) for x in c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at DESC,id DESC',(q.get('task_id',[0])[0],))]
        elif p.path=='/api/routine/templates': out=[rowdict(x) for x in c.execute("SELECT tt.*,u.name AS owner_name,n.name AS node_name FROM task_template tt LEFT JOIN user u ON u.id=tt.owner_id LEFT JOIN org_node n ON n.id=tt.org_node_id WHERE tt.status='active' ORDER BY tt.id DESC")]
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
        if not self.require_auth(): return
        c=db(); p=urllib.parse.urlparse(self.path)
        try:
            fields,files=self.multipart() if p.path=='/api/task/input/extract' else ({},[])
            b=fields if p.path=='/api/task/input/extract' else self.body()
        except (ValueError,json.JSONDecodeError) as exc:
            c.close(); return self.json(400,{'error':str(exc) or '请求格式不正确'})
        t=now()
        try:
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
            elif p.path=='/api/tree':
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_node(parent_id,node_type,name,boundary,owner_id,effective_at,created_at) VALUES(?,?,?,?,?,?,?)',(b.get('parent_id'),b.get('node_type','domain'),b['name'],b.get('boundary',''),b.get('owner_id'),t,t)).lastrowid; audit(c,'org_node',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/users':
                required=['name']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if b.get('role','member') not in ('manager','pm','member'): return self.json(400,{'error':'invalid role'})
                try:
                    oid=c.execute('INSERT INTO user(name,department,role,wecom_userid) VALUES(?,?,?,?)',(b['name'],b.get('department','科技产品中心'),b.get('role','member'),b.get('wecom_userid') or None)).lastrowid
                except sqlite3.IntegrityError: return self.json(409,{'error':'wecom_userid already exists'})
                audit(c,'user',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/users/') and p.path.endswith('/manage'):
                uid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM user WHERE id=?',(uid,)).fetchone()
                if not old: return self.json(404,{'error':'user not found'})
                action=b.get('action')
                if action=='delete':
                    if c.execute('SELECT 1 FROM task WHERE owner_id=? LIMIT 1',(uid,)).fetchone(): return self.json(409,{'error':'该成员仍有关联任务，暂不能删除'})
                    c.execute('DELETE FROM reporting_line WHERE user_id=? OR direct_leader_id=? OR approver_id=?',(uid,uid,uid)); c.execute('DELETE FROM user_position WHERE user_id=?',(uid,)); c.execute('DELETE FROM delegate WHERE user_id=? OR delegate_user_id=?',(uid,uid)); c.execute('DELETE FROM user WHERE id=?',(uid,)); audit(c,'user',uid,'delete','local',before=rowdict(old)); c.commit(); out={'id':uid,'status':'deleted'}
                elif action=='update':
                    if not b.get('name'): return self.json(400,{'error':'name is required'})
                    if b.get('role','member') not in ('manager','pm','member'): return self.json(400,{'error':'invalid role'})
                    try: c.execute('UPDATE user SET name=?,department=?,role=?,wecom_userid=? WHERE id=?',(b['name'].strip(),b.get('department','').strip(),b.get('role','member'),b.get('wecom_userid') or None,uid))
                    except sqlite3.IntegrityError: return self.json(409,{'error':'wecom_userid already exists'})
                    audit(c,'user',uid,'update','local',before=rowdict(old),after=b); c.commit(); out={'id':uid,'status':'updated'}
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
            elif p.path=='/api/routine/templates':
                required=['org_node_id','title','owner_id','deliverable','recurrence']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                recurrence=b['recurrence'].upper()
                if recurrence not in ('DAILY','WEEKLY'): return self.json(400,{'error':'recurrence must be DAILY or WEEKLY'})
                oid=c.execute('INSERT INTO task_template(org_node_id,title,description,owner_id,deliverable,priority,recurrence,due_offset_days,review_at,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(b['org_node_id'],b['title'],b.get('description',''),b['owner_id'],b['deliverable'],b.get('priority','P1'),recurrence,int(b.get('due_offset_days',0)),b.get('review_at'),t)).lastrowid; audit(c,'task_template',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/routine/generate':
                generated=generate_routines(c,b.get('instance_date')); c.commit(); out={'generated_task_ids':generated,'count':len(generated)}
            elif p.path=='/api/ops/daily-run':
                out=run_daily_jobs(c,b.get('report_date'),b.get('kind','all')); c.commit()
            elif p.path=='/api/task/draft/parse':
                if not b.get('text'): return self.json(400,{'error':'text is required'})
                from datetime import timedelta
                slots,missing=draft_slots(b['text'],b.get('user_id'),c); expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
                oid=c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(b.get('session_id') or f"web:{b.get('user_id','anonymous')}",b.get('user_id'),b.get('source_type','text'),b['text'],json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',expire,t,t)).lastrowid; c.commit(); out={'id':oid,'slots':slots,'missing_slots':missing,'status':'collecting' if missing else 'preview','expire_at':expire}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/complete'):
                did=int(p.path.split('/')[4]); draft=c.execute('SELECT * FROM conversation_draft WHERE id=?',(did,)).fetchone()
                if not draft or draft['expire_at']<t: return self.json(410,{'error':'draft expired or not found'})
                slots=json.loads(draft['slots_json']); slots.update({k:v for k,v in b.items() if v not in (None,'')}); missing=[k for k in ('title','owner_id','due_at') if not slots.get(k)]
                c.execute('UPDATE conversation_draft SET slots_json=?,missing_slots=?,status=?,updated_at=? WHERE id=?',(json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',t,did)); c.commit(); out={'id':did,'slots':slots,'missing_slots':missing,'status':'collecting' if missing else 'preview'}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/confirm'):
                did=int(p.path.split('/')[4]); draft=c.execute('SELECT * FROM conversation_draft WHERE id=?',(did,)).fetchone()
                if not draft or draft['expire_at']<t: return self.json(410,{'error':'draft expired or not found'})
                slots=json.loads(draft['slots_json']); missing=[k for k in ('title','owner_id','due_at') if not slots.get(k)]
                if missing: return self.json(400,{'error':'missing slots','fields':missing})
                approver_id=approver_for(c,slots['owner_id']); oid=c.execute("INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,risk_status,deliverable,source,task_type,approval_status,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(slots.get('org_node_id'),slots['title'],slots.get('description',''),slots['owner_id'],slots['due_at'],slots.get('priority','P1'),'normal',slots.get('deliverable') or '待明确','conversation',slots.get('task_type','PROJECT'),'pending',approver_id,t,t)).lastrowid; aid=create_approval(c,'task_confirm',oid,oid,approver_id); c.execute("UPDATE conversation_draft SET status='generated',updated_at=? WHERE id=?",(t,did)); audit(c,'task',oid,'create_from_draft',str(draft['user_id'] or 'local'),after=slots); c.commit(); out={'task_id':oid,'approval_id':aid,'status':'pending'}
            elif p.path.startswith('/api/task/draft/') and p.path.endswith('/discard'):
                did=int(p.path.split('/')[4])
                if not discard_draft(c,did,'local'): return self.json(404,{'error':'draft is no longer available'})
                c.commit(); out={'id':did,'status':'discarded'}
            elif p.path=='/api/tasks':
                required=['title','owner_id','due_at','deliverable']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if not c.execute('SELECT 1 FROM user WHERE id=?',(b['owner_id'],)).fetchone(): return self.json(400,{'error':'owner_id does not exist'})
                approver_id=approver_for(c,b['owner_id'])
                oid=c.execute("INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,risk_status,deliverable,source,task_type,approval_status,approver_id,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",(b.get('org_node_id'),b['title'],b.get('description',''),b['owner_id'],b['due_at'],b.get('priority','P1'),b.get('risk_status','normal'),b['deliverable'],b.get('source','manual'),b.get('task_type','PROJECT'),'pending',approver_id,t,t)).lastrowid
                aid=create_approval(c,'task_confirm',oid,oid,approver_id); audit(c,'task',oid,'create_pending','local',after=b); c.commit(); out={'id':oid,'approval_id':aid,'approval_status':'pending'}
            elif p.path=='/api/ai/draft': out=ai_draft(b.get('text',''))
            elif p.path.startswith('/api/approvals/') and p.path.endswith('/confirm'):
                aid=int(p.path.split('/')[3]); approval=c.execute('SELECT * FROM approval_record WHERE id=?',(aid,)).fetchone()
                if not approval or approval['status']!='pending': return self.json(404,{'error':'pending approval not found'})
                result=b.get('result'); operator=self.actor_id(b.get('operator_id'))
                if result not in ('approved','rejected'): return self.json(400,{'error':'result must be approved or rejected'})
                if approval['approver_id'] and operator and int(operator)!=approval['approver_id']: return self.json(403,{'error':'only assigned approver may confirm'})
                c.execute('UPDATE approval_record SET status=?,result_reason=?,operated_by=?,operated_at=? WHERE id=?',(result,b.get('reason',''),operator,t,aid))
                if approval['biz_type']=='task_confirm': c.execute("UPDATE task SET approval_status=?,work_status=?,confirmed_by=?,confirmed_at=?,confirm_source='manual',updated_at=? WHERE id=?",('approved' if result=='approved' else 'rejected','assigned' if result=='approved' else 'draft',operator,t,t,approval['task_id']))
                elif approval['biz_type']=='progress_confirm' and result=='approved':
                    record=c.execute('SELECT * FROM progress_record WHERE id=?',(approval['biz_id'],)).fetchone(); c.execute("UPDATE progress_record SET status='approved',effective_percent=?,confirmed_by=?,confirmed_at=? WHERE id=?",(record['reported_percent'],operator,t,record['id'])); c.execute('UPDATE task SET effective_percent=?,progress_percent=?,risk_status=?,expected_finish=?,work_status=?,updated_at=? WHERE id=?',(record['reported_percent'],record['reported_percent'],'blocked' if record['is_blocked'] else 'normal',record['expected_finish'],'completed' if record['reported_percent']==100 else 'in_progress',t,approval['task_id']))
                audit(c,'approval',aid,result,str(operator or 'local'),after=b); c.commit(); out={'id':aid,'status':result}
            elif p.path.startswith('/api/tasks/') and p.path.endswith('/progress'):
                tid=int(p.path.split('/')[3]); task=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone()
                if not task or task['approval_status']!='approved': return self.json(400,{'error':'only approved tasks may receive progress'})
                percent=b.get('percent');
                if not isinstance(percent,int) or percent<0 or percent>100 or not b.get('comment'): return self.json(400,{'error':'percent (0-100 integer) and comment are required'})
                reporter=self.actor_id(b.get('reporter_id',task['owner_id']))
                if int(reporter)!=int(task['owner_id']): return self.json(403,{'error':'only task owner may report progress'})
                rid,aid,_=submit_progress(c,tid,reporter,percent,b['comment'],b.get('blocked',False),b.get('block_reason',''),b.get('expected_finish'),b.get('source','web')); c.commit(); out={'id':rid,'approval_id':aid,'status':'pending'}
            elif p.path.startswith('/api/tasks/'):
                tid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone();
                if not old: return self.json(404,{'error':'task not found'})
                if p.path.endswith('/feedback'):
                    if not b.get('content'): return self.json(400,{'error':'content is required'})
                    feedback_user=self.actor_id(b.get('user_id'))
                    if feedback_user and int(feedback_user)!=int(old['owner_id']): return self.json(403,{'error':'only the task owner may submit feedback'})
                    c.execute('INSERT INTO feedback(task_id,user_id,content,parsed_due_at,progress,risk,created_at) VALUES(?,?,?,?,?,?,?)',(tid,feedback_user,b.get('content',''),parse_due(b.get('content','')),b.get('progress',''),b.get('risk',''),t)); c.execute("UPDATE task SET risk_status=?,updated_at=? WHERE id=?",('blocked' if b.get('risk') else old['risk_status'],t,tid)); action='feedback'
                elif p.path.endswith('/accept'):
                    result=b.get('result');
                    if result not in ('passed','rejected'): return self.json(400,{'error':'result must be passed or rejected'})
                    if old['work_status']!='awaiting_acceptance': return self.json(400,{'error':'task is not awaiting acceptance'})
                    acceptor_id=self.actor_id(b.get('acceptor_id'))
                    approver_id=old['approver_id'] or approver_for(c,old['owner_id'])
                    if not acceptor_id or int(acceptor_id)!=int(approver_id): return self.json(403,{'error':'only the task owner\'s approver may accept'})
                    c.execute('INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,?,?,?)',(tid,b.get('acceptor_id'),result,b.get('reason',''),t)); c.execute('UPDATE task SET work_status=?,updated_at=? WHERE id=?',('completed' if result=='passed' else 'in_progress',t,tid)); action='accept' if result=='passed' else 'reject'
                else:
                    allowed={'work_status','risk_status','due_at','priority','owner_id','deliverable'}; changes={k:v for k,v in b.items() if k in allowed};
                    if not changes: return self.json(400,{'error':'no editable fields'})
                    if 'work_status' in changes:
                        operator_id=self.actor_id(b.get('operator_id'))
                        if not operator_id or int(operator_id)!=int(old['owner_id']): return self.json(403,{'error':'only the task owner may update task status'})
                        transition=(old['work_status'],changes['work_status'])
                        if transition not in {('assigned','in_progress'),('in_progress','awaiting_acceptance')}:
                            return self.json(400,{'error':'invalid task status transition'})
                    changes['updated_at']=t; c.execute('UPDATE task SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),tid)); action='update'
                audit(c,'task',tid,action,'local',before=rowdict(old),after=b); c.commit(); out={'id':tid,'status':'ok'}
            elif p.path=='/api/notify': out=WeComAdapter().send(b.get('userid',''),b.get('text',''))
            else: return self.json(404,{'error':'not found'})
            self.json(200,out)
        finally: c.close()
    def handle_feishu_post(self):
        try:
            payload=self.body(); adapter=FeishuAdapter()
            if not adapter.configured(): return self.json(503,{'error':'Feishu credentials are required'})
            if payload.get('type')=='url_verification':
                if payload.get('token')!=adapter.verify_token: return self.json(403,{'error':'invalid verification token'})
                return self.json(200,{'challenge':payload.get('challenge','')})
            header=payload.get('header',{}); event=payload.get('event',{}); event_id=header.get('event_id') or event.get('message',{}).get('message_id')
            if not event_id: return self.json(400,{'error':'event id is required'})
            sender=(event.get('sender') or {}).get('sender_id',{}).get('open_id'); message=event.get('message') or {}; content=message.get('content','')
            c=db(); user=c.execute('SELECT id FROM user WHERE feishu_open_id=?',(sender,)).fetchone() if sender else None
            try: c.execute('INSERT INTO im_message_log(channel,external_msg_id,user_id,payload_json,created_at) VALUES(?,?,?,?,?)',('feishu',event_id,user[0] if user else None,json.dumps(payload,ensure_ascii=False),now()))
            except sqlite3.IntegrityError: c.close(); return self.json(200,{'code':0})
            text='';
            if message.get('message_type')=='text':
                try: text=json.loads(content).get('text','')
                except Exception: text=content
            if text:
                slots,missing=draft_slots(text,user[0] if user else None,c)
                expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
                c.execute('INSERT INTO conversation_draft(session_id,user_id,source_type,raw_text,slots_json,missing_slots,status,expire_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?)',(f'feishu:{sender or "anonymous"}',user[0] if user else None,'feishu',text,json.dumps(slots,ensure_ascii=False),json.dumps(missing,ensure_ascii=False),'collecting' if missing else 'preview',expire,now(),now()))
                domain_hint=('。建议责任域：'+slots['domain_name']) if slots.get('org_node_id') else '。责任域暂未自动确定，请在网页确认时选择'
                reply='已识别任务草稿：'+slots['title']+domain_hint+('。请补充：'+friendly_slot_labels(missing) if missing else '。请在网页确认生成。')
                if sender: adapter.send_text(sender,reply)
            c.commit(); c.close(); self.json(200,{'code':0})
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
    initialize(); port=int(os.getenv('PORT','8080')); print(f'Work Management Tree listening on http://127.0.0.1:{port}'); ThreadingHTTPServer(('0.0.0.0',port),Handler).serve_forever()
