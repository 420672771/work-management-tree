"""Weekly routine templates and task snapshots; no messaging side effects."""
import json
import math
from datetime import date, datetime, time, timedelta, timezone

CHINA = timezone(timedelta(hours=8))


def migrate(c):
    for table, fields in {
        'task_template': {'estimated_hours': 'REAL NOT NULL DEFAULT 0',
                          'assignment_type': "TEXT NOT NULL DEFAULT 'person'", 'position_id': 'INTEGER',
                          'owner_ids_json': 'TEXT'},
        'task': {'routine_week_start': 'TEXT', 'routine_week_end': 'TEXT',
                 'routine_hours_updated_at': 'TEXT', 'routine_position_id': 'INTEGER',
                 'routine_position_name': 'TEXT'},
    }.items():
        existing = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
        for field, definition in fields.items():
            if field not in existing:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
    # Rebuild the legacy tables atomically: a position template has no personal
    # owner, and instance uniqueness now includes the original recipient.
    c.execute('SAVEPOINT routine_assignment_schema')
    try:
        owner = next(r for r in c.execute('PRAGMA table_info(task_template)') if r[1]=='owner_id')
        if owner[3]:
            guard=c.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name='org_node_delete_referenced'").fetchone()
            if guard:c.execute('DROP TRIGGER org_node_delete_referenced')
            sequence = c.execute("SELECT seq FROM sqlite_sequence WHERE name='task_template'").fetchone()
            c.execute('''CREATE TABLE task_template_next (
                id INTEGER PRIMARY KEY AUTOINCREMENT, org_node_id INTEGER NOT NULL,
                title TEXT NOT NULL, description TEXT DEFAULT '', owner_id INTEGER,
                deliverable TEXT NOT NULL, priority TEXT NOT NULL DEFAULT 'P1', recurrence TEXT NOT NULL,
                due_offset_days INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'active',
                review_at TEXT, created_at TEXT NOT NULL, estimated_hours REAL NOT NULL DEFAULT 0,
                assignment_type TEXT NOT NULL DEFAULT 'person', position_id INTEGER, owner_ids_json TEXT)''')
            columns=[r[1] for r in c.execute('PRAGMA table_info(task_template_next)')]
            names=','.join(columns)
            c.execute(f'INSERT INTO task_template_next({names}) SELECT {names} FROM task_template')
            c.execute('DROP TABLE task_template')
            c.execute('ALTER TABLE task_template_next RENAME TO task_template')
            if guard:c.execute(guard[0])
            if sequence:c.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='task_template'",(sequence[0],))
        instance_columns={r[1] for r in c.execute('PRAGMA table_info(task_instance)')}
        old_unique=any([r[2] for r in c.execute(f'PRAGMA index_info("{idx[1]}")')]==['template_id','instance_date']
                       for idx in c.execute('PRAGMA index_list(task_instance)').fetchall() if idx[2])
        if 'owner_id' not in instance_columns or old_unique:
            sequence=c.execute("SELECT seq FROM sqlite_sequence WHERE name='task_instance'").fetchone()
            c.execute('''CREATE TABLE task_instance_next (
                id INTEGER PRIMARY KEY AUTOINCREMENT, template_id INTEGER NOT NULL,
                instance_date TEXT NOT NULL, owner_id INTEGER NOT NULL, task_id INTEGER NOT NULL,
                created_at TEXT NOT NULL, UNIQUE(template_id,instance_date,owner_id))''')
            original='i.owner_id' if 'owner_id' in instance_columns else 'COALESCE(t.owner_id,tt.owner_id,0)'
            c.execute(f'''INSERT INTO task_instance_next(id,template_id,instance_date,owner_id,task_id,created_at)
                SELECT i.id,i.template_id,i.instance_date,{original},i.task_id,i.created_at
                FROM task_instance i LEFT JOIN task t ON t.id=i.task_id LEFT JOIN task_template tt ON tt.id=i.template_id''')
            c.execute('DROP TABLE task_instance')
            c.execute('ALTER TABLE task_instance_next RENAME TO task_instance')
            if sequence:c.execute("UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name='task_instance'",(sequence[0],))
        # Freeze the former position rule into an explicit employee selection.
        # Future position changes must never change the template's recipients.
        for row in c.execute("SELECT * FROM task_template WHERE assignment_type='position'").fetchall():
            template=dict(row)
            owners=[person['id'] for person in legacy_position_recipients(c,template,target_week())]
            c.execute("UPDATE task_template SET assignment_type='people',position_id=NULL,owner_id=?,owner_ids_json=? WHERE id=?",
                      (owners[0] if len(owners)==1 else None,json.dumps(owners),template['id']))
            c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)',
                      ('task_template',template['id'],'position_to_people','system',
                       json.dumps({'position_id':template['position_id']}),json.dumps({'owner_ids':owners}),datetime.now(CHINA).isoformat()))
        c.execute('RELEASE routine_assignment_schema')
    except Exception:
        c.execute('ROLLBACK TO routine_assignment_schema')
        c.execute('RELEASE routine_assignment_schema')
        raise
    # Historical task instances stay intact; templates now run weekly.
    c.execute("UPDATE task_template SET recurrence='WEEKLY' WHERE recurrence='DAILY'")


