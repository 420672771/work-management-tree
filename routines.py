"""Weekly routine templates and task snapshots; no messaging side effects."""
import math
from datetime import date, datetime, time, timedelta, timezone

CHINA = timezone(timedelta(hours=8))


def migrate(c):
    for table, fields in {
        'task_template': {'estimated_hours': 'REAL NOT NULL DEFAULT 0'},
        'task': {'routine_week_start': 'TEXT', 'routine_week_end': 'TEXT',
                 'routine_hours_updated_at': 'TEXT'},
    }.items():
        existing = {r[1] for r in c.execute(f'PRAGMA table_info({table})')}
        for field, definition in fields.items():
            if field not in existing:
                c.execute(f'ALTER TABLE {table} ADD COLUMN {field} {definition}')
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
    if not c.execute('SELECT 1 FROM user WHERE id=?', (body.get('owner_id'),)).fetchone():
        raise ValueError('请选择有效责任人')
    if not c.execute("SELECT 1 FROM org_node WHERE id=? AND node_type IN ('domain','subdomain') AND inactive_at IS NULL",
                     (body.get('org_node_id'),)).fetchone():
        raise ValueError('请选择有效责任域')
    priority = body.get('priority', 'P1')
    if priority not in ('P0', 'P1', 'P2'):
        raise ValueError('优先级仅支持 P0、P1、P2')
    return dict(title=title, deliverable=deliverable, org_node_id=body['org_node_id'],
                owner_id=body['owner_id'], estimated_hours=hours(body.get('estimated_hours')),
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
        if c.execute('SELECT 1 FROM task_instance WHERE template_id=? AND instance_date=?',
                     (template['id'], monday.isoformat())).fetchone():
            continue
        approver = approver_for(c, template['owner_id']) if approver_for else None
        task_id = c.execute(
            "INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,deliverable,"
            "source,task_type,parent_template_id,approval_status,approver_id,estimated_hours,"
            "routine_week_start,routine_week_end,confirmed_at,confirm_source,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,'routine_generator','ROUTINE',?,'approved',?,?,?,?,?,'routine_generator',?,?)",
            (template['org_node_id'], template['title'], template['description'], template['owner_id'],
             due, template['priority'], template['deliverable'], template['id'], approver, standard,
             monday.isoformat(), sunday.isoformat(), stamp, stamp, stamp)).lastrowid
        c.execute('INSERT INTO task_instance(template_id,instance_date,task_id,created_at) VALUES(?,?,?,?)',
                  (template['id'], monday.isoformat(), task_id, stamp))
        created.append(task_id)
    return created
