"""Maintain responsibility definitions and their historical reference labels."""
import json
import re
from datetime import datetime, timezone

SCHEMA = """CREATE TABLE IF NOT EXISTS system_setting (
 setting_key TEXT PRIMARY KEY, setting_value TEXT NOT NULL
);"""


class Referenced(ValueError):
    pass


def stamp():
    return datetime.now(timezone.utc).isoformat()


def manager(c, user_id):
    user=c.execute('SELECT role FROM user WHERE id=?',(user_id,)).fetchone()
    if not user or user['role'] not in ('manager','pm'):
        raise PermissionError('仅部门负责人和管理 PM 可维护责任域')


def lock(c):
    if not c.in_transaction:c.execute('BEGIN IMMEDIATE')


def install_guards(c):
    # Enforce links at the write itself, including a task request that validated
    # its domain just before another connection deleted that unused definition.
    for table in ('task','task_template'):
        for event in ('INSERT','UPDATE OF org_node_id'):
            suffix='insert' if event=='INSERT' else 'update'
            c.execute(f'''CREATE TRIGGER IF NOT EXISTS {table}_domain_{suffix}
                BEFORE {event} ON {table} WHEN NEW.org_node_id IS NOT NULL
                AND NOT EXISTS(SELECT 1 FROM org_node WHERE id=NEW.org_node_id)
                BEGIN SELECT RAISE(ABORT,'责任域不存在或已删除'); END''')
    for event in ('INSERT','UPDATE OF parent_id'):
        suffix='insert' if event=='INSERT' else 'update'
        c.execute(f'''CREATE TRIGGER IF NOT EXISTS org_node_parent_{suffix}
            BEFORE {event} ON org_node WHEN NEW.parent_id IS NOT NULL
            AND NOT EXISTS(SELECT 1 FROM org_node WHERE id=NEW.parent_id)
            BEGIN SELECT RAISE(ABORT,'上级节点不存在或已删除'); END''')
    c.execute('''CREATE TRIGGER IF NOT EXISTS org_node_delete_referenced BEFORE DELETE ON org_node
        WHEN EXISTS(SELECT 1 FROM task WHERE org_node_id=OLD.id)
          OR EXISTS(SELECT 1 FROM task_template WHERE org_node_id=OLD.id)
          OR EXISTS(SELECT 1 FROM org_node WHERE parent_id=OLD.id)
        BEGIN SELECT RAISE(ABORT,'该责任域已被引用，不能删除'); END''')


def audit(c, node_id, action, actor, before=None, after=None):
    c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',
              ('org_node',node_id,action,str(actor),json.dumps(before,ensure_ascii=False) if before else None,
               json.dumps(after,ensure_ascii=False) if after else None,stamp()))


def node(c, node_id):
    row=c.execute("SELECT * FROM org_node WHERE id=? AND node_type IN ('domain','subdomain')",(node_id,)).fetchone()
    if not row:raise LookupError('责任域不存在')
    return dict(row)


def referenced_ids(value):
    result=set();stack=[value]
    while stack:
        item=stack.pop()
        if isinstance(item,dict):
            for key,value in item.items():
                if key in ('org_node_id','domain_id') and (type(value) is int or (isinstance(value,str) and value.isdigit())):
                    result.add(int(value))
                if isinstance(value,(dict,list)):stack.append(value)
        elif isinstance(item,list):stack.extend(item)
    return result


