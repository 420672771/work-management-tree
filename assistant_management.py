"""Permission-checked, reviewable reference-data changes from assistant messages."""
import json
import re
from datetime import datetime, timedelta, timezone

import accounts
import routines
import responsibility_domains

ENTITIES = {'部门':'department','岗位':'position','员工':'user','成员':'user','用户':'user',
            '子责任域':'subdomain','责任域':'domain','例行任务模板':'template','例行模板':'template'}
LABELS = {'名称':'name','姓名':'name','部门':'department','所属部门':'department',
          '上级部门':'parent_department','上级责任域':'parent_domain','父责任域':'parent_domain',
          '责任域':'domain','所属责任域':'domain','责任人员':'owner','责任人':'owner','负责人':'owner',
          '责任边界':'boundary','边界':'boundary','角色':'role','默认角色':'role',
          '账号':'username','登录账号':'username','岗位':'position','适用岗位':'position','主岗位':'position',
          '直属上级':'leader','确认人':'approver','职级':'level',
          '每周标准工时':'estimated_hours','标准工时':'estimated_hours','每周工时':'estimated_hours',
          '交付物':'deliverable','验收标准':'deliverable','优先级':'priority','说明':'description'}
ALLOWED = {
    'department': {'name','parent_department','owner'},
    'position': {'name','department','level','role'},
    'domain': {'name','parent_domain','owner','boundary'},
    'subdomain': {'name','parent_domain','owner','boundary'},
    'user': {'name','department','role','username','position','leader','approver'},
    'template': {'name','domain','owner','estimated_hours','deliverable','priority','description'},
}
REQUIRED = {'department': ('name',),'position': ('name','department'),
            'domain': ('name',),'subdomain': ('name','parent_domain'),
            'user': ('name',),'template': ('name','domain','owner','estimated_hours','deliverable')}
DISPLAY = {'name':'名称/姓名','department':'部门','parent_department':'上级部门','parent_domain':'上级责任域',
           'domain':'责任域','owner':'负责人','boundary':'责任边界','role':'角色','username':'登录账号',
           'position':'主岗位','leader':'直属上级','approver':'确认人','level':'职级',
           'estimated_hours':'每周标准工时','deliverable':'交付物','priority':'优先级','description':'说明'}
ROLES = {'成员':'member','普通成员':'member','员工':'member','member':'member',
         '负责人':'manager','部门负责人':'manager','manager':'manager',
         '管理PM':'pm','管理pm':'pm','PM':'pm','pm':'pm','管理 PM':'pm'}
FIELD_PATTERN = '|'.join(re.escape(s) for s in sorted(LABELS,key=len,reverse=True))
ENTITY_PATTERN = '|'.join(re.escape(s) for s in sorted(ENTITIES,key=len,reverse=True))


def now():
    return datetime.now(timezone.utc).isoformat()


def management_user(c, user_id):
    user = c.execute('SELECT * FROM user WHERE id=?',(user_id,)).fetchone()
    if not user or user['role'] not in ('manager','pm'):
        raise PermissionError('仅部门负责人和管理 PM 可通过助手维护基础资料和成员信息。')
    return user


def clean(value):
    return str(value).strip(' \t。；;，,:：“”"「」')


def read_fields(text):
    fields = {}; unknown = []
    for part in re.split(r'[，,；;\n]+',text):
        if not part.strip(): continue
        match = re.fullmatch(r'\s*('+FIELD_PATTERN+r')\s*(?:修改为|调整为|变更为|设置为|改为|改成|改到|为|是|[:：=])?\s*(.+?)\s*[。]?\s*',part)
        if match:
            key=LABELS[match[1]]
            if key in fields: raise ValueError('同一字段重复填写，请每项只提供一个值。')
            fields[key]=clean(match[2])
        else: unknown.append(part.strip())
    return fields,unknown


def pending(c, session_id, user_id):
    row=c.execute("SELECT * FROM bot_pending_action WHERE session_id=? AND user_id=? AND status='collecting' AND expire_at>? ORDER BY id DESC LIMIT 1",(session_id,user_id,now())).fetchone()
    return row if row and 'proposal' in json.loads(row['action_json']) else None


