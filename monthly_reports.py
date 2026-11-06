"""Private employee monthly snapshots and editable, archived mind maps."""
import calendar
import json
import re
from datetime import date, datetime, time, timedelta
import weekly_reports as facts

SCHEMA = """
CREATE TABLE IF NOT EXISTS monthly_report (
 id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL, month TEXT NOT NULL,
 version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'draft', snapshot_json TEXT NOT NULL,
 outline_json TEXT NOT NULL, edit_revision INTEGER NOT NULL DEFAULT 0,
 generated_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT, revision_of INTEGER,
 UNIQUE(user_id,month,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS monthly_report_one_draft ON monthly_report(user_id,month) WHERE status='draft';
CREATE INDEX IF NOT EXISTS monthly_report_owner_month ON monthly_report(user_id,month DESC);
"""
SECTIONS = {'overview':'月度概览','achievements':'重点工作与成果','progress':'任务进展',
            'risks':'风险与阻塞','next_month':'下月计划','summary':'工作总结'}
MAX_NODES, MAX_DEPTH, MAX_TEXT, MAX_BYTES = 5000, 8, 4000, 768 * 1024


class Conflict(ValueError):
    pass


def period(month=None):
    month = facts.local_time().strftime('%Y-%m') if month is None else month
    if not isinstance(month, str) or not re.fullmatch(r'\d{4}-\d{2}', month):
        raise ValueError('请选择有效月份（YYYY-MM）')
    try:
        year, number = map(int, month.split('-'))
        start = date(year, number, 1)
        end = date(year, number, calendar.monthrange(year, number)[1])
    except ValueError:
        raise ValueError('请选择有效月份（YYYY-MM）')
    return month, start, end


def actor(c, user_id):
    row = c.execute('SELECT id,name,role FROM user WHERE id=?', (user_id,)).fetchone()
    if not row:
        raise PermissionError('请使用已绑定的员工账号登录')
    return dict(row)


def can_view_all(user):
    return user['role'] in ('manager', 'pm')


def audit(c, report_id, action, user_id):
    c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,created_at) VALUES(?,?,?,?,?)',
              ('monthly_report', report_id, action, str(user_id), facts.local_time().isoformat()))


def view(row, user):
    out = dict(row)
    out['snapshot'] = json.loads(out.pop('snapshot_json'))
    out['editorial'] = {'outline': json.loads(out.pop('outline_json'))}
    out['can_edit'] = out['user_id'] == user['id'] and out['status'] == 'draft'
    out['can_revise'] = out['user_id'] == user['id'] and out['status'] == 'archived'
    out['status_label'] = '已归档' if out['status'] == 'archived' else '草稿'
    return out


def get_report(c, report_id, user_id):
    user = actor(c, user_id)
    row = c.execute('SELECT * FROM monthly_report WHERE id=?', (report_id,)).fetchone()
    # Do not reveal the existence of a colleague's report to another employee.
    if not row or (row['user_id'] != user['id'] and not can_view_all(user)):
        raise LookupError('月报不存在或无权查看')
    return view(row, user)


def list_reports(c, user_id, month=None, scope='mine', owner_id=None):
    user = actor(c, user_id)
    if scope not in ('mine', 'all'):
        raise ValueError('不支持的月报查询范围')
    if month:
        period(month)
    if owner_id not in (None, ''):
        try:
            owner_id = int(owner_id)
        except (ValueError, TypeError):
            raise ValueError('员工参数无效')
        if owner_id != user['id'] and not can_view_all(user):
            raise PermissionError('只能查看自己的月报')
    else:
        owner_id = None
    if scope == 'all' and not can_view_all(user):
        raise PermissionError('仅部门负责人和管理 PM 可查看全部月报')
    query = 'SELECT r.*,u.name AS owner_name FROM monthly_report r JOIN user u ON u.id=r.user_id WHERE 1=1'
    params = []
    target = owner_id if scope == 'all' else user['id']
    if target is not None:
        query += ' AND r.user_id=?'; params.append(target)
    if month:
        query += ' AND r.month=?'; params.append(month)
    keys = ('id','user_id','owner_name','month','version','status','generated_at','updated_at','archived_at','revision_of')
    items = [{k: row[k] for k in keys} for row in c.execute(query + ' ORDER BY r.month DESC,r.updated_at DESC,r.version DESC', params)]
    # Employee names for the team filter are only sent to privileged viewers.
    people = [dict(r) for r in c.execute('SELECT id,name FROM user ORDER BY name,id')] if can_view_all(user) else [{'id':user['id'],'name':user['name']}]
    return {'items': items, 'can_view_all': can_view_all(user), 'people': people, 'user': user}


