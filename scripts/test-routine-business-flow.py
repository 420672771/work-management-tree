#!/usr/bin/env python3
"""Exercise real account/API workflows and retain labelled routine demonstration data.

Run first against a copy with --database; --retain explicitly permits adding the
same labelled fixture to the website database. Existing users/tasks are not edited.
"""
import argparse
import html
import http.cookiejar
import json
import os
import sqlite3
import sys
import threading
from datetime import date, datetime, timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPCookieProcessor

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import accounts
import routines

PREFIX = '[例行演练]'
STAFF = [('manager', '演练·部门负责人', 'routine.manager', 'manager'),
         ('pm', '演练·管理PM', 'routine.pm', 'pm'),
         ('ops', '演练·系统运维', 'routine.ops', 'member'),
         ('req', '演练·需求管理', 'routine.req', 'member'),
         ('dev', '演练·需求开发', 'routine.dev', 'member'),
         ('it', '演练·网络桌面', 'routine.it', 'member')]


class QuietHandler(app.Handler):
    def log_message(self, *_):
        pass


class Client:
    def __init__(self, base):
        self.base = base
        self.http = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def call(self, path, body=None):
        request = Request(self.base + path, None if body is None else json.dumps(body).encode(),
                          {'Content-Type': 'application/json'} if body is not None else {})
        try:
            with self.http.open(request, timeout=20) as response:
                return response.status, json.loads(response.read())
        except HTTPError as exc:
            return exc.code, json.loads(exc.read())


