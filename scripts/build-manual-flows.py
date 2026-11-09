#!/usr/bin/env python3
"""Build text-searchable SVG workflow illustrations for the user handbook."""
from html import escape
from pathlib import Path

OUTPUT = Path(__file__).resolve().parents[1] / 'static' / 'manual-assets'


def diagram(name, title, subtitle, lanes, note):
    height = 154 + len(lanes) * 176 + 68
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="1120" height="{height}" viewBox="0 0 1120 {height}" role="img" aria-labelledby="title desc">',
           f'<title id="title">{escape(title)}</title><desc id="desc">{escape(subtitle+"。"+note)}</desc>',
           '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="4" orient="auto"><path d="M0 0L8 4L0 8" fill="none" stroke="#5577ac" stroke-width="2"/></marker></defs>',
           f'<rect width="1120" height="{height}" rx="18" fill="#f3f7fe"/>',
           '<g font-family="Microsoft YaHei,Noto Sans CJK SC,sans-serif">',
           f'<text x="32" y="44" font-size="25" font-weight="700" fill="#17385e">{escape(title)}</text>',
           f'<text x="32" y="76" font-size="16" fill="#536b89">{escape(subtitle)}</text>']
    for row, (label, steps) in enumerate(lanes):
        y = 124 + row * 176
        out.append(f'<text x="34" y="{y}" font-size="17" font-weight="700" fill="#285ca2">{escape(label)}</text>')
        width = (1056 - 30 * (len(steps)-1)) / len(steps)
        for index, (heading, line1, line2) in enumerate(steps):
            x = 32 + index * (width + 30)
            color = '#e9f6ef' if index == len(steps)-1 else '#fff'
            out.append(f'<rect x="{x}" y="{y+16}" width="{width}" height="108" rx="12" fill="{color}" stroke="#bfd0e8"/>')
            out.append(f'<circle cx="{x+23}" cy="{y+42}" r="13" fill="#2b65c5"/><text x="{x+23}" y="{y+47}" text-anchor="middle" font-size="15" fill="#fff">{index+1}</text>')
            out.append(f'<text x="{x+45}" y="{y+48}" font-size="19" font-weight="700" fill="#17385e">{escape(heading)}</text>')
            for offset, text in ((78, line1), (101, line2)):
                out.append(f'<text x="{x+17}" y="{y+offset}" font-size="16" fill="#4d6481">{escape(text)}</text>')
            if index < len(steps)-1:
                out.append(f'<path d="M{x+width+3} {y+70}H{x+width+25}" stroke="#5577ac" stroke-width="2" marker-end="url(#arrow)"/>')
    out.append(f'<rect x="32" y="{height-68}" width="1056" height="43" rx="8" fill="#fff6e6"/>')
    out.append(f'<text x="48" y="{height-40}" font-size="16" fill="#805619">{escape(note)}</text></g></svg>')
    (OUTPUT / name).write_text('\n'.join(out), encoding='utf-8')


diagram('flow-login.svg', '首次登录与飞书绑定', '先使用已分配的员工账号，再完成本人密码修改和飞书绑定。', [
    ('首次使用', [('获取账号', '正式地址、本人用户名', '初始密码由管理者通知'), ('账号登录', '输入账号和密码', '登录后核对右上角姓名'), ('修改密码', '个人中心 → 修改密码', '输入当前密码及两次新密码'), ('绑定飞书', '个人中心 → 扫码绑定', '以后可使用飞书扫码登录')]),
], '按钮提示未配置时，继续使用账号密码登录，并联系运维人员；不要绑定他人的飞书账号。')

diagram('flow-task.svg', '从创建到交付：谁来处理下一步', '责任人负责执行；指定确认人负责确认安排和验收结果。', [
    ('安排工作', [('创建任务', '填写责任域、责任人', '截止日期与交付要求'), ('确认安排', '指定确认人核对', '驳回则补充后重新提交'), ('开始执行', '责任人点击“开始”', '任务进入“进行中”')]),
    ('交付闭环', [('反馈进展', '比例、说明与阻塞原因', '提交后等待进度确认'), ('提交交付', '补齐交付物或链接', '点击“完成并提交验收”'), ('确认人验收', '通过 → 已完成', '驳回 → 继续处理并重提')]),
], '每周例行任务的 100% 进度确认后仍需验收；其他任务的 100% 进度完成规则见正文。')