def hours(value, *, actual=False):
    message = '实际工时须为 0—168 小时的有效数字' if actual else '每周标准工时须为 0.5—168 小时的有效数字'
    if isinstance(value, bool) or value is None:
        raise ValueError(message)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise ValueError(message)
    if not math.isfinite(result) or not (0 if actual else 0.5) <= result <= 168:
        raise ValueError(message)
    return result


def template_fields(c, body):
    title = str(body.get('title') or '').strip()
    deliverable = str(body.get('deliverable') or '').strip()
    if not title or len(title) > 200:
        raise ValueError('模板名称须为 1—200 个字符')
    if not deliverable or len(deliverable) > 5000:
        raise ValueError('请填写交付物，最多 5000 个字符')
    if body.get('recurrence', 'WEEKLY') != 'WEEKLY':
        raise ValueError('例行任务统一按周生成')
    if body.get('assignment_type','person') not in ('person','people') or body.get('position_id') not in (None,''):
        raise ValueError('例行模板统一按人员配置，请选择责任人员（可多选）')
    explicit='owner_ids' in body
    owners=body['owner_ids'] if explicit else [body.get('owner_id')]
    if not isinstance(owners,list) or not owners or any(type(value) is not int or value<1 for value in owners):
        raise ValueError('请选择至少一名有效责任人员')
    owners=list(dict.fromkeys(owners))
    existing={row[0] for row in c.execute('SELECT id FROM user')}
    if not set(owners).issubset(existing):raise ValueError('所选责任人员不存在，请刷新后重新选择')
    if explicit and body.get('owner_id') is not None and body['owner_id'] not in owners:
        raise ValueError('责任人员参数不一致，请重新选择')
    if not c.execute("SELECT 1 FROM org_node WHERE id=? AND node_type IN ('domain','subdomain') AND inactive_at IS NULL",
                     (body.get('org_node_id'),)).fetchone():
        raise ValueError('请选择有效责任域')
    priority = body.get('priority', 'P1')
    if priority not in ('P0', 'P1', 'P2'):
        raise ValueError('优先级仅支持 P0、P1、P2')
    return dict(title=title, deliverable=deliverable, org_node_id=body['org_node_id'],
                owner_id=owners[0] if len(owners)==1 else None,
                assignment_type='people' if explicit else 'person',position_id=None,
                owner_ids_json=json.dumps(owners) if explicit else None,
                estimated_hours=hours(body.get('estimated_hours')),
                recurrence='WEEKLY', priority=priority,
                description=str(body.get('description') or ''))


def target_week(instance_date=None, current_time=None):
    """Explicit dates select their week; the scheduler selects the last due run."""
    if instance_date is not None:
        if not isinstance(instance_date, str):
            raise ValueError('补生成日期须为 YYYY-MM-DD')
        day = date.fromisoformat(instance_date)
        return day - timedelta(days=day.weekday())
    current = current_time or datetime.now(CHINA)
    if current.tzinfo is None:
        raise ValueError('调度时间须包含时区')
    current = current.astimezone(CHINA)
    sunday = current.date() - timedelta(days=(current.weekday() + 1) % 7)
    if current < datetime.combine(sunday, time(18), CHINA):
        sunday -= timedelta(days=7)
    return sunday + timedelta(days=1)


def assignment_time(value):
    if not value:return None
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    return parsed.replace(tzinfo=CHINA) if parsed.tzinfo is None else parsed.astimezone(CHINA)


