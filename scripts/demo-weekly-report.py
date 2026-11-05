#!/usr/bin/env python3
"""Build an isolated, repeatable weekly-report demo and an offline HTML preview."""
import argparse
import json
import sqlite3
import sys
from datetime import datetime, time, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app
import weekly_reports as reports


def create_demo(output, reference):
    output.mkdir(parents=True, exist_ok=True)
    database = output / 'demo.db'
    # Refuse to reset a database that is not this script's own demo.
    if database.exists():
        check = sqlite3.connect(database)
        try:
            marker = check.execute("SELECT value FROM demo_metadata WHERE key='generator'").fetchone()
            if marker != ('weekly-report-demo-v1',):
                raise ValueError('目标目录内存在非演示数据库，请选择其他输出目录')
        finally:
            check.close()
        database.unlink()
    start, end = reports.period(reference)
    simulated = datetime.combine(end, time(18), reports.CHINA)
    def stamp(day=0, hour=10):
        return datetime.combine(start + timedelta(days=day), time(hour), reports.CHINA).isoformat()
    real_local_time = reports.local_time
    clock = lambda value=None: real_local_time(value) if value is not None else simulated
    with patch.object(app, 'DB_PATH', database), patch.object(reports, 'local_time', side_effect=clock):
        app.initialize()
        c = app.db()
        try:
            c.execute('CREATE TABLE demo_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)')
            c.execute("INSERT INTO demo_metadata VALUES('generator','weekly-report-demo-v1')")
            manager = c.execute("SELECT id FROM user WHERE role='manager'").fetchone()[0]
            pm = c.execute("SELECT id FROM user WHERE role='pm'").fetchone()[0]
            c.execute("UPDATE user SET name='中心负责人（演示）' WHERE id=?", (manager,))
            c.execute("UPDATE user SET name='管理 PM（演示）' WHERE id=?", (pm,))
            domains = {r['domain_code']: dict(r) for r in c.execute("SELECT * FROM org_node WHERE node_type='domain'")}
            members = {}
            for code, name in (('A1','钱缘（演示）'),('A2','闫兴赫（演示）'),('A3','覃俞淦（演示）'),('B1','汪文娟（演示）'),('B2','封滘生（演示）')):
                members[code] = c.execute('INSERT INTO user(name,department,role) VALUES(?,?,?)', (name,reports.DEPARTMENT,'member')).lastrowid
            scenarios = []
            ids = {}
            deliverables = {
                'p0_accepted':'方案评审纪要、测试环境地址及 12 条核心流程验证记录',
                'p1_accepted':'50 条基准问题评测集、召回率与引用准确率对比报告',
                'p2_accepted':'培训资料、操作录屏及首批 18 条问题答疑清单',
                'large_p2':'ERP 二期需求排期表、人员资源测算及冲突项协调建议',
                'progress_100':'UPS 告警联动验证记录及告警响应处置流程',
                'old_complete':'服务器资产清单、责任人及账实核对差异记录',
                'legacy_complete':'终端巡检台账与问题闭环归档目录',
                'overdue':'订单接口字段字典、税额差异口径及业务确认记录',
                'blocked':'不少于 30 条脱敏采购样本，覆盖正常审批与异常退回流程',
                'risk_marked':'变更步骤、回退方案、演练记录及批准后的变更窗口',
                'stale_acceptance':'检索参数配置清单、回归测试结果及验收签字记录',
                'recent_acceptance':'10 万行报表导出耗时对比、测试记录及上线回退方案',
                'no_progress':'AI 应用效果指标字典、数据来源与月度追踪样表',
                'normal':'模型网关接入清单、系统责任人及联调验收用例',
                'due_7_days':'试用验收清单、业务确认人及问题跟踪台账',
                'next_week_p0':'结算规则评审纪要、争议项裁定及后续排期',
                'next_week_p2':'终端维护知识条目、分类索引及例行发布记录',
                'ongoing_next':'权限矩阵、审计事件字典及跨系统联调结果',
                'previously_created':'历史需求范围确认单及负责人、交付时间排期表',
                'ungrouped':'例会材料包、责任分工核对表及待协调事项',
                'deleted_after_publish':'演示交付清单，用于验证发布后删除任务不影响历史快照',
            }
            def task(key, title, code='A1', description='', **changes):
                fields = dict(title=title, owner_id=members[code], org_node_id=domains[code]['id'],
                              description=description, due_at=stamp(9,17), priority='P1', work_status='in_progress',
                              deliverable=deliverables.get(key,'测试任务交付记录'), source='weekly-report-demo',
                              approval_status='approved', confirmed_at=stamp(0,11), approver_id=manager,
                              created_at=stamp(0,9), updated_at=stamp(4,15), estimated_hours=8, actual_hours=9,
                              complexity='general')
                fields.update(changes)
                tid=c.execute('INSERT INTO task('+','.join(fields)+') VALUES('+','.join('?' for _ in fields)+')',tuple(fields.values())).lastrowid
                ids[key]=tid
                scenarios.append({'key':key,'task_id':tid,'title':title})
                return tid
            def progress(tid, text='已完成主要验证，按计划推进', day=4, hour=14, percent=60, blocked='', status='approved'):
                c.execute('''INSERT INTO progress_record(task_id,reported_percent,report_text,is_blocked,block_reason,status,
                             reported_by,reported_at,confirmed_at,effective_percent,source) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                          (tid,percent,text,bool(blocked),blocked,status,
                           c.execute('SELECT owner_id FROM task WHERE id=?',(tid,)).fetchone()[0],stamp(day,hour),
                           stamp(day,hour) if status in ('approved','auto_approved') else None,
                           percent if status in ('approved','auto_approved') else None,'demo'))
            def finish(key,title,code,day,hours,actual,priority='P1',complexity='general',accepted=True):
                tid=task(key,title,code,work_status='completed',due_at=stamp(day,17),updated_at=stamp(day,16),
                         priority=priority,estimated_hours=hours,actual_hours=actual,complexity=complexity)
                progress(tid,'交付物已提交，验证通过并完成确认',day=day,hour=15,percent=100)
                if accepted:
                    c.execute("INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,'passed',?,?)",
                              (tid,manager,'演示：验收证据完整，符合交付标准',stamp(day,16)))
                return tid
            finish('p0_accepted','采购审批 Agent 方案评审与测试环境交付','A1',1,24,28,'P0','complex')
            finish('p1_accepted','知识库检索质量与引用准确率基线验证','A2',2,16,15,complexity='complex')
            finish('p2_accepted','发布 AI 工具培训资料及首批答疑记录','A3',3,4,5,'P2')
            finish('large_p2','外综服 ERP 二期需求排期与资源建议','B1',4,32,38,'P2','major',False)
            finish('progress_100','完成机房 UPS 告警联动验证','B2',5,8,7,accepted=False)
            old=finish('old_complete','上周完成服务器资产盘点','B2',-2,6,6)
            c.execute('UPDATE task SET created_at=?,confirmed_at=? WHERE id=?',(stamp(-7),stamp(-7,11),old))
            legacy=task('legacy_complete','完成历史终端巡检记录归档','B2',work_status='completed',priority='P2',
                        due_at=stamp(3,17),updated_at=stamp(3,16),estimated_hours=2,actual_hours=2)
            overdue=task('overdue','ERP 二期订单接口字段梳理','B1',due_at=stamp(2,17),risk_status='normal')
            progress(overdue,'主链路字段已确认，税额差异字段待业务复核',percent=70)
            block=task('blocked','采购审批 Agent 业务试用数据准备','A1',risk_status='blocked',priority='P0')
            progress(block,'测试环境可用，业务样本尚未齐备',percent=40,blocked='缺少采购异常单与历史审批样本，需采购部补充')
            marked=task('risk_marked','网络核心交换机变更演练','B2',risk_status='at_risk')
            progress(marked,'回退方案已验证，需协调周末变更窗口',percent=65)
            waiting=task('stale_acceptance','上线知识库检索参数优化','A2',work_status='awaiting_acceptance',updated_at=stamp(6,15))
            c.execute('''INSERT INTO audit_log(object_type,object_id,action,actor,created_at)
                         VALUES('task',?,'submit_acceptance',?,?)''',(waiting,str(members['A2']),stamp(2,10)))
            progress(waiting,'优化验证已完成，等待验收人确认',day=2,percent=95)
            recent=task('recent_acceptance','ERP 报表导出性能优化','B1',work_status='awaiting_acceptance',updated_at=stamp(6,10))
            progress(recent,'测试记录齐备，今天上午提交验收',day=6,hour=10,percent=95)
            quiet=task('no_progress','建立 AI 应用效果月度追踪口径','A3',priority='P0',due_at=stamp(10,17))
            normal=task('normal','梳理统一模型网关接入清单','A2',due_at=stamp(20,17))
            progress(normal,'已确认接入范围与验收用例，按长期计划推进',percent=30)
            due=task('due_7_days','发布采购审批 Agent 试用验收清单','A1',work_status='assigned',due_at=stamp(8,17))
            progress(due,'验收清单已草拟，计划下周二评审',percent=10)
            next_p0=task('next_week_p0','ERP 二期结算规则评审','B1',priority='P0',work_status='assigned',due_at=stamp(9,17))
            progress(next_p0,'方案和争议项已提交，评审议程已确认',percent=15)
            next_p2=task('next_week_p2','例行整理终端维护知识条目','B2',priority='P2',task_type='ROUTINE',work_status='assigned',due_at=stamp(9,17))
            progress(next_p2,'常规资料已整理，按例行计划发布',percent=20)
            long_p0=task('ongoing_next','统一大模型权限与审计能力建设','A2',priority='P0',due_at=stamp(25,17),complexity='major')
            progress(long_p0,'审计事件结构已确定，下周推进权限联调',percent=45)
            outside_new=task('previously_created','本周确认生效的历史需求排期','B1',created_at=stamp(-5),confirmed_at=stamp(1,11))
            progress(outside_new,'范围已确认，本周正式纳入交付计划',percent=25)
            ungrouped=task('ungrouped','中心例会材料与责任分工核对','A3',org_node_id=None,priority='P2',due_at=stamp(10,17))
            progress(ungrouped,'责任分工已复核',percent=80)
            removable=task('deleted_after_publish','已发布快照保留：旧版演示交付清单','A1',priority='P2',due_at=stamp(12,17))
            progress(removable,'此任务将在发布快照后删除，用于演示历史快照保持完整',percent=50)
            task('pending_excluded','不应计入：待确认任务','A1',approval_status='pending',confirmed_at=None)
            task('rejected_excluded','不应计入：已驳回任务','A2',approval_status='rejected',work_status='draft')
            task('draft_excluded','不应计入：草稿任务','A3',work_status='draft')
            task('cancelled_excluded','不应计入：已取消任务','B2',work_status='cancelled')
            deleted=task('deleted_excluded','不应计入：已删除任务','B1')
            c.execute('DELETE FROM task WHERE id=?',(deleted,))
            task('future_creation_excluded','不应计入：快照生成后的新增任务','A1',created_at=stamp(6,20),confirmed_at=stamp(6,20))
            task('future_confirmation_excluded','不应计入：快照之后才确认','A2',confirmed_at=stamp(6,20))
            other=c.execute("INSERT INTO org_node(node_type,name,created_at) VALUES('department','其他部门（演示）',?)",(stamp(0),)).lastrowid
            task('other_department_excluded','不应计入：其他部门任务','B1',org_node_id=other)
            # A post-cutoff progress message must not replace the last visible progress.
            progress(normal,'不应出现在快照中的周日 20 点更新',day=6,hour=20,percent=90)
            c.commit()
            result=reports.run_due(c,simulated);c.commit()
            original=reports.get_report(c,result['report_id'],manager)
            metrics=original['snapshot']['metrics']
            assert metrics['completed']==6,metrics
            assert metrics['completed_hours']=={'standard':86.0,'actual':95.0},metrics
            assert metrics['domains']==5,metrics
            original_ids={t['id'] for t in original['snapshot']['tasks']}
            excluded={v for k,v in ids.items() if k.endswith('_excluded')}
            assert original_ids.isdisjoint(excluded)
            initial_risks={t['id']:t['risk_reasons'] for t in original['snapshot']['tasks']}
            assert '待验收超过 48 小时' in initial_risks[waiting]
            assert '待验收超过 48 小时' not in initial_risks[recent]
            assert '本周无进展填报' in initial_risks[quiet]
            assert next_p2 not in original['snapshot']['sections']['next_week']
            assert next_p0 in original['snapshot']['sections']['next_week'] and long_p0 in original['snapshot']['sections']['next_week']
            assert '不应出现在' not in next(t['latest_progress'] for t in original['snapshot']['tasks'] if t['id']==normal)
            reviewed=reports.update_report(c,original['id'],pm,{
                'edit_revision':0,'summary':'本周完成采购审批 Agent 测试环境、检索质量基线与 ERP 二期资源建议等 6 项交付；业务样本缺口、逾期接口梳理和待验收事项需在周会协调。',
                'conclusion':'已形成可试用的交付基础。优先处理业务样本与接口口径依赖，控制后续交付风险。',
                'requirements':'下周优先完成 ERP 结算规则评审和 Agent 试用验收，明确每项交付的验收人及时间。',
                'support':'请采购部周二前补充异常单样本；请业务与财务共同确认税额差异字段。',
                'notes':{str(block):'周会讨论：请采购部指定样本接口人，PM 跟踪到周二。',str(waiting):'周会讨论：请验收人确认周一可用时段。'},
                'orders':{'next_week':[next_p0]+[i for i in original['snapshot']['sections']['next_week'] if i!=next_p0]}})
            c.commit()
            published=reports.update_report(c,reviewed['id'],manager,{'action':'publish','edit_revision':1});c.commit()
            frozen=published['snapshot']
            # Demonstrate that a deleted live task is retained in the published snapshot.
            c.execute('DELETE FROM progress_record WHERE task_id=?',(removable,))
            c.execute('DELETE FROM task WHERE id=?',(removable,));c.commit()
            revision=reports.revise_report(c,published['id'],manager);c.commit()
            current=reports.refresh_report(c,revision['id'],pm,0);c.commit()
            assert reports.get_report(c,published['id'],manager)['snapshot']==frozen
            assert removable not in {t['id'] for t in current['snapshot']['tasks']}
            assert current['status']=='pending'
            assert current['editorial']['summary']==reviewed['editorial']['summary']
            assert current['editorial']['notes']==reviewed['editorial']['notes']
            checks=[]
            for label,call,error in (
                ('普通成员不能查看部门周报',lambda:reports.get_report(c,current['id'],members['A1']),PermissionError),
                ('PM 可编辑但不能发布',lambda:reports.update_report(c,current['id'],pm,{'action':'publish','edit_revision':1}),PermissionError),
                ('已发布版本不能直接修改',lambda:reports.update_report(c,published['id'],manager,{'edit_revision':2}),reports.ReportConflict),
                ('不能手动修改统计数字',lambda:reports.update_report(c,current['id'],pm,{'edit_revision':1,'metrics':{}}),ValueError),
                ('过期编辑触发冲突提示',lambda:reports.update_report(c,current['id'],manager,{'edit_revision':0,'summary':'冲突修改'}),reports.ReportConflict)):
                try:call()
                except error:checks.append({'scenario':label,'passed':True})
                else:raise AssertionError(label)
            c.rollback()
            assert reports.run_due(c,simulated)=={'generated':0}
            checks += [{'scenario':label,'passed':True} for label in (
                '五个责任域成果与工时汇总','P0 未完成重点任务，以及本周完成的全部优先级任务',
                '100% 进度确认完成及无事件历史完成','上周完成事项不计入本周完成',
                '逾期、阻塞、风险标记、7 天内到期、无进展重点任务',
                '待验收超过 / 未超过 48 小时','下周 P0/P1 到期及长期推进任务',
                'P2 例行任务不进入下周重点','按确认时间统计本周新增',
                '未归类任务保留且不增加责任域数','草稿、待确认、驳回、取消、删除与其他部门任务过滤',
                '快照之后的创建、确认和进展不纳入','任务删除后已发布历史快照不变',
                '一键建立 V2 待发布修订稿，V1 已发布快照保持不变','周日定时任务重复执行不覆盖编辑内容',
                'PM 补充摘要、管理结论、风险说明和下周重点排序')]
            versions=[reports.get_report(c,i,manager) for i in (current['id'],published['id'])]
            bundle={'is_demo':True,'simulated_time':simulated.isoformat(),'period_start':start.isoformat(),'period_end':end.isoformat(),
                    'current_id':current['id'],'versions':versions,'checks':checks,'scenarios':scenarios,
                    'meeting_text':{str(r['id']):reports.meeting_text(r) for r in versions}}
            (output/'report.json').write_text(json.dumps(bundle,ensure_ascii=False,indent=2))
            (output/'meeting-material.txt').write_text(reports.meeting_text(current))
            template=(ROOT/'scripts'/'weekly-report-preview.html').read_text()
            data=json.dumps(bundle,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026')
            (output/'index.html').write_text(template.replace('__DEMO_DATA__',data))
            c.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return {'preview':str(output/'index.html'),'database':str(database),'tasks_created':len(scenarios),
                    'included_tasks':len(current['snapshot']['tasks']),'metrics':current['snapshot']['metrics'],
                    'checks_passed':len(checks),'period':f'{start}—{end}'}
        finally:c.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=ROOT/'artifacts'/'weekly-report-demo')
    parser.add_argument('--week',default=reports.local_time().date().isoformat())
    args=parser.parse_args()
    output=args.output.resolve()
    if output==ROOT/'data' or output==ROOT:
        parser.error('请使用独立演示输出目录')
    print(json.dumps(create_demo(output,args.week),ensure_ascii=False,indent=2))