def build_snapshot(c, user, month, generated_at):
    month, start, end = period(month)
    snapshot = facts.build_snapshot(c, start, end, generated_at, owner_id=user['id'])
    next_start = end + timedelta(days=1)
    next_end = date(next_start.year, next_start.month, calendar.monthrange(next_start.year, next_start.month)[1])
    next_cutoff = datetime.combine(next_end + timedelta(days=1), time.min, facts.CHINA)
    next_begin = datetime.combine(next_start, time.min, facts.CHINA)
    tasks = snapshot['tasks']
    for task in tasks:
        task['risk_reasons'] = [reason.replace('本周', '本月') for reason in task['risk_reasons']]
    next_ids = []
    for task in sorted(tasks, key=lambda t: (t['priority'], t['due_at'], t['id'])):
        due = facts.local_time(task['due_at'])
        if task['work_status'] != 'completed' and ((due and next_begin <= due < next_cutoff) or
            (task['priority'] in ('P0','P1') and task['work_status'] == 'in_progress')):
            next_ids.append(task['id'])
    snapshot['sections'] = {
        'achievements': snapshot['sections']['achievements'],
        'progress': [t['id'] for t in tasks if t['work_status'] != 'completed'],
        'risks': snapshot['sections']['risks'], 'next_month': next_ids,
    }
    m = snapshot['metrics']
    snapshot.update({'title': f"{user['name']} · {month} 个人月报", 'month': month,
        'owner': {'id':user['id'],'name':user['name']},
        'summary': f"本月新增 {m['new']} 项、完成 {m['completed']} 项，当前进行中 {m['in_progress']} 项、逾期 {m['overdue']} 项。"})
    snapshot['rules'] = {'cutoff':'基于生成时本人已确认的正式任务；草稿、取消和删除任务不纳入。',
                         'achievements':'展示未完成的 P0 重点任务，以及本月已完成的全部优先级任务。',
                         'completion_time':snapshot['rules']['completion_time']}
    return snapshot


def node(node_id, text, kind='detail', children=None, task_id=None, domain_id=None):
    value = {'id':node_id,'text':str(text)[:MAX_TEXT],'kind':kind,'children':children or []}
    if task_id is not None: value['task_id'] = task_id
    if domain_id is not None: value['domain_id'] = domain_id
    return value


def default_outline(snapshot):
    m = snapshot['metrics']
    metric_text = {'new':f"新增任务：{m['new']} 项",'completed':f"完成任务：{m['completed']} 项",
        'in_progress':f"进行中：{m['in_progress']} 项",'overdue':f"已逾期：{m['overdue']} 项",
        'awaiting_acceptance':f"待验收：{m['awaiting_acceptance']} 项",
        'standard':f"完成标准工时：{m['completed_hours']['standard']}h",
        'actual':f"完成实际工时：{m['completed_hours']['actual']}h",'domains':f"责任域覆盖：{m['domains']} 个"}
    branches = [node('section:overview', SECTIONS['overview'], 'section',
                     [node('metric:'+key,text,'metric') for key,text in metric_text.items()])]
    tasks = {t['id']:t for t in snapshot['tasks']}
    for section, ids in snapshot['sections'].items():
        groups = {}
        for task_id in ids:
            task = tasks[task_id]
            key = f'{section}:task:{task_id}'
            details = [node(key+':delivery','交付物：'+(task['deliverable'] or '未填写'),task_id=task_id),
                       node(key+':progress','进展：'+(task['latest_progress'] or '暂无进展记录'),task_id=task_id)]
            if section == 'risks':
                details += [node(key+':risk','关注：'+'；'.join(task['risk_reasons']),task_id=task_id),
                            node(key+':action','建议：'+task['suggested_action'],task_id=task_id)]
            label = f"{task['title']}｜{task['priority']}｜{facts.WORK.get(task['work_status'],task['work_status'])}｜截止 {task['due_at'][:10]}"
            groups.setdefault(task['domain_name'],[]).append(node(key,label,'task',details,task_id))
        children = [node(f'{section}:domain:{index}',name,'group',items,domain_id=tasks[items[0]['task_id']]['domain_id']) for index,(name,items) in enumerate(groups.items())]
        branches.append(node('section:'+section,SECTIONS[section],'section',children))
    branches.append(node('section:summary',SECTIONS['summary'],'section',[
        node('summary:conclusion','本月总结：待补充'),node('summary:improvements','不足与改进：待补充'),
        node('summary:support','需协同支持：待补充')]))
    return node('root',snapshot['title'],'root',branches)


