"""Department meeting reports: frozen facts with separately editable commentary."""
import json
from datetime import date, datetime, time, timedelta, timezone

DEPARTMENT = '科技产品中心'
CHINA = timezone(timedelta(hours=8))
STATUSES = {'pending': '待发布', 'reviewing': '待发布', 'published': '已发布', 'superseded': '已替代草稿'}
WORK = {'assigned': '待开始', 'in_progress': '进行中', 'awaiting_acceptance': '待验收', 'completed': '已完成'}
SCHEMA = """
CREATE TABLE IF NOT EXISTS weekly_report (
 id INTEGER PRIMARY KEY AUTOINCREMENT, period_start TEXT NOT NULL, period_end TEXT NOT NULL,
 version INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending', snapshot_json TEXT NOT NULL,
 editorial_json TEXT NOT NULL DEFAULT '{}', generated_at TEXT NOT NULL, generated_by INTEGER,
 updated_at TEXT NOT NULL, updated_by INTEGER, published_at TEXT, published_by INTEGER,
 publisher_name TEXT, revision_of INTEGER, source TEXT NOT NULL DEFAULT 'manual',
 edit_revision INTEGER NOT NULL DEFAULT 0, UNIQUE(period_start,version)
);
CREATE UNIQUE INDEX IF NOT EXISTS weekly_report_one_draft ON weekly_report(period_start)
 WHERE status IN ('pending','reviewing');
CREATE TABLE IF NOT EXISTS weekly_report_job (
 period_start TEXT PRIMARY KEY, report_id INTEGER NOT NULL, generated_at TEXT NOT NULL
);
"""


class ReportConflict(ValueError):
    pass


def local_time(value=None):
    if value is None:
        return datetime.now(CHINA)
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return (value.replace(tzinfo=CHINA) if value.tzinfo is None else value).astimezone(CHINA)


def period(day=None):
    try:
        day = date.fromisoformat(day) if day else local_time().date()
    except (TypeError, ValueError):
        raise ValueError('请选择有效日期（YYYY-MM-DD）')
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def actor(c, user_id, publish=False):
    row = c.execute('SELECT id,name,role FROM user WHERE id=?', (user_id,)).fetchone()
    if not row or row['role'] not in (('manager',) if publish else ('manager', 'pm')):
        raise PermissionError('仅部门负责人可执行此操作' if publish else '仅部门负责人和管理 PM 可查看或编辑部门周报')
    return dict(row)


def write_lock(c):
    if not c.in_transaction:
        c.execute('BEGIN IMMEDIATE')


def audit(c, report_id, action, user_id, stamp):
    c.execute('INSERT INTO audit_log(object_type,object_id,action,actor,created_at) VALUES(?,?,?,?,?)',
              ('weekly_report', report_id, action, str(user_id or 'system'), stamp))


def department_nodes(c):
    nodes = {r['id']: dict(r) for r in c.execute('SELECT * FROM org_node')}
    included = {i for i, r in nodes.items() if r['name'] == DEPARTMENT and r['node_type'] == 'department'}
    while True:
        more = {i for i, r in nodes.items() if r['parent_id'] in included} - included
        if not more:
            break
        included.update(more)
    return nodes, included