def run(database, output, retain):
    database = database.resolve(); output.mkdir(parents=True, exist_ok=True)
    live = (ROOT / 'data/worktree.db').resolve()
    if database == live and not retain:
        raise ValueError('网站数据库须显式指定 --retain；默认应使用隔离副本')
    if not database.exists():
        raise ValueError('请先提供现有测试数据库的副本')
    # The fixture accounts have no IM bindings; the runner also has no external credentials.
    for key in ('FEISHU_APP_ID', 'FEISHU_APP_SECRET', 'FEISHU_VERIFICATION_TOKEN',
                'WECOM_CORP_ID', 'WECOM_AGENT_ID', 'WECOM_SECRET', 'AI_API_KEY', 'WMT_PUBLIC_USER', 'WMT_PUBLIC_PASSWORD'):
        os.environ[key] = ''
    os.environ.update(WMT_AUTH_MODE='account', WMT_MONTHLY_DEMO='0')
    app.DB_PATH = database
    app.initialize()
    c = app.db()
    if c.execute("SELECT 1 FROM user_account WHERE username LIKE 'routine.%'").fetchone():
        raise ValueError('本数据库已有本批演练账号，拒绝覆盖；请保留数据或使用新的隔离副本')
    original = {table: [dict(r) for r in c.execute('SELECT * FROM ' + table + ' ORDER BY 1')]
                for table in ('user', 'user_account', 'task', 'task_template')}
    uid, usernames = {}, {}
    for key, name, username, role in STAFF:
        uid[key] = c.execute('INSERT INTO user(name,department,role) VALUES(?,?,?)',
                             (name, '科技产品中心', role)).lastrowid
        accounts.configure(c, uid[key], username, None, 'routine-business-test')
        usernames[key] = username
    dept = c.execute('SELECT id FROM org_department ORDER BY id LIMIT 1').fetchone()[0]
    for key, name, _, role in STAFF:
        position = c.execute('INSERT INTO org_position(name,dept_id,default_role) VALUES(?,?,?)',
                              (PREFIX + name.removeprefix('演练·'), dept, role)).lastrowid
        c.execute('INSERT INTO user_position(user_id,position_id,is_primary,valid_from) VALUES(?,?,1,?)',
                  (uid[key], position, app.now()))
        if key != 'manager':
            c.execute('INSERT INTO reporting_line(user_id,direct_leader_id,approver_id,effective_from) VALUES(?,?,?,?)',
                      (uid[key], uid['manager'], uid['manager'], app.now()))
    c.commit()
    manifest = {'database': str(database), 'prefix': PREFIX, 'users': uid, 'accounts': usernames,
                'templates': {}, 'tasks': {}, 'checks': [], 'performance': {}}
    (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    server = ThreadingHTTPServer(('127.0.0.1', 0), QuietHandler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .1}, daemon=True)
    thread.start()
    base = 'http://127.0.0.1:' + str(server.server_port)
    clients = {key: Client(base) for key in uid}

    def check(name, condition, detail=''):
        manifest['checks'].append({'name': name, 'passed': bool(condition), 'detail': detail})
        if not condition:
            raise AssertionError(name + ': ' + str(detail))

    def api(key, path, body=None, expect=200):
        status, result = clients[key].call(path, body)
        check(f'{key} {path} → {expect}', status == expect, f'HTTP {status}' + (': ' + str(result.get('error')) if isinstance(result, dict) and result.get('error') else ''))
        return result

    def task(tid):
        return dict(c.execute('SELECT * FROM task WHERE id=?', (tid,)).fetchone())

    def generate(week, ids):
        return api('manager', '/api/routine/generate', {'instance_date': week, 'template_ids': ids})['generated_task_ids']

    def status(key, tid, value, expect=200):
        return api(key, f'/api/tasks/{tid}', {'work_status': value}, expect)

    def hours(key, tid, value, expect=200):
        return api(key, f'/api/tasks/{tid}/hours', {'actual_hours': value}, expect)

    def finish(key, tid, actual, rejected=False):
        status(key, tid, 'in_progress'); hours(key, tid, actual)
        api(key, f'/api/tasks/{tid}/feedback', {'content': '本周交付记录已整理：检查完成、事项处理有记录，待验收。'})
        status(key, tid, 'awaiting_acceptance')
        if rejected:
            api('manager', f'/api/tasks/{tid}/accept', {'result': 'rejected', 'reason': '演练：交付记录缺少回归验证，补齐后重新提交'})
            check('验收驳回回到进行中', task(tid)['work_status'] == 'in_progress')
            api(key, f'/api/tasks/{tid}/feedback', {'content': '已补齐回归记录与复核结论。'})
            status(key, tid, 'awaiting_acceptance')
        api('manager', f'/api/tasks/{tid}/accept', {'result': 'passed', 'reason': '演练：已核对交付物、工时与处理记录，验收通过'})
        check('验收后完成且实际工时保存', task(tid)['work_status'] == 'completed' and task(tid)['actual_hours'] == actual)

    try:
        for key in clients:
            api(key, '/api/auth/login', {'username': usernames[key], 'password': accounts.DEFAULT_INITIAL_PASSWORD})
            check('真实账号登录身份正确：' + key, api(key, '/api/me')['user']['id'] == uid[key])
        domains = {r['domain_code']: r['id'] for r in c.execute("SELECT * FROM org_node WHERE domain_code IN ('B1','B2')")}
        specs = {'ops': ('核心系统日常运维', 16.5, '巡检、备份核验、接口异常处置与周结记录', 'B1'),
                 'req': ('核心系统需求管理', 12, '本周需求澄清、优先级清单与排期结论', 'B1'),
                 'dev': ('核心系统日常需求开发', 20, '本周约定需求交付、回归验证与验收记录', 'B1'),
                 'it': ('网络与桌面运维', 8, '网络检查、终端支持与遗留问题记录', 'B2')}
        payloads = {}
        for key, (title, standard, deliverable, domain) in specs.items():
            body = dict(title=PREFIX + title, owner_id=uid[key], org_node_id=domains[domain],
                        deliverable=deliverable, recurrence='WEEKLY', estimated_hours=standard)
            payloads[key] = body
            creator = 'pm' if key in ('req', 'dev') else 'manager'
            manifest['templates'][key] = api(creator, '/api/routine/templates', body)['id']
        ids = list(manifest['templates'].values())
        # Invalid fields must fail without adding partial templates.
        for value in (None, '', 0, -1, True, 'NaN', 'Infinity', 169):
            api('manager', '/api/routine/templates', {**payloads['ops'], 'estimated_hours': value}, 400)
        for fields in ({'title': ''}, {'deliverable': ''}, {'owner_id': 999999}, {'org_node_id': 999999}, {'recurrence': 'DAILY'}, {'priority': 'P9'}):
            api('pm', '/api/routine/templates', {**payloads['ops'], **fields}, 400)
        api('ops', '/api/routine/templates', {**payloads['ops'], 'actor_id': uid['manager']}, 403)
        api('ops', '/api/routine/templates/' + str(ids[0]), {**payloads['ops'], 'estimated_hours': 100, 'actor_id': uid['manager']}, 403)
        api('ops', '/api/routine/generate', {'instance_date': '2026-11-02', 'template_ids': ids, 'actor_id': uid['manager']}, 403)
        for week in ('2026-10-26', '2026-11-02'):
            new = generate(week, ids); check('每周每模板各一条：' + week, len(new) == 4)
            manifest['tasks'][week] = {key: c.execute('SELECT task_id FROM task_instance WHERE template_id=? AND instance_date=?', (template, week)).fetchone()[0] for key, template in manifest['templates'].items()}
            check('同周再次生成不重复', generate(week, ids) == [])
            check('同周其他日期不重复', generate((date.fromisoformat(week) + timedelta(days=6)).isoformat(), ids) == [])
        current = manifest['tasks']['2026-11-02']; previous = manifest['tasks']['2026-10-26']
        for key, tid in current.items():
            t = task(tid)
            check('生成即生效且待开始：' + key, t['approval_status'] == 'approved' and t['work_status'] == 'assigned')
            check('生成无任务确认审批：' + key, c.execute("SELECT count(*) FROM approval_record WHERE task_id=? AND biz_type='task_confirm'", (tid,)).fetchone()[0] == 0)
            check('模板工时准确带入：' + key, t['estimated_hours'] == specs[key][1])
        # Update weekly estimate/owner; already-generated snapshots must stay unchanged.
        api('pm', '/api/routine/templates/' + str(ids[0]), {**payloads['ops'], 'estimated_hours': 20})
        api('pm', '/api/routine/templates/' + str(ids[2]), {**payloads['dev'], 'owner_id': uid['req'], 'estimated_hours': 24})
        generate('2026-11-09', ids)
        next_tasks = {key: c.execute('SELECT task_id FROM task_instance WHERE template_id=? AND instance_date=?', (template, '2026-11-09')).fetchone()[0] for key, template in manifest['templates'].items()}
        manifest['tasks']['2026-11-09'] = next_tasks
        check('旧周标准工时不被模板更新覆盖', task(current['ops'])['estimated_hours'] == 16.5 and task(next_tasks['ops'])['estimated_hours'] == 20)
        check('换责任人只影响后续任务', task(current['dev'])['owner_id'] == uid['dev'] and task(next_tasks['dev'])['owner_id'] == uid['req'])
        api('pm', '/api/routine/templates/' + str(ids[2]), payloads['dev'])
        for week in ('2026-11-30', '2026-12-28'):
            check('跨月/跨年一周仍只生成一条：' + week, len(generate(week, ids)) == 4)
            manifest['tasks'][week] = {key: c.execute('SELECT task_id FROM task_instance WHERE template_id=? AND instance_date=?', (template, week)).fetchone()[0] for key, template in manifest['templates'].items()}
        # Owner/manager/PM can enter actual hours; other employees cannot forge actors.
        hours('req', current['ops'], 30, 403)
        api('req', f"/api/tasks/{current['ops']}/hours", {'actual_hours': 30, 'operator_id': uid['ops']}, 403)
        for value in (-1, 169, True, 'NaN', None):hours('ops', current['ops'], value, 400)
        for value in (18.5, 20, 0):
            hours('ops', current['ops'], value)
            check('累计工时替换而非累加', task(current['ops'])['actual_hours'] == value)
        hours('pm', current['ops'], 18.5)
        api('ops', f"/api/tasks/{current['ops']}", {'estimated_hours': 100}, 400)
        status('req', current['ops'], 'in_progress', 403)
        status('ops', current['ops'], 'completed', 400)
        api('ops', f"/api/tasks/{current['ops']}/accept", {'result': 'passed'}, 400)
        # Four distinct, inspectable business outcomes this week.
        finish('ops', current['ops'], 18.5)
        finish('req', current['req'], 10)
        status('dev', current['dev'], 'in_progress'); hours('dev', current['dev'], 22)
        api('dev', f"/api/tasks/{current['dev']}/feedback", {'content': '本周已交付需求，回归发现边界场景，准备修复。'})
        status('dev', current['dev'], 'awaiting_acceptance')
        api('pm', f"/api/tasks/{current['dev']}/accept", {'result': 'passed'}, 403)
        api('manager', f"/api/tasks/{current['dev']}/accept", {'result': 'rejected', 'reason': '演练：权限边界用例未通过，退回修复'})
        hours('it', current['it'], 0)
        # Historical week: late completion, blocker, 100% awaiting acceptance, rework then acceptance.
        finish('ops', previous['ops'], 20)
        status('req', previous['req'], 'in_progress'); hours('req', previous['req'], 8)
        progress = api('req', f"/api/tasks/{previous['req']}/progress", {'percent': 60, 'comment': '需求已澄清，等待业务方确认字段口径', 'blocked': True, 'block_reason': '业务方尚未确认结算口径'})
        api('manager', f"/api/approvals/{progress['approval_id']}/confirm", {'result': 'approved'})
        status('dev', previous['dev'], 'in_progress'); hours('dev', previous['dev'], 18)
        progress = api('dev', f"/api/tasks/{previous['dev']}/progress", {'percent': 100, 'comment': '开发与回归完成，提交验收'})
        api('manager', f"/api/approvals/{progress['approval_id']}/confirm", {'result': 'approved'})
        check('例行任务100%进度仍须验收', task(previous['dev'])['work_status'] == 'awaiting_acceptance')
        finish('it', previous['it'], 9, rejected=True)
        check('历史逾期任务显示逾期', api('req', f"/api/tasks/{previous['req']}")['time_status'] == 'overdue')
        # Compatibility fixture: unconfigured and inactive templates do not silently produce tasks.
        auxiliary = []
        for title, standard, state in [('旧模板缺少标准工时', 0, 'active'), ('停用模板', 4, 'inactive')]:
            auxiliary.append(c.execute('INSERT INTO task_template(title,owner_id,org_node_id,deliverable,recurrence,estimated_hours,status,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (PREFIX + title, uid['it'], domains['B2'], '兼容性演练记录', 'WEEKLY', standard, state, app.now())).lastrowid)
        c.commit(); check('缺工时与停用模板不生成', generate('2026-11-02', auxiliary) == [])
        # Delete a throwaway generated task and retain its template/audit evidence.
        deletion_template = api('pm', '/api/routine/templates', {**payloads['it'], 'title': PREFIX + '删除流程验证', 'estimated_hours': 1})['id']
        deletion_task = generate('2026-11-02', [deletion_template])[0]
        status('it', deletion_task, 'in_progress'); hours('it', deletion_task, 1)
        progress = api('it', f'/api/tasks/{deletion_task}/progress', {'percent': 30, 'comment': '删除前的关联进度验证'})
        api('it', f'/api/tasks/{deletion_task}/feedback', {'content': '删除前的关联反馈验证'})
        api('req', f'/api/tasks/{deletion_task}', {'action': 'delete'}, 403)
        api('it', f'/api/tasks/{deletion_task}', {'action': 'delete'})
        check('删除后任务及关联记录清理', all(c.execute('SELECT count(*) FROM ' + table + ' WHERE ' + field + '=?', (deletion_task,)).fetchone()[0] == 0 for table, field in [('task','id'),('progress_record','task_id'),('approval_record','task_id'),('feedback','task_id'),('acceptance','task_id')]))
        check('删除审计保留', c.execute("SELECT count(*) FROM audit_log WHERE object_type='task' AND object_id=? AND action='delete'", (deletion_task,)).fetchone()[0] == 1)
        check('已删除实例同周不悄悄重建', generate('2026-11-02', [deletion_template]) == [])
        c.execute("UPDATE task_template SET status='inactive' WHERE id IN (?,?,?)", (*auxiliary, deletion_template)); c.commit()
        manifest['auxiliary_templates'] = auxiliary + [deletion_template]
        manifest['deleted_task_id'] = deletion_task
        # Performance: individual, manager and PM, with independent arithmetic checks.
        for key in specs:
            report = api(key, '/api/performance/personal?start=2026-11-02&end=2026-11-08&user_id=' + str(uid[key]))
            manifest['performance'][key] = {'week': report, 'month': api(key, '/api/performance/personal?period=2026-11&user_id=' + str(uid[key]))}
        check('已填周工时无需日报也覆盖过程记录', manifest['performance']['ops']['week']['process'] == 100 and manifest['performance']['it']['week']['process'] == 100)
        check('正常交付周绩效为100分', manifest['performance']['ops']['week']['score'] == 100)
        check('待验收和驳回不计有效交付', manifest['performance']['dev']['week']['earned_points'] == 0)
        for key in specs:
            own = manifest['performance'][key]['week']
            check('标准/实际工时汇总准确：' + key, own['estimated_hours'] == specs[key][1] and own['actual_hours'] == task(current[key])['actual_hours'])
        late = api('manager', '/api/performance/personal?start=2026-10-26&end=2026-11-01&user_id=' + str(uid['ops']))
        check('逾期与返工影响交付点而非虚增工时', late['evidence'][0]['days_late'] >= 1 and late['evidence'][0]['earned_points'] < late['evidence'][0]['base_points'])
        before_score = manifest['performance']['ops']['week']['score']; hours('ops', current['ops'], 25)
        check('增加实际工时不直接提高绩效', api('ops', '/api/performance/personal?start=2026-11-02&end=2026-11-08')['score'] == before_score)
        hours('ops', current['ops'], 18.5)
        api('ops', '/api/performance/personal?user_id=' + str(uid['req']) + '&actor_id=' + str(uid['manager']), expect=403)
        for key in ('manager', 'pm'):
            team = api(key, '/api/performance/team?period=2026-11')
            check('管理人员可查看演练员工绩效：' + key, set(uid[k] for k in specs).issubset({r['user']['id'] for r in team['members']}))
            api(key, '/api/performance/department?period=2026-11')
        api('ops', '/api/performance/department?period=2026-11', expect=403)
        check('跨月任务按截止日期归入12月', task(manifest['tasks']['2026-11-30']['ops'])['due_at'].startswith('2026-12-06'))
        # Baseline preservation compares every pre-existing row, not just counts.
        for table, rows in original.items():
            check('既有数据保持不变：' + table, all(dict(c.execute('SELECT * FROM ' + table + ' WHERE ' + ('user_id' if table == 'user_account' else 'id') + '=?', (r['user_id'] if table == 'user_account' else r['id'],)).fetchone()) == r for r in rows))
        check('演练账号均无飞书绑定', all(not c.execute('SELECT feishu_open_id FROM user WHERE id=?', (i,)).fetchone()[0] for i in uid.values()))
        manifest['task_snapshots'] = {str(tid): task(tid) for group in manifest['tasks'].values() for tid in group.values()}
        manifest['completed_at'] = app.now(); manifest['passed'] = True
        manifest['notes'] = ['停用/缺工时兼容性场景使用明确标注的数据库夹具；当前页面尚无模板停用和删除按钮。',
                             '已生成任务可以删除；删除实例本周不会自动重建，删除操作和模板保留审计。',
                             '绩效按任务截止日期所在周期统计；跨月周工时不按日拆分。月中分数属于过程预览。',
                             '仅四个主要业务模板保持生效，辅助模板停用；全部演练账号未绑定飞书。']
    finally:
        for client in clients.values():
            client.call('/api/auth/logout', {})
        server.shutdown(); server.server_close(); thread.join(timeout=5)
        c.close()
        (output / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        render_report(manifest, output)
    return manifest


def render_report(result, output):
    esc = lambda v: html.escape(str(v))
    checks = result['checks']; passed = sum(x['passed'] for x in checks)
    labels = {'manager':'部门负责人','pm':'管理 PM','ops':'系统运维','req':'需求管理','dev':'需求开发','it':'网络与桌面运维'}
    rows = ''.join(f"<tr><td>{esc(labels.get(key,key))}</td><td>{esc(result['accounts'][key])}</td><td>{esc(uid)}</td></tr>" for key, uid in result['users'].items())
    cases = ''.join(f"<tr><td>{i+1}</td><td>{esc(x['name'])}</td><td>{'通过' if x['passed'] else '失败'}</td><td>{esc(x['detail'])}</td></tr>" for i, x in enumerate(checks))
    states = {'assigned':'待开始','in_progress':'进行中','awaiting_acceptance':'待验收','completed':'已完成'}
    tasks = ''
    for week, items in result['tasks'].items():
        for key, tid in items.items():
            t = result.get('task_snapshots', {}).get(str(tid), {})
            actual = str(t.get('actual_hours', 0))+'h' if t.get('routine_hours_updated_at') else '待填'
            tasks += f"<tr><td>{esc(week)}</td><td>{esc(labels.get(key,key))}</td><td>#{tid}</td><td>{esc(states.get(t.get('work_status'), '查看任务'))}</td><td>{esc(t.get('estimated_hours', '—'))}h</td><td>{esc(actual)}</td></tr>"

    scores = ''.join(f"<tr><td>{esc(labels.get(key,key))}</td><td>{r['week']['estimated_hours']}h</td><td>{r['week']['actual_hours']}h</td><td>{r['week']['completed_count']}/{r['week']['task_count']}</td><td>{r['week']['score']}</td><td>{r['month']['score']}</td></tr>" for key, r in result['performance'].items())
    notes = ''.join('<li>' + esc(n) + '</li>' for n in result.get('notes', []))
    report = f'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>例行任务全流程演练</title><style>body{{font:15px/1.7 system-ui,sans-serif;background:#f3f6fb;color:#213954;margin:0;padding:24px}}main{{max-width:1120px;margin:auto}}section{{background:white;border:1px solid #e0e7f1;border-radius:12px;padding:24px;margin:18px 0;overflow:auto}}h1{{font-size:25px}}h2{{font-size:19px}}table{{border-collapse:collapse;width:100%;font-size:14px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid #e6ebf3;vertical-align:top}}th{{background:#f5f8fd}}a{{color:#2863c7}}.result{{color:#13784c;font-size:22px;font-weight:bold}}</style><main><a href="/">返回工作管理树</a><h1>例行任务全流程演练</h1><p class="result">{'已完成' if result.get('passed') else '未完成'} · {passed}/{len(checks)} 项检查通过</p><p>模板与任务统一标记为 {esc(PREFIX)}。数据已保留，可切换演练身份或使用演练账号复查。保留 6 个演练账号、4 类生效业务模板、20 条业务任务；辅助兼容性模板另行停用保留。</p><section><h2>演练账号</h2><table><tr><th>场景</th><th>登录账号</th><th>员工编号</th></tr>{rows}</table></section><section><h2>绩效分析</h2><p>周口径：2026-11-02 至 2026-11-08；月口径：2026-11。周与月周期均未结束，分数均为演练过程预览；月内还包含其他周的未完结演练任务。</p><table><tr><th>员工</th><th>本周标准</th><th>本周实际</th><th>本周完成</th><th>周得分</th><th>月预览分</th></tr>{scores}</table></section><section><h2>保留任务索引</h2><table><tr><th>周起始日</th><th>业务</th><th>任务编号</th><th>状态</th><th>标准工时</th><th>实际工时</th></tr>{tasks}</table></section><section><details><summary>展开完整检查记录</summary><table><tr><th>#</th><th>检查</th><th>结果</th><th>说明</th></tr>{cases}</table></details></section><section><h2>口径与当前边界</h2><ul>{notes}</ul></section></main></html>'''
    (output / 'report.html').write_text(report)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--retain', action='store_true')
    args = parser.parse_args()
    result = run(args.database, args.output, args.retain)
    print(json.dumps({'passed': result.get('passed', False), 'checks': len(result['checks']),
                      'users': result['users'], 'tasks': result['tasks']}, ensure_ascii=False))