def validate_outline(outline, snapshot):
    expected = default_outline(snapshot)
    metric_nodes = {n['id']:n for n in expected['children'][0]['children']}
    section_ids = {'section:'+key for key in SECTIONS}
    if not isinstance(outline,dict):
        raise ValueError('导图内容格式无效')
    # The invisible document container allows every visible node to be deleted,
    # including the generated title. Legacy single-root outlines remain valid.
    container = outline.get('kind') == 'container'
    if container:
        if set(outline) != {'id','text','kind','children'} or outline.get('id') != 'monthly:outline' or outline.get('text') != '':
            raise ValueError('导图容器格式无效')
    elif outline.get('kind') != 'root':
        raise ValueError('导图根节点格式无效')
    seen = set(); count = 0; task_ids = {t['id'] for t in snapshot['tasks']}
    stack = [(outline,-1 if container else 0,None)]
    while stack:
        item,depth,parent = stack.pop(); count += 1
        if depth>MAX_DEPTH or count>MAX_NODES or not isinstance(item,dict):
            raise ValueError('导图层级或节点数量超出限制')
        if set(item)-{'id','text','kind','children','task_id','domain_id'}:
            raise ValueError('导图节点包含不支持的字段')
        key,text,kind,children = (item.get(k) for k in ('id','text','kind','children'))
        if not isinstance(key,str) or not key or len(key)>160 or key in seen:
            raise ValueError('导图节点编号无效或重复')
        seen.add(key)
        if not isinstance(text,str) or len(text)>MAX_TEXT or not isinstance(children,list):
            raise ValueError('导图内容格式无效或单节点超过 4000 字')
        if kind not in ('root','section','metric','group','task','detail','custom','container'):
            raise ValueError('导图节点类型无效')
        if kind=='container' and depth!=-1:
            raise ValueError('导图容器不能作为内容节点')
        if key=='root' and kind!='root':
            raise ValueError('月报标题节点类型不能修改')
        if kind=='root' and (key!='root' or depth!=0 or text!=snapshot['title']):
            raise ValueError('月报标题节点须保留本人姓名与报告月份')
        if (key in section_ids and kind!='section') or (kind=='section' and key not in section_ids):
            raise ValueError('导图分支类型无效')
        if kind=='metric':
            if key not in metric_nodes or text!=metric_nodes[key]['text'] or 'task_id' in item:
                raise ValueError('统计节点来自任务快照，不能手动修改')
        if key in metric_nodes and kind!='metric':
            raise ValueError('统计节点类型不能修改')
        if 'task_id' in item and (type(item['task_id']) is not int or item['task_id'] not in task_ids or kind not in ('task','detail')):
            raise ValueError('导图只能关联本月报快照中的本人任务')
        if 'domain_id' in item and (kind!='group' or type(item['domain_id']) is not int or item['domain_id'] not in {t['domain_id'] for t in snapshot['tasks']}):
            raise ValueError('导图只能关联本月报快照中的责任域')
        stack.extend((child,depth+1,key) for child in children)
    if len(json.dumps(outline,ensure_ascii=False).encode())>MAX_BYTES:
        raise ValueError('导图内容过大，请精简补充内容')