def usages(c):
    result={}
    def add(key,kind,count=1):
        if key is not None:
            counts=result.setdefault(int(key),{});counts[kind]=counts.get(kind,0)+count
    for table,column,label in (('task','org_node_id','任务'),('task_template','org_node_id','例行模板'),('org_node','parent_id','下级节点')):
        for row in c.execute(f'SELECT {column},count(*) FROM {table} WHERE {column} IS NOT NULL GROUP BY {column}'):
            add(row[0],label,row[1])
    for row in c.execute('SELECT slots_json FROM conversation_draft'):
        for key in referenced_ids(json.loads(row[0])):add(key,'任务草稿')
    for table,label in (('weekly_report','历史周报'),('monthly_report','历史月报')):
        for row in c.execute(f'SELECT snapshot_json FROM {table}'):
            for key in referenced_ids(json.loads(row[0])):add(key,label)
    # Deleted tasks and old task assignments still have an auditable reference.
    for row in c.execute("SELECT before_json,after_json FROM audit_log WHERE object_type IN ('task','task_template','conversation_draft','weekly_report','monthly_report')"):
        ids=set()
        for value in row:
            if value:ids.update(referenced_ids(json.loads(value)))
        for key in ids:add(key,'历史引用记录')
    for row in c.execute("SELECT action_json FROM bot_pending_action WHERE status='pending' AND expire_at>?",(stamp(),)):
        for key in referenced_ids(json.loads(row[0])):add(key,'待确认操作')
    return result


def listing(c):
    references=usages(c);items=[]
    for row in c.execute("""SELECT n.*,u.name AS owner_name,p.name AS parent_name FROM org_node n
         LEFT JOIN user u ON u.id=n.owner_id LEFT JOIN org_node p ON p.id=n.parent_id
         WHERE n.node_type IN ('domain','subdomain') ORDER BY n.id"""):
        item=dict(row);item['references']=references.get(item['id'],{})
        item['can_delete']=not bool(item['references']);items.append(item)
    parents=[dict(row) for row in c.execute("SELECT id,name,node_type FROM org_node WHERE inactive_at IS NULL AND node_type IN ('department','domain','subdomain') ORDER BY id")]
    return {'items':items,'parents':parents}


def fields(c, payload, previous=None):
    allowed={'name','boundary','owner_id','domain_code','operating_line'}
    if previous is None:allowed.add('parent_id')
    if not isinstance(payload,dict) or set(payload)-allowed:raise ValueError('责任域包含不支持的修改字段')
    values={**(previous or {}),**payload}
    name=values.get('name')
    if not isinstance(name,str) or not 1<=len(name.strip())<=200:raise ValueError('责任域名称须为 1—200 个字符')
    name=name.strip()
    for row in c.execute("SELECT id,name FROM org_node WHERE node_type IN ('domain','subdomain')"):
        if row['id']!=(previous or {}).get('id') and row['name'].casefold()==name.casefold():
            raise ValueError('责任域名称已存在，请使用其他名称')
    result={'name':name}
    for key,limit in (('boundary',5000),('domain_code',32),('operating_line',100)):
        value=values.get(key) or ''
        if not isinstance(value,str) or len(value)>limit:raise ValueError('责任域字段格式或长度无效')
        result[key]=value.strip()
    if result['domain_code']:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',result['domain_code']):raise ValueError('责任域编码只支持字母、数字、下划线、点和连字符')
        duplicate=c.execute('SELECT id FROM org_node WHERE lower(domain_code)=lower(?) AND id<>?',
                            (result['domain_code'],(previous or {}).get('id',0))).fetchone()
        if duplicate:raise ValueError('责任域编码已被使用')
    owner=values.get('owner_id')
    if owner not in (None,'') and (type(owner) is not int or not c.execute('SELECT 1 FROM user WHERE id=?',(owner,)).fetchone()):
        raise ValueError('请选择有效的责任域负责人')
    result['owner_id']=owner or None
    if previous is None:
        parent=payload.get('parent_id')
        if parent is None:
            roots=c.execute("SELECT id FROM org_node WHERE node_type='department' AND inactive_at IS NULL").fetchall()
            if len(roots)!=1:raise ValueError('请选择上级节点')
            parent=roots[0]['id']
        if type(parent) is not int:raise ValueError('上级节点无效')
        row=c.execute("SELECT node_type FROM org_node WHERE id=? AND inactive_at IS NULL AND node_type IN ('department','domain','subdomain')",(parent,)).fetchone()
        if not row:raise ValueError('上级节点不存在或已停用')
        result.update(parent_id=parent,node_type='domain' if row['node_type']=='department' else 'subdomain')
    return result