def build_snapshot(c, start, end, generated_at, owner_id=None):
    week_start = datetime.combine(start, time.min, CHINA)
    week_end = datetime.combine(end + timedelta(days=1), time.min, CHINA)
    cutoff = min(generated_at, week_end - timedelta(microseconds=1))
    next_end = week_end + timedelta(days=7)
    nodes, included = department_nodes(c)
    tasks = []
    query = """SELECT t.*,u.name AS owner_name,u.department AS owner_department
                         FROM task t LEFT JOIN user u ON u.id=t.owner_id
                         WHERE t.approval_status='approved'
                         AND t.work_status NOT IN ('draft','cancelled','deleted')"""
    if owner_id is not None:
        query += ' AND t.owner_id=?'
    for r in c.execute(query + ' ORDER BY t.id', () if owner_id is None else (owner_id,)):
        t = dict(r)
        if owner_id is None and t['org_node_id'] not in included and not (t['org_node_id'] is None and t['owner_department'] == DEPARTMENT):
            continue
        created = local_time(t['created_at'])
        confirmed = local_time(t['confirmed_at']) if t.get('confirmed_at') else None
        if not created or created > cutoff or (confirmed and confirmed > cutoff):
            continue
        domain = nodes.get(t['org_node_id'])
        seen = set()
        while domain and domain['node_type'] != 'domain' and domain['id'] not in seen:
            seen.add(domain['id'])
            domain = nodes.get(domain['parent_id'])
        t['domain_name'] = domain['name'] if domain and domain['node_type'] == 'domain' else '未归类'
        t['domain_id'] = domain['id'] if domain and domain['node_type'] == 'domain' else None
        progress = [dict(p) for p in c.execute('SELECT * FROM progress_record WHERE task_id=? ORDER BY reported_at,id', (t['id'],))
                    if local_time(p['reported_at']) and local_time(p['reported_at']) <= cutoff]
        accepted = [dict(a) for a in c.execute('SELECT * FROM acceptance WHERE task_id=? ORDER BY created_at,id', (t['id'],))
                    if local_time(a['created_at']) and local_time(a['created_at']) <= cutoff]
        progress.sort(key=lambda p: (local_time(p['reported_at']), p['id']))
        accepted.sort(key=lambda a: (local_time(a['created_at']), a['id']))
        completed_times = [local_time(a['created_at']) for a in accepted if a['result'] == 'passed']
        completed_times += [local_time(p['confirmed_at']) for p in progress
                            if p['status'] in ('approved', 'auto_approved') and p['effective_percent'] == 100
                            and p['confirmed_at'] and local_time(p['confirmed_at']) and local_time(p['confirmed_at']) <= cutoff]
        completed_at = max(completed_times) if completed_times else None
        # Legacy tasks have no separate completion event. Use their recorded
        # update time only as an explicitly labelled fallback, never generation time.
        completion_source = '验收或进度确认'
        if t['work_status'] == 'completed' and not completed_at:
            completed_at = local_time(t['updated_at'])
            completion_source = '历史任务更新时间'
        if t['work_status'] != 'completed' or not completed_at or completed_at > cutoff:
            completed_at = None
        t['completed_at'] = completed_at.isoformat() if completed_at else None
        t['completion_source'] = completion_source if completed_at else ''
        if t['work_status'] == 'completed' and not completed_at:
            t['work_status'] = 'in_progress'
        latest = progress[-1] if progress else None
        t['latest_progress'] = latest['report_text'] if latest else ''
        t['latest_progress_at'] = latest['reported_at'] if latest else None
        t['block_reason'] = latest['block_reason'] if latest and latest['is_blocked'] else ''
        t['reported_this_week'] = any(local_time(p['reported_at']) >= week_start for p in progress)
        due = local_time(t['due_at'])
        active = t['work_status'] != 'completed'
        t['overdue'] = bool(active and due and due < cutoff)
        t['completed_this_week'] = bool(completed_at and week_start <= completed_at < week_end)
        # A task becomes a formal new task when confirmed, if that time exists.
        t['new_this_week'] = week_start <= (confirmed or created) < week_end
        reasons = []
        if t['overdue']:
            reasons.append('逾期未完成')
        if active and (t['risk_status'] != 'normal' or t['block_reason']):
            reasons.append('存在风险或阻塞')
        if active and t['priority'] in ('P0', 'P1') and due and cutoff <= due < cutoff + timedelta(days=7):
            reasons.append('未来 7 天重点任务到期')
        waiting_since = local_time(t['updated_at'])
        if t['work_status'] == 'awaiting_acceptance':
            submissions = []
            for event in c.execute("SELECT action,after_json,created_at FROM audit_log WHERE object_type='task' AND object_id=?", (t['id'],)):
                stamp = local_time(event['created_at'])
                try:
                    changes = json.loads(event['after_json'] or '{}')
                except (ValueError, TypeError):
                    changes = {}
                if stamp and stamp <= cutoff and (event['action'] == 'submit_acceptance' or
                    (isinstance(changes, dict) and changes.get('work_status') == 'awaiting_acceptance')):
                    submissions.append(stamp)
            if submissions:
                waiting_since = max(submissions)
        if t['work_status'] == 'awaiting_acceptance' and waiting_since and cutoff - waiting_since > timedelta(hours=48):
            reasons.append('待验收超过 48 小时')
        if active and t['priority'] in ('P0', 'P1') and not t['reported_this_week']:
            reasons.append('本周无进展填报')
        t['risk_reasons'] = reasons
        t['suggested_action'] = ('协调责任人与资源，明确解除阻塞计划' if t['block_reason'] or (active and t['risk_status'] != 'normal') else
                                 '确认补救计划与新的交付安排' if t['overdue'] else
                                 '请验收人安排验收并反馈结论' if '待验收超过 48 小时' in reasons else
                                 '请责任人补充进展与交付计划' if '本周无进展填报' in reasons else '跟进交付准备与验收安排')
        t['next_week'] = bool(active and t['priority'] in ('P0', 'P1') and
                              ((due and week_end <= due < next_end) or t['work_status'] == 'in_progress'))
        t['accepted'] = bool(accepted and accepted[-1]['result'] == 'passed')
        # Keep only meeting facts, never mutable database rows or personal IDs.
        keys = ('id', 'title', 'owner_id', 'owner_name', 'org_node_id', 'domain_id', 'domain_name', 'due_at', 'priority',
                'work_status', 'risk_status', 'deliverable', 'estimated_hours', 'actual_hours', 'complexity',
                'completed_at', 'completion_source', 'latest_progress', 'latest_progress_at', 'block_reason',
                'reported_this_week', 'overdue', 'completed_this_week', 'new_this_week', 'risk_reasons',
                'suggested_action', 'next_week', 'accepted')
        tasks.append({k: t.get(k) for k in keys})
    completed = [t for t in tasks if t['completed_this_week']]
    sections = {
        'achievements': [t['id'] for t in sorted(tasks, key=lambda t: (t['domain_name'], t['priority'], t['id']))
                         if (t['priority'] == 'P0' and t['work_status'] != 'completed') or t['completed_this_week']],
        'risks': [t['id'] for t in tasks if t['risk_reasons']],
        'next_week': [t['id'] for t in sorted(tasks, key=lambda t: (t['priority'], t['due_at'], t['id'])) if t['next_week']],
    }
    metrics = {
        'new': [t['id'] for t in tasks if t['new_this_week']],
        'completed': [t['id'] for t in completed],
        'in_progress': [t['id'] for t in tasks if t['work_status'] == 'in_progress'],
        'overdue': [t['id'] for t in tasks if t['overdue']],
        'awaiting_acceptance': [t['id'] for t in tasks if t['work_status'] == 'awaiting_acceptance'],
        'domains': [t['id'] for t in tasks if t['domain_id']],
    }
    hours = {'standard': round(sum(float(t['estimated_hours'] or 0) for t in completed), 1),
             'actual': round(sum(float(t['actual_hours'] or 0) for t in completed), 1)}
    highlights = [t['title'] for t in completed if t['id'] in sections['achievements']][:2]
    summary = f"本周新增正式任务 {len(metrics['new'])} 项、完成 {len(completed)} 项"
    if highlights:
        summary += '，重点成果包括' + '、'.join(highlights)
    summary += f"；当前进行中 {len(metrics['in_progress'])} 项，需关注逾期 {len(metrics['overdue'])} 项、待验收 {len(metrics['awaiting_acceptance'])} 项。"
    return {'title': DEPARTMENT + '部门工作总结报告', 'department': DEPARTMENT,
            'period_start': start.isoformat(), 'period_end': end.isoformat(),
            'data_cutoff': cutoff.isoformat(), 'tasks': tasks, 'sections': sections, 'metric_tasks': metrics,
            'metrics': {**{k: len(v) for k, v in metrics.items()},
                        'domains': len({t['domain_id'] for t in tasks if t['domain_id']}), 'completed_hours': hours},
            'summary': summary, 'rules': {'acceptance_threshold_hours': 48,
             'achievements': '展示未完成的 P0 重点任务，以及本周已完成的全部优先级任务。',
             'completion_time': '以验收通过或 100% 进度确认时间统计；无事件记录的历史任务使用更新时间。',
             'cutoff': '按生成时可见的正式任务数据生成；周日 18:00 后的变更须重新生成草稿才纳入。'}}