def generate(c, user_id, month=None):
    user = actor(c,user_id)
    month,start,_ = period(month)
    if start>facts.local_time().date(): raise ValueError('不能生成未来月份的月报')
    facts.write_lock(c)
    previous=c.execute('SELECT * FROM monthly_report WHERE user_id=? AND month=? ORDER BY version DESC LIMIT 1',(user_id,month)).fetchone()
    if previous: return view(previous,user)
    stamp=facts.local_time(); snapshot=build_snapshot(c,user,month,stamp); outline=default_outline(snapshot)
    validate_outline(outline,snapshot)
    report_id=c.execute('''INSERT INTO monthly_report(user_id,month,version,snapshot_json,outline_json,generated_at,updated_at)
                           VALUES(?,?,1,?,?,?,?)''',(user_id,month,json.dumps(snapshot,ensure_ascii=False),json.dumps(outline,ensure_ascii=False),stamp.isoformat(),stamp.isoformat())).lastrowid
    audit(c,report_id,'generate',user_id)
    return get_report(c,report_id,user_id)


def update(c, report_id, user_id, changes):
    if not isinstance(changes,dict) or set(changes)-{'action','edit_revision','outline'}:
        raise ValueError('只能编辑导图内容，不能修改归属、月份或任务快照')
    action=changes.get('action','save')
    if action not in ('save','archive'): raise ValueError('不支持的月报操作')
    facts.write_lock(c); report=get_report(c,report_id,user_id)
    if report['user_id']!=user_id: raise PermissionError('只能编辑和归档自己的月报')
    if report['status']!='draft': raise Conflict('已归档月报不能直接修改，请新建修订稿')
    if type(changes.get('edit_revision')) is not int or changes['edit_revision']!=report['edit_revision']:
        raise Conflict('月报已更新，请重新加载后再编辑')
    outline=changes.get('outline',report['editorial']['outline']); validate_outline(outline,report['snapshot'])
    stamp=facts.local_time().isoformat(); status='archived' if action=='archive' else 'draft'
    c.execute('UPDATE monthly_report SET outline_json=?,status=?,edit_revision=edit_revision+1,updated_at=?,archived_at=? WHERE id=?',
              (json.dumps(outline,ensure_ascii=False),status,stamp,stamp if action=='archive' else None,report_id))
    audit(c,report_id,action,user_id)
    return get_report(c,report_id,user_id)


def revise(c, report_id, user_id):
    facts.write_lock(c); original=get_report(c,report_id,user_id)
    if original['user_id']!=user_id: raise PermissionError('只能修订自己的月报')
    if original['status']!='archived': raise Conflict('只能从已归档月报新建修订稿')
    active=c.execute("SELECT * FROM monthly_report WHERE user_id=? AND month=? AND status='draft'",(user_id,original['month'])).fetchone()
    if active: return view(active,actor(c,user_id))
    latest=c.execute('SELECT id,version FROM monthly_report WHERE user_id=? AND month=? ORDER BY version DESC LIMIT 1',(user_id,original['month'])).fetchone()
    if latest['id']!=report_id: raise Conflict('请从该月最新归档版本建立修订稿')
    stamp=facts.local_time().isoformat()
    new_id=c.execute('''INSERT INTO monthly_report(user_id,month,version,snapshot_json,outline_json,generated_at,updated_at,revision_of)
                       VALUES(?,?,?,?,?,?,?,?)''',(user_id,original['month'],latest['version']+1,json.dumps(original['snapshot'],ensure_ascii=False),
                         json.dumps(original['editorial']['outline'],ensure_ascii=False),stamp,stamp,report_id)).lastrowid
    audit(c,new_id,'revise',user_id)
    return get_report(c,new_id,user_id)


def text_export(report):
    lines=[report['snapshot']['title'],f"{report['status_label']} · V{report['version']} · 数据截至 {report['snapshot']['data_cutoff']}"]
    def visit(item,depth):
        if item['kind']=='container':
            for child in item['children']: visit(child,depth)
            return
        lines.append('  '*depth+'- '+item['text'])
        for child in item['children']: visit(child,depth+1)
    visit(report['editorial']['outline'],0)
    return '\n'.join(lines)