diagram('flow-routines.svg', '多人例行任务：名单明确、每人一份', '按模板直接多选实际承担人员，同岗位员工也可以配置不同工作。', [
    ('负责人 / 管理 PM', [('建立模板', '填写名称和责任域', '选中张三、李四'), ('设置工时', '每人每周标准 2 小时', '保存后核对名单'), ('生成任务', '每周自动生成下周任务', '当周缺人时手动补生成')]),
    ('所选每位员工', [('各收一条任务', '张三 2 小时、李四 2 小时', '两人的任务和进度独立'), ('填报累计工时', '本周先填 3 再填 5', '最新累计值为 5 小时'), ('分别提交验收', '各自补充交付说明', '指定确认人逐条验收')]),
], '移除人员或修改模板只影响后续生成；已有任务保留。重复补生成会跳过已生成的员工。')

diagram('flow-assistant.svg', '智能助手：先核对，再确认执行', '在飞书打开“科技产品中心智能助手”；示例为对话步骤示意，并非飞书界面截图。', [
    ('维护基础资料 / 修改或删除任务', [('说明目标', '例：新建部门 数据产品部', '使用本人已绑定飞书发送'), ('补齐信息', '缺少字段时继续补充', '重名对象使用 #编号'), ('核对回显', '检查对象、人员和字段', '有误回复“取消”'), ('确认执行', '核对正确后回复“确认”', '查看成功提示并核对网页')]),
], '资料维护限负责人和管理 PM；助手不会因消息自称管理人员而提权。待确认操作通常 10 分钟内有效。')

diagram('flow-domains.svg', '责任域维护：修改同步历史，删除先看引用', '入口：组织配置 → 责任域维护。只有负责人和管理 PM 可以维护。', [
    ('新增与修改', [('新增责任域', '填写名称和上级节点', '按需设置负责人及边界'), ('在业务中选择', '创建任务、草稿或模板', '统一使用责任域编号关联'), ('修改后同步', '名称及标签更新到历史', '任务状态与统计不会重算')]),
    ('删除分支 A：已经被引用', [('查看引用情况', '任务、模板、草稿或下级', '历史业务也可能保留引用'), ('停止删除', '删除按钮禁用', '不能删除后重建来改名'), ('使用修改', '保留编号与历史关联', '直接更新名称和责任边界')]),
    ('删除分支 B：没有任何引用', [('查看引用情况', '核对列表的引用提示', '确认没有业务和历史引用'), ('点击删除', '核对弹窗中的名称', '取消则保留原责任域'), ('确认删除', '该责任域从列表移除', '服务端会再次核对引用')]),
], '已完成任务、停用模板及删除任务的历史业务记录仍可能构成引用；以系统显示的引用情况为准。')

diagram('flow-reports.svg', '周报与月报：编辑、保存、固定版本', '编辑后先确认“已自动保存”，再发布或归档；两类报表的处理人不同。', [
    ('部门周报：PM 整理，部门负责人发布', [('打开本周', '核对周期和任务快照', '缺少时自动补生成草稿'), ('编辑并等保存', '补充摘要、总结和说明', '任务变化可更新任务数据'), ('发布周报', '负责人点击发布', '发布版本只读'), ('需要修改', '负责人点击“修订”', '形成修订稿后重新发布')]),
    ('个人月报：本人生成、编辑与归档', [('选择月份', '生成 / 打开我的月报', '只生成本人报告'), ('编辑节点', '新增、删除和上下移动', '输入后等待自动保存'), ('归档月报', '本人核对后归档', '归档版本只读'), ('需要修改', '点击“新建修订稿”', '修改后再次归档')]),
], '保存失败或版本冲突时保留输入并先处理提示；管理者查看他人月报时只读，不能替本人编辑。')