def report_view(row):
    out = dict(row)
    if out['status'] == 'reviewing':
        out['status'] = 'pending'
    out['snapshot'] = json.loads(out.pop('snapshot_json'))
    out['editorial'] = json.loads(out.pop('editorial_json'))
    out['status_label'] = STATUSES[out['status']]
    return out


def get_report(c, report_id, user_id):
    actor(c, user_id)
    row = c.execute('SELECT * FROM weekly_report WHERE id=?', (report_id,)).fetchone()
    if not row:
        raise LookupError('周报不存在')
    return report_view(row)


def list_reports(c, user_id, year=None, month=None, week=None):
    user = actor(c, user_id)
    filters = {}
    for key, value, maximum in (('year', year, 9999), ('month', month, 12), ('week', week, 53)):
        if value not in (None, ''):
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValueError('年份、月份与周次须为数字')
            if not 1 <= value <= maximum:
                raise ValueError('年份、月份或周次超出范围')
            filters[key] = value
    items = []
    for r in c.execute('SELECT * FROM weekly_report ORDER BY period_start DESC,version DESC'):
        d = date.fromisoformat(r['period_start'])
        values = {'year': d.isocalendar().year if 'week' in filters else d.year, 'month': d.month, 'week': d.isocalendar().week}
        if any(values[k] != v for k, v in filters.items()):
            continue
        item = {k: r[k] for k in ('id', 'period_start', 'period_end', 'version', 'status', 'generated_at', 'published_at', 'publisher_name', 'revision_of')}
        if item['status'] == 'reviewing': item['status'] = 'pending'
        items.append(item)
    monday, _ = period()
    pending = [dict(r) for r in c.execute("SELECT id,period_start,version FROM weekly_report WHERE status IN ('pending','reviewing') ORDER BY period_start DESC")]
    return {'items': items, 'can_publish': user['role'] == 'manager', 'current_period_start': monday.isoformat(), 'pending': pending}


