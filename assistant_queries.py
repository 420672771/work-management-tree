"""Read-only task queries with explicit scope and short-lived conversational filters."""
import re
from datetime import datetime, timedelta, timezone

CHINA = timezone(timedelta(hours=8))
STATUSES = {'assigned': '待开始', 'in_progress': '进行中', 'awaiting_acceptance': '待验收',
            'completed': '已完成', 'cancelled': '已取消'}


def options(text, api):
    source = re.sub(r'\s+', '', str(text or '')).lower()
    # A query must never consume draft corrections, progress reports or mutations.
    if (re.search(r'新建|新增|创建|添加|修改|改为|改成|调整|变更|删除|移除|作废|提交|设置|配置', source)
            or re.search(r'\d+[%％]', source) or len(api.assistant_dialogue.labelled_fields(text)) >= 2):
        return None
    scope = None
    if re.search(r'我的|本人|个人|我负责|我手上|我(?:今天|当前|现在|都|还)?(?:有哪些|有什么|要做|待办|任务)', source):
        scope = 'self'
    elif re.search(r'部门|全体|全员|所有人|所有成员|所有员工|全部人员|大家|全局|(?:所有|全部).*任务', source):
        scope = 'department'
    elif re.search(r'团队|下属|组员|直属', source):
        scope = 'team'
    query = (api.is_task_query_request(source) or api.is_department_progress_query(source)
             or api.is_team_progress_query(source)
             or bool(scope and re.search(r'任务|待办|事项|工作', source) and re.search(r'看|查|列|汇总|概况|进展|进度|情况', source))
             or bool(re.fullmatch(r'(?:部门|团队|全体|所有人(?:的)?|我的)(?:任务|待办|事项|工作)(?:清单|列表)?[。！？!?]*', source)))
    # Bare scope/role corrections can refine a recent query, but are not queries
    # on their own. The actual database role is checked again before any read.
    correction = bool(re.fullmatch(r'(?:不是我的[，,。]?|我要看|看|查看|查询|只看|换成|改看|那就看)?'
                                   r'(?:全部门|部门全部|部门|所有人(?:的)?|全部人(?:的)?|全体|全员|大家(?:的)?|团队|下属|我的|本人)[。！？!?]*', source)
                      or re.fullmatch(r'我(?:是|的角色是)(?:管理)?pm[，,。！？!?]*', source))
    if correction and scope is None and 'pm' in source:
        scope = 'department'
    if not query and not correction:
        return None
    state = 'active'
    if re.search(r'所有状态|全部状态|包括已完成', source):
        state = 'all'
    elif '已完成' in source:
        state = 'completed'
    elif '待验收' in source:
        state = 'awaiting_acceptance'
    due_today = bool(re.search(r'(?:今天|今日)(?:到期|截止)|(?:到期|截止)(?:日期)?(?:是|为|在)?(?:今天|今日)', source))
    return {'scope': scope, 'state': state, 'due_today': due_today,
            'summary': bool(re.search(r'汇总|概况|总结|报表|进展|进度', source)), 'correction': correction,
            'scope_only': correction or bool(scope and re.search(r'我(?:是|的角色是).*pm|我(?:可以|能|有权限).*看|^(?:只看|改看|换成)', source)
                                              and not re.search(r'今天|今日|到期|截止|未完成|已完成|待验收|所有状态|全部状态|汇总|概况|总结|进度|进展', source))}