def store(c, session_id, user_id, action, status):
    c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id=? AND status IN ('pending','collecting')",(session_id,user_id))
    c.execute('INSERT INTO bot_pending_action(session_id,user_id,action_json,status,expire_at,created_at) VALUES(?,?,?,?,?,?)',
              (session_id,user_id,json.dumps(action,ensure_ascii=False),status,
               (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat(),now()))


def lookup(c, table, value, *, active=False, nodes=False):
    # Table names come only from these internal call sites, never from chat/model input.
    if table not in ('user','org_department','org_position','org_node'): raise ValueError('资料类型无效')
    value=clean(value)
    number=re.fullmatch(r'#(\d+)',value)
    condition='id=?' if number else 'name=?'
    params=(int(number[1]) if number else value,)
    if active: condition+=" AND status='active'"
    if nodes: condition+=" AND inactive_at IS NULL AND node_type IN ('domain','subdomain')"
    rows=c.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY id LIMIT 2',params).fetchall()
    if not rows: raise ValueError(f'未找到“{value}”，请先新建或提供正确名称。')
    if len(rows)>1: raise ValueError(f'“{value}”存在重名，请使用 #编号 明确对象。')
    return dict(rows[0])


def role(value):
    if value not in ROLES: raise ValueError('角色仅支持普通成员、管理PM、部门负责人。')
    return ROLES[value]


def optional_person(c, value):
    return None if value in ('无','清空','取消','不设置') else lookup(c,'user',value)['id']


def validate(c, proposal, user_id):
    management_user(c,user_id)
    entity=proposal.get('entity'); operation=proposal.get('operation'); fields=proposal.get('fields')
    if entity not in ALLOWED or not isinstance(fields,dict) or set(fields)-ALLOWED[entity]:
        raise ValueError('包含不支持的资料字段。可说“助手帮助”查看示例。')
    if operation not in ('create','update') or (operation=='update' and entity!='user'):
        raise ValueError('当前支持新建基础资料和修改成员信息。')
    if any(not isinstance(v,str) or not v.strip() or len(v)>5000 for v in fields.values()):
        raise ValueError('字段内容不能为空或超过 5000 字。')
    if len(fields.get('name',''))>200: raise ValueError('名称/姓名最多 200 个字符。')
    if operation=='create' and any(not fields.get(key) for key in REQUIRED[entity]):
        raise ValueError('请补齐必填资料。')
    before=None; data={}
    if operation=='update':
        before=lookup(c,'user',proposal.get('target',''))
        if not fields: raise ValueError('请说明需要修改的成员字段。')
        data['id']=before['id']
        if 'username' in fields: data['previous_account']=accounts.metadata(c,before['id'])
        if 'position' in fields:
            data['previous_positions']=[dict(r) for r in c.execute('SELECT * FROM user_position WHERE user_id=? AND is_primary=1 AND valid_to IS NULL ORDER BY id',(before['id'],))]
        if {'leader','approver'}&fields.keys():
            data['previous_reporting']=[dict(r) for r in c.execute('SELECT * FROM reporting_line WHERE user_id=? AND effective_to IS NULL ORDER BY id',(before['id'],))]
    if 'name' in fields: data['name']=fields['name']
    if operation=='create':
        table={'user':'user','department':'org_department','position':'org_position','domain':'org_node','subdomain':'org_node','template':'task_template'}[entity]
        if c.execute(f'SELECT 1 FROM {table} WHERE '+('title' if entity=='template' else 'name')+'=?',(fields['name'],)).fetchone():
            raise ValueError('已有同名资料，请核对后使用现有记录。')
    if 'department' in fields:
        dept=lookup(c,'org_department',fields['department'],active=True)
        data['department']=dept['name'];data['dept_id']=dept['id']
    if 'parent_department' in fields: data['parent_id']=lookup(c,'org_department',fields['parent_department'],active=True)['id']
    if 'owner' in fields and entity!='template': data['owner_id']=lookup(c,'user',fields['owner'])['id']
    if 'role' in fields: data['role']=role(fields['role'])
    if before and before['role']=='manager' and data.get('role','manager')!='manager':
        if c.execute("SELECT count(*) FROM user WHERE role='manager'").fetchone()[0]<=1:
            raise ValueError('不能移除最后一位部门负责人的管理角色。')
    if 'username' in fields:
        data['username']=accounts.username(fields['username'])
        account=c.execute('SELECT user_id FROM user_account WHERE username=?',(data['username'],)).fetchone()
        if account and (not before or account['user_id']!=before['id']): raise ValueError('登录账号已被其他员工使用。')
    if 'position' in fields and entity=='user':
        position=lookup(c,'org_position',fields['position'],active=True)
        data['position_id']=position['id']
        dept=c.execute("SELECT * FROM org_department WHERE id=? AND status='active'",(position['dept_id'],)).fetchone()
        if not dept: raise ValueError('岗位所属部门无效，请先修正岗位。')
        target_dept=data.get('department',before['department'] if before else dept['name'])
        if dept['name']!=target_dept: raise ValueError('岗位与成员部门不一致，请同时指定正确部门和岗位。')
        data['department']=dept['name']
    if 'leader' in fields or 'approver' in fields:
        old_line=c.execute('SELECT * FROM reporting_line WHERE user_id=? AND effective_to IS NULL ORDER BY id DESC LIMIT 1',(before['id'] if before else 0,)).fetchone()
        leader=optional_person(c,fields['leader']) if 'leader' in fields else (old_line['direct_leader_id'] if old_line else None)
        approver=optional_person(c,fields['approver']) if 'approver' in fields else (leader if 'leader' in fields else (old_line['approver_id'] if old_line else None))
        seen={before['id']} if before else set(); current=leader
        while current:
            if current in seen: raise ValueError('直属上级不能是本人，也不能形成循环汇报关系。')
            seen.add(current)
            line=c.execute('SELECT direct_leader_id FROM reporting_line WHERE user_id=? AND effective_to IS NULL ORDER BY id DESC LIMIT 1',(current,)).fetchone()
            current=line[0] if line else None
        if before and approver==before['id']: raise ValueError('不能将本人设置为自己的确认人。')
        data.update(leader_id=leader,approver_id=approver)
    if entity in ('domain','subdomain'):
        if 'parent_domain' in fields: data['parent_id']=lookup(c,'org_node',fields['parent_domain'],nodes=True)['id']
        else:
            roots=c.execute("SELECT id FROM org_node WHERE node_type='department' AND inactive_at IS NULL LIMIT 2").fetchall()
            if len(roots)!=1: raise ValueError('请指定上级责任域。')
            data['parent_id']=roots[0]['id']
        data['boundary']=fields.get('boundary','')
    if entity=='position': data['level']=fields.get('level','')
    if entity=='template':
        people=[clean(value) for value in re.split(r'[、/]',fields['owner'])]
        if any(not value for value in people):raise ValueError('请填写责任人员姓名，多人用顿号分隔，例如“张三、李四”。')
        owners=list(dict.fromkeys(lookup(c,'user',value)['id'] for value in people))
        domain=lookup(c,'org_node',fields['domain'],nodes=True)
        data.update(org_node_id=domain['id'],title=fields['name'],deliverable=fields['deliverable'],
                    estimated_hours=routines.hours(re.sub(r'\s*(?:小时|h)\s*$','',fields['estimated_hours'],flags=re.I)),
                    priority=fields.get('priority','P1').upper(),description=fields.get('description',''))
        data.update(routines.template_fields(c,{**data,'owner_ids':owners}))
    return data,before


def plan(c, text, user_id, session_id, extract=None):
    """Return (handled, action, reply); incomplete proposals are private to the sender."""
    source=text.strip(); proposal=None
    query=re.fullmatch(r'(?:查询|查看|列出|看看)\s*('+ENTITY_PATTERN+r')(?:列表|清单)?\s*([^，,；;\n]*)[？?。]?',source)
    if query:
        try:
            management_user(c,user_id)
            entity=ENTITIES[query[1]];target=clean(query[2])
            table={'user':'user','department':'org_department','position':'org_position','domain':'org_node','subdomain':'org_node','template':'task_template'}[entity]
            name='title' if entity=='template' else 'name'
            condition="status='active'" if table in ('org_department','org_position','task_template') else ("inactive_at IS NULL AND node_type IN ('domain','subdomain')" if table=='org_node' else '1=1')
            params=[]
            if target:
                number=re.fullmatch(r'#(\d+)',target)
                condition+=' AND '+('id=?' if number else name+'=?');params.append(int(number[1]) if number else target)
            rows=c.execute(f'SELECT * FROM {table} WHERE {condition} ORDER BY id LIMIT 31',params).fetchall()
            if not rows:return True,None,'未找到对应资料。说“助手帮助”可查看新建方法。'
            lines=[]
            for row in rows[:30]:
                label=f'#{row["id"]} {row[name]}'
                if entity=='user':label+=f' / {row["department"]} / {row["role"]} / 账号：'+(accounts.metadata(c,row['id'])['username'] or '未配置')
                lines.append(label)
            if len(rows)>30:lines.append('仅展示前 30 条，可提供完整名称或 #编号 查询具体资料。')
            return True,None,'\n'.join(lines)
        except PermissionError as exc:return True,None,str(exc)
    polite=r'(?:(?:请|帮我|帮忙|麻烦|给我)\s*)*'
    create=re.match(polite+r'(?:新建|新增|创建|添加|建立|增加)\s*(?:一[个名条]\s*)?('+ENTITY_PATTERN+r')\s*(.*)$',source,re.S)
    update=re.match(polite+r'(?:修改|更新|调整|变更|设置|更改)\s*(?:员工|成员|用户)(?:信息)?\s*(.*)$',source,re.S)
    natural=re.match(r'(?:请|帮我|麻烦)?\s*(?:把|将)\s*(.+?)的?\s*('+FIELD_PATTERN+r')\s*(?:改为|改成|修改为|调整为|设置为)\s*(.+)$',source)
    prior=pending(c,session_id,user_id) if user_id else None
    if create:
        entity=ENTITIES[create[1]]; parts=re.split(r'[，,；;\n]',create[2],maxsplit=1)
        fields,unknown=read_fields(parts[1] if len(parts)>1 else '')
        name=clean(re.sub(r'^(?:名称|姓名)\s*[:：]?\s*','',parts[0]))
        if name: fields['name']=name
        proposal={'operation':'create','entity':entity,'fields':fields}
    elif update:
        parts=re.split(r'[，,；;\n]',update[1],maxsplit=1)
        target=clean(parts[0]); rest=parts[1] if len(parts)>1 else ''
        if not rest:
            inline=re.match(r'(.+?)的?\s*('+FIELD_PATTERN+r')\s*(?:改为|改成|修改为|调整为|设置为|[:：])\s*(.+)$',target)
            if inline: target=clean(inline[1]);rest=inline[2]+'改为'+inline[3]
        fields,unknown=read_fields(rest)
        proposal={'operation':'update','entity':'user','target':target,'fields':fields}
    elif natural and not re.search(r'(?:任务|事项|截止|交付物|验收标准)',source):
        fields,unknown=read_fields(natural[2]+'改为'+natural[3])
        proposal={'operation':'update','entity':'user','target':clean(natural[1]),'fields':fields}
    elif prior:
        fields,unknown=read_fields(source)
        if fields and not unknown:
            proposal=json.loads(prior['action_json'])['proposal'];proposal['fields'].update(fields)
    hinted=bool(re.search(r'(?:新建|新增|创建|添加|建立|增加|修改|更新|变更|更改).{0,15}(?:部门|岗位|员工|成员|用户|责任域|例行.*模板)',source)) and not re.search(r'(?:新建|新增|创建)\s*任务(?!模板)',source)
    if extract and ((proposal is not None and unknown) or (proposal is None and hinted)):
        try:
            management_user(c,user_id)
            extracted=extract(source)
            if isinstance(extracted,dict) and extracted.get('entity') in ALLOWED and extracted.get('operation') in ('create','update') and isinstance(extracted.get('fields'),dict):
                proposal={k:extracted[k] for k in ('operation','entity','fields')}
                if proposal['operation']=='update':proposal['target']=clean(extracted.get('target',''))
                unknown=[]
        except (ValueError,PermissionError,TypeError):pass
    if proposal is None:
        if hinted:
            try:management_user(c,user_id)
            except PermissionError as exc:return True,None,str(exc)
            return True,None,'可以维护基础资料。请说明对象和字段，例如“新建部门 数据产品部”或“修改成员 张三，部门：数据产品部”；说“助手帮助”查看完整示例。'
        return False,None,None
    try:
        management_user(c,user_id)
        # A new request always replaces the previous confirmation, including an
        # invalid request, so "confirm" cannot apply to an older unseen proposal.
        c.execute("UPDATE bot_pending_action SET status='superseded' WHERE session_id=? AND user_id=? AND status IN ('pending','collecting')",(session_id,user_id))
        if unknown: raise ValueError('未识别这些资料：'+'、'.join(unknown)+'。请使用“字段：内容”，多项用逗号分隔。')
        if set(proposal['fields'])-ALLOWED[proposal['entity']]: raise ValueError('该资料不支持这些字段，请说“助手帮助”查看示例。')
        missing=[key for key in REQUIRED[proposal['entity']] if not proposal['fields'].get(key)] if proposal['operation']=='create' else []
        if proposal['operation']=='update' and not proposal['target']: raise ValueError('请提供成员姓名或 #编号。')
        if missing or not proposal['fields']:
            store(c,session_id,user_id,{'proposal':proposal},'collecting')
            labels=[('责任人员（可多选）' if proposal['entity']=='template' and key=='owner' else DISPLAY[key]) for key in missing]
            example='责任人员：张三、李四' if proposal['entity']=='template' and 'owner' in missing else '部门：科技产品中心'
            return True,None,'请补充'+('、'.join(labels) if missing else '需要修改的字段')+'，例如“'+example+'”；补齐后我会回显供你确认。'
        data,before=validate(c,proposal,user_id)
        action={'type':'manage_reference','proposal':proposal,'resolved':data,'before':before}
        store(c,session_id,user_id,action,'pending')
        lines=['拟'+('新建'+next(k for k,v in ENTITIES.items() if v==proposal['entity']) if proposal['operation']=='create' else '修改成员“'+before['name']+'”')+'：']
        lines += [('责任人员' if k=='owner' and proposal['entity']=='template' else DISPLAY[k])+'：'+v for k,v in proposal['fields'].items()]
        if proposal['entity']=='template':lines.append('所选每位员工每周各一份任务，标准工时按每人计算，分别反馈和验收。')
        if before: lines.append('当前资料：'+before['name']+' / '+before['department']+' / '+before['role'])
        if 'username' in proposal['fields']:
            lines.append('首次配置账号使用系统初始密码；已有账号保留密码，账号变更会注销旧登录会话。')
        lines.append('回复“确认”执行，回复“取消”放弃；10 分钟内有效。')
        return True,action,'\n'.join(lines)
    except (ValueError,PermissionError,LookupError) as exc:
        return True,None,str(exc)


def execute(c, action, user_id):
    proposal=action['proposal']; data,before=validate(c,proposal,user_id)
    if data!=action['resolved'] or before!=action['before']:
        raise ValueError('资料已发生变化，请重新提出操作并核对最新内容。')
    entity=proposal['entity']; operation=proposal['operation']; stamp=now()
    if entity=='department':
        oid=c.execute("INSERT INTO org_department(name,parent_id,owner_user_id,status,created_at) VALUES(?,?,?,'active',?)",(data['name'],data.get('parent_id'),data.get('owner_id'),stamp)).lastrowid
        table='org_department'
    elif entity=='position':
        oid=c.execute("INSERT INTO org_position(name,dept_id,level,default_role,status) VALUES(?,?,?,?,'active')",(data['name'],data['dept_id'],data['level'],data.get('role','member'))).lastrowid
        table='org_position'
    elif entity in ('domain','subdomain'):
        oid=responsibility_domains.create(c,{'parent_id':data['parent_id'],'name':data['name'],
            'boundary':data['boundary'],'owner_id':data.get('owner_id')},user_id)['id']
        table='org_node'
    elif entity=='template':
        oid=c.execute("INSERT INTO task_template(org_node_id,title,description,owner_id,deliverable,priority,recurrence,estimated_hours,assignment_type,position_id,owner_ids_json,status,created_at) VALUES(?,?,?,?,?,?,'WEEKLY',?,?,?,?,'active',?)",(data['org_node_id'],data['title'],data['description'],data['owner_id'],data['deliverable'],data['priority'],data['estimated_hours'],data['assignment_type'],data['position_id'],data['owner_ids_json'],stamp)).lastrowid
        table='task_template'
    elif entity=='user':
        if operation=='create':
            oid=c.execute('INSERT INTO user(name,department,role) VALUES(?,?,?)',(data['name'],data.get('department','科技产品中心'),data.get('role','member'))).lastrowid
        else:
            oid=data['id']; changes={key:data[key] for key in ('name','department','role') if key in data}
            if changes:c.execute('UPDATE user SET '+','.join(key+'=?' for key in changes)+' WHERE id=?',(*changes.values(),oid))
        if 'username' in data: accounts.configure(c,oid,data['username'],'',user_id)
        if 'position_id' in data:
            c.execute('UPDATE user_position SET valid_to=? WHERE user_id=? AND is_primary=1 AND valid_to IS NULL',(stamp,oid))
            c.execute('INSERT INTO user_position(user_id,position_id,is_primary,valid_from) VALUES(?,?,1,?)',(oid,data['position_id'],stamp))
        if 'leader_id' in data:
            c.execute('UPDATE reporting_line SET effective_to=? WHERE user_id=? AND effective_to IS NULL',(stamp,oid))
            if data['leader_id'] or data['approver_id']:
                c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)',(oid,data['leader_id'],data['approver_id'],stamp))
        table='user'
    else: raise ValueError('不支持的资料类型')
    c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',
              (table,oid,'assistant_'+operation,str(user_id),json.dumps(before,ensure_ascii=False) if before else None,json.dumps(proposal['fields'],ensure_ascii=False),stamp))
    return ('已新建' if operation=='create' else '已修改')+'资料 #'+str(oid)+'，已记录操作审计。'