def generate_report(c, user_id=None, day=None, generated_at=None, automatic=False, create_if_missing=False):
    if not automatic:
        actor(c, user_id, publish=not create_if_missing)
    current = local_time(generated_at)
    start, end = period(day or current.date().isoformat())
    if start > current.date():
        raise ValueError('不能生成未来周的报告')
    write_lock(c)
    if automatic:
        existing = c.execute('SELECT report_id FROM weekly_report_job WHERE period_start=?', (start.isoformat(),)).fetchone()
        if existing:
            return report_view(c.execute('SELECT * FROM weekly_report WHERE id=?', (existing['report_id'],)).fetchone())
        existing = c.execute('SELECT * FROM weekly_report WHERE period_start=? ORDER BY version DESC LIMIT 1', (start.isoformat(),)).fetchone()
        if existing:
            c.execute('INSERT INTO weekly_report_job VALUES(?,?,?)', (start.isoformat(), existing['id'], current.isoformat()))
            if existing['status'] in ('pending', 'reviewing'):
                return refresh_report(c, existing['id'], None, existing['edit_revision'], automatic=True, refreshed_at=current)
            return report_view(existing)
    previous = c.execute('SELECT * FROM weekly_report WHERE period_start=? ORDER BY version DESC LIMIT 1', (start.isoformat(),)).fetchone()
    if create_if_missing and previous:
        return report_view(previous)
    published = c.execute("SELECT id FROM weekly_report WHERE period_start=? AND status='published' ORDER BY version DESC LIMIT 1", (start.isoformat(),)).fetchone()
    c.execute("UPDATE weekly_report SET status='superseded' WHERE period_start=? AND status IN ('pending','reviewing')", (start.isoformat(),))
    snapshot = build_snapshot(c, start, end, current)
    # Preserve optional management conclusions on regeneration; task-specific
    # notes and order are reset because the selected task set may have changed.
    editorial = {'summary': snapshot['summary'], 'conclusion': '', 'requirements': '', 'support': '', 'notes': {}, 'orders': {}}
    if previous:
        old = json.loads(previous['editorial_json'])
        for key in ('conclusion', 'requirements', 'support'):
            editorial[key] = old.get(key, '')
    report_id = c.execute('''INSERT INTO weekly_report(period_start,period_end,version,snapshot_json,editorial_json,
                            generated_at,generated_by,updated_at,updated_by,revision_of,source)
                            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                         (start.isoformat(), end.isoformat(), previous['version'] + 1 if previous else 1,
                          json.dumps(snapshot, ensure_ascii=False), json.dumps(editorial, ensure_ascii=False),
                          current.isoformat(), user_id, current.isoformat(), user_id, published['id'] if published else None,
                          'automatic' if automatic else ('on_open' if create_if_missing else 'manual'))).lastrowid
    if automatic:
        c.execute('INSERT INTO weekly_report_job VALUES(?,?,?)', (start.isoformat(), report_id, current.isoformat()))
    audit(c, report_id, 'generate', user_id, current.isoformat())
    return report_view(c.execute('SELECT * FROM weekly_report WHERE id=?', (report_id,)).fetchone())


def update_report(c, report_id, user_id, changes):
    if not isinstance(changes, dict):
        raise ValueError('请求须为 JSON 对象')
    user = actor(c, user_id, publish=changes.get('action') == 'publish')
    allowed = {'action', 'edit_revision', 'summary', 'conclusion', 'requirements', 'support', 'notes', 'orders'}
    if set(changes) - allowed:
        raise ValueError('只能编辑摘要、管理总结、展示顺序和补充说明，不能修改统计数据')
    if changes.get('action', 'save') not in ('save', 'review', 'publish'):
        raise ValueError('不支持的周报操作')
    write_lock(c)
    report = get_report(c, report_id, user_id)
    if report['status'] not in ('pending', 'reviewing'):
        raise ReportConflict('该版本已固化，请由部门负责人重新生成修订草稿')
    if type(changes.get('edit_revision')) is not int or changes['edit_revision'] != report['edit_revision']:
        raise ReportConflict('周报已被其他人编辑，请重新加载后再保存')
    editorial = report['editorial']
    for key in ('summary', 'conclusion', 'requirements', 'support'):
        if key in changes:
            if not isinstance(changes[key], str) or len(changes[key]) > 10000:
                raise ValueError('摘要或总结须为不超过 10000 字的文本')
            editorial[key] = changes[key]
    if 'notes' in changes:
        notes = changes['notes']
        ids = {str(t['id']) for t in report['snapshot']['tasks']}
        if not isinstance(notes, dict) or any(k not in ids or not isinstance(v, str) or len(v) > 4000 for k, v in notes.items()):
            raise ValueError('补充说明须关联本周报内的任务且不超过 4000 字')
        editorial['notes'] = notes
    if 'orders' in changes:
        orders = changes['orders']
        if not isinstance(orders, dict) or set(orders) - set(report['snapshot']['sections']):
            raise ValueError('展示顺序无效')
        for section, ids in orders.items():
            if not isinstance(ids, list) or any(type(i) is not int for i in ids) or sorted(ids) != sorted(report['snapshot']['sections'][section]):
                raise ValueError('只能调整已有条目的顺序，不能增删统计任务')
        editorial['orders'] = orders
    stamp = local_time().isoformat()
    status = 'published' if changes.get('action') == 'publish' else 'pending'
    c.execute('''UPDATE weekly_report SET editorial_json=?,status=?,updated_at=?,updated_by=?,edit_revision=edit_revision+1,
                 published_at=?,published_by=?,publisher_name=? WHERE id=?''',
              (json.dumps(editorial, ensure_ascii=False), status, stamp, user_id,
               stamp if status == 'published' else None, user_id if status == 'published' else None,
               user['name'] if status == 'published' else None, report_id))
    audit(c, report_id, status, user_id, stamp)
    return get_report(c, report_id, user_id)


def snapshot_changes(old, new):
    before = {t['id']: t for t in old['tasks']}
    after = {t['id']: t for t in new['tasks']}
    detail = lambda t: {key: t[key] for key in ('id', 'title', 'owner_name')}
    result = {
        'added': [detail(t) for tid, t in after.items() if tid not in before],
        'removed': [detail(t) for tid, t in before.items() if tid not in after],
        'changed': [detail(t) for tid, t in after.items() if tid in before and t != before[tid]],
        'metrics_changed': old['metrics'] != new['metrics'],
        'sections_changed': old['sections'] != new['sections'],
        'rules_changed': old.get('rules') != new.get('rules'),
    }
    result['has_changes'] = any(result.values())
    return result


def report_changes(c, report_id, user_id):
    report = get_report(c, report_id, user_id)
    start, end = period(report['period_start'])
    fresh = build_snapshot(c, start, end, local_time())
    return snapshot_changes(report['snapshot'], fresh)


def refresh_report(c, report_id, user_id, edit_revision, automatic=False, refreshed_at=None):
    if not automatic:
        actor(c, user_id)
    write_lock(c)
    row = c.execute('SELECT * FROM weekly_report WHERE id=?', (report_id,)).fetchone()
    if not row:
        raise LookupError('周报不存在')
    report = report_view(row)
    if report['status'] != 'pending':
        raise ReportConflict('已发布周报不能更新任务数据，请先建立修订稿')
    if type(edit_revision) is not int or edit_revision != report['edit_revision']:
        raise ReportConflict('周报已被其他人编辑，请重新加载后再更新任务数据')
    stamp = local_time(refreshed_at)
    start, end = period(report['period_start'])
    snapshot = build_snapshot(c, start, end, stamp)
    changes = snapshot_changes(report['snapshot'], snapshot)
    old_editorial = report['editorial']
    editorial = json.loads(json.dumps(old_editorial))
    if editorial.get('summary') == report['snapshot']['summary']:
        editorial['summary'] = snapshot['summary']
    task_ids = {str(t['id']) for t in snapshot['tasks']}
    editorial['notes'] = {key: value for key, value in editorial.get('notes', {}).items() if key in task_ids}
    editorial['orders'] = {}
    for section, ids in snapshot['sections'].items():
        old_order = old_editorial.get('orders', {}).get(section, report['snapshot']['sections'][section])
        kept = [tid for tid in old_order if tid in ids]
        editorial['orders'][section] = kept + [tid for tid in ids if tid not in kept]
    snapshot['change_summary'] = changes
    c.execute('''UPDATE weekly_report SET snapshot_json=?,editorial_json=?,updated_at=?,updated_by=?,
                 status='pending',edit_revision=edit_revision+1 WHERE id=?''',
              (json.dumps(snapshot, ensure_ascii=False), json.dumps(editorial, ensure_ascii=False), stamp.isoformat(), user_id, report_id))
    # Retain the replaced draft facts in the audit trail without introducing
    # another user-visible report version for every data refresh.
    c.execute('''INSERT INTO audit_log(object_type,object_id,action,actor,before_json,after_json,created_at)
                 VALUES('weekly_report',?,'refresh_data',?,?,?,?)''',
              (report_id, str(user_id or 'system'), json.dumps({'snapshot': report['snapshot'], 'editorial': old_editorial}, ensure_ascii=False),
               json.dumps(changes, ensure_ascii=False), stamp.isoformat()))
    return report_view(c.execute('SELECT * FROM weekly_report WHERE id=?', (report_id,)).fetchone())


def revise_report(c, report_id, user_id):
    actor(c, user_id, publish=True)
    write_lock(c)
    published = get_report(c, report_id, user_id)
    if published['status'] != 'published':
        raise ReportConflict('只能从已发布周报建立修订稿')
    existing = c.execute("SELECT * FROM weekly_report WHERE period_start=? AND status IN ('pending','reviewing')", (published['period_start'],)).fetchone()
    if existing:
        return report_view(existing)
    latest = c.execute("SELECT id FROM weekly_report WHERE period_start=? AND status='published' ORDER BY version DESC LIMIT 1", (published['period_start'],)).fetchone()
    if latest['id'] != report_id:
        raise ReportConflict('该周已有更新的发布版本，请从最新版本建立修订稿')
    version = c.execute('SELECT max(version)+1 FROM weekly_report WHERE period_start=?', (published['period_start'],)).fetchone()[0]
    stamp = local_time().isoformat()
    new_id = c.execute('''INSERT INTO weekly_report(period_start,period_end,version,snapshot_json,editorial_json,
                         generated_at,generated_by,updated_at,updated_by,revision_of,source)
                         VALUES(?,?,?,?,?,?,?,?,?,?,'revision')''',
                       (published['period_start'], published['period_end'], version, json.dumps(published['snapshot'], ensure_ascii=False),
                        json.dumps(published['editorial'], ensure_ascii=False), stamp, user_id, stamp, user_id, report_id)).lastrowid
    audit(c, new_id, 'revise', user_id, stamp)
    return get_report(c, new_id, user_id)


def run_due(c, local_now=None):
    current = local_time(local_now)
    # Catch up the last due Sunday after downtime, even on Monday. One durable
    # marker per week prevents repeated jobs from replacing an edited draft.
    days = (current.weekday() - 6) % 7
    sunday = current.date() - timedelta(days=days)
    due = datetime.combine(sunday, time(18), CHINA)
    if current < due:
        sunday -= timedelta(days=7)
    start, _ = period(sunday.isoformat())
    if c.execute('SELECT 1 FROM weekly_report_job WHERE period_start=?', (start.isoformat(),)).fetchone():
        return {'generated': 0}
    report = generate_report(c, day=sunday.isoformat(), generated_at=current, automatic=True)
    return {'generated': 1, 'report_id': report['id'], 'period_start': start.isoformat()}


def meeting_text(report):
    s, e = report['snapshot'], report['editorial']
    metrics = s['metrics']
    lines = [s['title'], f"报告周期：{s['period_start']}—{s['period_end']}｜V{report['version']}｜{report['status_label']}",
             f"生成时间：{report['generated_at']}｜数据截至：{s['data_cutoff']}",
             f"发布人：{report['publisher_name'] or '尚未发布'}｜发布时间：{report['published_at'] or '—'}", '', e.get('summary', s['summary']), '', '一、本周任务概览',
             '｜'.join(f'{label} {metrics[key]} 项' for key, label in (('new', '新增'), ('completed', '完成'), ('in_progress', '进行中'), ('overdue', '逾期'), ('awaiting_acceptance', '待验收'))),
             f"本周完成标准工时 {metrics['completed_hours']['standard']}h / 实际工时 {metrics['completed_hours']['actual']}h；责任域覆盖 {metrics['domains']} 个。"]
    tasks = {t['id']: t for t in s['tasks']}
    for key, title in (('achievements', '二、本周重点工作与成果'), ('risks', '三、任务进展与风险'), ('next_week', '四、下周重点计划')):
        lines += ['', title]
        ids = e.get('orders', {}).get(key, s['sections'][key])
        if not ids:
            lines.append('暂无符合条件的事项。')
        last_domain = None
        for task_id in ids:
            t = tasks[task_id]
            if key == 'achievements' and t['domain_name'] != last_domain:
                lines.append(t['domain_name'])
                last_domain = t['domain_name']
            lines.append(f"- #{t['id']} {t['title']}｜{t['owner_name'] or '未知责任人'}｜{t['domain_name']}｜{t['priority']}｜{WORK.get(t['work_status'], t['work_status'])}｜截止 {t['due_at'][:10]}")
            lines.append('  交付物/验收标准：' + (t['deliverable'] or '未填写'))
            if key == 'risks':
                lines += ['  关注原因：' + '；'.join(t['risk_reasons']), '  风险/最后进展：' + (t['block_reason'] or t['latest_progress'] or '暂无进展记录'), '  建议动作：' + t['suggested_action']]
            if e.get('notes', {}).get(str(task_id)):
                lines.append('  补充说明：' + e['notes'][str(task_id)])
    lines += ['', '五、负责人总结']
    for key, label in (('conclusion', '本周管理结论'), ('requirements', '下周工作要求'), ('support', '需上级或其他部门支持')):
        lines.append(label + '：' + (e.get(key) or '未补充'))
    return '\n'.join(lines)
