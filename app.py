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
"""

def now(): return datetime.now(timezone.utc).isoformat()
def db():
    c = sqlite3.connect(DB_PATH, timeout=5); c.row_factory = sqlite3.Row; c.executescript(SCHEMA); return c
def rowdict(row): return dict(row) if row else None
def audit(c, typ, oid, action, actor, before=None, after=None):
    c.execute("INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)", (typ, oid, action, actor, json.dumps(before, ensure_ascii=False) if before else None, json.dumps(after, ensure_ascii=False) if after else None, now()))
def seed(c):
    if c.execute("SELECT COUNT(*) FROM user").fetchone()[0] == 0:
        c.execute("INSERT INTO user(name,department,role) VALUES('科技产品中心负责人','科技产品中心','manager')")
        c.execute("INSERT INTO user(name,department,role) VALUES('部门管理PM','科技产品中心','pm')")
        c.execute("INSERT INTO user(name,department,role) VALUES('待分配责任人','科技产品中心','owner')")
    if c.execute("SELECT COUNT(*) FROM org_node").fetchone()[0] == 0:
        t=now(); c.execute("INSERT INTO org_node(node_type,name,boundary,effective_at,created_at) VALUES('department','科技产品中心','科技产品中心全部责任域',?,?)",(t,t))
        root=c.execute("INSERT INTO org_node(parent_id,node_type,name,boundary,effective_at,created_at) VALUES(?,?,?,?,?,?)",(c.execute("SELECT id FROM org_node ORDER BY id DESC LIMIT 1").fetchone()[0],'domain','产品与项目管理','负责产品规划、项目交付和跨部门推进',t,t)).lastrowid

def initialize():
    c=db()
    try:
        seed(c); c.commit()
    finally:
        c.close()

def task_view(c, r):
    d=rowdict(r); owner=c.execute('SELECT name FROM user WHERE id=?',(d['owner_id'],)).fetchone(); d['owner_name']=owner[0] if owner else '未知责任人'
    due=datetime.fromisoformat(d['due_at'].replace('Z','+00:00')); current=datetime.now(timezone.utc)
    d['time_status']='overdue' if due < current and d['work_status'] not in ('completed','cancelled') else ('due_soon' if (due-current).total_seconds()<86400 and d['work_status'] not in ('completed','cancelled') else 'normal')
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
        elif p.path=='/api/dashboard':
            tasks=[task_view(c,x) for x in c.execute('SELECT * FROM task')]; out={'total':len(tasks),'completed':sum(x['work_status']=='completed' for x in tasks),'overdue':sum(x['time_status']=='overdue' for x in tasks),'risk':sum(x['risk_status']!='normal' for x in tasks)}
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
            elif p.path=='/api/tasks':
                required=['title','owner_id','due_at','deliverable']; missing=[x for x in required if not b.get(x)]
                if missing: return self.json(400,{'error':'missing fields','fields':missing})
                if not c.execute('SELECT 1 FROM user WHERE id=?',(b['owner_id'],)).fetchone(): return self.json(400,{'error':'owner_id does not exist'})
                oid=c.execute('INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,risk_status,deliverable,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(b.get('org_node_id'),b['title'],b.get('description',''),b['owner_id'],b['due_at'],b.get('priority','P1'),b.get('risk_status','normal'),b['deliverable'],b.get('source','manual'),t,t)).lastrowid; audit(c,'task',oid,'create','local',after=b); c.commit(); out={'id':oid}
            elif p.path=='/api/ai/draft': out=ai_draft(b.get('text',''))
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
