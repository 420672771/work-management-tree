import base64, hashlib, json, os, re, sqlite3, struct, urllib.request, urllib.parse
from base64 import b64decode
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).parent
DB_PATH = Path(os.getenv("WMT_DB", ROOT / "data" / "worktree.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

SCHEMA = """
CREATE TABLE IF NOT EXISTS org_node (id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INTEGER, node_type TEXT NOT NULL, name TEXT NOT NULL, boundary TEXT DEFAULT '', owner_id INTEGER, effective_at TEXT, inactive_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS user (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, department TEXT DEFAULT '', role TEXT DEFAULT 'owner', wecom_userid TEXT UNIQUE);
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
"""

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c = sqlite3.connect(DB_PATH, timeout=5); c.row_factory = sqlite3.Row; c.executescript(SCHEMA)
    for statement in ("ALTER TABLE user ADD COLUMN feishu_open_id TEXT", "ALTER TABLE task ADD COLUMN task_type TEXT NOT NULL DEFAULT 'PROJECT'", "ALTER TABLE task ADD COLUMN parent_template_id INTEGER", "ALTER TABLE task ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'approved'", "ALTER TABLE task ADD COLUMN approver_id INTEGER", "ALTER TABLE task ADD COLUMN progress_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN effective_percent INTEGER NOT NULL DEFAULT 0", "ALTER TABLE task ADD COLUMN expected_finish TEXT", "ALTER TABLE task ADD COLUMN confirmed_by INTEGER", "ALTER TABLE task ADD COLUMN confirmed_at TEXT", "ALTER TABLE task ADD COLUMN confirm_source TEXT"):
        try: c.execute(statement)
        except sqlite3.OperationalError: pass
    return c
def rowdict(row): return dict(row) if row else None
def audit(c, typ, oid, action, actor, before=None, after=None):
    c.execute("INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)", (typ, oid, action, actor, json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False) if after else None, now()))
def approver_for(c, user_id):
    line=c.execute("SELECT approver_id,direct_leader_id FROM reporting_line WHERE user_id=? AND (effective_to IS NULL OR effective_to>?) ORDER BY effective_from DESC LIMIT 1",(user_id,now())).fetchone()
    if line and (line['approver_id'] or line['direct_leader_id']): return line['approver_id'] or line['direct_leader_id']
    manager=c.execute("SELECT id FROM user WHERE role='manager' ORDER BY id LIMIT 1").fetchone()
    return manager[0] if manager else None
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
def run_daily_jobs(c, report_date=None, kind='all'):
    day=report_date or datetime.now(timezone.utc).date().isoformat(); out={'date':day,'morning':0,'evening':0,'redboard':0,'approval_reminders':0}
    if kind in ('all','morning'):
        targets=daily_targets(c,day); out['morning']=len(targets)
        for target in targets: c.execute("INSERT OR REPLACE INTO notify_log(task_id,user_id,kind,notify_day,status,response_json,created_at) VALUES(?,?,?,?,?,?,?)",(target['tasks'][0]['id'],target['user_id'],'morning_digest',day,'dry-run',json.dumps({'count':len(target['tasks'])},ensure_ascii=False),now()))
    if kind in ('all','evening','close'):
        for target in daily_targets(c,day):
            reported=c.execute("SELECT 1 FROM progress_record WHERE reported_by=? AND substr(reported_at,1,10)=? LIMIT 1",(target['user_id'],day)).fetchone()
            status='reported' if reported else 'unreported'; c.execute("INSERT OR REPLACE INTO daily_report_status(report_date,user_id,status,task_count,created_at) VALUES(?,?,?,?,?)",(day,target['user_id'],status,len(target['tasks']),now()))
            if kind!='close': out['evening']+=1
            if status=='unreported': out['redboard']+=1
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
        pending=c.execute("SELECT count(*) FROM approval_record WHERE status='pending'").fetchone()[0]; out['approval_reminders']=pending; out['auto_confirmed_progress']=auto
    c.execute("INSERT OR REPLACE INTO job_run_log(job_name,run_key,result_json,created_at) VALUES(?,?,?,?)",('daily_'+kind,day,json.dumps(out,ensure_ascii=False),now()))
    return out
def draft_slots(text, owner_id=None):
    draft=ai_draft(text); slots={'title':draft['title'],'due_at':draft['due_at'],'priority':draft['priority'],'deliverable':draft['deliverable'],'owner_id':owner_id,'org_node_id':None,'task_type':'PROJECT'}
    missing=[k for k in ('owner_id','due_at') if not slots.get(k)]
    return slots,missing
def seed(c):
    if c.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0:
        c.execute("INSERT INTO user(name,department,role) VALUES('科技产品中心负责人','科技产品中心','manager')")
        c.execute("INSERT INTO user(name,department,role) VALUES('部门管理PM','科技产品中心','pm')")
        c.execute("INSERT INTO user(name,department,role) VALUES('待分配责任人','科技产品中心','owner')")
    if c.execute("SELECT COUNT(*) FROM org_node").fetchone()[0] == 0:
        t=now(); c.execute("INSERT INTO org_node(node_type,name,boundary,effective_at,created_at) VALUES('department','科技产品中心','科技产品中心全部责任域',?,?)",(t,t))
        root=c.execute("INSERT INTO org_node(parent_id,node_type,name,boundary,effective_at,created_at) VALUES(?,?,?,?,?,?)",(c.execute("SELECT id FROM org_node ORDER BY id DESC LIMIT 1").fetchone()[0],'domain','产品与项目管理','负责产品规划、项目交付和跨部门推进',t,t)).lastrowid
    manager=c.execute("SELECT id FROM user WHERE role='manager' ORDER BY id LIMIT 1").fetchone()
    if manager:
        for row in c.execute("SELECT id FROM user WHERE id<>?",(manager[0],)):
            if not c.execute("SELECT 1 FROM reporting_line WHERE user_id=? AND effective_to IS NULL",(row[0],)).fetchone(): c.execute("INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)",(row[0],manager[0],manager[0],now()))

def initialize():
    c=db()
    try:
        seed(c); c.commit()
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
    m=re.search(r'(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})',text)
    if m: return f'{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}T17:00:00+08:00'
    if '明天' in text:
        from datetime import timedelta
        return (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()
    return None

def ai_draft(text):
    """Creates a non-binding task draft. Uses an OpenAI-compatible endpoint when configured."""
    api_base, api_key, model = os.getenv('AI_API_BASE'), os.getenv('AI_API_KEY'), os.getenv('AI_MODEL')
    if api_base and api_key and model:
        prompt = ('将以下会议事项提取为 JSON，只输出 JSON。字段为 title,due_at,priority,deliverable,owner_id,confidence。'
                  'owner_id 必须为 null；无法确定的截止时间为 null；不得编造验收标准。事项：' + text)
        payload=json.dumps({'model':model,'messages':[{'role':'user','content':prompt}],'temperature':0.1}).encode()
        req=urllib.request.Request(api_base.rstrip('/')+'/chat/completions',payload,{'Authorization':'Bearer '+api_key,'Content-Type':'application/json'})
        try:
            with urllib.request.urlopen(req,timeout=20) as f:
                content=json.load(f)['choices'][0]['message']['content']
            draft=json.loads(content.removeprefix('```json').removesuffix('```').strip())
            return {'title':draft.get('title') or text.strip()[:120], 'due_at':draft.get('due_at'), 'priority':draft.get('priority','P1'), 'deliverable':draft.get('deliverable') or '待补充验收标准', 'owner_id':None, 'confidence':draft.get('confidence',0.5), 'requires_human_confirmation':True, 'mode':'model'}
        except Exception:
            # Safe fallback: a model outage must never block a human-created task.
            pass
    due=parse_due(text)
    return {'title': text.strip()[:120], 'due_at': due, 'priority':'P1', 'deliverable':'待补充验收标准', 'owner_id':None, 'confidence':0.35 if not due else 0.65, 'requires_human_confirmation':True, 'mode':'rule'}

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
    def authorized(self):
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
        if self.authorized(): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Work Management Tree"'); self.send_header('Content-Length','0'); self.end_headers(); return False
    def json(self, code, payload):
        data=json.dumps(payload,ensure_ascii=False).encode(); self.send_response(code); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data)
    def body(self):
        n=int(self.headers.get('Content-Length','0')); return json.loads(self.rfile.read(n) or '{}')
    def do_GET(self):
        if not self.path.startswith('/wecom/callback') and not self.require_auth(): return
        if self.path=='/' or self.path=='/index.html':
            data=(ROOT/'static'/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
        if self.path.startswith('/wecom/callback'):
            self.handle_wecom_get(); return
        c=db(); p=urllib.parse.urlparse(self.path); q=urllib.parse.parse_qs(p.query)
        if p.path=='/api/users': out=[rowdict(x) for x in c.execute('SELECT * FROM user ORDER BY id')]
        elif p.path=='/api/tree': out=[rowdict(x) for x in c.execute('SELECT * FROM org_node ORDER BY id')]
        elif p.path=='/api/tasks': out=[task_view(c,x) for x in c.execute('SELECT * FROM task ORDER BY due_at')]
        elif p.path=='/api/approvals':
            out=[rowdict(x) for x in c.execute("SELECT a.*,t.title,u.name AS approver_name,p.reported_percent,p.report_text,p.is_blocked,p.block_reason,p.expected_finish FROM approval_record a LEFT JOIN task t ON t.id=a.task_id LEFT JOIN user u ON u.id=a.approver_id LEFT JOIN progress_record p ON a.biz_type='progress_confirm' AND p.id=a.biz_id WHERE a.status=? ORDER BY a.created_at",(q.get('status',['pending'])[0],))]
        elif p.path=='/api/reporting-lines': out=[rowdict(x) for x in c.execute('SELECT * FROM reporting_line ORDER BY user_id,effective_from DESC')]
        elif p.path=='/api/org/departments': out=[rowdict(x) for x in c.execute('SELECT * FROM org_department WHERE status=? ORDER BY name',('active',))]
        elif p.path=='/api/progress': out=[rowdict(x) for x in c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at DESC,id DESC',(q.get('task_id',[0])[0],))]
        elif p.path=='/api/routine/templates': out=[rowdict(x) for x in c.execute("SELECT tt.*,u.name AS owner_name,n.name AS node_name FROM task_template tt LEFT JOIN user u ON u.id=tt.owner_id LEFT JOIN org_node n ON n.id=tt.org_node_id WHERE tt.status='active' ORDER BY tt.id DESC")]
        elif p.path=='/api/ops/daily-preview': out={'date':q.get('date',[None])[0] or datetime.now(timezone.utc).date().isoformat(),'targets':daily_targets(c,q.get('date',[None])[0])}
        elif p.path=='/api/dashboard':
            tasks=[task_view(c,x) for x in c.execute("SELECT * FROM task WHERE approval_status='approved'")]; today=datetime.now(timezone.utc).date().isoformat(); out={'total':len(tasks),'active':sum(x['work_status']!='completed' for x in tasks),'completed':sum(x['work_status']=='completed' for x in tasks),'overdue':sum(x['time_status']=='overdue' for x in tasks),'risk':sum(x['risk_status']!='normal' for x in tasks),'pending_approval':c.execute("SELECT count(*) FROM approval_record WHERE status='pending'").fetchone()[0],'unreported':c.execute("SELECT count(*) FROM daily_report_status WHERE report_date=? AND status='unreported'",(today,)).fetchone()[0]}
        else: self.json(404,{'error':'not found'}); c.close(); return
        self.json(200,out); c.close()
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
        c=db(); p=urllib.parse.urlparse(self.path); b=self.body(); t=now()
        try:
            if p.path=='/api/tree':
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_node(parent_id,node_type,name,boundary,owner_id,effective_at,created_at) VALUES(?,?,?,?,?,?,?)',(b.get('parent_id'),b.get('node_type','domain'),b['name'],b.get('boundary',''),b.get('owner_id'),t,t)).lastrowid; audit(c,'org_node',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/users':
                required=['name','wecom_userid']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                try:
                    oid=c.execute('INSERT INTO user(name,department,role,wecom_userid) VALUES(?,?,?,?)',(b['name'],b.get('department','科技产品中心'),b.get('role','owner'),b['wecom_userid'])).lastrowid
                except sqlite3.IntegrityError: return self.json(409,{'error':'wecom_userid already exists'})
                audit(c,'user',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path.startswith('/api/users/') and p.path.endswith('/feishu-open-id'):
                uid=int(p.path.split('/')[3])
                if not b.get('feishu_open_id'): return self.json(400,{'error':'feishu_open_id is required'})
                c.execute('UPDATE user SET feishu_open_id=? WHERE id=?',(b['feishu_open_id'],uid)); audit(c,'user',uid,'bind_feishu','local',after={'feishu_open_id':b['feishu_open_id']}); c.commit(); out={'id':uid}
            elif p.path=='/api/reporting-lines':
                required=['user_id','direct_leader_id']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if int(b['user_id'])==int(b['direct_leader_id']): return self.json(400,{'error':'a user cannot report to self'})
                c.execute('UPDATE reporting_line SET effective_to=? WHERE user_id=? AND effective_to IS NULL',(t,b['user_id']))
                oid=c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,dotted_leader_id,effective_from) VALUES(?,?,?,?,?)',(b['user_id'],b['direct_leader_id'],b.get('approver_id') or b['direct_leader_id'],b.get('dotted_leader_id'),t)).lastrowid; audit(c,'reporting_line',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/org/departments':
                if not b.get('name'): return self.json(400,{'error':'name is required'})
                oid=c.execute('INSERT INTO org_department(name,parent_id,owner_user_id,status,created_at) VALUES(?,?,?,?,?)',(b['name'],b.get('parent_id'),b.get('owner_user_id'),'active',t)).lastrowid; audit(c,'org_department',oid,'create','local',after=b); c.commit(); out={'id':oid}
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
                slots,missing=draft_slots(b['text'],b.get('user_id')); expire=(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat()
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
                result=b.get('result'); operator=b.get('operator_id')
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
                reporter=b.get('reporter_id',task['owner_id']); rid=c.execute("INSERT INTO progress_record(task_id,reported_percent,report_text,is_blocked,block_reason,expected_finish,reported_by,reported_at,source) VALUES(?,?,?,?,?,?,?,?,?)",(tid,percent,b['comment'],1 if b.get('blocked') else 0,b.get('block_reason',''),b.get('expected_finish'),reporter,t,b.get('source','web'))).lastrowid
                aid=create_approval(c,'progress_confirm',rid,tid,task['approver_id']); c.execute("UPDATE task SET work_status='in_progress',updated_at=? WHERE id=?",(t,tid)); audit(c,'progress',rid,'report_pending',str(reporter),after=b); c.commit(); out={'id':rid,'approval_id':aid,'status':'pending'}
            elif p.path.startswith('/api/tasks/'):
                tid=int(p.path.split('/')[3]); old=c.execute('SELECT * FROM task WHERE id=?',(tid,)).fetchone();
                if not old: return self.json(404,{'error':'task not found'})
                if p.path.endswith('/feedback'):
                    if not b.get('content'): return self.json(400,{'error':'content is required'})
                    c.execute('INSERT INTO feedback(task_id,user_id,content,parsed_due_at,progress,risk,created_at) VALUES(?,?,?,?,?,?,?)',(tid,b.get('user_id'),b.get('content',''),parse_due(b.get('content','')),b.get('progress',''),b.get('risk',''),t)); c.execute("UPDATE task SET risk_status=?,updated_at=? WHERE id=?",('blocked' if b.get('risk') else old['risk_status'],t,tid)); action='feedback'
                elif p.path.endswith('/accept'):
                    result=b.get('result');
                    if result not in ('passed','rejected'): return self.json(400,{'error':'result must be passed or rejected'})
                    c.execute('INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,?,?,?)',(tid,b.get('acceptor_id'),result,b.get('reason',''),t)); c.execute('UPDATE task SET work_status=?,updated_at=? WHERE id=?',('completed' if result=='passed' else 'in_progress',t,tid)); action='accept' if result=='passed' else 'reject'
                else:
                    allowed={'work_status','risk_status','due_at','priority','owner_id','deliverable'}; changes={k:v for k,v in b.items() if k in allowed};
                    if not changes: return self.json(400,{'error':'no editable fields'})
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
                slots,missing=draft_slots(text,user[0] if user else None)
                reply='已识别任务草稿：'+slots['title']+('。请补充：'+ '、'.join(missing) if missing else '。请在网页确认生成。')
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
