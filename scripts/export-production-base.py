#!/usr/bin/env python3
"""Export an isolated production reference database; never modify the source."""
import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import app

PREFIXES=('[例行演练]','演练·','[自动测试]','[演示]')
BASE_TABLES=('user','org_department','org_position','user_position','reporting_line','delegate',
             'org_node','task_template','user_account','feishu_profile','performance_config','system_setting')


def fixture(value):
    return isinstance(value,str) and value.startswith(PREFIXES)


def rows(c,table):
    return [dict(row) for row in c.execute('SELECT * FROM '+table)]


def not_ended(value):
    if not value:return True
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        if parsed.tzinfo is None:parsed=parsed.replace(tzinfo=app.weekly_reports.CHINA)
        return parsed>datetime.now(timezone.utc)
    except (ValueError,TypeError):raise ValueError('任职或汇报关系的结束日期无效，请先修正基础资料')


def selected_people(template):
    if template.get('owner_ids_json') is None:return [template['owner_id']] if template.get('owner_id') else []
    try:value=json.loads(template['owner_ids_json'])
    except (ValueError,TypeError):raise ValueError(f'例行模板 #{template["id"]} 的人员名单格式无效')
    if not isinstance(value,list) or any(type(item) is not int or item<1 for item in value):
        raise ValueError(f'例行模板 #{template["id"]} 的人员名单格式无效')
    return list(dict.fromkeys(value))


def topological(records,table):
    pending={row['id']:row for row in records};ordered=[];done=set()
    while pending:
        ready=[row for row in pending.values() if row.get('parent_id') is None or row['parent_id'] in done]
        if not ready:raise ValueError(f'{table} 存在缺失上级、引用被排除资料或循环关系，请先修正')
        for row in sorted(ready,key=lambda row:row['id']):
            ordered.append(row);done.add(row['id']);pending.pop(row['id'])
    return ordered


def reference(row,key,known,table,required=False):
    value=row.get(key)
    if value is None and not required:return
    if value not in known:raise ValueError(f'{table} #{row.get("id", "配置")} 的 {key} 引用了不存在或已排除的资料')


def prepare(c):
    source={table:rows(c,table) for table in BASE_TABLES}
    excluded_users={row['id'] for row in source['user'] if fixture(row['name'])}
    excluded_users.update(row['user_id'] for row in source['user_account'] if row['username'].lower().startswith('routine.'))
    result={table:[] for table in BASE_TABLES};adjustments=[]
    result['user']=[row for row in source['user'] if row['id'] not in excluded_users]
    for table in ('org_department','org_position','org_node'):
        result[table]=[row for row in source[table] if not fixture(row['name'])]
    users={row['id'] for row in result['user']};departments={row['id'] for row in result['org_department']}
    positions={row['id'] for row in result['org_position']};nodes={row['id'] for row in result['org_node']}
    for row in result['org_department']:reference(row,'owner_user_id',users,'org_department')
    for row in result['org_position']:reference(row,'dept_id',departments,'org_position')
    for row in result['org_node']:reference(row,'owner_id',users,'org_node')
    result['org_department']=topological(result['org_department'],'org_department')
    result['org_node']=topological(result['org_node'],'org_node')
    result['user_position']=[row for row in source['user_position']
        if row['user_id'] in users and row['position_id'] in positions and not_ended(row['valid_to'])]
    for table,owner,end,links in (
        ('reporting_line','user_id','effective_to',('direct_leader_id','approver_id','dotted_leader_id')),
        ('delegate','user_id','end_at',('delegate_user_id',))):
        for row in source[table]:
            if row[owner] in excluded_users or not not_ended(row[end]):continue
            reference(row,owner,users,table,required=True)
            for key in links:reference(row,key,users,table)
            result[table].append(row)
    for row in source['task_template']:
        if fixture(row['title']):continue
        owners=selected_people(row)
        retained=[owner for owner in owners if owner not in excluded_users]
        if owners and not retained:continue
        if not set(retained).issubset(users):raise ValueError(f'例行模板 #{row["id"]} 引用了不存在的员工')
        reference(row,'org_node_id',nodes,'task_template',required=True)
        if retained!=owners:
            row={**row,'owner_ids_json':json.dumps(retained),'owner_id':retained[0] if len(retained)==1 else None,
                 'assignment_type':'people','position_id':None}
            adjustments.append({'template_id':row['id'],'removed_fixture_recipients':len(owners)-len(retained)})
        result['task_template'].append(row)
    result['user_account']=[row for row in source['user_account'] if row['user_id'] in users]
    bindings={row['feishu_open_id'] for row in result['user'] if row.get('feishu_open_id')}
    result['feishu_profile']=[row for row in source['feishu_profile'] if row['open_id'] in bindings]
    for row in source['performance_config']:
        if row['updated_by'] not in users:row={**row,'updated_by':None}
        result['performance_config'].append(row)
    result['system_setting']=source['system_setting']
    if result['org_node']:
        result['system_setting']=[row for row in result['system_setting'] if row['setting_key']!='responsibility_domains_initialized']
        result['system_setting'].append({'setting_key':'responsibility_domains_initialized','setting_value':'1'})
    managers={row['id'] for row in result['user'] if row['role']=='manager'}
    accounts={row['user_id'] for row in result['user_account']}
    if not managers.intersection(accounts):raise ValueError('至少需要一名已配置登录账号的正式部门负责人，请先配置后导出')
    if not any(row['node_type']=='department' for row in result['org_node']):raise ValueError('责任树缺少正式部门根节点')
    source_counts={row[0]:c.execute('SELECT count(*) FROM "'+row[0]+'"').fetchone()[0]
                   for row in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()}
    report={'scope':'base','generated_at':datetime.now(timezone.utc).isoformat(),
        'included_counts':{table:len(value) for table,value in result.items()},
        'excluded_counts':{table:source_counts.get(table,0)-len(result.get(table,[])) for table in source_counts if table!='system_setting'},
        'excluded_fixture_user_ids':sorted(excluded_users),'recipient_adjustments':adjustments,
        'users_without_accounts':len(users-accounts),
        'notes':['仅导出正式基础资料，任务、反馈、报表、草稿、审计、登录会话和发送队列不导出。',
                 '员工、岗位、部门、责任域、模板及关联编号保留；账号密码哈希和当前飞书绑定保留。',
                 'routine.*、例行演练及明确标记的自动测试/演示资料排除；源数据库不修改。']}
    return result,report