def create(c, payload, user_id):
    manager(c,user_id);lock(c);values=fields(c,payload);values.update(effective_at=stamp(),created_at=stamp())
    node_id=c.execute('INSERT INTO org_node('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values())).lastrowid
    audit(c,node_id,'create',user_id,after=values)
    return {'id':node_id,'status':'created'}


def rename_snapshot(snapshot, node_id, new_name):
    changed=False
    for task in snapshot.get('tasks',[]):
        if task.get('domain_id')==node_id or (task.get('domain_id') is None and task.get('org_node_id')==node_id):
            if task.get('domain_name')!=new_name:task['domain_name']=new_name;changed=True
    return changed


def rename_outline(outline, snapshot, node_id, old_name, new_name):
    tasks={task['id']:task for task in snapshot.get('tasks',[])}
    known_domain=any(task.get('domain_id')==node_id for task in tasks.values())
    def visit(item):
        linked={item['task_id']} if 'task_id' in item else set();changed=False
        for child in item.get('children',[]):
            ids,updated=visit(child);linked.update(ids);changed=changed or updated
        if item.get('kind')=='group' and item.get('text')==old_name:
            domains={tasks[key].get('domain_id') for key in linked if key in tasks}
            matching=item.get('domain_id')==node_id or (item.get('domain_id') is None and (node_id in domains or (not domains and known_domain)))
            if matching:item['text']=new_name;changed=True
        return linked,changed
    return visit(outline)[1]


def sync_history(c, before, after, user_id):
    counts={'drafts':0,'weekly_reports':0,'monthly_reports':0}
    if before['name']==after['name']:return counts
    for row in c.execute('SELECT id,slots_json FROM conversation_draft').fetchall():
        slots=json.loads(row['slots_json'])
        if str(slots.get('org_node_id'))==str(before['id']):
            slots['domain_name']=after['name']
            c.execute('UPDATE conversation_draft SET slots_json=?,updated_at=? WHERE id=?',(json.dumps(slots,ensure_ascii=False),stamp(),row['id']))
            counts['drafts']+=1
    for table,column,label in (('weekly_report','editorial_json','weekly_reports'),('monthly_report','outline_json','monthly_reports')):
        for row in c.execute(f'SELECT id,snapshot_json,{column} FROM {table}').fetchall():
            snapshot=json.loads(row['snapshot_json']);changed=rename_snapshot(snapshot,before['id'],after['name'])
            editorial=json.loads(row[column])
            if table=='monthly_report':changed=rename_outline(editorial,snapshot,before['id'],before['name'],after['name']) or changed
            if not changed:continue
            c.execute(f'UPDATE {table} SET snapshot_json=?,{column}=?,edit_revision=edit_revision+1,updated_at=? WHERE id=?',
                      (json.dumps(snapshot,ensure_ascii=False),json.dumps(editorial,ensure_ascii=False),stamp(),row['id']))
            c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,after_json,created_at) VALUES(?,?,?,?,?,?)',
                      (table,row['id'],'domain_metadata_updated',str(user_id),json.dumps({'domain_id':before['id'],'name':after['name']},ensure_ascii=False),stamp()))
            counts[label]+=1
    return counts


def update(c, node_id, payload, user_id):
    manager(c,user_id);lock(c);before=node(c,node_id);values=fields(c,payload,before)
    c.execute('UPDATE org_node SET '+','.join(key+'=?' for key in values)+' WHERE id=?',(*values.values(),node_id))
    counts=sync_history(c,before,{**before,**values},user_id)
    audit(c,node_id,'update',user_id,before=before,after={**values,'history_updated':counts})
    return {'id':node_id,'status':'updated','history_updated':counts}


def delete(c, node_id, user_id):
    manager(c,user_id);lock(c);before=node(c,node_id)
    references=usages(c).get(node_id,{})
    if references:raise Referenced('该责任域已被引用，不能删除；可修改责任域信息。引用：'+'、'.join(f'{key} {value}' for key,value in references.items()))
    c.execute('DELETE FROM org_node WHERE id=?',(node_id,));audit(c,node_id,'delete',user_id,before=before)
    return {'id':node_id,'status':'deleted'}