def reply(c, text, user_id, session_id, api, *, known_query=False):
    current = options(text, api)
    if current is None:
        if not known_query:
            return None
        current = {'scope': None, 'state': 'active', 'due_today': False, 'summary': False, 'scope_only': False, 'correction': False}
    previous = None
    for turn in api.assistant_dialogue.history(c, session_id, user_id):
        if turn['role'] != 'user':
            continue
        candidate = options(turn['content'], api)
        if candidate is None:
            previous = None
        elif candidate['correction'] and previous is None:
            continue
        elif candidate['scope_only'] and previous:
            previous = {**previous, 'scope': candidate['scope']}
        else:
            previous = candidate
    if current['correction'] and previous is None and not known_query:
        return None
    if current['scope_only'] and previous:
        current = {**previous, 'scope': current['scope']}
    if not user_id:
        return '你的飞书账号尚未绑定系统成员，暂不能查询任务。'
    actor = c.execute('SELECT role FROM user WHERE id=?', (user_id,)).fetchone()
    if not actor:
        return '未找到对应系统成员，请联系部门负责人或管理 PM 核对绑定。'
    managers = actor['role'] in ('manager', 'pm')
    people = api.managed_user_ids(c, user_id)
    scope = current['scope'] or ('department' if managers else 'team' if people else 'self')
    if scope == 'department' and not managers:
        return '部门任务查询仅对管理PM和部门负责人开放；系统当前角色没有此权限。可以查询“我的任务”或已授权的团队任务。'
    if scope == 'team' and not people:
        return '当前没有配置在你名下的直属成员，因此暂无可查询的团队任务。'
    # Preserve the established aggregate report for explicit summary requests.
    if current['summary'] and current['state'] == 'active' and not current['due_today']:
        if scope == 'department':
            return api.feishu_department_progress(c, user_id)
        if scope == 'team':
            return api.feishu_team_progress(c, user_id)
    conditions = ["t.approval_status='approved'", "t.work_status IN ('assigned','in_progress','awaiting_acceptance','completed','cancelled')"]
    params = []
    if scope != 'department':
        owners = people if scope == 'team' else [user_id]
        conditions.append('t.owner_id IN (' + ','.join('?' for _ in owners) + ')')
        params.extend(owners)
    if current['state'] == 'active':
        conditions.append("t.work_status NOT IN ('completed','cancelled')")
    elif current['state'] != 'all':
        conditions.append('t.work_status=?')
        params.append(current['state'])
    rows = c.execute('SELECT t.id,t.title,t.due_at,t.owner_id,t.work_status,t.effective_percent,u.name AS owner_name '
                     'FROM task t LEFT JOIN user u ON u.id=t.owner_id WHERE ' + ' AND '.join(conditions) + ' ORDER BY t.due_at,t.id', params).fetchall()
    today = datetime.fromisoformat(api.now()).astimezone(CHINA).date()
    def due_date(row):
        try:
            value = datetime.fromisoformat(row['due_at'].replace('Z', '+00:00'))
            return (value.replace(tzinfo=CHINA) if value.tzinfo is None else value).astimezone(CHINA).date()
        except (ValueError, TypeError, AttributeError):
            return None
    if current['due_today']:
        rows = [row for row in rows if due_date(row) == today]
    labels = {'self': '我的', 'team': '团队', 'department': '部门'}
    state = {'active': '待办', 'all': '任务', 'completed': '已完成任务', 'awaiting_acceptance': '待验收任务'}[current['state']]
    heading = f'【{labels[scope]}{state}】'
    bounds = ('今日截止｜' if current['due_today'] else f'截至 {today}｜') + {'active': '已生效且未完成', 'all': '已生效的全部状态', 'completed': '已生效且已完成', 'awaiting_acceptance': '已生效且待验收'}[current['state']]
    if not rows:
        personal = '你当前没有待完成任务。' if scope == 'self' and current['state'] == 'active' and not current['due_today'] else '当前范围内没有符合条件的任务。'
        return heading + '\n' + bounds + '\n' + personal
    if scope == 'self':
        api.save_bot_task_context(c, session_id, user_id, rows[0]['id'])
    items = []
    for row in rows[:15]:
        due = due_date(row)
        tag = '｜今日截止' if due == today else '｜逾期' if due and due < today and row['work_status'] not in ('completed', 'cancelled') else ''
        progress = f'｜已确认进度 {row["effective_percent"]}%' if row['effective_percent'] is not None else ''
        items.append(f'#{row["id"]}《{row["title"]}》｜{row["owner_name"] or "未知责任人"}｜{STATUSES[row["work_status"]]}{progress}｜截止 {due or "未填写"}{tag}')
    tail = '\n仅展示前15项，其余请在网页任务列表查看。' if len(rows) > 15 else ''
    return heading + '\n' + bounds + f'｜共 {len(rows)} 项\n' + '\n'.join(items) + tail