def legacy_position_recipients(c, template, monday):
    start=datetime.combine(monday,time.min,CHINA); end=start+timedelta(days=7)
    rows=c.execute('''SELECT u.id,u.name,up.valid_from,up.valid_to FROM user_position up
        JOIN user u ON u.id=up.user_id JOIN org_position p ON p.id=up.position_id
        LEFT JOIN org_department d ON d.id=p.dept_id
        WHERE up.position_id=? AND p.status='active' AND (p.dept_id IS NULL OR d.status='active')
        ORDER BY u.id,up.id''',(template['position_id'],)).fetchall()
    people={}
    for row in rows:
        try:begin,finish=assignment_time(row['valid_from']),assignment_time(row['valid_to'])
        except (ValueError,TypeError):continue
        if (begin is None or begin<end) and (finish is None or finish>start) and (not begin or not finish or begin<finish):
            people[row['id']]={'id':row['id'],'name':row['name']}
    return list(people.values())


def selected_owner_ids(template):
    if template.get('owner_ids_json') is None:
        return [template['owner_id']] if template.get('owner_id') else []
    try:owners=json.loads(template['owner_ids_json'])
    except (ValueError,TypeError):return []
    if not isinstance(owners,list) or any(type(value) is not int or value<1 for value in owners):return []
    return list(dict.fromkeys(owners))


def recipients(c, template, monday):
    selected=selected_owner_ids(template)
    people={row['id']:dict(row) for row in c.execute('SELECT id,name FROM user')}
    return [people[owner] for owner in selected if owner in people]


def list_templates(c, instance_date=None):
    monday=target_week(instance_date)
    result=[]
    for row in c.execute('''SELECT tt.*,u.name AS owner_name,n.name AS node_name,p.name AS position_name,
        d.name AS position_department FROM task_template tt LEFT JOIN user u ON u.id=tt.owner_id
        LEFT JOIN org_node n ON n.id=tt.org_node_id LEFT JOIN org_position p ON p.id=tt.position_id
        LEFT JOIN org_department d ON d.id=p.dept_id WHERE tt.status='active' ORDER BY tt.id DESC''').fetchall():
        item=dict(row);item['owner_ids']=selected_owner_ids(item);item['target_members']=recipients(c,item,monday)
        item['target_count']=len(item['target_members']);item['target_week_start']=monday.isoformat()
        result.append(item)
    return result


def generate(c, instance_date=None, current_time=None, approver_for=None, template_ids=None):
    monday = target_week(instance_date, current_time)
    sunday = monday + timedelta(days=6)
    stamp = (current_time or datetime.now(CHINA)).isoformat()
    due = datetime.combine(sunday, time(23, 59, 59), CHINA).isoformat()
    # Serialize competing timer/manual invocations before checking idempotency.
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')
    created = []
    selected = None
    if template_ids is not None:
        if not isinstance(template_ids, list) or any(type(i) is not int or i < 1 for i in template_ids):
            raise ValueError('模板编号须为正整数列表')
        selected = set(template_ids)
    for row in c.execute("SELECT * FROM task_template WHERE status='active'").fetchall():
        template = dict(row)
        if selected is not None and template['id'] not in selected:
            continue
        try:
            standard = hours(template.get('estimated_hours'))
        except ValueError:
            # Legacy templates require an explicit weekly estimate, never a fake 2h.
            continue
        if template.get('assignment_type','person')=='person' and c.execute(
                'SELECT 1 FROM task_instance WHERE template_id=? AND instance_date=?',
                (template['id'], monday.isoformat())).fetchone():
            continue
        if not c.execute("SELECT 1 FROM org_node WHERE id=? AND node_type IN ('domain','subdomain') AND inactive_at IS NULL",(template['org_node_id'],)).fetchone():continue
        for person in recipients(c,template,monday):
            if c.execute('SELECT 1 FROM task_instance WHERE template_id=? AND instance_date=? AND owner_id=?',
                         (template['id'],monday.isoformat(),person['id'])).fetchone():continue
            approver = approver_for(c, person['id']) if approver_for else None
            task_id = c.execute(
            "INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,deliverable,"
            "source,task_type,parent_template_id,approval_status,approver_id,estimated_hours,"
            "routine_week_start,routine_week_end,routine_position_id,routine_position_name,confirmed_at,confirm_source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'routine_generator','ROUTINE',?,'approved',?,?,?,?,?,?,?,'routine_generator',?,?)",
            (template['org_node_id'], template['title'], template['description'], person['id'],
             due, template['priority'], template['deliverable'], template['id'], approver, standard,
             monday.isoformat(), sunday.isoformat(),None,None,stamp, stamp, stamp)).lastrowid
            c.execute('INSERT INTO task_instance(template_id,instance_date,owner_id,task_id,created_at) VALUES(?,?,?,?,?)',
                      (template['id'], monday.isoformat(),person['id'], task_id, stamp))
            created.append(task_id)
    return created
