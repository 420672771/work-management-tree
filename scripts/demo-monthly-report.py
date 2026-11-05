#!/usr/bin/env python3
"""Generate fictional monthly reports through the real workflow in an isolated DB."""
import argparse
import copy
import json
import sqlite3
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import monthly_reports as monthly
import weekly_reports as facts


def create_demo(output, month):
    output = output.resolve()
    database = output / 'demo.db'
    if output in (ROOT, ROOT/'data') or database == app.DB_PATH.resolve():
        raise ValueError('请使用独立演示目录，不可使用业务数据库')
    month, start, end = monthly.period(month)
    output.mkdir(parents=True, exist_ok=True)
    if database.exists():
        check = sqlite3.connect(database)
        try:
            marker = check.execute("SELECT value FROM demo_metadata WHERE key='generator'").fetchone()
            if marker != ('monthly-report-demo-v1',):
                raise ValueError('目标数据库不是本脚本的演示数据库')
        finally:
            check.close()
        database.unlink()
    simulated = datetime.combine(end + timedelta(days=1), time(9), facts.CHINA)
    real_clock = facts.local_time
    clock = lambda value=None: real_clock(value) if value is not None else simulated
    def stamp(day=1, hour=10):
        return datetime.combine(start+timedelta(days=day-1),time(hour),facts.CHINA).isoformat()
    with patch.object(app,'DB_PATH',database), patch.object(facts,'local_time',side_effect=clock):
        app.initialize()
        c = app.db()
        try:
            c.execute('CREATE TABLE demo_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.execute("INSERT INTO demo_metadata VALUES('generator','monthly-report-demo-v1')")
            manager = c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
            pm = c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
            c.execute("UPDATE user SET name='部门负责人（演示）' WHERE id=?",(manager,))
            c.execute("UPDATE user SET name='管理 PM（演示）' WHERE id=?",(pm,))
            domains = {r['domain_code']:r['id'] for r in c.execute("SELECT * FROM org_node WHERE node_type='domain'")}
            employees = []
            scenarios = [('林晓','A1','成果与归档','已完成交付、进行中的 P0 重点工作，归档后只读'),
                         ('陈晨','A2','风险与阻塞','接口逾期、外部资源阻塞、跨团队协同计划'),
                         ('周宁','B2','归档与修订','保留 V1 归档快照，创建 V2 补充改进计划'),
                         ('赵悦','B1','待验收与计划','待验收超时、正常进行中、下月到期事项'),
                         ('孙然','A3','无任务与补充','新入职暂无正式任务，手动补充学习与下月计划')]
            for name,code,label,description in scenarios:
                uid = c.execute('INSERT INTO user(name,department,role) VALUES(?,?,?)',(name+'（演示）',facts.DEPARTMENT,'member')).lastrowid
                employees.append({'id':uid,'name':name+'（演示）','role':'member','code':code,'scenario':label,'description':description})
            ids = {}
            def task(key,person,title,**changes):
                fields = dict(title=title,owner_id=employees[person]['id'],org_node_id=domains[employees[person]['code']],
                              priority='P1',work_status='in_progress',approval_status='approved',source='monthly-report-demo',
                              deliverable='方案文档、测试记录与交付清单',estimated_hours=8,actual_hours=10,
                              created_at=stamp(2),confirmed_at=stamp(2,11),updated_at=stamp(25),due_at=stamp(28,17))
                fields.update(changes)
                tid = c.execute('INSERT INTO task('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
                ids[key]=tid
                if fields['work_status']=='completed':
                    c.execute("INSERT INTO acceptance(task_id,result,created_at) VALUES(?,'passed',?)",(tid,fields['updated_at']))
                return tid
            def progress(tid,text,day=26,percent=60,blocked=''):
                c.execute('''INSERT INTO progress_record(task_id,reported_percent,report_text,is_blocked,block_reason,status,
                    reported_by,reported_at,confirmed_at,effective_percent) VALUES(?,?,?,?,?,'approved',?,?,?,?)''',
                    (tid,percent,text,bool(blocked),blocked,manager,stamp(day,14),stamp(day,15),percent))
            task('lin_done',0,'采购审批 Agent 验证与交付',priority='P0',work_status='completed',estimated_hours=24,actual_hours=26,
                 deliverable='采购审批 Agent 测试环境、12 条核心流程验证及业务验收记录')
            task('lin_p2',0,'发布业务操作指引与问题答疑',priority='P2',work_status='completed',estimated_hours=4,actual_hours=5,
                 deliverable='操作手册、培训录屏及 18 条常见问题清单')
            progress(task('lin_p0',0,'推进 ERP 二期核心流程评审',priority='P0',due_at=stamp(35,17)),
                     '已完成采购和库存流程评审，财务结算规则待业务确认',percent=70)
            task('lin_next',0,'整理下月业务试用反馈',priority='P2',work_status='assigned',due_at=stamp(40,17),
                 deliverable='试用意见清单、问题分级与修复排期')
            task('lin_draft',0,'尚未正式确认的想法',work_status='draft',approval_status='pending')
            task('lin_old',0,'历史已完成事项',work_status='completed',created_at=stamp(-20),confirmed_at=stamp(-20),updated_at=stamp(-3))
            progress(task('chen_overdue',1,'模型网关鉴权与审计接口联调',priority='P0',due_at=stamp(20,17)),
                     '鉴权链路通过，审计日志字段与对接方仍存在差异',blocked='等待平台团队确认审计字段口径')
            progress(task('chen_blocked',1,'知识库检索质量基线评测',risk_status='risk'),
                     '已准备 50 条问题集，部分业务样本尚未到位',blocked='业务部门需补充脱敏样本')
            task('chen_done',1,'完成检索服务压测与容量建议',work_status='completed',estimated_hours=16,actual_hours=18,
                 deliverable='压测报告、容量基线及扩容建议')
            task('chen_cancelled',1,'已取消的旧评测方案',work_status='cancelled')
            task('zhou_done',2,'完成机房 UPS 告警联动验证',work_status='completed',estimated_hours=12,actual_hours=11,
                 deliverable='告警联动测试记录与值班处置流程')
            progress(task('zhou_p0',2,'核心交换机升级与回退演练',priority='P0',due_at=stamp(36,17)),
                     '测试环境演练完成，生产变更窗口已提报',percent=80)
            task('zhou_next',2,'终端资产台账季度核对',priority='P2',work_status='assigned',due_at=stamp(42,17))
            task('zhao_wait',3,'经营分析看板数据口径验收',work_status='awaiting_acceptance',updated_at=stamp(23),
                 deliverable='指标字典、数据核对报告及待验收版本')
            progress(task('zhao_progress',3,'ERP 二期需求分级与资源测算',due_at=stamp(37,17)),
                     '已梳理 32 项需求，完成第一轮优先级评审',percent=65)
            task('zhao_done',3,'修复月度经营报表导出问题',priority='P2',work_status='completed',estimated_hours=6,actual_hours=7)
            task('zhao_boundary',3,'月底新增的业务指标核对',priority='P2',work_status='assigned',created_at=stamp((end-start).days+1),
                 confirmed_at=stamp((end-start).days+1,11),due_at=stamp(38,17))
            task('zhao_future',3,'下月才新建的任务',created_at=stamp(34),confirmed_at=stamp(34))
            c.commit()
            conclusions = [
                ('已完成采购审批交付及操作培训，ERP 二期评审持续推进。','按业务试用反馈安排下一轮迭代。','请业务确认财务结算规则。'),
                ('已完成压测，当前重点解决审计字段与评测样本阻塞。','建立联调问题每日跟踪机制。','需要平台团队和业务部门提供字段口径与脱敏样本。'),
                ('已验证告警联动，核心网络升级进入生产窗口协调阶段。','完善变更前检查和回退演练清单。','请确认生产变更窗口及业务通知安排。'),
                ('完成报表修复与需求分级，经营看板等待业务验收。','明确每项数据口径的业务确认人。','请业务安排看板验收。'),
                ('本月入职熟悉业务，暂无已确认的正式任务。','完成制度、工具与业务流程学习。','下月计划：参与知识库维护并提交首份学习记录。')]
            events=[]
            for person,summary in zip(employees,conclusions):
                report=monthly.generate(c,person['id'],month);c.commit()
                events.append({'employee':person['name'],'action':'生成月报','version':1})
                outline=copy.deepcopy(report['editorial']['outline'])
                for index,text in enumerate(summary):outline['children'][-1]['children'][index]['text']=text
                report=monthly.update(c,report['id'],person['id'],{'edit_revision':0,'outline':outline});c.commit()
                events.append({'employee':person['name'],'action':'编辑总结并自动保存','version':1})
                if person in (employees[0],employees[2]):
                    report=monthly.update(c,report['id'],person['id'],{'action':'archive','edit_revision':report['edit_revision']});c.commit()
                    events.append({'employee':person['name'],'action':'归档，固定快照','version':1})
                if person==employees[2]:
                    original=report
                    report=monthly.revise(c,original['id'],person['id']);c.commit()
                    outline=copy.deepcopy(report['editorial']['outline'])
                    outline['children'][-1]['children'][1]['text']='修订补充：增加生产变更前双人复核、回退耗时记录及演练复盘。'
                    monthly.update(c,report['id'],person['id'],{'edit_revision':0,'outline':outline});c.commit()
                    assert monthly.get_report(c,original['id'],person['id'])['editorial']==original['editorial']
                    events.append({'employee':person['name'],'action':'新建 V2 修订稿，原 V1 归档不变','version':2})
            versions=[monthly.get_report(c,row['id'],manager) for row in c.execute('SELECT id FROM monthly_report ORDER BY id')]
            for employee in employees:
                assert all(r['user_id']==employee['id'] for r in monthly.list_reports(c,employee['id'])['items'])
                foreign=next(r for r in versions if r['user_id']!=employee['id'])
                try: monthly.get_report(c,foreign['id'],employee['id'])
                except LookupError: pass
                else:raise AssertionError('员工之间隔离失败')
            assert len(monthly.list_reports(c,pm,scope='all')['items'])==len(versions)
            assert ids['lin_draft'] not in [t['id'] for r in versions for t in r['snapshot']['tasks']]
            assert ids['zhao_future'] not in [t['id'] for r in versions for t in r['snapshot']['tasks']]
            viewers=employees+[{'id':manager,'name':'部门负责人（演示）','role':'manager'}, {'id':pm,'name':'管理 PM（演示）','role':'pm'}]
            bundle={'is_demo':True,'month':month,'users':viewers,'reports':versions,'events':events,
                    'checks':['员工仅能列出本人月报','员工无法读取他人详情','管理 PM 可查看全部 6 份版本',
                              '归档后的任务快照与人工内容不被修订覆盖','草稿与下月新建任务被排除']}
            data=json.dumps(bundle,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            template=(ROOT/'scripts'/'monthly-report-preview.html').read_text()
            autosave=(ROOT/'static'/'weekly-reports.js').read_text().split('(() => {',1)[0]
            (output/'report.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2))
            (output/'index.html').write_text(template.replace('__DEMO_DATA__',data).replace('__AUTOSAVE__',autosave))
            return {'month':month,'employees':len(employees),'reports':len(versions),'tasks':len(ids),'actions':len(events),
                    'preview':str(output/'index.html'),'checks':bundle['checks']}
        finally:c.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--month',default=(facts.local_time().date().replace(day=1)-timedelta(days=1)).strftime('%Y-%m'))
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'monthly-report-demo')
    args=parser.parse_args()
    print(json.dumps(create_demo(args.output,args.month),ensure_ascii=False,indent=2))
