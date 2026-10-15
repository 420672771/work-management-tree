#!/usr/bin/env python3
"""Populate the live database with an idempotent, end-to-end pilot dataset."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import app


def iso(days=0, hour=17):
    d = datetime.now(timezone.utc) + timedelta(days=days)
    return d.replace(hour=hour, minute=0, second=0, microsecond=0).isoformat()


def main():
    app.initialize()
    c = app.db()
    try:
        users = [
            ('张晨（产品负责人）', '科技产品中心', 'owner', 'demo-zhang'),
            ('李娜（项目交付）', '科技产品中心', 'owner', 'demo-li'),
            ('王磊（研发负责人）', '科技产品中心', 'owner', 'demo-wang'),
            ('陈静（数据与运营）', '科技产品中心', 'owner', 'demo-chen'),
            ('赵敏（部门管理PM）', '科技产品中心', 'pm', 'demo-zhao'),
        ]
        ids = {}
        for name, dept, role, wxid in users:
            row = c.execute('SELECT id FROM user WHERE wecom_userid=?', (wxid,)).fetchone()
            ids[name] = row[0] if row else c.execute(
                'INSERT INTO user(name,department,role,wecom_userid) VALUES(?,?,?,?)',
                (name, dept, role, wxid)).lastrowid

        root = c.execute("SELECT id FROM org_node WHERE node_type='department' ORDER BY id LIMIT 1").fetchone()[0]
        domains = {}
        for name, boundary in [
            ('产品规划与需求管理', '产品路线图、需求评审与版本规划'),
            ('项目交付与质量管理', '项目计划、交付质量与验收闭环'),
            ('研发与技术平台', '研发效能、架构和技术风险'),
            ('数据运营与客户反馈', '数据指标、客户反馈和运营改进'),
        ]:
            row = c.execute('SELECT id FROM org_node WHERE name=?', (name,)).fetchone()
            domains[name] = row[0] if row else c.execute(
                'INSERT INTO org_node(parent_id,node_type,name,boundary,owner_id,effective_at,created_at) VALUES(?,?,?,?,?,?,?)',
                (root, 'domain', name, boundary, ids['赵敏（部门管理PM）'], app.now(), app.now())).lastrowid

        task_rows = [
            (domains['产品规划与需求管理'], 'Q4 工作管理树试点需求评审', '完成试点部门需求范围、优先级和验收口径确认。', ids['张晨（产品负责人）'], 2, 'P1', '评审纪要 + 已确认需求清单', 'in_progress', 'normal'),
            (domains['产品规划与需求管理'], '输出 11 月产品路线图', '结合客户反馈和研发容量形成版本路线图。', ids['张晨（产品负责人）'], 8, 'P1', '路线图文档（含版本目标和负责人）', 'assigned', 'normal'),
            (domains['项目交付与质量管理'], '工作管理树 MVP 试点上线验收', '完成关键流程演示、缺陷确认和上线验收。', ids['李娜（项目交付）'], 1, 'P0', '验收记录 + 遗留问题清单', 'awaiting_acceptance', 'normal'),
            (domains['项目交付与质量管理'], '补齐项目风险与周报机制', '建立每周风险盘点、升级和周报发布机制。', ids['李娜（项目交付）'], 5, 'P1', '风险台账 + 周报模板', 'in_progress', 'blocked'),
            (domains['研发与技术平台'], '完成权限与审计日志检查', '检查公网访问、认证、操作审计和敏感配置。', ids['王磊（研发负责人）'], -2, 'P0', '安全检查报告', 'completed', 'normal'),
            (domains['研发与技术平台'], '企业微信接入前置配置', '准备回调地址、Token、EncodingAESKey 和应用权限。', ids['王磊（研发负责人）'], 12, 'P1', '企微配置清单 + 联调记录', 'assigned', 'normal'),
            (domains['数据运营与客户反馈'], '建立试点数据看板指标', '确认任务入库率、按时交付率、风险提前暴露率等指标。', ids['陈静（数据与运营）'], 4, 'P1', '指标口径文档 + 看板截图', 'in_progress', 'normal'),
            (domains['数据运营与客户反馈'], '整理首轮员工使用反馈', '收集试点员工反馈并形成优化项，区分必须改进和观察项。', ids['陈静（数据与运营）'], -5, 'P2', '反馈汇总表 + 优化建议', 'completed', 'normal'),
        ]
        created = 0
        for node_id, title, desc, owner, due_days, priority, deliverable, status, risk in task_rows:
            hit = c.execute("SELECT id FROM task WHERE title=?", (title,)).fetchone()
            if hit:
                tid = hit[0]
            else:
                tid = c.execute(
                    'INSERT INTO task(org_node_id,title,description,owner_id,due_at,priority,work_status,risk_status,deliverable,source,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (node_id, title, desc, owner, iso(due_days), priority, status, risk, deliverable, 'demo-rich', app.now(), app.now())).lastrowid
                app.audit(c, 'task', tid, 'create', 'demo-seed', after={'title': title, 'owner_id': owner})
                created += 1
            if title == 'Q4 工作管理树试点需求评审' and not c.execute('SELECT 1 FROM feedback WHERE task_id=?', (tid,)).fetchone():
                c.execute('INSERT INTO feedback(task_id,user_id,content,progress,risk,created_at) VALUES(?,?,?,?,?,?)',
                          (tid, owner, '已完成范围梳理，正在确认验收口径，预计明天完成。', '60%', '', app.now()))
            if title == '补齐项目风险与周报机制' and not c.execute('SELECT 1 FROM feedback WHERE task_id=?', (tid,)).fetchone():
                c.execute('INSERT INTO feedback(task_id,user_id,content,progress,risk,created_at) VALUES(?,?,?,?,?,?)',
                          (tid, owner, '依赖各项目负责人补充风险信息，当前存在阻塞。', '35%', '等待项目负责人补充风险台账', app.now()))
            if status == 'completed' and not c.execute("SELECT 1 FROM acceptance WHERE task_id=? AND result='passed'", (tid,)).fetchone():
                c.execute('INSERT INTO acceptance(task_id,acceptor_id,result,reason,created_at) VALUES(?,?,?,?,?)',
                          (tid, ids['赵敏（部门管理PM）'], 'passed', '交付物已检查，符合验收标准。', app.now()))
                app.audit(c, 'task', tid, 'accept', 'demo-seed')
        c.commit()
        print({'users': len(ids), 'domains': len(domains), 'new_tasks': created, 'db': str(app.DB_PATH)})
    finally:
        c.close()


if __name__ == '__main__':
    main()