def insert_records(c,table,records):
    if not records:return
    columns=list(records[0]);sql='INSERT INTO '+table+'('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')'
    c.executemany(sql,[tuple(row[key] for key in columns) for row in records])


def export(source,output):
    source=Path(source).resolve(strict=True);output=Path(output).resolve()
    report_path=output.with_suffix(output.suffix+'.report.json')
    if source==output:raise ValueError('源数据库与导出文件不能相同')
    if output.exists() or report_path.exists():raise ValueError('导出目标已存在，拒绝覆盖；请使用新的文件名')
    output.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    snapshot=sqlite3.connect(':memory:');snapshot.row_factory=sqlite3.Row
    temporary=None;published=False
    try:
        original=sqlite3.connect('file:'+quote(str(source))+'?mode=ro',uri=True)
        try:original.backup(snapshot)
        finally:original.close()
        app.migrate(snapshot);snapshot.commit()
        snapshot.execute("UPDATE user SET role='member' WHERE role='owner'");snapshot.commit()
        selected,report=prepare(snapshot)
        handle,path=tempfile.mkstemp(prefix='.production-base-',suffix='.db',dir=output.parent);os.close(handle);temporary=Path(path)
        target=sqlite3.connect(temporary);target.row_factory=sqlite3.Row
        try:
            target.execute('PRAGMA foreign_keys=ON');app.migrate(target);target.commit()
            for table in BASE_TABLES:insert_records(target,table,selected[table])
            # Preserve ID high-water marks so references from the old site cannot
            # accidentally identify a different, newly created production object.
            for name,seq in snapshot.execute('SELECT name,seq FROM sqlite_sequence'):
                current=target.execute('SELECT seq FROM sqlite_sequence WHERE name=?',(name,)).fetchone()
                if current:target.execute('UPDATE sqlite_sequence SET seq=max(seq,?) WHERE name=?',(seq,name))
                else:target.execute('INSERT INTO sqlite_sequence(name,seq) VALUES(?,?)',(name,seq))
            target.commit()
            if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise ValueError('导出数据库完整性检查失败')
            if target.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('导出数据库外键检查失败')
            for table in ('task','task_instance','login_session','login_state','notify_log','bot_pending_action'):
                if target.execute('SELECT count(*) FROM '+table).fetchone()[0]:raise ValueError('导出结果混入运行态或业务数据')
            target.execute('PRAGMA wal_checkpoint(TRUNCATE)');target.execute('PRAGMA journal_mode=DELETE')
            report['validation']={'integrity_check':'ok','foreign_key_check':'ok','business_tables_empty':True}
        finally:target.close()
        os.link(temporary,output);published=True
        with open(os.open(report_path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w',encoding='utf-8') as stream:
            json.dump(report,stream,ensure_ascii=False,indent=2);stream.write('\n')
        return report
    except Exception:
        if published:output.unlink(missing_ok=True)
        raise
    finally:
        snapshot.close()
        if temporary:
            temporary.unlink(missing_ok=True)
            Path(str(temporary)+'-wal').unlink(missing_ok=True);Path(str(temporary)+'-shm').unlink(missing_ok=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source',type=Path,required=True,help='现有测试站数据库，只读')
    parser.add_argument('--output',type=Path,required=True,help='新的生产基础资料数据库，不覆盖已有文件')
    args=parser.parse_args()
    try:
        report=export(args.source,args.output)
    except (ValueError,OSError,sqlite3.Error) as exc:raise SystemExit('导出失败：'+str(exc))
    print(json.dumps({'output':str(args.output.resolve()),'report':str(args.output.with_suffix(args.output.suffix+'.report.json').resolve()),
                      'included_counts':report['included_counts'],'excluded_fixture_users':len(report['excluded_fixture_user_ids']),
                      'validation':report['validation']},ensure_ascii=False,indent=2))
